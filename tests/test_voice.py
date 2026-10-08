"""Voice tests with fakes only: no TTS/ASR model is installed or run. Run: python tests/test_voice.py
A fake Chatterbox worker (stdlib only) speaks the JSON-line protocol and writes short tones; Kokoro and
faster-whisper are replaced or made absent. ffmpeg runs the real master() chain on a tone."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import wave
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cqf import pipeline, voice  # noqa: E402

FAKE_WORKER = r'''
import array, json, math, os, sys, time, wave
mode = os.environ.get("FAKE_CB_MODE", "ok")
if mode == "exit":
    sys.exit(3)
if mode == "hang":
    time.sleep(30)
    sys.exit(0)
print(json.dumps({"ready": True, "device": "cpu" if mode == "cpu" else "fake", "sr": 24000}), flush=True)
for raw in sys.stdin:
    if not raw.strip():
        continue
    job = json.loads(raw)
    if os.environ.get("FAKE_CB_COUNT"):
        with open(os.environ["FAKE_CB_COUNT"], "a") as fh:
            fh.write(json.dumps(job) + "\n")
    if mode == "fail":
        print(json.dumps({"ok": False, "error": "CUDA out of memory (fake)"}), flush=True)
        continue
    if mode == "crash":
        sys.exit(5)
    if mode == "glue":                                   # stray output: a bare JSON number, then text glued to the reply
        print("42", flush=True)
        sys.stdout.write("native noise without a newline ")
    if mode == "slow":
        time.sleep(30)
    secs = []
    for n, out in enumerate(job["outs"]):
        seed, sr = int(job.get("seed", 1234)) + n, 24000
        depth, half = (seed % 7) / 2.0, 0.25 + 0.02 * len(job["text"])   # vibrato depth (st) follows the seed
        a, ph, total = array.array("h"), 0.0, int((2 * half + 0.5) * sr)
        for i in range(total):
            t = i / sr
            if half <= t < half + 0.5:                                    # one 0.5 s inner pause
                a.append(0)
                continue
            ph += 2 * math.pi * 200 * 2 ** (depth * math.sin(2 * math.pi * 3 * t) / 12) / sr
            a.append(int(8000 * math.sin(ph)))
        with wave.open(out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(a.tobytes())
        secs.append(round(total / sr, 3))
    print(json.dumps({"ok": True, "outs": job["outs"], "sr": 24000, "secs": secs}), flush=True)
'''


@contextlib.contextmanager
def patched(obj, name, value):
    had, old = hasattr(obj, name), getattr(obj, name, None)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old) if had else delattr(obj, name)


@contextlib.contextmanager
def modules(**mods):
    """sys.modules entries for the duration: a module object (fake) or None (import raises ImportError)."""
    old = {k: sys.modules.get(k, "absent") for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, v in old.items():
            if v == "absent":
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    os.environ.update({k: str(v) for k, v in kv.items()})
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def tone(path: Path, secs: float, sr: int = 24000, f: float = 220.0, gap: tuple | None = None, ch: int = 1, amp: float = 0.3):
    t = np.arange(int(secs * sr)) / sr
    y = amp * np.sin(2 * np.pi * f * t)
    if gap:
        y[(t >= gap[0]) & (t < gap[1])] = 0
    pcm = (np.repeat(y[:, None], ch, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch), w.setsampwidth(2), w.setframerate(sr), w.writeframes(pcm.tobytes())
    return path


def info(path: Path) -> tuple[int, int, float]:
    with wave.open(str(path)) as w:
        return w.getframerate(), w.getnchannels(), w.getnframes() / w.getframerate()


class FakeKokoro:
    """Stands in for voice._kokoro: writes a tone, records (text, voice, speed)."""
    def __init__(self):
        self.calls = []

    def __call__(self, text, out, vid, speed, lang, sr, lexicon):
        self.calls.append((text, vid, speed))
        d = 0.3 + 0.06 * len(text.split())
        tone(out, d, sr)
        return d, voice.estimate_words(text, 0.0, d)


@contextlib.contextmanager
def fakes(tmp: Path, mode: str = "ok"):
    """Fake kokoro module + _kokoro, fake worker script, no faster-whisper / parselmouth; fresh worker state."""
    wk = tmp / "fake_worker.py"
    wk.write_text(FAKE_WORKER)
    count = tmp / "jobs.jsonl"
    count.touch()
    voice.shutdown()
    fk = FakeKokoro()
    with modules(kokoro=types.ModuleType("kokoro"), faster_whisper=None, parselmouth=None), \
            patched(voice, "_kokoro", fk), patched(voice, "WORKER", wk), env(FAKE_CB_MODE=mode, FAKE_CB_COUNT=count):
        try:
            yield fk, count
        finally:
            voice.shutdown()


def jobs(count: Path) -> list[dict]:
    return [json.loads(x) for x in count.read_text().splitlines() if x.strip()]


def cfg_for(tmp: Path, cb_python, **chatterbox) -> dict:
    """The real preset voice block, with the reference clips pointed at tmp tones."""
    pv = copy.deepcopy(yaml.safe_load((ROOT / "presets" / "chequp_meta.yaml").read_text(encoding="utf-8"))["voice"])
    pv["chatterbox"]["ref"] = str(tone(tmp / "ann_ref.wav", 2.0))
    pv["cast"]["customer"]["ref"] = str(tone(tmp / "cust_ref.wav", 2.0, f=260))
    pv["chatterbox"].update(chatterbox)
    return {"voice": {"backend": "tts", "lang_code": "b", "sample_rate": 24000, "chatterbox_python": cb_python},
            "_preset": {"voice": pv}}


def board() -> dict:
    return {"id": "t", "transition": {"type": "fade", "dur": 0.4}, "scenes": [
        {"type": "media", "dur": 2.0, "vo": "Day twenty-three, late shift, and no plan for dinner."},
        {"type": "chat", "dur": 3.0, "vo": [{"voice": "customer", "text": "What do I actually eat?"},
                                             {"voice": "cheqqup", "text": "The WeightWatchers app, included."},
                                             {"voice": "chequp", "text": "That’s The CheqUp Method.", "style": "tagline"}]},
        {"type": "endcard", "dur": 2.0, "vo": [{"voice": "male", "text": "See if it’s right for you."}]},
    ]}


def run_voice(cfg, b, ep, skip=False) -> str:
    ep.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        pipeline._voice(cfg, b, ep, skip)
    return buf.getvalue()


# ------------------------------------------------------------------ tests
def test_import_does_not_load_models():
    code = ("import sys; import cqf, cqf.voice, cqf.pipeline, cqf.cli; "
            "bad = [m for m in ('kokoro', 'torch', 'faster_whisper', 'parselmouth', 'soundfile', 'chatterbox') if m in sys.modules]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


def test_respell():
    assert voice.respell("That’s The CheqUp Method.", "chatterbox") == "That's The CheckUp Method."
    assert voice.respell("Visit chequp.com today", "chatterbox") == "Visit CheckUp dot com today"
    k = voice.respell("CheqUp at chequp.com", "kokoro")
    assert k == "[CheqUp](/ʧˈɛkʌp/) at [chequp](/ʧˈɛkʌp/) dot com", k
    assert voice.respell("Hello Numan", "chatterbox", {"chatterbox": {r"\bNuman\b": "New-man"}}) == "Hello New-man"
    assert voice._norm("The CheqUp Method") == voice._norm("the check up method") == ["the", "checkup", "method"]


def test_estimate_words():
    w = voice.estimate_words("Day twenty-three, late shift — and no plan.", 1.0, 2.0)
    assert [x["w"] for x in w] == ["Day", "twenty-three", "late", "shift", "and", "no", "plan"]
    assert w[0]["s"] == 1.0 and w[-1]["e"] <= 3.0


def test_cast_merge_order():
    pv = {"engine": "chatterbox", "voices": ["v0"], "speed": 1.05,
          "chatterbox": {"ref": "base.wav", "exaggeration": 0.85, "cfg_weight": 0.4, "seed": 1},
          "cast": {"customer": {"ref": "cust.wav", "exaggeration": 0.5, "voice": "bf_isabella"}, "chequp": {},
                   "male": {"engine": "kokoro", "voice": "bm_fable"}},
          "styles": {"tagline": {"exaggeration": 0.3, "tempo": 0.8, "pause_before": 0.35}}}
    b = {"voices": {"customer": {"exaggeration": 0.6, "seed": 2}, "chequp": "bf_alice"}}
    spec = pipeline._cast(b, pv, {"voice": "customer", "text": "x", "style": "tagline", "seed": 3, "pause_after": 1})
    assert spec == {"engine": "chatterbox", "voice": "bf_isabella", "speed": 1.05, "ref": "cust.wav", "exaggeration": 0.3,
                    "cfg_weight": 0.4, "seed": 3, "tempo": 0.8, "pause_before": 0.35}, spec
    # base -> cast -> board.voices: board 0.6 beats cast 0.5 when no style/line key is set
    assert pipeline._cast(b, pv, {"voice": "customer", "text": "x"})["exaggeration"] == 0.6
    # misspelt role, and a bare string layer is a Kokoro voice id
    s = pipeline._cast(b, pv, {"voice": "cheqqup", "text": "x"})
    assert s["voice"] == "bf_alice" and s["ref"] == "base.wav" and pipeline._role({"voice": "cheqqup"}) == "chequp"
    assert pipeline._cast({}, pv, {"text": "x"})["voice"] == "v0"                 # default role announcer
    assert pipeline._cast({"voice": "bf_lily"}, pv, {"text": "x"})["voice"] == "bf_lily"
    assert pipeline._cast({}, pv, {"voice": "male", "text": "x"})["engine"] == "kokoro"


def test_preset_block_matches_spec():
    pv = yaml.safe_load((ROOT / "presets" / "chequp_meta.yaml").read_text(encoding="utf-8"))["voice"]
    assert pv["engine"] == "chatterbox" and pv["voices"] == ["bf_emma,bf_alice"] and pv["speed"] == 1.05
    assert pv["chatterbox"] == {"ref": "brand/voice/announcer_bouncy_ref.wav", "exaggeration": 0.85, "cfg_weight": 0.4,
                                "temperature": 0.9, "tighten_pauses": {"max_gap": 0.22, "to": 0.15}, "takes": 3, "seed": 1234}
    assert pv["cast"]["customer"]["ref"] == "brand/voice/customer_ref.wav" and pv["cast"]["male"]["engine"] == "kokoro"
    assert pv["styles"]["tagline"]["pause_before"] == 0.35 and pv["master"] == {"pitch_st": 0}


def test_tighten_shortens_inner_pauses_only():
    with tempfile.TemporaryDirectory() as d:
        p = tone(Path(d) / "a.wav", 2.0, gap=(0.8, 1.4))
        y = np.frombuffer(wave.open(str(p)).readframes(48000), "<i2").copy()
        y[: int(0.2 * 24000)] = 0                                                   # 0.2 s lead-in silence
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(y.tobytes())
        voice._tighten(p, 0.22, 0.15)
        dur = info(p)[2]
        assert abs(dur - (2.0 - 0.6 + 0.15)) < 0.03, dur                            # 0.6 s gap -> 0.15 s; lead-in kept
        voice._tighten(p, 0.22, 0.15)
        assert abs(info(p)[2] - dur) < 0.005                                         # idempotent


def test_master_runs_ffmpeg_chain():
    assert voice.MASTER.startswith("highpass=f=80:poles=2,") and voice.MASTER.endswith("loudnorm=I=-16:TP=-1.5:LRA=7")
    with tempfile.TemporaryDirectory() as d:
        src = tone(Path(d) / "raw.wav", 1.5)
        for cfg in ({}, {"pitch_st": 0.5}, {"pitch_st": 3}, {"filters": "volume=0.5"}):
            out = voice.master(src, Path(d) / "vo.wav", cfg)
            sr, ch, dur = info(out)
            assert (sr, ch) == (48000, 1) and abs(dur - 1.5) < 0.05, (cfg, sr, ch, dur)
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(voice.master(src, Path(d) / "vo.wav")), "-af",
                            "ebur128", "-f", "null", "-"], capture_output=True, text=True)
        lufs = float(r.stderr.rsplit("I:", 1)[1].split("LUFS")[0])
        assert -19 < lufs < -13, lufs                                                # loudnorm I=-16 on the VO stem


def test_pcm16_converts():
    with tempfile.TemporaryDirectory() as d:
        a = tone(Path(d) / "a.wav", 1.0, sr=48000, ch=2)
        assert len(voice.pcm16(a, 24000)) == 2 * 24000
        b = tone(Path(d) / "b.wav", 1.0)
        assert voice.pcm16(b, 24000) == wave.open(str(b)).readframes(24000)


def test_asr_absent_is_harmless():
    with tempfile.TemporaryDirectory() as d, modules(faster_whisper=None):
        p = tone(Path(d) / "a.wav", 0.5)
        voice._said.clear()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert voice._asr_check(p, "hello there", "small.en") == (None, None)
            assert voice._asr_check(p, "hello there", "small.en") == (None, None)
        assert buf.getvalue().count("asr check skipped") == 1                        # said once, not per take


def test_worker_singleton_protocol_and_cache():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp) as (fk, count):
            ref = tone(tmp / "ref.wav", 2.0)
            o = {"ref": str(ref), "python": sys.executable, "takes": 2, "tighten_pauses": {"max_gap": 0.22, "to": 0.15}}
            out = tmp / "s00_0.wav"
            dur, words = voice.speak("Weight loss made simple.", out, engine="chatterbox", opts=o)
            assert [w["w"] for w in words] == ["Weight", "loss", "made", "simple"]
            assert (tmp / "s00_0.t1.wav").exists() and (tmp / "s00_0.t2.wav").exists() and (tmp / "s00_0.job.json").exists()
            half = 0.25 + 0.02 * len("Weight loss made simple.")
            assert abs(info(tmp / "s00_0.t1.wav")[2] - (2 * half + 0.15)) < 0.03          # 0.5 s pause tightened
            assert abs(dur - info(out)[2]) < 1e-6 and len(jobs(count)) == 1
            pid = voice._workers[sys.executable].pid
            voice.speak("Weight loss made simple.", out, engine="chatterbox", opts=o)    # same text/spec/ref: cached
            assert len(jobs(count)) == 1
            voice.speak("Another line, please.", tmp / "s00_1.wav", engine="chatterbox", opts=o)
            assert len(jobs(count)) == 2 and voice._workers[sys.executable].pid == pid  # one long-lived worker
            j = jobs(count)[0]
            assert j["seed"] == 1234 and j["ref_stat"][0] == ref.stat().st_size and j["tighten"] == o["tighten_pauses"]
            voice.speak("Weight loss made simple.", out, engine="chatterbox", opts={**o, "exaggeration": 0.9})
            assert len(jobs(count)) == 3                                                 # spec change re-renders
            tone(ref, 2.5)                                                               # new reference file (size/mtime)
            voice.speak("Weight loss made simple.", out, engine="chatterbox", opts={**o, "exaggeration": 0.9})
            assert len(jobs(count)) == 4
            voice.speak("Weight loss made simple.", out, engine="chatterbox", opts={**o, "exaggeration": 0.9, "tempo": 0.8})
            assert len(jobs(count)) == 4 and info(out)[2] > (2 * half + 0.15) / 0.8 - 0.05  # tempo: post-step, no re-render


def test_pipeline_falls_back_when_env_missing():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp) as (fk, count):
            cfg, b = cfg_for(tmp, str(tmp / "no-venv" / "python")), board()
            out = run_voice(cfg, b, tmp / "ep")
            eng = b["audio"]["voice_engines"]
            assert [(e["scene"], e["line"], e["role"], e["engine"]) for e in eng] == [
                (0, 0, "announcer", "kokoro"), (1, 0, "customer", "kokoro"), (1, 1, "chequp", "kokoro"),
                (1, 2, "chequp", "kokoro"), (2, 0, "male", "kokoro")]
            assert all("chatterbox env missing" in e["fallback"] for e in eng[:4]) and eng[4]["fallback"] is None
            assert "WARNING voice scene 1 line 1 (chequp)" in out and "The WeightWatchers app" in out
            assert out.count("WARNING voice") == 4 and not jobs(count)
            by_text = {t: (v, s) for t, v, s in fk.calls}
            assert by_text["What do I actually eat?"] == ("bf_isabella", 1.05)            # cast voice for the fallback
            assert by_text["Day twenty-three, late shift, and no plan for dinner."] == ("bf_emma,bf_alice", 1.05)
            assert by_text["That’s The CheqUp Method."] == ("bf_emma,bf_alice", 0.9)       # tagline style speed
            assert by_text["See if it’s right for you."] == ("bm_fable", 1.05)
            # timing behaviour kept from the dialogue code: _t0, _line_times, scene stretch, captions
            s0, s1 = b["scenes"][0], b["scenes"][1]
            assert s0["_t0"] == 0 and s1["_t0"] == round(s0["dur"] - 0.4, 3)
            lt = s1["_line_times"]
            assert len(lt) == 3 and lt[0][0] == 0.15
            assert abs(lt[1][0] - (lt[0][1] + 0.3)) < 0.002 and abs(lt[2][0] - (lt[1][1] + 0.3 + 0.35)) < 0.002
            assert s1["dur"] >= lt[2][1] + 0.35 - 0.01
            assert len(b["captions"]) == sum(len(t.split()) for t, _, _ in fk.calls)
            ep = tmp / "ep"
            assert info(ep / "vo_raw.wav")[:2] == (24000, 1) and info(ep / "vo.wav")[:2] == (48000, 1)
            assert b["audio"]["vo"] == str(ep / "vo.wav")


def test_pipeline_uses_chatterbox_and_caches():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp) as (fk, count):
            cfg = cfg_for(tmp, sys.executable)
            b = board()
            out = run_voice(cfg, b, tmp / "ep")
            eng = b["audio"]["voice_engines"]
            assert [e["engine"] for e in eng] == ["chatterbox"] * 4 + ["kokoro"] and all(e["fallback"] is None for e in eng)
            assert "WARNING" not in out and "4/5 lines Chatterbox" in out
            js = jobs(count)
            assert len(js) == 4 and all(len(j["outs"]) == 3 for j in js)
            assert js[1]["ref"] == str(Path(cfg["_preset"]["voice"]["cast"]["customer"]["ref"]).resolve())
            assert js[1]["exaggeration"] == 0.5 and js[0]["exaggeration"] == 0.85 and js[3]["exaggeration"] == 0.5
            assert js[2]["text"] == "The WeightWatchers app, included." and js[3]["text"] == "That's The CheckUp Method."
            assert (tmp / "ep" / "vo" / "s01_2.t3.wav").exists() and (tmp / "ep" / "vo.wav").exists()
            run_voice(cfg, board(), tmp / "ep")                                          # re-render: no re-synthesis
            assert len(jobs(count)) == 4


def test_pipeline_ref_missing_falls_back():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp) as (fk, count):
            cfg = cfg_for(tmp, sys.executable)
            cfg["_preset"]["voice"]["cast"]["customer"]["ref"] = "brand/voice/no_such_ref.wav"
            b = board()
            out = run_voice(cfg, b, tmp / "ep")
            eng = {(e["scene"], e["line"]): e for e in b["audio"]["voice_engines"]}
            assert eng[(1, 0)]["engine"] == "kokoro" and "reference clip missing" in eng[(1, 0)]["fallback"]
            assert eng[(0, 0)]["engine"] == "chatterbox" and "scene 1 line 0 (customer)" in out


def test_pipeline_worker_failures_fall_back():
    for mode, extra, expect in (("fail", {}, "CUDA out of memory (fake)"),
                                ("exit", {}, "failed to start"),
                                ("hang", {"start_timeout_s": 1.5}, "no answer in 1.5s"),
                                ("slow", {"timeout_s": 0.5, "takes": 1}, "no answer in 0.5s")):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with fakes(tmp, mode) as (fk, count):
                cfg, b = cfg_for(tmp, sys.executable, **extra), board()
                out = run_voice(cfg, b, tmp / "ep")
                eng = b["audio"]["voice_engines"]
                assert [e["engine"] for e in eng] == ["kokoro"] * 5, (mode, eng)
                assert all(expect in e["fallback"] for e in eng[:4]), (mode, [e["fallback"] for e in eng])
                assert out.count("WARNING voice") == 4 and (tmp / "ep" / "vo.wav").exists()
                assert not list((tmp / "ep" / "vo").glob("*.job.json"))                  # failures are never cached
                if mode in ("exit", "hang", "slow"):
                    assert sys.executable in voice._dead                                 # no restart per line


def test_worker_crashing_mid_job_is_restarted_once_then_dropped():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp, "crash") as (fk, count):
            cfg, b = cfg_for(tmp, sys.executable), board()
            out = run_voice(cfg, b, tmp / "ep")
            eng = b["audio"]["voice_engines"]
            assert [e["engine"] for e in eng] == ["kokoro"] * 5, eng
            assert len(jobs(count)) == 2                                                 # first line + one restart
            assert all("worker exited (code 5)" in e["fallback"] for e in eng[:2]) and "died 2 times" in eng[2]["fallback"]
            assert eng[3]["fallback"] == eng[2]["fallback"] and sys.executable in voice._dead
            assert out.count("WARNING voice") == 4


def test_reply_survives_stray_output_and_cpu_warns():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for mode in ("glue", "cpu"):
            with fakes(tmp, mode) as (fk, count):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    res = voice.chatterbox_job({"text": "Hi there.", "outs": [str(tmp / f"{mode}.wav")], "ref": "x"},
                                               sys.executable, 20)
                assert res["ok"] and (tmp / f"{mode}.wav").exists(), res
                assert ("on the CPU" in buf.getvalue()) == (mode == "cpu"), buf.getvalue()


def test_corrupt_job_file_rerenders_and_failed_stretch_keeps_timing():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp) as (fk, count):
            ref = tone(tmp / "ref.wav", 2.0)
            o = {"ref": str(ref), "python": sys.executable, "takes": 1}
            out = tmp / "s00_0.wav"
            voice.speak("Weight loss made simple.", out, engine="chatterbox", opts=o)
            (tmp / "s00_0.job.json").write_text('{"text": "Weight lo')                 # half-written (crash)
            voice.speak("Weight loss made simple.", out, engine="chatterbox", opts=o)
            assert len(jobs(count)) == 2 and json.loads((tmp / "s00_0.job.json").read_text())["text"]
            plain = voice.speak("Weight loss made simple.", out, engine="chatterbox", opts=o)
            buf = io.StringIO()
            with patched(voice, "_stretch", lambda p, t: False), contextlib.redirect_stdout(buf):
                slow = voice.speak("Weight loss made simple.", out, engine="chatterbox", opts={**o, "tempo": 0.8})
            assert slow == plain and len(jobs(count)) == 2                               # words not rescaled


def test_master_keeps_polish_when_rubberband_missing():
    real = subprocess.run

    def no_rubberband(cmd, *a, **k):
        if any("rubberband" in str(x) for x in cmd):
            raise subprocess.CalledProcessError(1, cmd)
        return real(cmd, *a, **k)
    with tempfile.TemporaryDirectory() as d, patched(voice.subprocess, "run", no_rubberband):
        src = tone(Path(d) / "raw.wav", 1.0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = voice.master(src, Path(d) / "vo.wav", {"pitch_st": 0.5})
        assert info(out)[:2] == (48000, 1) and "pitch_st 0.5 skipped" in buf.getvalue()


def test_frees_comfy_vram_once_before_chatterbox_loads():
    import http.server
    import threading
    posts = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            posts.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    farm = {"machines": [{"name": "mama", "host": "100.75.169.5", "first_port": 8189, "gpus": 6, "enabled": False},
                         {"name": "pc", "host": "127.0.0.1", "first_port": srv.server_address[1], "gpus": 1, "enabled": True}]}
    try:
        with tempfile.TemporaryDirectory() as d, patched(voice.time, "sleep", lambda s: None):
            tmp = Path(d)
            with fakes(tmp) as (fk, count):
                cfg = {**cfg_for(tmp, str(tmp / "no-venv" / "python")), "farm": farm}
                run_voice(cfg, board(), tmp / "ep")                                      # env missing: leave ComfyUI alone
                assert posts == []
                cfg = {**cfg_for(tmp, sys.executable), "farm": farm}
                out = run_voice(cfg, board(), tmp / "ep2")
                assert posts == [("/free", {"unload_models": True, "free_memory": True})], posts
                assert "unload its models" in out
                run_voice(cfg, board(), tmp / "ep3")                                     # worker already up: no second ask
                assert len(posts) == 1
    finally:
        srv.shutdown()


def test_silent_render_records_none():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp) as (fk, count):
            b = board()
            run_voice(cfg_for(tmp, sys.executable), b, tmp / "ep", skip=True)
            assert {e["engine"] for e in b["audio"]["voice_engines"]} == {"none"} and not fk.calls
            assert b["audio"]["voice_engines"][0]["fallback"] == "--no-voice" and "vo" not in b["audio"]
            assert b["scenes"][1]["_line_times"] and b["captions"]
        with modules(kokoro=None):
            b = board()
            run_voice(cfg_for(tmp, sys.executable), b, tmp / "ep2")
            assert b["audio"]["voice_engines"][0] == {"scene": 0, "line": 0, "role": "announcer", "engine": "none",
                                                      "fallback": "kokoro not installed"}


def test_make_voice_refs_picks_bounciest_and_is_idempotent():
    spec = importlib.util.spec_from_file_location("make_voice_refs", ROOT / "scripts" / "make_voice_refs.py")
    mvr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mvr)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp) as (fk, count):
            brand, cands = tmp / "brand", tmp / "cands"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                assert mvr.run(brand, None, cand_dir=cands, cpu=True) == 3               # no Chatterbox env: bouncy MISS
            assert (brand / "announcer_ref.wav").exists() and (brand / "customer_ref.wav").exists()
            assert not (brand / "announcer_bouncy_ref.wav").exists()
            assert [c[1] for c in fk.calls] == ["bf_emma", "bf_isabella"]
            with contextlib.redirect_stdout(buf):
                assert mvr.run(brand, sys.executable, cand_dir=cands, cpu=True) == 0
            log = buf.getvalue()
            js = jobs(count)
            assert [j["seed"] for j in js] == [11, 22, 33] and all(j["exaggeration"] == 1.0 and j["cfg_weight"] == 0.35 for j in js)
            assert js[0]["ref"] == str((brand / "announcer_ref.wav").resolve()) and len(fk.calls) == 2
            sds = {s: mvr.pitch_stats(cands / f"announcer_bouncy_s{s}.wav")[0] for s in (11, 22, 33)}
            assert sds[33] > sds[11] > sds[22] > 0, sds                                  # vibrato 2.5 > 2.0 > 0.5 st
            assert (brand / "announcer_bouncy_ref.wav").read_bytes() == (cands / "announcer_bouncy_s33.wav").read_bytes()
            assert "candidate seed 22: pitch SD" in log and "kept seed 33" in log
            with contextlib.redirect_stdout(buf):
                assert mvr.run(brand, sys.executable, cand_dir=cands) == 0               # all present: nothing remade
            assert len(jobs(count)) == 3 and len(fk.calls) == 2                          # (and no GPU check needed)


def load_mvr():
    spec = importlib.util.spec_from_file_location("make_voice_refs", ROOT / "scripts" / "make_voice_refs.py")
    mvr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mvr)
    return mvr


def test_make_voice_refs_refuses_without_cuda():
    """It must never synthesise on a machine without a CUDA GPU (the cloud VM) unless --cpu is given."""
    mvr = load_mvr()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp) as (fk, count):
            brand = tmp / "brand"
            buf = io.StringIO()
            with modules(torch=None), contextlib.redirect_stdout(buf):                   # no torch = no CUDA
                assert mvr.run(brand, sys.executable, cand_dir=tmp / "c") == 4
            assert "REFUSED" in buf.getvalue() and not fk.calls and not jobs(count) and not brand.exists()
            gpu = types.ModuleType("torch")
            gpu.cuda = types.SimpleNamespace(is_available=lambda: True, get_device_name=lambda i: "Fake RTX 5090")
            buf = io.StringIO()
            with modules(torch=gpu), contextlib.redirect_stdout(buf):                    # GPU here, none in the cb env
                assert mvr.run(brand, sys.executable, cand_dir=tmp / "c") == 4
            log = buf.getvalue()
            assert "GPU: Fake RTX 5090" in log and "REFUSED announcer_bouncy_ref.wav" in log and "Chatterbox env" in log
            assert len(fk.calls) == 2 and not jobs(count) and not (brand / "announcer_bouncy_ref.wav").exists()
        with modules(torch=None), contextlib.redirect_stdout(io.StringIO()) as out:      # the script's own entry point
            assert mvr.main(["--config", "config.yaml"]) in (0, 4)
        assert "REFUSED" in out.getvalue() or all((ROOT / "brand" / "voice" / n).exists() for n in
                                                  ("announcer_ref.wav", "customer_ref.wav", "announcer_bouncy_ref.wav"))


FAKE_LIBS = {   # just enough torch / chatterbox / soundfile for cqf/tts_chatterbox.py; chatterbox prints to stdout on purpose
    "torch/__init__.py": "import types\nseeds = []\ncuda = types.SimpleNamespace(is_available=lambda: False)\n"
                         "backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))\n"
                         "def set_num_threads(n): pass\ndef manual_seed(s): seeds.append(s)\n",
    "chatterbox/__init__.py": "",
    "chatterbox/tts.py": "import numpy as np, torch\nclass _T:\n    def __init__(s, a): s.a = a\n"
                         "    def squeeze(s, i): return s\n    def detach(s): return s\n    def cpu(s): return s\n    def numpy(s): return s.a\n"
                         "class ChatterboxTTS:\n    sr = 24000\n    conds = []\n"
                         "    @classmethod\n    def from_pretrained(cls, device):\n        print('loading weights... (library noise on stdout)')\n"
                         "        import os\n        if os.environ.get('FAKE_LOAD_FAIL'): raise RuntimeError('CUDA out of memory (fake load)')\n"
                         "        return cls()\n"
                         "    def prepare_conditionals(self, ref, exaggeration):\n        print('conditioning', ref)\n        self.conds.append((ref, exaggeration))\n"
                         "    def generate(self, text, exaggeration, cfg_weight, temperature):\n"
                         "        if 'BOOM' in text: raise ValueError('bad text: ' + text)\n"
                         "        print('sampling', len(self.conds), torch.seeds[-1])\n"
                         "        return _T(np.full(2400 * (len(self.conds) + 1), 0.1 * (torch.seeds[-1] % 5), np.float32))\n",
    "soundfile.py": "import wave, numpy as np\ndef write(path, a, sr):\n    w = wave.open(str(path), 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)\n"
                    "    w.writeframes((np.asarray(a) * 32767).astype('<i2').tobytes()); w.close()\n",
}


def test_real_worker_script_protocol():
    """cqf/tts_chatterbox.py itself (as recovered) against fake libraries: stdout noise can't corrupt the protocol,
    conditioning is cached per (ref, exaggeration), take n uses seed+n, errors are reported and serving continues."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for rel, src in FAKE_LIBS.items():
            (tmp / "libs" / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp / "libs" / rel).write_text(src)
        o = [str(tmp / f"t{n}.wav") for n in range(3)]
        ra, rb = str(tone(tmp / "a.wav", 0.2)), str(tone(tmp / "b.wav", 0.2))
        jobs_in = [{"text": "Hello there.", "outs": o[:2], "ref": ra, "exaggeration": 0.85, "cfg_weight": 0.4, "seed": 10},
                   {"text": "BOOM café ’", "outs": o[2:], "ref": ra, "exaggeration": 0.85},
                   {"text": "Again.", "outs": o[2:], "ref": ra, "exaggeration": 0.85, "seed": 3},
                   {"text": "New ref.", "outs": o[2:], "ref": rb, "exaggeration": 0.5, "seed": 3},
                   {"text": "No ref.", "outs": o[2:], "ref": str(tmp / "missing.wav"), "exaggeration": 0.5}]
        # raw utf-8 in (not \u escapes) and a latin-1 io default: the worker must read utf-8 regardless
        r = subprocess.run([sys.executable, str(ROOT / "cqf" / "tts_chatterbox.py")],
                           input="".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs_in).encode("utf-8"),
                           capture_output=True, timeout=60,
                           env={**os.environ, "PYTHONPATH": str(tmp / "libs"), "PYTHONIOENCODING": "latin-1"})
        r.stdout, r.stderr = r.stdout.decode("utf-8"), r.stderr.decode("utf-8", "replace")
        lines = [json.loads(x) for x in r.stdout.splitlines()]                           # every stdout line is protocol
        assert lines[0] == {"ready": True, "device": "cpu", "sr": 24000}, (lines, r.stderr)
        assert lines[1] == {"ok": True, "outs": o[:2], "sr": 24000, "secs": [0.2, 0.2]}
        assert lines[2] == {"ok": False, "error": "ValueError: bad text: BOOM café ’"}, lines[2]
        assert lines[3]["ok"] and lines[4]["secs"] == [0.3]
        assert not lines[5]["ok"] and "missing.wav" in lines[5]["error"] and len(lines) == 6
        assert "loading weights" in r.stderr and "sampling" in r.stderr and r.stderr.count("conditioning") == 2
        assert info(Path(o[0]))[:2] == (24000, 1) and Path(o[1]).exists()
        r = subprocess.run([sys.executable, str(ROOT / "cqf" / "tts_chatterbox.py")], input="", capture_output=True,
                           text=True, timeout=60, env={**os.environ, "PYTHONPATH": str(tmp / "libs"), "FAKE_LOAD_FAIL": "1"})
        assert r.stdout.splitlines() == ['{"ready": false, "error": "RuntimeError: CUDA out of memory (fake load)"}'], r.stdout
        assert r.returncode == 1 and "Traceback" in r.stderr


def test_real_worker_load_error_reaches_the_report():
    """The real script through voice._worker (python -I, as on the PC): here it can't import its libraries, and that
    error, not just an exit code, becomes the line's fallback reason."""
    voice.shutdown()
    err, null = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)                                                                     # its traceback: not in test output
    try:
        with tempfile.TemporaryDirectory() as d:
            try:
                voice.chatterbox_job({"text": "Hi.", "outs": [str(Path(d) / "a.wav")], "ref": "x"}, sys.executable, 30, 60)
                raise AssertionError("expected a start failure")
            except RuntimeError as e:
                assert "failed to start" in str(e) and "ModuleNotFoundError" in str(e), e
    finally:
        os.dup2(err, 2)
        os.close(err), os.close(null)
        voice.shutdown()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
