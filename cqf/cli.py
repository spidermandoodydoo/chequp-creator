"""python -m cqf <command>

  doctor                 check node/ffmpeg/playwright, LLM, CheqUp's ComfyUI + GPU gate, b-roll models, Kokoro/Chatterbox, ACE-Step
  lint <board.json>...   compliance + brand-voice check (no rendering)
  plan <concept> [-n 3]  hook variants via the configured LLM (claude -p) → concepts/variants/
  make <board.json>...   lint → b-roll → voice → music → sfx → render all formats
  batch [--top 5]        make every concept in insights priority order (+ its variants)
  outbox                 collect renders into out/outbox/<date>/ with ads_sheet.csv + preview page
  report [board...]      out/REPORT.md + report.json: what actually made each piece (engines, shots, errors)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import requests

from . import compliance, config, farm, gpu_gate, pipeline, planner
from .config import ROOT


def _boards(paths: list[str]) -> list[Path]:
    out = []
    for p in paths:
        p = Path(p)
        if not p.exists() and (ROOT / "concepts" / f"{p}.json").exists():
            p = ROOT / "concepts" / f"{p}.json"
        out += sorted(p.glob("*.json")) if p.is_dir() else [p]
    return out


def _comfy_cheq(cfg: dict):
    """CheqUp's own ComfyUI (url, version) and the GPU gate (doctor). Read-only: GETs only, never waits."""
    url, cc = gpu_gate.cheq_url(cfg), cfg.get("comfy_cheq") or {}
    if url:
        try:
            ver = requests.get(f"{url}/system_stats", timeout=5).json().get("system", {}).get("comfyui_version", "?")
            print(f"ok   CheqUp ComfyUI {url} v{str(ver).lstrip('v')} ({cc.get('dir') or 'comfy_cheq.dir not set'})")
        except (requests.RequestException, ValueError, AttributeError):
            print(f"DOWN CheqUp ComfyUI {url} ({cc.get('dir') or '?'}): powershell -NoProfile -ExecutionPolicy Bypass -File "
                  "scripts/comfy_cheq_start.ps1 (first time: scripts/install_comfy_cheq_pc.ps1)")
    st = gpu_gate.status(cfg)
    if not st["enabled"]:
        print("off  gpu_gate: disabled (CheqUp does not wait for other GPU users)" + (
            f"; never sends jobs to {', '.join(y['url'] for y in st['yield_to'])}" if st["yield_to"] else ""))
        return
    ys = []
    for y in st["yield_to"]:
        q = y["state"]
        ys.append(f"{y['name']} {y['url']} (" + ("not answering" if q is None else "stalled: no reply to GET /queue, the gate waits"
                                                 if q == gpu_gate.SLOW else f"{q[0]} running, {q[1]} queued") + ")")
    g = gpu_gate.settings(cfg)
    need = {k: gpu_gate.need_for(cfg, k) for k in gpu_gate.DEFAULT_NEED}
    free = "no nvidia-smi" if st["free_gb"] is None else f"{st['free_gb']:.1f} GB free now"
    print(f"ok   gpu_gate: yields to {', '.join(ys) or 'nothing'}; needs VRAM GB {need} ({free}"
          + (f", CheqUp's ComfyUI holds {st['own_held_gb']:.1f} GB" if st.get("own_held_gb") else "") + "); "
          f"polls every {g.get('poll_s', 30)} s, gives up after {float(g.get('max_wait_s', 21600)) / 3600:g} h")


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
    pv, cb = cfg["_preset"].get("voice", {}), cfg["voice"].get("chatterbox_python")
    if pv.get("engine") == "chatterbox" or any((c or {}).get("engine") == "chatterbox" for c in (pv.get("cast") or {}).values() if isinstance(c, dict)):
        cbp = cb and (Path(cb) if Path(cb).is_absolute() else ROOT / cb)    # voice: a Kokoro fallback exists, so never fails doctor
        print(f"{'ok  ' if cbp and cbp.exists() else 'MISS'} chatterbox env {cb} (scripts/setup_local.sh / setup_pc.ps1) — else Kokoro fallback")
        refs = {(pv.get("chatterbox") or {}).get("ref")} | {c.get("ref") for c in (pv.get("cast") or {}).values() if isinstance(c, dict)}
        for ref in sorted(r for r in refs if r):
            have = (ROOT / ref).is_file()
            print(f"{'ok  ' if have else 'MISS'} chatterbox reference {ref}" + ("" if have else " (python scripts/make_voice_refs.py, on the PC) — else Kokoro fallback"))
        try:
            import faster_whisper  # noqa: F401
            print("ok   faster-whisper (take picking + caption timings)")
        except ImportError:
            print("MISS faster-whisper — first Chatterbox take is used, captions estimated")
        try:
            import parselmouth  # noqa: F401
            print("ok   praat-parselmouth (rejects takes with pitch squeaks)")
        except ImportError:
            print("MISS praat-parselmouth — takes ranked on Whisper alone")
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
    _comfy_cheq(cfg)
    live = farm.servers(cfg, use_5090=a.use_5090, need_clips=getattr(a, "clips", False))
    print(f"{'ok  ' if live else 'DOWN'} ComfyUI farm: {len(live)} servers {[s.url for s in live]}")
    mbk = (cfg.get("music") or {}).get("backend") or "procedural"
    if mbk not in ("ace_step", "procedural"):        # pipeline._music raises on this, so every board would stop
        ok = False
        print(f"FAIL music: music.backend {mbk!r} must be ace_step or procedural")
    elif mbk == "ace_step":    # music: a fallback exists, so ACE-Step being unavailable never fails doctor
        from . import music
        srv, why = music.ace_server(cfg, music.ace_graph(cfg))
        print(f"ok   music: ACE-Step 1.5 on {srv.url}" if srv else f"WARN music: ACE-Step 1.5 unavailable ({why}); procedural bed instead")
    else:
        print("ok   music: procedural bed (music.backend: procedural)")
    return 0 if ok else 1


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="cqf", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml", help="config.yaml = this machine (no GPU); config.pc.yaml = Dan's 5090 PC (all media generation)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor"); d.add_argument("--use-5090", action="store_true")
    d.add_argument("--clips", action="store_true", help="also require the Wan 2.2 i2v clip models (pc_run -Clips)")
    li = sub.add_parser("lint"); li.add_argument("boards", nargs="+")
    pl = sub.add_parser("plan"); pl.add_argument("concept"); pl.add_argument("-n", type=int, default=3); pl.add_argument("--brief", default="")
    mk = sub.add_parser("make"); mk.add_argument("boards", nargs="+")
    ba = sub.add_parser("batch"); ba.add_argument("--top", type=int, default=5); ba.add_argument("--variants", action="store_true")
    for p in (mk, ba):
        p.add_argument("--format", action="append", dest="formats", help="9x16 | 4x5 | 1x1 | 16x9 (repeatable; default: the board's formats)")
        p.add_argument("--use-5090", action="store_true", help="also queue b-roll on the PC 5090 (CheqUp's own ComfyUI; gpu_gate yields to shorts-factory)")
        p.add_argument("--no-broll", action="store_true", help="skip GPU b-roll; use each scene's fallback_src")
        p.add_argument("--no-voice", action="store_true")
        p.add_argument("--strict", action="store_true", help="refuse HOLD boards instead of rendering drafts")
        p.add_argument("--clips", action="store_true", help="every AI b-roll shot becomes a Wan 2.2 i2v clip (slow; pc_run -Clips)")
    ob = sub.add_parser("outbox"); ob.add_argument("--campaign", default="chequp_method")
    from . import report
    report.add_parser(sub)
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
        results = []
        for b in boards:          # one failed board must not cost the rest: its state.json says status "error"
            if gpu_gate.gave_up():    # the GPU gate already waited max_wait_s for shorts-factory: start nothing more
                print(f"SKIP {Path(b).stem}: not started (GPU gate gave up earlier in this run)")
                results.append(pipeline.skip_board(cfg, b, gpu_gate.gave_up()))
                continue
            try:
                results.append(pipeline.produce(cfg, b, a.formats, use_5090=a.use_5090, skip_broll=a.no_broll,
                                                skip_voice=a.no_voice, allow_hold=not a.strict, clips=a.clips))
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print(f"ERROR {Path(b).stem}: {type(e).__name__}: {e} (carrying on with the next board)")
                results.append({"id": Path(b).stem, "verdict": "ERROR", "files": []})
        print("\nsummary")
        for r in results:
            print(f"  {r['verdict']:5} {r['id']}: " + ", ".join(f"{f['format']} {f['dur']}s {f['mb']}MB" for f in r["files"]))
        if any(r["verdict"] in ("ERROR", "FAIL", "SKIP") for r in results) or gpu_gate.gave_up():
            sys.exit(1)       # a board failed or was skipped (pc_run.ps1 reads out/REPORT.md / report.json for which)
        return
    if a.cmd == "report":
        sys.exit(report.run(cfg, a))
    if a.cmd == "outbox":
        print("outbox ->", pipeline.outbox(cfg, a.campaign))
