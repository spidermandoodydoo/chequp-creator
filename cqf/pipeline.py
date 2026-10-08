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
    why = "--no-broll" if skip else None if cfg["farm"].get("machines") else "no farm.machines in this config"
    if not why:
        try:
            farm.render_shots(cfg, list(shots.values()), use_5090)
        except RuntimeError as e:
            why = str(e)
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
            raise RuntimeError(f"scene {i}: no b-roll plate ({shot.error or why or 'no plate made'}) and no fallback_src, "
                               "so this scene can only use an AI plate")
    return unverified


def _lines(s: dict) -> list[dict]:
    """A scene's VO as [{voice, text}]: a plain string is one announcer line; a list is dialogue."""
    vo = s.get("vo")
    if not vo:
        return []
    if isinstance(vo, str):
        return [{"voice": "announcer", "text": vo}]
    return [x if isinstance(x, dict) else {"voice": "announcer", "text": str(x)} for x in vo]


def _role(ln: dict) -> str:
    r = ln.get("voice", "announcer")
    return {"cheqqup": "chequp"}.get(r, r)


def _cast(board: dict, pv: dict, ln: dict) -> dict:
    """Full voice spec for one VO line. Later wins: preset defaults < preset voice.cast[role] <
    board.voices[role] < preset voice.styles[line.style] < keys on the line itself.
    Spec keys: engine, voice (Kokoro id or blend "a,b"), speed, ref + exaggeration/cfg_weight/temperature/
    takes/seed (Chatterbox), pause_before. A role/board value may be a bare Kokoro voice id string."""
    role = _role(ln)
    spec = {"engine": pv.get("engine", "kokoro"), "voice": board.get("voice") or pv["voices"][0],
            "speed": float(pv.get("speed", 1.0)), **(pv.get("chatterbox") or {})}
    for layer in ((pv.get("cast") or {}).get(role), (board.get("voices") or {}).get(role),
                  (pv.get("styles") or {}).get(ln.get("style")),
                  {k: v for k, v in ln.items() if k not in ("voice", "text", "style", "pause_after")}):
        spec.update({"voice": layer} if isinstance(layer, str) else (layer or {}))
    return spec


def _speak_line(voice, spec: dict, text: str, wav: Path, cb_py: str | None, lang: str, sr: int, lexicon: dict,
                where: str) -> tuple[float, list[dict], str, str | None]:
    """One VO line -> (seconds, words, engine used, fallback reason or None). A Chatterbox line whose env,
    reference clip or worker isn't there is read by Kokoro (the spec's voice/speed) with a warning naming it."""
    why = None
    if spec.get("engine", "kokoro") == "chatterbox":
        ref = spec.get("ref")
        ref = str(ROOT / ref) if ref and not Path(ref).is_absolute() else ref
        if not cb_py or not Path(cb_py).exists():
            why = f"chatterbox env missing ({cb_py or 'voice.chatterbox_python not set'})"
        elif not ref or not Path(ref).is_file():
            why = f"reference clip missing ({spec.get('ref')}; run scripts/make_voice_refs.py on the PC)"
        else:
            try:
                d, words = voice.speak(text, wav, engine="chatterbox", opts={**spec, "ref": ref, "python": cb_py}, lexicon=lexicon)
                return d, words, "chatterbox", None
            except Exception as e:  # the upgrade must never cost the render: Kokoro reads the line instead
                why = f"{type(e).__name__}: {e}"
        print(f'  WARNING voice {where}: Chatterbox unavailable, Kokoro fallback ({why}): "{text[:80]}"')
    d, words = voice.speak(text, wav, spec.get("voice") or "bf_emma", float(spec.get("speed") or 1.0), lang, sr,
                           engine="kokoro", lexicon=lexicon)
    return d, words, "kokoro", why


def _voice(cfg: dict, board: dict, ep: Path, skip: bool):
    """Synthesize VO per scene (one announcer line or a dialogue of several voices), stretch scenes
    to fit, record each scene's start (for SFX) and line timings, build captions + one vo.wav.
    Engine per line from _cast (Chatterbox; Kokoro when it can't run). Lines -> vo_raw.wav -> voice.master
    -> vo.wav. board.audio.voice_engines = [{scene, line, role, engine, fallback}] says what each line
    actually used (REPORT.md); engine "none" = rendered silent, with the reason in fallback."""
    from . import voice
    vcfg, pv = cfg["voice"], cfg["_preset"]["voice"]
    if not any(_lines(s) for s in board["scenes"]):
        return
    use_tts = not skip and vcfg.get("backend", "tts") in ("tts", "kokoro", "chatterbox")
    silent = "--no-voice" if skip else None if use_tts else f"voice backend {vcfg.get('backend')}"
    if use_tts:
        try:
            import kokoro  # noqa: F401
        except ImportError:
            print("  kokoro not installed — rendering silent with estimated caption timing")
            use_tts, silent = False, "kokoro not installed"
    cb_py = vcfg.get("chatterbox_python")
    cb_py = str(ROOT / cb_py) if cb_py and not Path(cb_py).is_absolute() else cb_py
    sr, lang, lex = int(vcfg.get("sample_rate", 24000)), vcfg.get("lang_code", "b"), pv.get("lexicon") or {}
    if use_tts and cb_py and Path(cb_py).exists() and not voice.worker_ready(cb_py) and any(
            sp.get("engine") == "chatterbox" and sp.get("ref") and (ROOT / sp["ref"]).is_file()
            for s in board["scenes"] for sp in (_cast(board, pv, ln) for ln in _lines(s))):
        voice.free_comfy_vram((cfg.get("farm") or {}).get("machines"))   # b-roll models still in VRAM would starve it
    vdir = ep / "vo"
    vdir.mkdir(exist_ok=True)
    words_all, segs, engines, t = [], [], [], 0.0
    lead, gap = 0.15, float(pv.get("dialogue_gap_s", 0.3))
    tr = board.get("transition") or {}
    xf = tr.get("dur", 0.43) if tr.get("type") == "fade" else 0
    for i, s in enumerate(board["scenes"]):
        s["_t0"] = round(t, 3)
        lines, cur, times = _lines(s), lead, []
        for j, ln in enumerate(lines):
            spec, role = _cast(board, pv, ln), _role(ln)
            cur += float(spec.get("pause_before") or 0)          # e.g. the tagline's beat
            if use_tts:
                wav = vdir / f"s{i:02d}_{j}.wav"
                d, words, eng, why = _speak_line(voice, spec, ln["text"], wav, cb_py, lang, sr, lex, f"scene {i} line {j} ({role})")
                segs.append((t + cur, wav))
            else:
                d = len(ln["text"].split()) / pv.get("words_per_second", 2.8)
                words, eng, why = voice.estimate_words(ln["text"], 0, d), "none", silent
            engines.append({"scene": i, "line": j, "role": role, "engine": eng, "fallback": why})
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
    audio = board.setdefault("audio", {})
    audio["voice_engines"] = engines
    if segs:
        out = bytearray(int(t * sr) * 2)
        for start, wav in segs:
            pcm = voice.pcm16(wav, sr)
            o = int(start * sr) * 2
            out[o:o + len(pcm)] = pcm[: max(0, len(out) - o)]
        raw = ep / "vo_raw.wav"
        with wave.open(str(raw), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr), w.writeframes(bytes(out))
        try:
            voice.master(raw, ep / "vo.wav", pv.get("master") or {})
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"  WARNING voice master chain failed ({e}); using the unpolished VO stem")
            shutil.copyfile(raw, ep / "vo.wav")
        audio["vo"] = str(ep / "vo.wav")
        n_cb = sum(e["engine"] == "chatterbox" for e in engines)
        print(f"  voice: {n_cb}/{len(engines)} lines Chatterbox" + "".join(
            f"\n    Kokoro fallback scene {e['scene']} line {e['line']} ({e['role']}): {e['fallback']}" for e in engines if e["fallback"]))


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
    """Generated bed under the VO (or as the only audio, like the live template). music.backend:
    ace_step = ACE-Step 1.5 on the PC's ComfyUI (procedural bed if it can't run), procedural (default)
    = numpy bed. Records audio.music_engine (ace_step | procedural | file | none) for REPORT.md."""
    mc = cfg.get("music", {})
    backend = mc.get("backend") or "procedural"
    if backend not in ("ace_step", "procedural"):
        raise ValueError(f"music.backend must be ace_step or procedural, not {backend!r}")
    audio = board.setdefault("audio", {})
    if audio.get("music"):
        audio["music_engine"] = "file"
        return
    if not mc.get("generate"):
        audio["music_engine"] = "none"
        return
    from . import music
    tr = board.get("transition") or {}
    xf = tr.get("dur", 0.43) if tr.get("type") == "fade" else 0
    dur = sum(float(s["dur"]) for s in board["scenes"]) - xf * (len(board["scenes"]) - 1)
    live = board.get("theme") == "meta-live"
    mood = "bright" if live else "warm"
    out = None
    if backend == "ace_step":       # board length + 1 s, whole seconds (ace_bed rounds up); render.mjs trims it
        try:
            out = music.ace_bed(cfg, dur + 1, ep / "music.wav", mood, int(mc.get("seed", 7)))
        except Exception as e:  # noqa: BLE001 — anything ace_bed didn't foresee (odd server reply): music never costs the board
            print(f"  music: ACE-Step failed ({type(e).__name__}: {str(e)[:200]}); procedural bed instead")
            out = None
    engine = "ace_step" if out else "procedural"
    if out is None:
        out = music.bed(dur + 0.5, ep / "music.wav", mood, 100 if live else 86)
    has_vo = bool(audio.get("vo"))
    audio.update(music=str(out), music_engine=engine,
                 music_gain_db=mc.get("gain_db_under_vo", -21) if has_vo else mc.get("gain_db_solo", -6))


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


class _Tee:
    """stdout that still prints but keeps a copy: farm/music name the reason for a fallback only in print."""
    def __init__(self, out):
        self.out, self.buf = out, []

    def write(self, s):
        self.buf.append(s)
        return self.out.write(s)

    def flush(self):
        self.out.flush()

    def isatty(self):
        return False

    @property
    def encoding(self):
        return getattr(self.out, "encoding", "utf-8")

    def __getattr__(self, name):          # fileno, buffer, errors, ...: the real stream's
        return getattr(self.out, name)

    def text(self) -> str:
        return "".join(self.buf)


def _tee_call(fn, *args):
    """Run fn(*args) with stdout teed; returns (result, printed text)."""
    import contextlib
    import sys
    tee = _Tee(sys.stdout)
    with contextlib.redirect_stdout(tee):
        return fn(*args), tee.text()


_WHY = {"broll": r"b-roll unavailable \((.*)\); using fallback stills",            # _broll
        "music": r"music: ACE-Step (?:unavailable|failed) \((.*)\); procedural bed instead"}   # music.ace_bed


def _reasons(text: str) -> dict:
    import re
    out = {}
    for k, rx in _WHY.items():
        m = re.search(rx, text)
        if m:
            out[k] = m.group(1)[:300]
    return out


def _broll_log(cfg: dict) -> tuple[Path, dict]:
    f = path(cfg, "out_dir") / "broll" / "broll_log.json"
    try:
        return f, (json.loads(f.read_text()) if f.exists() else {})
    except ValueError:
        return f, {}


def _broll_record(cfg: dict, board: dict, formats: list[str] | None, why: str | None,
                  before: dict | None = None) -> tuple[list, str | None]:
    """(shots, engine) after _broll: one shot per b-roll scene and aspect, from the scene's src_by_aspect
    (an AI plate) or its fallback still, with score/UNVERIFIED/error from out/broll/broll_log.json.
    engine: qwen (every shot an AI plate), fallback (none), mixed, or None (no b-roll scenes).
    A cached plate (made by an earlier run or board) comes back from farm.render_shots with score 0, not
    UNVERIFIED and no log, and its broll_log.json entry is overwritten. Its real vision-check record is taken
    from `before` (the log as it was before _broll) and written back; with no real record it counts as
    UNVERIFIED, so a plate nobody vision-checked can never take a board off HOLD."""
    import hashlib
    log_f, log = _broll_log(cfg)
    before, kept = before or {}, {}
    shots = []
    for i, s in enumerate(board["scenes"]):
        if not (s.get("type") in ("media", "glass", "logo") and s.get("broll") and not s.get("src_locked")):
            continue
        h = hashlib.sha1(s["broll"].encode()).hexdigest()[:8]           # same key as _broll's farm.Shot
        for asp in _aspects(board, formats):
            key, src = f"{h}_{asp}", (s.get("src_by_aspect") or {}).get(asp)
            lg = log.get(key) or {}
            cached = bool(src) and not lg.get("log")
            if cached:
                old = before.get(key) or {}
                lg = kept[key] = old if old.get("log") else {**lg, "score": None, "unverified": True}
            shots.append({"scene": i, "aspect": asp, "key": key, "people": s.get("people"), "mode": s.get("broll_mode", "still"),
                          "ok": bool(src), "src": src or s.get("src"), "clip": bool(src) and str(src).lower().endswith(VIDEO),
                          "score": lg.get("score") if src else None, "unverified": bool(lg.get("unverified")) if src else None,
                          "cached": cached, "error": (why or lg.get("error")) if not src else None})
    real = {k: v for k, v in kept.items() if v.get("log")}
    if real:                          # put the real records back so the next run still has them
        log.update(real)
        try:
            log_f.write_text(json.dumps(log, indent=2))
        except OSError:
            pass
    n_ai = sum(1 for x in shots if x["ok"])
    return shots, (None if not shots else "qwen" if n_ai == len(shots) else "fallback" if not n_ai else "mixed")


def _engines(board: dict, broll: str | None) -> dict:
    """What actually made this board (REPORT.md): voice per line from _voice, music from _music, b-roll."""
    a = board.get("audio") or {}
    return {"voice": a.get("voice_engines") or [], "music": a.get("music_engine"), "broll": broll}


def produce(cfg: dict, board_path: Path, formats: list[str] | None = None, *, use_5090=False, skip_broll=False,
            skip_voice=False, allow_hold=True, clips=False) -> dict:
    """lint -> b-roll -> voice -> music -> sfx -> render. state.json records `engines` {voice: one entry per VO line,
    music, broll}, the b-roll `shots`, `errors` and `fallback_reasons` as it goes. A board that raises ends as
    status "error" (with the error) and the exception goes on to the caller: `cqf make` carries on with the
    next board. clips=True: every AI b-roll shot becomes a Wan 2.2 i2v clip from its chosen still."""
    import traceback
    now = dt.datetime.now().isoformat(timespec="seconds")
    try:
        board = compliance.fix_board(json.loads(Path(board_path).read_text(encoding="utf-8")))
    except Exception as e:
        ep = path(cfg, "out_dir") / "episodes" / Path(board_path).stem
        ep.mkdir(parents=True, exist_ok=True)
        _state(ep, status="error", verdict=None, started=now, board_file=str(board_path), files=[], engines={}, shots=[],
               errors=[f"board file: {type(e).__name__}: {e}"])
        raise
    ep = path(cfg, "out_dir") / "episodes" / board["id"]
    ep.mkdir(parents=True, exist_ok=True)
    _save(ep / "board.json", board)
    rec = {"engines": {}, "shots": [], "errors": [], "fallback_reasons": {}, "ai_clips": 0}
    _state(ep, status="started", verdict=None, started=now, board_file=str(board_path), files=[], approvals_needed=[],
           broll_skipped=bool(skip_broll), clips_requested=bool(clips), blocked_reason=None, traceback=None, **rec)
    try:
        v, issues = lint(board)
        (ep / "lint.txt").write_text("\n".join(map(str, issues)) or "clean", encoding="utf-8")
        print(f"{board['id']}: lint {v}" + "".join(f"\n  {i}" for i in issues if i.level != "WARN"))
        if v == "FAIL" or (v == "HOLD" and not allow_hold):
            bad = [str(i) for i in issues if i.level == v]
            _state(ep, status="blocked", verdict=v, blocked_reason=f"lint {v}" + ("" if v == "FAIL" else " refused (--strict)"),
                   errors=[f"lint FAIL: {x}" for x in bad] if v == "FAIL" else [])
            return {"id": board["id"], "verdict": v, "files": []}
        _state(ep, status="linted", verdict=v)
        if clips:
            for s in board["scenes"]:
                if s.get("broll"):
                    s["broll_mode"] = "clip"
        _, before = _broll_log(cfg)
        unverified, said = _tee_call(_broll, cfg, board, ep, use_5090, skip_broll, formats)
        rec["fallback_reasons"].update(_reasons(said))
        rec["shots"], broll = _broll_record(cfg, board, formats, rec["fallback_reasons"].get("broll"), before)
        unverified = bool(unverified) or any(x["ok"] and x["unverified"] for x in rec["shots"])   # cached, unchecked
        rec["ai_clips"] = sum(1 for x in rec["shots"] if x["clip"])
        rec["engines"] = _engines(board, broll)
        _state(ep, status="broll", broll_unverified=unverified, **rec)
        _voice(cfg, board, ep, skip_voice)
        _, said = _tee_call(_music, cfg, board, ep)
        rec["fallback_reasons"].update(_reasons(said))
        _sfx(board, ep)
        rec["engines"] = _engines(board, broll)
        _state(ep, status="voice", **rec)
        files = []
        for fmt in formats or board.get("formats") or cfg["render"]["formats"]:
            out = _render(cfg, board, ep, fmt)
            files.append({"format": fmt, "file": str(out), **_probe(out)})
        needs = sorted({i.rule.split(' — ')[0] for i in issues if i.level == "HOLD"})
        if unverified:
            needs.append("b-roll vision check (plates UNVERIFIED: check by eye)")
            v = "HOLD" if v == "PASS" else v
        _state(ep, status="rendered", verdict=v, files=files, approvals_needed=needs, **rec)
        return {"id": board["id"], "verdict": v, "files": files}
    except Exception as e:
        rec["engines"] = _engines(board, rec["engines"].get("broll"))
        rec["errors"].append(f"{type(e).__name__}: {e}"[:2000])
        _state(ep, status="error", traceback=traceback.format_exc()[-4000:], **rec)
        raise


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
