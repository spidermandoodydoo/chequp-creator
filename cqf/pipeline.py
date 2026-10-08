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


def _aspects(board: dict, formats: list[str] | None) -> list[str]:
    fmts = formats or board.get("formats") or ["9x16"]
    out = []
    if any(f in ("9x16", "4x5", "1x1") for f in fmts):
        out.append("9x16")                 # 4:5 and 1:1 are crops of the 9:16 plate
    if "16x9" in fmts:
        out.append("16x9")                 # VOD gets its own native horizontal plate
    return out


def _broll(cfg: dict, board: dict, ep: Path, use_5090: bool, skip: bool, formats: list[str] | None = None) -> bool:
    """One plate per distinct (prompt, aspect); scenes sharing a prompt share it. Returns True if any
    plate is UNVERIFIED (no vision check), which keeps the board on HOLD."""
    import hashlib
    shots: dict[tuple, farm.Shot] = {}
    need = []
    for i, s in enumerate(board["scenes"]):
        if s.get("type") in ("media", "glass", "logo") and s.get("broll") and not s.get("src_locked"):
            if s["type"] != "media" and s.get("src") and not s.get("fallback_src"):
                s["fallback_src"] = s["src"]          # meta-live boards carry their still as the fallback
            for asp in _aspects(board, formats):
                k = (s["broll"], asp)
                if k not in shots:
                    h = hashlib.sha1(s["broll"].encode()).hexdigest()[:8]
                    # cached by prompt+aspect across ALL boards (out/broll/): a shot used by four boards renders once
                    shots[k] = farm.Shot(key=f"{h}_{asp}", prompt=s["broll"], out_dir=path(cfg, "out_dir") / "broll", aspect=asp,
                                         people=s.get("people"), mode=s.get("broll_mode", "still"),
                                         motion=s.get("motion", "The light stays steady. The camera pushes in very slowly."))
                need.append((i, s, asp, shots[k]))
    if not need:
        return False
    if not skip and cfg["farm"].get("machines"):
        try:
            farm.render_shots(cfg, list(shots.values()), use_5090)
        except RuntimeError as e:
            print(f"  b-roll unavailable ({e}); using fallback stills")
    unverified = False
    for i, s, asp, shot in need:
        if shot.result and shot.result.exists():
            s.setdefault("src_by_aspect", {})[asp] = str(shot.result)
            unverified |= shot.unverified
        elif s.get("fallback_src"):
            fb = s["fallback_src"]
            s["src"] = str((ROOT / fb).resolve()) if not Path(fb).is_absolute() else fb
        else:
            raise RuntimeError(f"scene {i}: no b-roll and no fallback_src")
    return unverified


def _lines(s: dict) -> list[dict]:
    """A scene's VO as [{voice, text}]: a plain string is one announcer line; a list is dialogue."""
    vo = s.get("vo")
    if not vo:
        return []
    if isinstance(vo, str):
        return [{"voice": "announcer", "text": vo}]
    return [x if isinstance(x, dict) else {"voice": "announcer", "text": str(x)} for x in vo]


def _cast(board: dict, pv: dict, role: str) -> tuple[str, float]:
    """Voice id + speed for a role: board.voices > preset voice.cast > board.voice > first preset voice."""
    role = {"cheqqup": "chequp"}.get(role, role)
    c = (board.get("voices") or {}).get(role) or (pv.get("cast") or {}).get(role) or {}
    if isinstance(c, str):
        c = {"voice": c}
    return c.get("voice") or board.get("voice") or pv["voices"][0], float(c.get("speed") or pv.get("speed", 1.0))


def _voice(cfg: dict, board: dict, ep: Path, skip: bool):
    """Synthesize VO per scene (one announcer line or a dialogue of several voices), stretch scenes
    to fit, record each scene's start (for SFX) and line timings, build captions + one vo.wav."""
    from . import voice
    vcfg, pv = cfg["voice"], cfg["_preset"]["voice"]
    if not any(_lines(s) for s in board["scenes"]):
        return
    use_tts = not skip and vcfg.get("backend") == "kokoro"
    if use_tts:
        try:
            import kokoro  # noqa: F401
        except ImportError:
            print("  kokoro not installed — rendering silent with estimated caption timing")
            use_tts = False
    vdir = ep / "vo"
    vdir.mkdir(exist_ok=True)
    words_all, segs, t = [], [], 0.0
    lead, gap = 0.15, float(pv.get("dialogue_gap_s", 0.3))
    tr = board.get("transition") or {}
    xf = tr.get("dur", 0.43) if tr.get("type") == "fade" else 0
    for i, s in enumerate(board["scenes"]):
        s["_t0"] = round(t, 3)
        lines, cur, times = _lines(s), lead, []
        for j, ln in enumerate(lines):
            vid, spd = _cast(board, pv, ln.get("voice", "announcer"))
            if use_tts:
                wav = vdir / f"s{i:02d}_{j}.wav"
                d, words = voice.speak(ln["text"], wav, vid, spd, vcfg.get("lang_code", "b"), vcfg.get("sample_rate", 24000))
                segs.append((t + cur, wav))
            else:
                d = len(ln["text"].split()) / pv.get("words_per_second", 2.8)
                words = voice.estimate_words(ln["text"], 0, d)
            words_all += [{**w, "s": round(w["s"] + t + cur, 3), "e": round(w["e"] + t + cur, 3)} for w in words]
            times.append([round(cur, 3), round(cur + d, 3)])
            cur += d + (float(ln.get("pause_after", gap)) if j < len(lines) - 1 else 0)
        if lines:
            s["_line_times"] = times
            s["dur"] = round(max(float(s["dur"]), cur + 0.35), 2)
        t += float(s["dur"]) - xf
    t += xf
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


def _sfx(board: dict, ep: Path):
    """Scene-level sfx [{at (s from scene start), kind, dur?}] → one track; 'silence' ducks the music."""
    from . import sfx
    cues, quiet = [], []
    t = 0.0
    tr = board.get("transition") or {}
    xf = tr.get("dur", 0.43) if tr.get("type") == "fade" else 0
    for s in board["scenes"]:
        t0 = s.get("_t0", t)
        for c in s.get("sfx") or []:
            at = t0 + float(c.get("at", 0))
            if c.get("kind") == "silence":
                quiet.append((at, at + float(c.get("dur", 1.5))))
            else:
                cues.append({**c, "at": at})
        t = t0 + float(s["dur"]) - xf
    total = sum(float(s["dur"]) for s in board["scenes"]) - xf * (len(board["scenes"]) - 1)
    if cues:
        board.setdefault("audio", {})["sfx"] = str(sfx.track(cues, total, ep / "sfx.wav"))
    music = (board.get("audio") or {}).get("music")
    if quiet and music and Path(music).exists() and str(music).startswith(str(ep)):
        sfx.silence_music(Path(music), quiet)


def _music(cfg: dict, board: dict, ep: Path):
    """Generated bed under the VO (or as the only audio, like the live template)."""
    mc = cfg.get("music", {})
    if not mc.get("generate") or (board.get("audio") or {}).get("music"):
        return
    from . import music
    tr = board.get("transition") or {}
    xf = tr.get("dur", 0.43) if tr.get("type") == "fade" else 0
    dur = sum(float(s["dur"]) for s in board["scenes"]) - xf * (len(board["scenes"]) - 1)
    live = board.get("theme") == "meta-live"
    out = music.bed(dur + 0.5, ep / "music.wav", "bright" if live else "warm", 100 if live else 86)
    has_vo = bool((board.get("audio") or {}).get("vo"))
    board.setdefault("audio", {}).update(music=str(out), music_gain_db=mc.get("gain_db_under_vo", -21) if has_vo else mc.get("gain_db_solo", -6))


def _render(cfg: dict, board: dict, ep: Path, fmt: str) -> Path:
    b = copy.deepcopy(board)
    b["format"], b["fps"] = fmt, cfg["render"]["fps"]
    asp = "16x9" if fmt == "16x9" else "9x16"
    for s in b["scenes"]:
        by = s.pop("src_by_aspect", None)
        if by:
            s["src"] = by.get(asp) or next(iter(by.values()))
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
    unverified = _broll(cfg, board, ep, use_5090, skip_broll, formats)
    _state(ep, status="broll", broll_unverified=unverified)
    _voice(cfg, board, ep, skip_voice)
    _music(cfg, board, ep)
    _sfx(board, ep)
    _state(ep, status="voice")
    files = []
    for fmt in formats or board.get("formats") or cfg["render"]["formats"]:
        out = _render(cfg, board, ep, fmt)
        files.append({"format": fmt, "file": str(out), **_probe(out)})
    needs = sorted({i.rule.split(' — ')[0] for i in issues if i.level == "HOLD"})
    if unverified:
        needs.append("b-roll vision check (plates UNVERIFIED: check by eye)")
        v = "HOLD" if v == "PASS" else v
    _state(ep, status="rendered", verdict=v, files=files, approvals_needed=needs)
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
