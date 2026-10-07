"""Hook/angle variants of a concept, written by Qwen on mama (or Claude as fallback),
linted, and re-asked with the lint findings until they pass."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import compliance, llm
from .config import ROOT


def system_prompt(cfg: dict) -> str:
    pre, ins = cfg["_preset"], cfg["_insights"]
    evidence = {k: ins[k] for k in ("account", "proof", "offer", "competitors", "compliance_history")}
    text = (ROOT / "prompts" / "storyboard.md").read_text(encoding="utf-8")
    for k, v in {"{never}": "\n".join(f"- {n}" for n in pre["never"]),
                 "{broll_subjects}": ", ".join(pre["broll"]["allowed_subjects"]),
                 "{insights}": yaml.safe_dump(evidence, sort_keys=False, allow_unicode=True, width=120)}.items():
        text = text.replace(k, v)
    return text


def variants(cfg: dict, concept_path: Path, n: int = 3, brief: str = "", max_rounds: int = 3) -> list[Path]:
    base = json.loads(Path(concept_path).read_text(encoding="utf-8"))
    concept = next((c for c in cfg["_insights"]["concepts"] if c["id"] == base.get("concept")), {})
    ask = (f"Concept: {concept.get('name', base.get('concept'))} — why it should work: {concept.get('why', '')}\n"
           f"Here is the approved base storyboard:\n{json.dumps(base, ensure_ascii=False, indent=1)}\n\n"
           f"Write {n} variants that keep the structure and proof but each test a DIFFERENT hook in scene 0 "
           f"(different first line, and different first-scene type where it helps). Keep fallback_src values. "
           f"ids must be '{base['id']}-h<1..{n}>' and variant 'h<1..{n}>'. {brief}")
    sys = system_prompt(cfg)
    out_dir = ROOT / "concepts" / "variants"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    feedback = ""
    for rnd in range(max_rounds):
        reply = llm.complete(cfg, sys, ask + feedback)
        boards = llm.extract_json(reply)
        boards = boards if isinstance(boards, list) else [boards]
        failed = []
        for b in boards:
            b = compliance.fix_board(b)
            issues = compliance.lint_board(b)
            v = compliance.verdict(issues)
            if v == "FAIL":
                failed.append((b.get("id"), [str(i) for i in issues if i.level == "FAIL"]))
                continue
            p = out_dir / f"{b['id']}.json"
            p.write_text(json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8")
            saved.append(p)
            print(f"  {b['id']}: {v}")
        if len(saved) >= n or not failed:
            break
        feedback = "\n\nThese variants were REJECTED by compliance — rewrite them:\n" + json.dumps(failed, indent=1)
        print(f"  round {rnd + 1}: {len(failed)} rejected, re-asking")
    return saved
