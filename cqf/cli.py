"""python -m cqf <command>

  doctor                 check node/ffmpeg/playwright, mama's LM Studio + ComfyUI farm, Kokoro
  lint <board.json>...   compliance + brand-voice check (no rendering)
  plan <concept> [-n 3]  hook variants via Qwen on mama → concepts/variants/
  make <board.json>...   lint → b-roll → voice → render all formats
  batch [--top 5]        make every concept in insights priority order (+ its variants)
  outbox                 collect renders into out/outbox/<date>/ with ads_sheet.csv + preview page
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import requests

from . import compliance, config, farm, pipeline, planner
from .config import ROOT


def _boards(paths: list[str]) -> list[Path]:
    out = []
    for p in paths:
        p = Path(p)
        if not p.exists() and (ROOT / "concepts" / f"{p}.json").exists():
            p = ROOT / "concepts" / f"{p}.json"
        out += sorted(p.glob("*.json")) if p.is_dir() else [p]
    return out


def doctor(cfg: dict, a):
    ok = True
    for tool in ("node", "ffmpeg", "ffprobe"):
        print(f"{'ok  ' if shutil.which(tool) else 'MISS'} {tool}")
        ok &= bool(shutil.which(tool))
    r = subprocess.run(["node", "-e", "import('playwright').then(()=>console.log('ok')).catch(()=>{try{require('playwright');console.log('ok')}catch(e){console.log('no')}})"],
                       capture_output=True, text=True, cwd=ROOT / "render")
    print(f"{'ok  ' if 'ok' in r.stdout else 'MISS'} playwright (cd render && npm install && npx playwright install chromium)")
    try:
        import kokoro  # noqa: F401
        print("ok   kokoro")
    except ImportError:
        print("MISS kokoro (pip install kokoro soundfile) — renders will be silent")
    if cfg["llm"]["backend"] != "lmstudio":
        from .llm import find_claude
        print(f"{'ok  ' if find_claude() else 'MISS'} LLM backend {cfg['llm']['backend']} (local profile)")
    else:
      try:
        models = requests.get(f"{cfg['llm']['base_url']}/models", timeout=5).json().get("data", [])
        ids = [m["id"] for m in models]
        print(f"{'ok  ' if cfg['llm']['model'] in ids else 'IDLE'} LM Studio on mama: {ids or 'no model loaded'}")
      except requests.RequestException:
        print("DOWN LM Studio on mama (render phase, or mama offline) — planning falls back to claude -p")
    live = farm.servers(cfg, use_5090=a.use_5090)
    print(f"{'ok  ' if live else 'DOWN'} ComfyUI farm: {len(live)} servers {[s.url for s in live]}")
    return 0 if ok else 1


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="cqf", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml", help="config.yaml = this machine; config.mama.yaml = mama's GPUs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor"); d.add_argument("--use-5090", action="store_true")
    li = sub.add_parser("lint"); li.add_argument("boards", nargs="+")
    pl = sub.add_parser("plan"); pl.add_argument("concept"); pl.add_argument("-n", type=int, default=3); pl.add_argument("--brief", default="")
    mk = sub.add_parser("make"); mk.add_argument("boards", nargs="+")
    ba = sub.add_parser("batch"); ba.add_argument("--top", type=int, default=5); ba.add_argument("--variants", action="store_true")
    for p in (mk, ba):
        p.add_argument("--format", action="append", dest="formats", help="9x16 | 4x5 | 1x1 (repeatable; default all)")
        p.add_argument("--use-5090", action="store_true", help="also queue b-roll on the PC 5090 (shared with shorts-factory)")
        p.add_argument("--no-broll", action="store_true", help="skip GPU b-roll; use each scene's fallback_src")
        p.add_argument("--no-voice", action="store_true")
        p.add_argument("--strict", action="store_true", help="refuse HOLD boards instead of rendering drafts")
    ob = sub.add_parser("outbox"); ob.add_argument("--campaign", default="chequp_method")
    a = ap.parse_args(argv)
    cfg = config.load(a.config)

    if a.cmd == "doctor":
        sys.exit(doctor(cfg, a))
    if a.cmd == "lint":
        worst = 0
        for b in _boards(a.boards):
            import json
            issues = compliance.lint_board(compliance.fix_board(json.loads(b.read_text(encoding="utf-8"))))
            v = compliance.verdict(issues)
            worst = max(worst, {"PASS": 0, "HOLD": 1, "FAIL": 2}[v])
            print(f"{v:4} {b.name}" + "".join(f"\n     {i}" for i in issues))
        sys.exit(2 if worst == 2 else 0)
    if a.cmd == "plan":
        paths = planner.variants(cfg, _boards([a.concept])[0], a.n, a.brief)
        print(f"{len(paths)} variants saved")
        return
    if a.cmd in ("make", "batch"):
        if a.cmd == "make":
            boards = _boards(a.boards)
        else:
            top = sorted(cfg["_insights"]["concepts"], key=lambda c: c["priority"])[: a.top]
            boards = [ROOT / "concepts" / f"{c['id']}.json" for c in top if (ROOT / "concepts" / f"{c['id']}.json").exists()]
            if a.variants:
                boards += [p for c in top for p in sorted((ROOT / "concepts" / "variants").glob(f"{c['id']}-*.json"))]
        results = [pipeline.produce(cfg, b, a.formats, use_5090=a.use_5090, skip_broll=a.no_broll, skip_voice=a.no_voice,
                                    allow_hold=not a.strict) for b in boards]
        print("\nsummary")
        for r in results:
            print(f"  {r['verdict']:4} {r['id']}: " + ", ".join(f"{f['format']} {f['dur']}s {f['mb']}MB" for f in r["files"]))
        return
    if a.cmd == "outbox":
        print("outbox ->", pipeline.outbox(cfg, a.campaign))
