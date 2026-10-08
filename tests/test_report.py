"""REPORT.md / report.json, engine recording in produce(), make carrying on past a failed board, and the PC
runner scripts' static checks. No models, no GPU: fake episodes, monkeypatched pipeline steps and an ffmpeg
test pattern. Run: python -m pytest tests  (or python tests/test_report.py)."""
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cqf import cli, config, pipeline, report  # noqa: E402
from cqf.config import ROOT  # noqa: E402


def _cfg(tmp: Path, music="ace_step", farm=True) -> dict:
    cfg = config.load("config.yaml")
    cfg["out_dir"] = str(tmp / "out")
    cfg["music"]["backend"] = music
    cfg["farm"]["machines"] = [{"name": "mama", "host": "100.75.169.5", "first_port": 8189, "gpus": 6, "role": "broll", "enabled": False},
                               {"name": "pc-5090", "host": "127.0.0.1", "first_port": 8188, "gpus": 1, "role": "hero", "enabled": farm}]
    cfg["_config_name"] = "config.test.yaml"
    return cfg


def _episode(tmp: Path, bid: str, state: dict, board: dict | None = None) -> Path:
    ep = tmp / "out" / "episodes" / bid
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (ep / "board.json").write_text(json.dumps(board or {"id": bid, "scenes": []}), encoding="utf-8")
    return ep


def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


VOICE = [{"scene": 0, "line": 0, "role": "announcer", "engine": "chatterbox", "fallback": None},
         {"scene": 1, "line": 0, "role": "customer", "engine": "kokoro", "fallback": "reference clip missing (brand/voice/customer_ref.wav)"},
         {"scene": 1, "line": 1, "role": "male", "engine": "kokoro", "fallback": None}]


def test_exit_codes():
    ok = {"failed": False, "missing": False, "stale": False, "fallback": False}
    assert report.exit_code([ok]) == 0
    assert report.exit_code([ok, {**ok, "fallback": True}]) == 8
    assert report.exit_code([{**ok, "fallback": True}, {**ok, "failed": True}]) == 1
    assert report.exit_code([ok, {**ok, "missing": True}]) == 9          # make stopped before this board
    assert report.exit_code([ok, {**ok, "stale": True}], make_exit=0) == 9
    assert report.exit_code([ok], make_exit=-9) == 9                     # make killed
    assert report.exit_code([ok], make_exit=1) == 1
    assert report.exit_code([ok], make_exit=0, outbox_exit=1) == 9
    assert report.exit_code([ok, {**ok, "incomplete": True}], make_exit=0) == 9   # a board left at status "voice"


def test_voice_fallback_rules():
    assert report.voice_fallback(VOICE[1]).startswith("reference clip")
    assert report.voice_fallback(VOICE[2]) is None                       # Kokoro by design (cast)
    assert report.voice_fallback({"engine": "none", "fallback": "--no-voice"}) is None
    assert report.voice_fallback({"engine": "none", "fallback": "voice backend none"}) is None
    assert report.voice_fallback({"engine": "none", "fallback": "kokoro not installed"}) == "kokoro not installed"


def test_report_from_states():
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = _cfg(tmp)
        vid = tmp / "out" / "episodes" / "good" / "renders" / "good_9x16.mp4"
        _episode(tmp, "good", {
            "status": "rendered", "verdict": "HOLD", "started": "2026-10-08T10:00:05", "updated": "2026-10-08T10:20:00",
            "files": [{"format": "9x16", "file": str(vid), "w": 1080, "h": 1920, "audio": True, "dur": 15.2, "mb": 3.1}],
            "approvals_needed": ["WeightWatchers brand"],
            "engines": {"voice": VOICE, "music": "procedural", "broll": "mixed"},
            "fallback_reasons": {"music": "http://127.0.0.1:8188 not responding"},
            "shots": [{"scene": 2, "aspect": "9x16", "ok": True, "src": "/x/a.png", "clip": False, "score": 8.5, "unverified": True},
                      {"scene": 3, "aspect": "9x16", "ok": False, "src": "brand/x.jpg", "clip": False, "error": "no still passed the vision check"}],
            "errors": []})
        _episode(tmp, "broken", {"status": "error", "verdict": "PASS", "started": "2026-10-08T10:21:00",
                                 "engines": {"voice": [], "music": None, "broll": None}, "shots": [],
                                 "errors": ["RuntimeError: scene 1: no b-roll and no fallback_src"]})
        _episode(tmp, "old", {"status": "rendered", "verdict": "PASS", "started": "2026-10-01T09:00:00", "files": [],
                              "engines": {"voice": [], "music": "ace_step", "broll": None}})
        data = report.collect(cfg, ["good", "broken", "old", "never-made"], since="2026-10-08T10:00:00", make_exit=1,
                              run={"id": "20261008-100000", "flags": "-Boards x", "notes": ["shorts-factory is not paused"]})
        by = {b["id"]: b for b in data["boards"]}
        assert by["good"]["fallback"] and not by["good"]["failed"] and by["good"]["clips"] == 0
        assert [f["what"] for f in by["good"]["fallbacks"]] == ["voice", "music", "b-roll"]
        assert by["broken"]["failed"] and by["old"]["stale"] and by["never-made"]["missing"]
        assert data["exit"] == 1 and data["meaning"] == "a board failed"
        out = report.write(cfg, data)
        md = out.read_text(encoding="utf-8")
        assert (tmp / "out" / "report.json").exists() and json.loads((tmp / "out" / "report.json").read_text())["exit"] == 1
        for s in ("exit 1 (a board failed)", "`20261008-100000`", "shorts-factory is not paused", "**UNVERIFIED**",
                  "Chatterbox 1/3", "Kokoro 1/3 (by design)", "**1 fallback**", "procedural bed (fallback)",
                  "not responding", "Qwen 1/2, own stills 1", "no still passed the vision check", "AI video clips: 0",
                  "RuntimeError: scene 1", "Not run this time", "no state.json"):
            assert s in md, (s, md)
        # everything fine except a fallback -> 8; nothing wrong -> 0
        data = report.collect(cfg, ["good"], since="2026-10-08T10:00:00", make_exit=0)
        assert data["exit"] == 8
        cfg2 = _cfg(tmp, music="procedural", farm=False)              # cloud profile: neither is a fallback
        st = json.loads((tmp / "out" / "episodes" / "good" / "state.json").read_text())
        st["engines"]["voice"] = VOICE[:1]
        (tmp / "out" / "episodes" / "good" / "state.json").write_text(json.dumps(st))
        assert report.collect(cfg2, ["good"], make_exit=0)["exit"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_guess_old_episode():
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = _cfg(tmp)
        ep = _episode(tmp, "legacy", {"status": "rendered", "verdict": "PASS", "updated": "2026-10-07T21:00:00", "files": []})
        (ep / "vo").mkdir()
        for n in ("s00_0.wav", "s00_0.t0.wav", "s00_0.t1.wav", "s01_0.wav"):
            (ep / "vo" / n).write_bytes(b"")
        plate = tmp / "out" / "broll" / "abcd1234_9x16.png"
        plate.parent.mkdir(parents=True)
        plate.write_bytes(b"")
        (ep / "board_9x16.json").write_text(json.dumps({"id": "legacy", "format": "9x16", "audio": {"music": str(ep / "music.wav")},
                                                        "scenes": [{"type": "media", "broll": "x", "src": str(plate)},
                                                                   {"type": "media", "broll": "y", "src": str(ROOT / "brand" / "x.jpg")}]}))
        b = report.board_info(cfg, ep)
        assert b["guessed"] and [v["engine"] for v in b["voice"]] == ["chatterbox", "kokoro"]
        assert b["music"] == "procedural" and b["broll"] == "mixed" and [s["ok"] for s in b["shots"]] == [True, False]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_boards():
    tmp = Path(tempfile.mkdtemp())
    try:
        c = tmp / "concepts"
        c.mkdir()
        for n in ("made-simple-vod", "made-simple-social", "live-coach", "live-ww", "method-20"):
            (c / f"{n}.json").write_text(json.dumps({"id": n}))
        files, unk = report.resolve_boards([], c)
        assert [f.stem for f in files] == ["made-simple-social", "made-simple-vod"] and not unk     # no numan boards yet
        (c / "numan-assistance-15.json").write_text("{}")
        (c / "numan-good-luck-30.json").write_text("{}")
        files, _ = report.resolve_boards([], c)
        assert [f.stem for f in files] == ["numan-assistance-15", "numan-good-luck-30"]           # numan wins when present
        files, unk = report.resolve_boards(["live,method-20", "nope", str(c / "live-ww.json")], c)
        assert [f.stem for f in files] == ["live-coach", "live-ww", "method-20"] and unk == ["nope"]  # deduped
        files, _ = report.resolve_boards(["concepts/made-*"], c)
        assert [f.stem for f in files] == ["made-simple-social", "made-simple-vod"]
        assert len(report.resolve_boards(["all"], c)[0]) == 7
        assert report.groups(c) == ["live", "made-simple", "numan"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_info_flags_remote_comfy():
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = _cfg(tmp)
        assert report.info(cfg)["remote_enabled"] == []
        cfg["farm"]["machines"][0]["enabled"] = True                       # mama switched on: pc_run refuses (exit 5)
        assert report.info(cfg)["remote_enabled"] == ["mama"]
        pc = config.load("config.pc.yaml")
        assert report.info(pc)["remote_enabled"] == [], "config.pc.yaml must never enable mama"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_produce_records_engines_and_errors():
    tmp = Path(tempfile.mkdtemp())
    saved = {k: getattr(pipeline, k) for k in ("_broll", "_voice", "_music", "_sfx", "_render", "_probe")}
    try:
        cfg = _cfg(tmp)
        board = json.loads((ROOT / "concepts" / "made-simple-social.json").read_text(encoding="utf-8"))   # lints HOLD
        board.update(id="rec", formats=["9x16"])
        media = [i for i, s in enumerate(board["scenes"]) if s.get("type") == "media" and s.get("broll")]
        assert len(media) == 2, media
        bp = tmp / "rec.json"
        bp.write_text(json.dumps(board))
        seen = {}

        def fake_broll(cfg_, b, ep, use_5090, skip, formats=None):
            seen["modes"] = [b["scenes"][i].get("broll_mode") for i in media]
            b["scenes"][media[0]]["src_by_aspect"] = {"9x16": str(tmp / "out" / "broll" / "k_9x16.png")}
            b["scenes"][media[1]]["src"] = b["scenes"][media[1]]["fallback_src"]
            print("  b-roll unavailable (no ComfyUI server passed the pre-flight); using fallback stills")
            return True

        def fake_voice(cfg_, b, ep, skip):
            b.setdefault("audio", {})["voice_engines"] = VOICE[:2]

        def fake_music(cfg_, b, ep):
            print("  music: ACE-Step unavailable (checkpoint missing); procedural bed instead")
            b.setdefault("audio", {})["music_engine"] = "procedural"

        pipeline._broll, pipeline._voice, pipeline._music = fake_broll, fake_voice, fake_music
        pipeline._sfx = lambda b, ep: None
        pipeline._render = lambda cfg_, b, ep, fmt: ep / "renders" / f"rec_{fmt}.mp4"
        pipeline._probe = lambda p: {"w": 1080, "h": 1920, "audio": True, "dur": 8.0, "mb": 1.0}
        r, log = _quiet(pipeline.produce, cfg, bp, clips=True)
        assert "b-roll unavailable" in log and "procedural bed instead" in log          # still printed (teed)
        st = json.loads((tmp / "out" / "episodes" / "rec" / "state.json").read_text())
        assert st["status"] == "rendered" and seen["modes"] == ["clip", "clip"] and st["clips_requested"]
        assert st["engines"] == {"voice": VOICE[:2], "music": "procedural", "broll": "mixed"}, st["engines"]
        assert [s["ok"] for s in st["shots"]] == [True, False] and st["shots"][1]["error"].startswith("no ComfyUI")
        assert st["fallback_reasons"] == {"broll": "no ComfyUI server passed the pre-flight", "music": "checkpoint missing"}
        assert st["errors"] == [] and r["verdict"] == "HOLD"                            # UNVERIFIED plate -> HOLD

        def boom(cfg_, b, ep):
            raise RuntimeError("ffmpeg exploded")
        pipeline._music = boom
        try:
            _quiet(pipeline.produce, cfg, bp)
            raise AssertionError("produce must re-raise")
        except RuntimeError:
            pass
        st = json.loads((tmp / "out" / "episodes" / "rec" / "state.json").read_text())
        assert st["status"] == "error" and st["errors"] == ["RuntimeError: ffmpeg exploded"] and st["files"] == []
        assert st["engines"]["voice"] == VOICE[:2] and "ffmpeg exploded" in st["traceback"]
        b = report.board_info(cfg, tmp / "out" / "episodes" / "rec")
        assert b["failed"] and report.exit_code([b]) == 1
    finally:
        for k, v in saved.items():
            setattr(pipeline, k, v)
        shutil.rmtree(tmp, ignore_errors=True)


def test_make_carries_on_past_a_failed_board():
    calls, saved = [], pipeline.produce

    def fake(cfg, b, formats=None, **kw):
        calls.append((Path(b).stem, kw.get("clips")))
        if Path(b).stem == "made-simple-live":
            raise RuntimeError("boom")
        return {"id": Path(b).stem, "verdict": "PASS", "files": []}
    pipeline.produce = fake
    try:
        try:
            _quiet(cli.main, ["make", "made-simple-live", "made-simple-social", "--clips"])
            raise AssertionError("make must exit 1 when a board fails")
        except SystemExit as e:
            assert e.code == 1
        assert calls == [("made-simple-live", True), ("made-simple-social", True)]   # carried on
        calls.clear()
        _quiet(cli.main, ["make", "made-simple-social"])                            # all fine: no SystemExit
        assert calls == [("made-simple-social", False)]
    finally:
        pipeline.produce = saved


def test_pack_zip():
    if not shutil.which("ffmpeg"):
        print("skip test_pack_zip (no ffmpeg)")
        return
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = _cfg(tmp)
        ren = tmp / "out" / "episodes" / "z" / "renders"
        ren.mkdir(parents=True)
        vid = ren / "z_9x16.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=4",
                        "-pix_fmt", "yuv420p", str(vid)], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(vid), "-frames:v", "1", str(ren / "z_9x16_poster.png")], check=True)
        _episode(tmp, "z", {"status": "rendered", "verdict": "PASS", "files": [{"format": "9x16", "file": str(vid), "dur": 4.0, "mb": 0.1}],
                            "engines": {"voice": [], "music": "ace_step", "broll": "qwen"},
                            "shots": [{"scene": 0, "aspect": "9x16", "ok": True, "src": str(ren / "z_9x16_poster.png"), "clip": False}]})
        log = tmp / "pc_run_x.log"
        log.write_text("log line\n")
        report.write(cfg, report.collect(cfg, ["z"]))
        zp, _ = _quiet(report.pack, cfg, tmp / "review.zip", str(log))
        names = zipfile.ZipFile(zp).namelist()
        for n in ("REPORT.md", "report.json", "pc_run_x.log", "boards/z/state.json", "boards/z/z_9x16_poster.png",
                  "boards/z/contact_9x16.jpg", "boards/z/broll/scene0_9x16_qwen.jpg"):
            assert n in names, (n, names)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cached_plate_keeps_its_vision_check():
    """farm.render_shots hands back a cached plate with score 0, not UNVERIFIED and no log, and overwrites its
    broll_log.json entry. produce() must keep the real record, and treat a plate with no record as UNVERIFIED."""
    import hashlib
    tmp = Path(tempfile.mkdtemp())
    saved = {k: getattr(pipeline, k) for k in ("_broll", "_voice", "_music", "_sfx", "_render", "_probe")}
    try:
        cfg = _cfg(tmp)
        board = json.loads((ROOT / "concepts" / "made-simple-social.json").read_text(encoding="utf-8"))
        board.update(id="cache", formats=["9x16"])
        media = [i for i, s in enumerate(board["scenes"]) if s.get("type") == "media" and s.get("broll")]
        keys = [hashlib.sha1(board["scenes"][i]["broll"].encode()).hexdigest()[:8] + "_9x16" for i in media]
        bp = tmp / "cache.json"
        bp.write_text(json.dumps(board))
        log_f = tmp / "out" / "broll" / "broll_log.json"
        log_f.parent.mkdir(parents=True)
        lost = {"ok": True, "score": 0, "unverified": False, "error": None, "log": []}

        def fake_broll(cfg_, b, ep, use_5090, skip, formats=None):     # what farm does with two cached plates
            for i, k in zip(media, keys):
                b["scenes"][i]["src_by_aspect"] = {"9x16": str(log_f.parent / f"{k}.png")}
            log = json.loads(log_f.read_text())
            log.update({k: dict(lost) for k in keys})
            log_f.write_text(json.dumps(log))
            return False
        pipeline._broll = fake_broll
        pipeline._voice = lambda cfg_, b, ep, skip: None
        pipeline._music = lambda cfg_, b, ep: b.setdefault("audio", {}).update(music_engine="procedural")
        pipeline._sfx = lambda b, ep: None
        pipeline._render = lambda cfg_, b, ep, fmt: ep / "renders" / f"cache_{fmt}.mp4"
        pipeline._probe = lambda p: {"w": 1080, "h": 1920, "audio": True, "dur": 8.0, "mb": 1.0}

        checked = {"ok": True, "score": 8.0, "unverified": False, "error": None, "log": [{"seed": 1, "ok": True, "score": 8.0}]}
        log_f.write_text(json.dumps({k: checked for k in keys}))
        _quiet(pipeline.produce, cfg, bp)
        st = json.loads((tmp / "out" / "episodes" / "cache" / "state.json").read_text())
        assert [(x["cached"], x["unverified"], x["score"]) for x in st["shots"]] == [(True, False, 8.0)] * 2
        assert not st["broll_unverified"]
        assert json.loads(log_f.read_text())[keys[0]]["log"], "the real record must be written back"

        unchecked = {**checked, "score": 0, "unverified": True}             # first run had no vision check
        log_f.write_text(json.dumps({keys[0]: unchecked, keys[1]: dict(lost)}))   # keys[1]: record already lost
        r, _ = _quiet(pipeline.produce, cfg, bp)
        st = json.loads((tmp / "out" / "episodes" / "cache" / "state.json").read_text())
        assert [x["unverified"] for x in st["shots"]] == [True, True] and st["broll_unverified"]
        assert r["verdict"] == "HOLD" and any("vision check" in n for n in st["approvals_needed"])
        assert json.loads(log_f.read_text())[keys[0]]["unverified"] is True
    finally:
        for k, v in saved.items():
            setattr(pipeline, k, v)
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_engine_keys_are_guessed():
    """Cross-track contract: if _voice / _music leave voice_engines / music_engine out, REPORT.md guesses (marked)
    instead of claiming the board had no VO or no music."""
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = _cfg(tmp)
        ep = _episode(tmp, "nokeys", {"status": "rendered", "verdict": "PASS", "started": "2026-10-08T10:00:00", "files": [],
                                      "engines": {"voice": [], "music": None, "broll": None}, "shots": []},
                      {"id": "nokeys", "scenes": [{"type": "hook", "vo": "Hello there."}, {"type": "endcard", "vo": "Bye."}]})
        (ep / "vo").mkdir()
        for n in ("s00_0.wav", "s00_0.t1.wav", "s00_0.job.json", "s01_0.wav"):
            (ep / "vo" / n).write_bytes(b"")
        (ep / "board_9x16.json").write_text(json.dumps({"id": "nokeys", "audio": {"music": str(ep / "music.wav")}, "scenes": []}))
        b = report.board_info(cfg, ep)
        assert b["guessed"] and [v["engine"] for v in b["voice"]] == ["chatterbox", "kokoro"] and b["music"] == "unknown"
        md = report.render_md(report.collect(cfg, ["nokeys"]))
        assert "not recorded" in md and "no VO" not in md and "guessed" in md
        assert report.collect(cfg, ["nokeys"], make_exit=0)["exit"] == 0
        nv = _episode(tmp, "novo", {"status": "rendered", "verdict": "PASS", "files": [],
                                    "engines": {"voice": [], "music": "ace_step", "broll": None}, "shots": []},
                      {"id": "novo", "scenes": [{"type": "hook"}]})
        b = report.board_info(cfg, nv)
        assert b["voice"] == [] and not b["guessed"] and report._voice_cell(b) == "no VO"   # a board without VO lines
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pack_never_ships_a_stale_report():
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = _cfg(tmp)
        (tmp / "out").mkdir(parents=True)
        (tmp / "out" / "REPORT.md").write_text("# an earlier run's report\n")      # report.json missing: report failed
        zp, _ = _quiet(report.pack, cfg, tmp / "review.zip", None)
        assert "REPORT.md" not in zipfile.ZipFile(zp).namelist()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


PS1 = [ROOT / "scripts" / n for n in ("pc_run.ps1", "pc_start.ps1", "pc_wait.ps1")]


def test_ps1_windows_powershell_51_safe():
    """Windows PowerShell 5.1 reads BOM-less files as ANSI (a UTF-8 dash can end a string) and lacks PS7 syntax."""
    for p in PS1:
        s = p.read_text(encoding="utf-8")
        assert s.isascii(), f"{p.name}: non-ASCII character"
        code = "\n".join(re.sub(r"#.*$", "", ln) for ln in s.splitlines() if not ln.lstrip().startswith("#"))
        for bad in ("??", "?.", "&&", "||", "-Parallel", "$IsWindows", "-AsHashtable", "utf8NoBOM", "::new(", "-NoProxy"):
            assert bad not in code, f"{p.name}: {bad}"
    run = PS1[0].read_text()
    assert "'config.pc.yaml'" in run and "config.mama" not in run.replace("never config.mama.yaml", "")
    assert "update_comfy_pc.ps1" in run and not re.search(r"&\s*[^\n]*update_comfy_pc", run)       # printed, never run
    for code in range(0, 11):
        assert f"{code} = '" in run, code
    assert "Start-Transcript" in run and "LASTEXITCODE" in run and ".done" in run
    start, wait = PS1[1].read_text(), PS1[2].read_text()
    assert "-WindowStyle Hidden" in start and "pc_run.lock" in start and "exit 10" in start
    assert "exit 11" in wait and "Stop-Process" not in wait + start + run and "taskkill" not in (wait + start + run).lower()
    # Claude Code on Windows runs commands in Git Bash, which eats unquoted backslashes: every command it may copy
    # (script hints, PUPPET.md) must use forward slashes.
    for t in (run, start, wait, (ROOT / "PUPPET.md").read_text(encoding="utf-8")):
        assert "-File scripts\\" not in t and "python.exe scripts\\" not in t


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
