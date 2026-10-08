"""REPORT.md for a render run: what actually made each piece, for the cloud session and Dan.

  python -m cqf --config config.pc.yaml report [boards...] [--since ISO] [--run-id ID] [--log F] [--note TEXT]...
  python -m cqf.report pack --zip out/logs/review_<ts>.zip [--log F] [--config config.pc.yaml]
  python -m cqf.report boards [tokens...] [--formats 9x16,4x5]    # pc_run.ps1: -Boards/-Formats -> files (JSON)
  python -m cqf.report info [--config config.pc.yaml]             # pc_run.ps1: config facts it checks (JSON)

It reads out/episodes/<id>/state.json, where pipeline.produce() records `engines` {voice: one entry per VO
line (Chatterbox, or Kokoro with the fallback reason), music: ace_step | procedural | file | none, broll:
qwen | fallback | mixed}, the b-roll `shots` and `errors`. Episodes from before produce() recorded engines
get them guessed from the episode folder, marked "guessed".

report.json (next to REPORT.md) carries the same facts plus the run's exit code, which pc_run.ps1 uses:
0 ok, 1 a board failed, 8 fallbacks used, 9 the run crashed (pre-flight codes 2-7 are pc_run's own).
Nothing here makes media; ffmpeg only cuts contact sheets and thumbnails for the review zip."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .config import ROOT

EXIT = {0: "ok", 1: "a board failed", 2: "tools missing", 3: "ComfyUI down", 4: "ComfyUI too old",
        5: "Python env or config", 6: "models missing", 7: "unknown board or format", 8: "fallbacks used",
        9: "run crashed", 10: "already running", 11: "still running"}
VIDEO = (".mp4", ".mov", ".webm")
FORMATS = ("9x16", "4x5", "1x1", "16x9")
_BROLL_TYPES = ("media", "glass", "logo")


def _load(p: Path) -> dict | None:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _when(s) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(s)) if s else None
    except ValueError:
        return None


def _out_dir(cfg: dict) -> Path:
    return ROOT / cfg.get("out_dir", "out")


def _rel(p) -> str:
    """Repo-relative path where possible (shorter in REPORT.md), else as given."""
    try:
        return str(Path(p).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(p)


def farm_enabled(cfg: dict) -> bool:
    """Same machine rules as farm.servers() without --use-5090: is any ComfyUI expected to make b-roll?"""
    return any(m.get("enabled") is True or (m.get("enabled") is not False and m.get("role") != "hero")
               for m in (cfg.get("farm") or {}).get("machines") or [])


def voice_fallback(line: dict) -> str | None:
    """The reason a VO line didn't get its intended engine (None = it did, or silence was asked for)."""
    why = line.get("fallback")
    if not why or why == "--no-voice" or str(why).startswith("voice backend"):
        return None
    return str(why)


# --- old runs: guess the engines from the episode folder ---------------------------------------------

def _rendered_board(ep: Path) -> dict | None:
    for name in ("board_9x16.json", "board_16x9.json", "board_4x5.json", "board_1x1.json"):
        b = _load(ep / name)
        if b:
            return b
    return None


def _aspect_of(fmt: str) -> str:
    return "16x9" if fmt == "16x9" else "9x16"


def guess(cfg: dict, ep: Path) -> tuple[dict, list]:
    """(engines, shots) for an episode whose state.json predates engine recording."""
    rb = _rendered_board(ep) or _load(ep / "board.json") or {"scenes": []}
    audio = rb.get("audio") or {}
    voice = audio.get("voice_engines")
    if voice is None:               # before board.audio.voice_engines: Chatterbox leaves takes (sNN_J.tN.wav)
        voice = []
        names = {p.name for p in (ep / "vo").glob("*")} if (ep / "vo").is_dir() else set()
        for p in sorted(n for n in names if re.fullmatch(r"s\d\d_\d+\.wav", n)):
            i, j = (int(x) for x in p[1:-4].split("_"))
            cb = any(n.startswith(p[:-4] + ".t") or (n.startswith(p[:-4]) and n.endswith(".job.json")) for n in names)
            voice.append({"scene": i, "line": j, "role": "?", "engine": "chatterbox" if cb else "kokoro", "fallback": None,
                          "guessed": True})
    music = audio.get("music_engine")
    if music is None:
        m = audio.get("music")
        music = "none" if not m else "procedural" if str(m).startswith(str(ep)) else "file"
    broll_dir = str((_out_dir(cfg) / "broll").resolve())
    shots = []
    for i, s in enumerate(rb.get("scenes") or []):
        if s.get("type") in _BROLL_TYPES and s.get("broll"):
            src = s.get("src") or ""
            ai = bool(src) and str(Path(src).resolve()).startswith(broll_dir)
            shots.append({"scene": i, "aspect": _aspect_of(rb.get("format", "9x16")), "key": Path(src).stem if ai else None,
                          "people": s.get("people"), "mode": s.get("broll_mode", "still"), "ok": ai, "src": src,
                          "clip": ai and src.lower().endswith(VIDEO), "score": None, "unverified": None, "cached": None,
                          "error": None, "guessed": True})
    n_ai = sum(1 for s in shots if s["ok"])
    broll = None if not shots else "qwen" if n_ai == len(shots) else "fallback" if not n_ai else "mixed"
    return {"voice": voice, "music": music, "broll": broll, "guessed": True}, shots


# --- one board ------------------------------------------------------------------------------------------

def board_info(cfg: dict, ep: Path, since: dt.datetime | None = None, want: str | None = None) -> dict:
    st = _load(ep / "state.json")
    board = _load(ep / "board.json") or {}
    bid = board.get("id") or ep.name
    if st is None:
        return {"id": want or bid, "status": "missing", "verdict": None, "missing": True, "stale": False, "failed": False,
                "incomplete": False,
                "fallback": False, "fallbacks": [], "files": [], "shots": [], "voice": [], "music": None, "broll": None,
                "clips": 0, "errors": ["no state.json: make never reached this board"], "approvals_needed": [], "guessed": False}
    if "engines" in st:                                 # produce() always writes it; older states lack the key
        eng, shots, guessed = st["engines"] or {}, st.get("shots") or [], False
    else:
        eng, shots = guess(cfg, ep)
        guessed = True
    voice, music, broll = eng.get("voice") or [], eng.get("music"), eng.get("broll")
    why = st.get("fallback_reasons") or {}
    status, verdict = st.get("status"), st.get("verdict")
    if not guessed and status in ("voice", "rendered"):
        # Cross-track contract: _voice sets board.audio.voice_engines, _music sets audio.music_engine. If a step left
        # its key out, guess that piece (marked guessed) instead of reporting "no VO" / "no music".
        if not voice and any(s.get("vo") for s in board.get("scenes") or []):
            voice = guess(cfg, ep)[0]["voice"]
            guessed = guessed or bool(voice)
        if music is None:
            had = ((_rendered_board(ep) or board).get("audio") or {}).get("music")
            music, guessed = ("unknown" if had else "none"), True
    started = _when(st.get("started") or st.get("updated"))
    stale = bool(since and started and started < since)
    failed = status == "error" or (status == "blocked" and verdict == "FAIL")
    incomplete = not stale and status in ("started", "linted", "broll", "voice")     # its make was killed mid-board
    fb = []
    for ln in voice:
        r = voice_fallback(ln)
        if r:
            eng_used = {"kokoro": "Kokoro", "none": "silent"}.get(ln.get("engine"), ln.get("engine"))
            fb.append({"what": "voice", "detail": f"scene {ln.get('scene')} line {ln.get('line')} ({ln.get('role')}): {eng_used} ({r})"})
    if music == "procedural" and (cfg.get("music") or {}).get("backend") == "ace_step":
        fb.append({"what": "music", "detail": "procedural bed instead of ACE-Step" + (f" ({why['music']})" if why.get("music") else ""),
                   "reason": why.get("music")})
    if broll in ("fallback", "mixed") and farm_enabled(cfg) and not st.get("broll_skipped"):
        n_fb = sum(1 for s in shots if not s.get("ok"))
        fb.append({"what": "b-roll", "detail": f"{n_fb}/{len(shots)} shots used CheqUp's own stills"
                   + (f" ({why['broll']})" if why.get("broll") else "")})
    return {"id": bid, "status": status, "verdict": verdict, "missing": False, "stale": stale, "failed": failed,
            "incomplete": incomplete,
            "fallback": bool(fb), "fallbacks": fb, "files": st.get("files") or [], "shots": shots, "voice": voice,
            "music": music, "broll": broll, "broll_skipped": bool(st.get("broll_skipped")),
            "clips": sum(1 for s in shots if s.get("clip")), "errors": st.get("errors") or [],
            "approvals_needed": st.get("approvals_needed") or [], "guessed": guessed,
            "updated": st.get("updated"), "started": st.get("started"), "blocked_reason": st.get("blocked_reason")}


def exit_code(boards: list[dict], make_exit: int | None = None, outbox_exit: int | None = None) -> int:
    """The run's exit code from what the boards' states say (pc_run.ps1 returns it)."""
    if make_exit not in (None, 0, 1):
        return 9                                        # make itself died (killed, crashed before a board)
    if any(b["failed"] for b in boards):
        return 1
    if any(b["missing"] or b["stale"] or b.get("incomplete") for b in boards):
        return 9                                        # make stopped before finishing these boards
    if make_exit == 1:
        return 1
    if outbox_exit not in (None, 0):
        return 9
    if any(b["fallback"] for b in boards):
        return 8
    return 0


def _board_ids(tokens: list[str]) -> list[str]:
    """Board ids for files/ids given on the command line (an episode folder is named by board id)."""
    out = []
    for t in tokens:
        p = Path(t)
        if not p.exists() and (ROOT / "concepts" / f"{t}.json").exists():
            p = ROOT / "concepts" / f"{t}.json"
        files = sorted(p.glob("*.json")) if p.is_dir() else [p]
        for f in files:
            b = _load(f) if f.suffix == ".json" else None
            out.append((b or {}).get("id") or f.stem)
    return list(dict.fromkeys(out))


def _outbox(cfg: dict, given: str | None = None) -> dict | None:
    root = _out_dir(cfg) / "outbox"
    ob = Path(given) if given else max((d for d in root.glob("*") if (d / "ads_sheet.csv").exists()), default=None,
                                         key=lambda d: d.name)
    if not ob or not (ob / "ads_sheet.csv").exists():
        return None
    with open(ob / "ads_sheet.csv", encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("file")]
    return {"path": str(ob), "files": len(rows), "hold": sum(r.get("status") == "HOLD" for r in rows),
            "ready": sum(r.get("status") == "READY" for r in rows)}


def collect(cfg: dict, boards: list[str] | None = None, since: str | None = None, make_exit: int | None = None,
            outbox_exit: int | None = None, outbox: str | None = None, run: dict | None = None) -> dict:
    eps = _out_dir(cfg) / "episodes"
    t0 = _when(since)
    if boards:
        infos = [board_info(cfg, eps / bid, t0, want=bid) for bid in _board_ids(boards)]
    else:                                               # everything on disk, newest first
        dirs = sorted((d for d in eps.glob("*") if (d / "state.json").exists()),
                      key=lambda d: (_load(d / "state.json") or {}).get("updated") or "", reverse=True)
        infos = [board_info(cfg, d, t0) for d in dirs]
    code = exit_code(infos, make_exit, outbox_exit)
    return {"generated": dt.datetime.now().isoformat(timespec="seconds"), "host": platform.node(),
            "config": cfg.get("_config_name"), "run": run or {}, "since": since, "make_exit": make_exit,
            "outbox_exit": outbox_exit, "exit": code, "meaning": EXIT.get(code, "?"), "boards": infos,
            "outbox": _outbox(cfg, outbox), "music_backend": (cfg.get("music") or {}).get("backend", "procedural"),
            "farm_enabled": farm_enabled(cfg)}


# --- REPORT.md ------------------------------------------------------------------------------------------

def _not_reached(b: dict) -> bool:
    return b["status"] in ("error", "blocked", "missing", "started", "linted")


def _voice_cell(b: dict) -> str:
    v = b["voice"]
    if not v:
        return "-" if _not_reached(b) else "no VO"
    n = len(v)
    cb = sum(1 for x in v if x.get("engine") == "chatterbox")
    fb = sum(1 for x in v if voice_fallback(x))
    ko = sum(1 for x in v if x.get("engine") == "kokoro" and not voice_fallback(x))
    none = sum(1 for x in v if x.get("engine") == "none" and not voice_fallback(x))
    parts = ([f"Chatterbox {cb}/{n}"] if cb or fb else []) + ([f"Kokoro {ko}/{n} (by design)"] if ko else []) + \
            ([f"**{fb} fallback**"] if fb else []) + ([f"silent {none}/{n}"] if none else [])
    return ", ".join(parts)


def _music_cell(b: dict, backend: str) -> str:
    m = b["music"]
    if m is None:
        return "-" if _not_reached(b) else "none"
    name = {"ace_step": "ACE-Step", "procedural": "procedural bed", "file": "board's own file", "none": "none",
            "unknown": "not recorded"}.get(m, str(m))
    return f"**{name} (fallback)**" if any(f["what"] == "music" for f in b["fallbacks"]) else name


def _broll_cell(b: dict) -> str:
    sh = b["shots"]
    if not sh:
        return "-" if _not_reached(b) and b["broll"] is None else "no b-roll"
    ai = sum(1 for s in sh if s.get("ok"))
    txt = f"Qwen {ai}/{len(sh)}" + (f", own stills {len(sh) - ai}" if ai < len(sh) else "")
    if b.get("broll_skipped"):
        txt += " (--no-broll)"
    return f"**{txt}**" if any(f["what"] == "b-roll" for f in b["fallbacks"]) else txt


def _cell(x) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def render_md(data: dict) -> str:
    bs, run = data["boards"], data.get("run") or {}
    L = ["# CheqUp render report", ""]
    head = []
    if run.get("id"):
        head.append(f"- **Run:** `{run['id']}` on {data['host']} (`{data.get('config') or 'config'}`)"
                    + (f", started {run['started']}" if run.get("started") else "") + f", report {data['generated'].replace('T', ' ')}")
    else:
        head.append(f"- **Report:** {data['generated'].replace('T', ' ')} on {data['host']} (`{data.get('config') or 'config'}`)")
    if run.get("flags"):
        head.append(f"- **Flags:** `{run['flags']}`")
    n_r = sum(1 for b in bs if b["status"] == "rendered")
    n_f = sum(1 for b in bs if b["failed"])
    n_o = len(bs) - n_r - n_f
    head.append(f"- **Result:** exit {data['exit']} ({data['meaning']}). {len(bs)} board{'' if len(bs) == 1 else 's'}: {n_r} rendered, {n_f} failed"
                + (f", {n_o} other (blocked, missing or not run this time)" if n_o else ""))
    ob = data.get("outbox")
    head.append(f"- **Outbox:** `{_rel(ob['path'])}` ({ob['files']} file{'' if ob['files'] == 1 else 's'}: {ob['hold']} HOLD, {ob['ready']} READY; "
                f"`ads_sheet.csv`, `index.html`)" if ob else "- **Outbox:** none built")
    lines = [x for b in bs for x in b["voice"]]
    shots = [s for b in bs for s in b["shots"]]
    musics = [b["music"] for b in bs if b["music"]]
    eng = ["voice " + (_voice_cell({"voice": lines, "status": "rendered"}).replace("**", "") if lines else "no VO lines"),
           (f"music ACE-Step {musics.count('ace_step')}/{musics.count('ace_step') + musics.count('procedural')} beds"
            + (f", procedural {musics.count('procedural')}" if "procedural" in musics else "")
            + (f", board's own file {musics.count('file')}" if "file" in musics else "")
            + (f", no music {musics.count('none')}" if "none" in musics else "")
            + (f", not recorded {musics.count('unknown')}" if "unknown" in musics else "")),
           f"b-roll Qwen plates {sum(1 for s in shots if s.get('ok'))}/{len(shots)} shots",
           f"AI video clips: {sum(1 for s in shots if s.get('clip'))}"]
    head.append("- **Engines:** " + " · ".join(eng))
    if any(b.get("guessed") for b in bs):
        head.append("- Some engines were not recorded (a run from before engine recording, or a step that didn't record it): "
                    "those are guessed from the episode folder and marked guessed.")
    L += head + [""]
    if run.get("notes"):
        L += ["## Pre-flight notes", ""] + [f"- {n}" for n in run["notes"]] + [""]
    fbs = [(b["id"], f) for b in bs for f in b["fallbacks"]]
    L += ["## Fallbacks", ""]
    L += [f"- **{bid}** {f['what']}: {f['detail']}" for bid, f in fbs] if fbs else ["None: every piece came from its intended local model."]
    L += ["", "## Boards", "", "| Board | Verdict | Status | Files | Voice | Music | B-roll | AI clips |", "|---|---|---|---|---|---|---|---|"]
    for b in bs:
        st = b["status"] + (" (not run this time)" if b["stale"] else "")
        L.append(f"| {_cell(b['id'])} | {b['verdict'] or '-'} | {'**' + st + '**' if b['failed'] or b['missing'] else st} | {len(b['files'])} | "
                 f"{_voice_cell(b)} | {_music_cell(b, data['music_backend'])} | {_broll_cell(b)} | {b['clips']} |")
    for b in bs:
        L += ["", f"### {b['id']}: {b['verdict'] or '-'} ({b['status']})", ""]
        if b["stale"]:
            L.append(f"Not run this time: this state is from {b.get('started') or b.get('updated')}.")
        if b.get("incomplete"):
            L.append(f"**Did not finish:** make stopped during this board (at status {b['status']}): killed or crashed.")
        if b.get("blocked_reason"):
            L.append(f"Blocked: {b['blocked_reason']}")
        if b["approvals_needed"]:
            L.append("Approvals needed: " + "; ".join(b["approvals_needed"]))
        if b["files"]:
            L += ["", "| Format | Seconds | MB | Size | Audio | File |", "|---|---|---|---|---|---|"]
            L += [f"| {f.get('format')} | {f.get('dur')} | {f.get('mb')} | {f.get('w')}x{f.get('h')} | {'yes' if f.get('audio') else '**no**'} "
                  f"| `{_rel(f.get('file', ''))}` |" for f in b["files"]]
        elif not b["missing"]:
            L.append("No files rendered.")
        if b["shots"]:
            L += ["", "B-roll shots:", "", "| Scene | Aspect | Result | Score | Note |", "|---|---|---|---|---|"]
            for s in b["shots"]:
                kind = "Wan clip" if s.get("clip") else "Qwen still"
                res = f"ok ({kind})" if s.get("ok") else "**fallback still**"
                note = []
                if s.get("ok") and s.get("unverified"):
                    note.append("**UNVERIFIED** (no vision check: check by eye)")
                if s.get("ok") and s.get("cached"):
                    note.append("cached plate" + ("" if s.get("unverified") else ": vision check from when it was made"))
                if s.get("error"):
                    note.append(_cell(s["error"])[:200])
                if s.get("guessed"):
                    note.append("guessed")
                score = s.get("score")
                L.append(f"| {s.get('scene')} | {s.get('aspect')} | {res} | {score if score else '-'} | {'; '.join(note) or '-'} |")
        if b["voice"]:
            L += ["", "Voice:", "", "| Scene | Line | Role | Engine | Note |", "|---|---|---|---|---|"]
            for x in b["voice"]:
                r = voice_fallback(x)
                e = {"chatterbox": "Chatterbox", "kokoro": "Kokoro", "none": "silent"}.get(x.get("engine"), str(x.get("engine")))
                L.append(f"| {x.get('scene')} | {x.get('line')} | {x.get('role')} | {('**' + e + ' (fallback)**') if r else e} | "
                         f"{_cell(r or x.get('fallback') or ('guessed' if x.get('guessed') else '-'))[:200]} |")
        mus = next((f.get("reason") or "ACE-Step unavailable" for f in b["fallbacks"] if f["what"] == "music"), None)
        L += ["", f"Music: {_music_cell(b, data['music_backend'])}" + (f" ({mus})" if mus else "")]
        L += ["", "Errors: none"] if not b["errors"] else ["", "Errors:", ""] + [f"- {_cell(e)[:600]}" for e in b["errors"]]
    if run.get("log") or run.get("pack"):
        L += ["", "## Files", ""]
        if run.get("log"):
            L.append(f"- Log: `{_rel(run['log'])}`")
        if run.get("pack"):
            L.append(f"- Review zip: `{_rel(run['pack'])}` (this report, the log, ads sheet, posters, a 12-frame contact sheet "
                     "per board, b-roll plate thumbnails, state files)")
    return "\n".join(L) + "\n"


def json_path(report: Path) -> Path:
    """report.json next to REPORT.md (lower-case: pc_run.ps1 reads out/report.json)."""
    report = Path(report)
    return report.with_name(report.stem.lower() + ".json")


def write(cfg: dict, data: dict, out: Path | None = None) -> Path:
    out = Path(out) if out else _out_dir(cfg) / "REPORT.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_md(data), encoding="utf-8")
    json_path(out).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return out


# --- review zip -----------------------------------------------------------------------------------------

def _ffmpeg(args: list[str]) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True)
    return r.returncode == 0


def contact_sheet(video: Path, dur: float, out: Path, n: int = 12) -> Path | None:
    """n frames spread evenly over the video, tiled 4x3 (frames centred in n equal slices)."""
    if not video.exists() or not dur:
        return None
    fps = 30.0
    picks = sorted({min(int(dur * fps) - 1, max(0, round(dur * (k + 0.5) / n * fps))) for k in range(n)})
    sel = "+".join(f"eq(n\\,{p})" for p in picks)
    w = 320 if "16x9" in video.name else 180
    ok = _ffmpeg(["-i", str(video), "-vf", f"select='{sel}',scale={w}:-2,tile=4x3:padding=4:margin=4:color=white",
                  "-frames:v", "1", "-q:v", "4", str(out)])
    return out if ok and out.exists() else None


def thumb(src: Path, out: Path, width: int = 360) -> Path | None:
    if not src.exists():
        return None
    pre = ["-ss", "1"] if src.suffix.lower() in VIDEO else []
    ok = _ffmpeg([*pre, "-i", str(src), "-vf", f"scale={width}:-2", "-frames:v", "1", "-q:v", "4", str(out)])
    return out if ok and out.exists() else None


def pack(cfg: dict, zip_path: Path, log: str | None = None, report: Path | None = None) -> Path:
    """The review zip: REPORT.md + report.json, the log, the ads sheet, posters, a 12-frame contact sheet per
    board, b-roll plate thumbnails and the state files of the boards in report.json."""
    report = Path(report) if report else _out_dir(cfg) / "REPORT.md"
    data = _load(json_path(report)) or {"boards": []}
    eps = _out_dir(cfg) / "episodes"
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        tmp = Path(tmp)

        def add(p, arc):
            if p and Path(p).is_file():
                z.write(p, arc)
        if data.get("generated"):         # only a report.json that exists: never a previous run's REPORT.md
            add(report, "REPORT.md")
            add(json_path(report), "report.json")
        if log:
            add(log, Path(log).name)
        ob = data.get("outbox") or {}
        if ob.get("path"):
            add(Path(ob["path"]) / "ads_sheet.csv", "ads_sheet.csv")
        for b in data.get("boards", []):
            ep, bid = eps / b["id"], b["id"]
            for name in ("state.json", "lint.txt", "board.json"):
                add(ep / name, f"boards/{bid}/{name}")
            files = b.get("files") or []
            for f in files:
                vid = Path(f.get("file", ""))
                add(vid.with_name(vid.stem + "_poster.png"), f"boards/{bid}/{vid.stem}_poster.png")
            main = next((f for f in files if f.get("format") == "9x16"), files[0] if files else None)
            if main:
                sheet = contact_sheet(Path(main["file"]), float(main.get("dur") or 0), tmp / f"{bid}_contact.jpg")
                add(sheet, f"boards/{bid}/contact_{main.get('format')}.jpg")
            for s in b.get("shots") or []:
                if s.get("src"):
                    tag = ("clip" if s.get("clip") else "qwen") if s.get("ok") else "fallback"
                    t = thumb(Path(s["src"]), tmp / f"{bid}_{s.get('scene')}_{s.get('aspect')}.jpg")
                    add(t, f"boards/{bid}/broll/scene{s.get('scene')}_{s.get('aspect')}_{tag}.jpg")
    return zip_path


# --- helpers for pc_run.ps1 -----------------------------------------------------------------------------

def resolve_boards(tokens: list[str], concepts: Path | None = None) -> tuple[list[Path], list[str]]:
    """-Boards tokens -> board files. A token is `all`, a path, a glob, a board id (concepts/<id>.json) or a
    group (concepts/<group>-*.json); commas split tokens. No tokens: concepts/numan-*.json if there are any,
    else concepts/made-simple-*.json. Returns (files, unknown tokens)."""
    concepts = concepts or ROOT / "concepts"
    base = concepts.parent
    toks = [t.strip().strip('"').strip("'") for x in tokens for t in str(x).split(",") if t.strip()]
    if not toks:
        files = sorted(concepts.glob("numan-*.json")) or sorted(concepts.glob("made-simple-*.json"))
        return files, ([] if files else ["(default: no concepts/numan-*.json or concepts/made-simple-*.json)"])
    out, unknown = [], []
    for t in toks:
        t2 = t.replace("\\", "/") if os.sep == "/" else t
        p = Path(t2)
        if not p.is_absolute():
            p = base / p
        if t.lower() == "all":
            found = sorted(concepts.glob("*.json"))
        elif any(c in t for c in "*?["):
            found = sorted(Path(x) for x in glob.glob(str(p))) or sorted(concepts.glob(t2)) or sorted(concepts.glob(t2 + ".json"))
        elif p.is_file():
            found = [p]
        elif p.is_dir():
            found = sorted(p.glob("*.json"))
        elif (concepts / f"{t2}.json").is_file():
            found = [concepts / f"{t2}.json"]
        else:
            found = sorted(concepts.glob(f"{t2}-*.json"))
        found = [f for f in found if f.suffix.lower() == ".json" and f.is_file()]
        if not found:
            unknown.append(t)
        out += found
    seen, files = set(), []
    for f in out:
        k = str(f.resolve()).lower() if os.name == "nt" else str(f.resolve())
        if k not in seen:
            seen.add(k)
            files.append(f.resolve())
    return files, unknown


def groups(concepts: Path | None = None) -> list[str]:
    """Prefixes shared by two or more boards (made-simple, numan-assistance, ...), for -Boards hints."""
    concepts = concepts or ROOT / "concepts"
    stems = [p.stem for p in concepts.glob("*.json")]
    pre = {"-".join(s.split("-")[:k]) for s in stems for k in range(1, s.count("-") + 1)}
    mem = {g: frozenset(s for s in stems if s.startswith(g + "-")) for g in pre}
    return sorted(g for g in pre if len(mem[g]) >= 2 and not any(h.startswith(g + "-") and mem[h] == mem[g] for h in pre))


def info(cfg: dict) -> dict:
    """The config facts pc_run.ps1 checks before a run."""
    machines = (cfg.get("farm") or {}).get("machines") or []
    on = [m for m in machines if m.get("enabled") is True or (m.get("enabled") is not False and m.get("role") != "hero")]
    local = ("127.0.0.1", "localhost", "::1")
    return {"voice_backend": (cfg.get("voice") or {}).get("backend", "tts"),
            "chatterbox_python": (cfg.get("voice") or {}).get("chatterbox_python"),
            "music_backend": (cfg.get("music") or {}).get("backend", "procedural"),
            "music_generate": bool((cfg.get("music") or {}).get("generate")),
            "factory_dir": (cfg.get("shorts_factory") or {}).get("dir"),
            "farm": [{"name": m.get("name"), "host": m.get("host"), "port": m.get("first_port"), "gpus": m.get("gpus"),
                      "enabled": m in on} for m in machines],
            "remote_enabled": [m.get("name") or m.get("host") for m in on if str(m.get("host")) not in local],
            "formats": list((cfg.get("_preset") or {}).get("formats") or FORMATS)}


# --- CLI ------------------------------------------------------------------------------------------------

def add_parser(sub):
    """`cqf report` (cli.py)."""
    rp = sub.add_parser("report", help="REPORT.md + report.json from out/episodes/*/state.json")
    rp.add_argument("boards", nargs="*", help="board files or ids (default: every episode on disk)")
    rp.add_argument("--since", help="ISO time the run started: boards not updated since then are 'not run this time'")
    rp.add_argument("--run-id"), rp.add_argument("--log"), rp.add_argument("--pack-path", dest="pack_path")
    rp.add_argument("--flags", default="", help="pc_run options, for the header")
    rp.add_argument("--note", action="append", default=[], help="pre-flight note (repeatable)")
    rp.add_argument("--make-exit", type=int), rp.add_argument("--outbox-exit", type=int)
    rp.add_argument("--outbox", help="outbox folder (default: the newest out/outbox/<date>)")
    rp.add_argument("--out", help="REPORT.md path (default out/REPORT.md; report.json goes next to it)")
    return rp


def run(cfg: dict, a) -> int:
    cfg.setdefault("_config_name", getattr(a, "config", None))
    started = None
    if a.since:
        started = a.since.replace("T", " ")
    data = collect(cfg, a.boards, a.since, a.make_exit, a.outbox_exit, a.outbox,
                   run={"id": a.run_id, "started": started, "flags": a.flags, "notes": a.note, "log": a.log,
                        "pack": a.pack_path})
    out = write(cfg, data, a.out)
    print(f"report -> {out} (exit {data['exit']}: {data['meaning']})")
    for b in data["boards"]:
        print(f"  {b['verdict'] or '-':4} {b['status']:9} {b['id']}: voice {_voice_cell(b)}; music {_music_cell(b, data['music_backend'])}; "
              f"b-roll {_broll_cell(b)}".replace("**", ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    from . import config
    ap = argparse.ArgumentParser(prog="python -m cqf.report")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pk = sub.add_parser("pack")
    pk.add_argument("--zip", required=True), pk.add_argument("--log"), pk.add_argument("--report")
    bd = sub.add_parser("boards")
    bd.add_argument("tokens", nargs="*"), bd.add_argument("--formats", default="")
    sub.add_parser("info")
    a = ap.parse_args(argv)
    cfg = config.load(a.config)
    cfg["_config_name"] = a.config
    if a.cmd == "pack":
        print(f"review zip -> {pack(cfg, Path(a.zip), a.log, a.report)}")
    elif a.cmd == "boards":
        files, unknown = resolve_boards(a.tokens)
        fmts = [f.strip() for f in a.formats.split(",") if f.strip()]
        allowed = info(cfg)["formats"]
        print(json.dumps({"boards": [str(f) for f in files], "unknown": unknown, "formats": fmts,
                          "bad_formats": [f for f in fmts if f not in allowed], "allowed_formats": allowed,
                          "groups": groups(), "ids": sorted(p.stem for p in (ROOT / "concepts").glob("*.json"))}))
    else:
        print(json.dumps(info(cfg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
