"""LLM access. Default is Qwen3-235B on mama via LM Studio's OpenAI-compatible API,
so planning doesn't burn Claude limits (the bottleneck that stalled shorts-factory on
Oct 2-6). Falls back to headless `claude -p` when mama is in render phase."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import requests


class LLMUnavailable(RuntimeError):
    pass


def _lmstudio(cfg: dict, system: str, user: str) -> str:
    c = cfg["llm"]
    try:
        r = requests.post(f"{c['base_url']}/chat/completions", timeout=c.get("timeout_s", 600), json={
            "model": c["model"], "temperature": c.get("temperature", 0.7), "max_tokens": c.get("max_tokens", 6000),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        r.raise_for_status()
    except requests.RequestException as e:
        raise LLMUnavailable(f"LM Studio on mama: {e}") from e
    return r.json()["choices"][0]["message"]["content"]


def find_claude() -> str | None:
    """claude on PATH, else the desktop app's bundled exe (its folder depth changes between
    versions — shorts-factory hit this twice — so search rather than hard-code)."""
    exe = shutil.which("claude")
    if exe:
        return exe
    base = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code"
    hits = sorted(base.rglob("claude.exe"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    return str(hits[0]) if hits else None


def _claude_cli(cfg: dict, system: str, user: str) -> str:
    exe = find_claude()
    if not exe:
        raise LLMUnavailable("claude CLI not found")
    p = subprocess.run([exe, "-p", "--append-system-prompt", system, "--output-format", "text"],
                       input=user, capture_output=True, text=True, encoding="utf-8", timeout=cfg["llm"].get("timeout_s", 600))
    if p.returncode != 0:
        raise LLMUnavailable(f"claude -p failed: {p.stderr[-500:]}")
    return p.stdout


def _anthropic(cfg: dict, system: str, user: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=600, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": cfg["llm"].get("anthropic_model", "claude-sonnet-5-5"), "max_tokens": 8000, "system": system,
              "messages": [{"role": "user", "content": user}]})
    if r.status_code != 200:
        raise LLMUnavailable(f"Anthropic API {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json()["content"])


BACKENDS = {"lmstudio": _lmstudio, "claude_cli": _claude_cli, "anthropic_api": _anthropic}


def complete(cfg: dict, system: str, user: str) -> str:
    order = [cfg["llm"]["backend"]] + ([cfg["llm"]["fallback"]] if cfg["llm"].get("fallback") else [])
    errors = []
    for name in order:
        try:
            return BACKENDS[name](cfg, system, user)
        except LLMUnavailable as e:
            errors.append(str(e))
    raise LLMUnavailable("; ".join(errors))


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply (Qwen sometimes adds <think> or fences)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in reply")
    return json.JSONDecoder().raw_decode(text[start:])[0]
