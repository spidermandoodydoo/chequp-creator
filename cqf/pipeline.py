"""Storyboard → finished Meta video files. Mirrors shorts-factory's episode folders and
state.json statuses:  linted → broll → voice → rendered  (then `outbox` collects them)."""
from __future__ import annotations

import copy
import csv
import datetime as dt
import html
import json
import shutil
import subprocess
import wave
from pathlib import Path

from . import compliance, farm
from .config import ROOT, path

VIDEO = (".mp4", ".mov", ".webm")


def _save(p: Path, obj):
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _state(ep: Path, **kw) -> dict:
    f = ep / "state.json"
    st = json.loads(f.read_text()) if f.exists() else {}
    st.update(kw, updated=dt.datetime.now().isoformat(timespec="seconds"))
    _save(f, st)
    return st


def lint(board: dict) -> tuple[str, list]:
    issues = compliance.lint_board(board)
    return compliance.verdict(issues), issues


def _broll(cfg: dict, board: dict, ep: Path, use_5090: bool, skip: bool):
    shots, need = [], []
    for i, s in enumerate(board["scenes"]):
        if s.get("type") == "media" and s.get("broll") and not s.get("src_locked"):
            key = f"{board['id']}_s{i:02d}"
            shots.append(farm.Shot(key=key, prompt=s["broll"], motion=s.get("motion", "slow natural movement, gentle handheld camera"),
                                   out_dir=ep / "broll"))
            need.append((i, s))
    if not shots:
        return
    if not skip:
        try:
            farm.render_shots(cfg, shots, use_5090)
        except RuntimeError as e:
            print(f"  b-roll unavailable ({e}); using fallback stills")
    for (i, s), shot in zip(need, shots):
        if shot.result and shot.result.exists():
            s["src"] = str(shot.result)
        elif s.get("fallback_src"):
            s["src"] = str((ROOT / s["fallback_src"]).resolve()) if not Path(s["fallback_src"]).is_absolute() else s["fallback_src"]
        else:
            raise RuntimeError(f"scene {i}: no b-roll and no fallback_src")


def _voice(cfg: dict, board: dict, ep: Path, skip: bool):
    """Synthesize VO per scene, stretch scenes to fit their line, build captions + one vo.wav."""
    from . import voice
    vcfg, pv = cfg["voice"], cfg["_preset"]["voice"]
    lines = [(i, s) for i, s in enumerate(board["scenes"]) if s.get("vo")]
    if not lines:
        return
    use_tts = not skip and vcfg.get("backend") == "kokoro"
    if use_tts:
        try:
            import kokoro  # noqa: F401
        except ImportError:
            print("  kokoro not installed — rendering silent with estimated caption timing (pip install kokoro soundfile)")
            use_tts = False
    vdir = ep / "vo"
    vdir.mkdir(exist_ok=True)
    words_all, segs, t = [], [], 0.0
    lead = 0.15
    vname = board.get("voice") or pv["voices"][0]
    for i, s in enumerate(board["scenes"]):
        if s.get("vo"):
            if use_tts:
                wav = vdir / f"s{i:02d}.wav"
                d, words = voice.speak(s["vo"], wav, vname, pv.get("speed", 0.95), vcfg.get("lang_code", "b"), vcfg.get("sample_rate", 24000))
                segs.append((t + lead, wav))
            else:
                d = len(s["vo"].split()) / pv.get("words_per_second", 2.5)
                words = voice.estimate_words(s["vo"], 0, d)
            s["dur"] = round(max(float(s["dur"]), d + lead + 0.35), 2)
            words_all += [{**w, "s": round(w["s"] + t + lead, 3), "e": round(w["e"] + t + lead, 3)} for w in words]
        t += float(s["dur"])
    if board.get("captions") is None:
        board["captions"] = words_all
    if segs:
        sr = vcfg.get("sample_rate", 24000)
        out = bytearray(int(t * sr) * 2)
        for start, wav in segs:
            with wave.open(str(wav)) as w:
                pcm = w.readframes(w.getnframes())
            o = int(start * sr) * 2
            out[o:o + len(pcm)] = pcm[: max(0, len(out) - o)]
        with wave.open(str(ep / "vo.wav"), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr), w.writeframes(bytes(out))
        board.setdefault("audio", {})["vo"] = str(ep / "vo.wav")


def _render(cfg: dict, board: dict, ep: Path, fmt: str) -> Path:
    b = copy.deepcopy(board)
    b["format"], b["fps"] = fmt, cfg["render"]["fps"]
    for s in b["scenes"]:
        if s.get("src") and not Path(s["src"]).is_absolute():
            s["src"] = str((ROOT / s["src"]).resolve())
    if b.get("audio", {}).get("music") and not Path(b["audio"]["music"]).is_absolute():
        b["audio"]["music"] = str((ROOT / b["audio"]["music"]).resolve())
    bp = ep / f"board_{fmt}.json"
    _save(bp, b)
    cmd = [cfg["render"].get("node", "node"), str(ROOT / "render" / "render.mjs"), str(bp), "--out", str(ep / "renders"),
           "--format", fmt, "--jobs", str(cfg["render"].get("jobs", 4))]
    subprocess.run(cmd, check=True)
    return ep / "renders" / f"{b['id']}_{fmt}.mp4"


def _probe(p: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration,size", "-of", "json", str(p)],
                       capture_output=True, text=True)
    j = json.loads(r.stdout or "{}")
    v = next((s for s in j.get("streams", []) if s["codec_type"] == "video"), {})
    return {"w": v.get("width"), "h": v.get("height"), "audio": any(s["codec_type"] == "audio" for s in j.get("streams", [])),
            "dur": round(float(j.get("format", {}).get("duration", 0)), 2), "mb": round(int(j.get("format", {}).get("size", 0)) / 1e6, 1)}


def produce(cfg: dict, board_path: Path, formats: list[str] | None = None, *, use_5090=False, skip_broll=False,
            skip_voice=False, allow_hold=True) -> dict:
    board = compliance.fix_board(json.loads(Path(board_path).read_text(encoding="utf-8")))
    ep = path(cfg, "out_dir") / "episodes" / board["id"]
    ep.mkdir(parents=True, exist_ok=True)
    _save(ep / "board.json", board)
    v, issues = lint(board)
    (ep / "lint.txt").write_text("\n".join(map(str, issues)) or "clean", encoding="utf-8")
    print(f"{board['id']}: lint {v}" + "".join(f"\n  {i}" for i in issues if i.level != "WARN"))
    if v == "FAIL" or (v == "HOLD" and not allow_hold):
        _state(ep, status="blocked", verdict=v)
        return {"id": board["id"], "verdict": v, "files": []}
    _state(ep, status="linted", verdict=v)
    _broll(cfg, board, ep, use_5090, skip_broll)
    _state(ep, status="broll")
    _voice(cfg, board, ep, skip_voice)
    _state(ep, status="voice")
    files = []
    for fmt in formats or board.get("formats") or cfg["render"]["formats"]:
        out = _render(cfg, board, ep, fmt)
        files.append({"format": fmt, "file": str(out), **_probe(out)})
    _state(ep, status="rendered", verdict=v, files=files, approvals_needed=sorted({i.rule.split(' — ')[0] for i in issues if i.level == "HOLD"}))
    return {"id": board["id"], "verdict": v, "files": files}


def outbox(cfg: dict, campaign: str = "chequp_method") -> Path:
    """Collect rendered episodes into a dated upload folder + an Ads Manager sheet + a preview page.
    Nothing is sent to Meta: a human checks the HOLD column and uploads."""
    pre = cfg["_preset"]
    root = path(cfg, "out_dir")
    day = dt.date.today().isoformat()
    ob = root / "outbox" / day
    ob.mkdir(parents=True, exist_ok=True)
    rows = []
    for st_f in sorted((root / "episodes").glob("*/state.json")):
        st = json.loads(st_f.read_text())
        if st.get("status") != "rendered":
            continue
        ep = st_f.parent
        board = json.loads((ep / "board.json").read_text(encoding="utf-8"))
        meta = board.get("meta", {})
        for f in st.get("files", []):
            name = pre["publish"]["naming"].format(concept=board.get("concept", board["id"]), variant=board.get("variant", "a"),
                                                   format=f["format"], version=board.get("version", 1))
            dst = ob / f"{name}.mp4"
            shutil.copy2(f["file"], dst)
            poster = Path(f["file"]).with_name(Path(f["file"]).stem + "_poster.png")
            if poster.exists():
                shutil.copy2(poster, ob / f"{name}.png")
            url = meta.get("url", pre["landing_default"])
            utm = pre["publish"]["meta_utm"].format(campaign=campaign, ad_name=name)
            rows.append({"ad_name": name, "file": dst.name, "format": f["format"], "seconds": f["dur"],
                         "placements": ", ".join(pre["formats"][f["format"]].get("placements", [])),
                         "primary_text": meta.get("primary_text", ""), "headline": meta.get("headline", ""),
                         "description": meta.get("description", ""), "cta_button": meta.get("cta_button", "LEARN_MORE"),
                         "url": f"{url}{'&' if '?' in url else '?'}{utm}", "audience": board.get("audience", pre["audience"]["primary"]),
                         "status": "HOLD" if st.get("verdict") == "HOLD" else "READY",
                         "approvals_needed": "; ".join(st.get("approvals_needed", []))})
    with open(ob / "ads_sheet.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["ad_name"])
        w.writeheader()
        w.writerows(rows)
    cards = "".join(
        f'<article><video src="{html.escape(r["file"])}" controls muted playsinline preload="metadata" poster="{html.escape(r["file"][:-4])}.png"></video>'
        f'<h3>{html.escape(r["ad_name"])}</h3><p class="st {r["status"].lower()}">{r["status"]}{" · " + html.escape(r["approvals_needed"]) if r["approvals_needed"] else ""}</p>'
        f'<p><b>{html.escape(r["headline"])}</b><br>{html.escape(r["primary_text"])}</p><p class="m">{r["format"]} · {r["seconds"]}s · {html.escape(r["placements"])}</p></article>'
        for r in rows)
    (ob / "index.html").write_text(f"""<!doctype html><meta charset="utf-8"><title>CheqUp outbox {day}</title>
<style>body{{font-family:"Instrument Sans",system-ui,sans-serif;background:#FFF6EB;color:#210847;margin:0;padding:24px}}
h1{{font-weight:600;letter-spacing:-.02em}}main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:24px}}
article{{background:#fff;border-radius:24px;box-shadow:inset 0 0 0 1px rgba(33,8,71,.08);padding:16px}}video{{width:100%;border-radius:16px;background:#210847}}
h3{{font-size:14px;margin:12px 0 4px;word-break:break-all}}p{{font-size:14px;margin:6px 0;color:rgba(33,8,71,.72)}}.m{{font-size:12px}}
.st{{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.08em}}.hold{{color:#B33600}}.ready{{color:#007366}}</style>
<h1>CheqUp outbox · {day}</h1><p>{len(rows)} files. HOLD = needs the listed sign-off before upload. Import <code>ads_sheet.csv</code> alongside the files.</p><main>{cards}</main>""",
                                encoding="utf-8")
    return ob
