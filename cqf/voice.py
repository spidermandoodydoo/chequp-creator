"""Voiceover. Two engines, chosen per line by the preset's voice block (pipeline._cast):
  kokoro      Kokoro-82M (Apache-2.0), British voices via lang_code 'b'. CPU, instant, no energy control.
  chatterbox  Chatterbox 0.5B (MIT) cloning a reference clip we own, with exaggeration / cfg_weight for
              energy. Runs in its own Python env (cqf/tts_chatterbox.py) because it pins torch 2.6.
Returns per-line wav files plus word timings for the on-brand caption layer; master() polishes the
assembled VO stem (EQ, de-ess, compression, loudness) before the mix.

Every model runs on the machine that renders (Dan's 5090 PC). Nothing here imports kokoro, torch or
faster-whisper at module level, so `import cqf` works on a machine without them."""
from __future__ import annotations

import atexit
import json
import queue
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

_lock = threading.Lock()      # Kokoro isn't thread-safe (shorts-factory serialises it too); one worker pipe
_pipes: dict = {}
_workers: dict = {}
_dead: dict = {}              # python -> why its worker can't run: later lines fall back at once, no reload per line
_crashes: dict = {}           # python -> times its worker died mid-job (2 = dead for the run: no reload-crash loop)
_freed: set = set()           # ComfyUI urls already asked to unload their models this run
_asr: dict = {}
_said: set = set()
WORKER = Path(__file__).with_name("tts_chatterbox.py")
START_TIMEOUT_S = 1200        # first start downloads ~3 GB of Chatterbox weights; later starts take seconds on the 5090
TAKE_TIMEOUT_S = 180          # per take: seconds on the 5090, ~30-60 s on a CPU
MAX_CRASHES = 2

# Spoken-only respellings so the brand is said "check-up". Captions keep the written form.
LEXICON = {   # applied in order: the URL before the bare name
    "kokoro": {r"\bchequp\.com\b": "[chequp](/ʧˈɛkʌp/) dot com", r"(?<!\[)\bCheqUp\b(?!\])": "[CheqUp](/ʧˈɛkʌp/)"},
    "chatterbox": {r"\bchequp\.com\b": "CheckUp dot com", r"\bCheqUp\b": "CheckUp"},
}


def respell(text: str, engine: str, extra: dict | None = None) -> str:
    for pat, rep in {**LEXICON.get(engine, {}), **((extra or {}).get(engine) or {})}.items():
        text = re.sub(pat, rep, text, flags=re.I)
    return text.replace("’", "'") if engine == "chatterbox" else text


def _pipeline(lang: str):
    from kokoro import KPipeline  # imported lazily: optional dependency
    if lang not in _pipes:
        _pipes[lang] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M")
    return _pipes[lang]


def _write_wav(path: Path, audio, sr: int):
    import numpy as np
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr), w.writeframes(pcm.tobytes())


def speak(text: str, out: Path, voice: str = "bf_emma", speed: float = 1.0, lang: str = "b", sr: int = 24000,
          *, engine: str = "kokoro", opts: dict | None = None, lexicon: dict | None = None) -> tuple[float, list[dict]]:
    """Synthesize one line. Returns (seconds, words[{w,s,e}]) with times relative to the line.
    voice (kokoro): a voice id or a comma blend "bf_emma,bf_alice" (Kokoro averages the voice tensors).
    opts (chatterbox): ref, exaggeration, cfg_weight, temperature, takes, seed, tighten_pauses, tempo,
    python, asr_model, timeout_s (per take), start_timeout_s. Raises if Chatterbox can't run; the caller
    (pipeline._voice) then reads the line with Kokoro."""
    if engine == "chatterbox":
        return _chatterbox(text, Path(out), dict(opts or {}), lexicon)
    return _kokoro(text, Path(out), voice, speed, lang, sr, lexicon)


def _kokoro(text, out, voice, speed, lang, sr, lexicon):
    import numpy as np
    with _lock:
        chunks, words, t = [], [], 0.0
        for res in _pipeline(lang)(respell(text, "kokoro", lexicon), voice=voice, speed=speed):
            audio = res.audio.cpu().numpy() if hasattr(res.audio, "cpu") else np.asarray(res.audio)
            toks = getattr(res, "tokens", None) or []
            for tk in toks:  # Kokoro >=0.9 exposes token timestamps
                if getattr(tk, "start_ts", None) is not None and re.search(r"\w", tk.text):
                    words.append({"w": tk.text, "s": round(t + tk.start_ts, 3), "e": round(t + tk.end_ts, 3)})
            chunks.append(audio)
            t += len(audio) / sr
    audio = np.concatenate(chunks) if chunks else np.zeros(1)
    _write_wav(out, audio, sr)
    dur = len(audio) / sr
    if not words:
        words = estimate_words(text, 0.0, dur)
    return dur, words


# ---------------------------------------------------------------- chatterbox
def _reader(w, q):
    for line in w.stdout:
        q.put(line)
    q.put(None)                                       # EOF: the worker exited


def _reply(w, timeout: float) -> dict:
    """Next JSON line from the worker. Kills it and raises TimeoutError after `timeout` seconds."""
    end = time.monotonic() + timeout
    while True:
        try:
            line = w.replies.get(timeout=max(0.01, end - time.monotonic()))
        except queue.Empty:
            w.kill()
            raise TimeoutError(f"chatterbox worker gave no answer in {timeout:g}s (stopped)") from None
        if line is None:
            try:
                code = w.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = w.poll()
            raise RuntimeError(f"chatterbox worker exited (code {code})")
        cut = max(line.rfind('{"ok"'), line.rfind('{"ready"'))           # native output glued in front of a reply
        for cand in (line, line[cut:] if cut > 0 else ""):
            try:
                msg = json.loads(cand)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                return msg
        # not a protocol line: ignore it


def worker_ready(python: str) -> bool:
    w = _workers.get(python)
    return w is not None and w.poll() is None


def _worker(python: str, timeout: float = START_TIMEOUT_S):
    """The one long-lived worker per Python env (singleton): loads the model once, then serves jobs."""
    if python in _dead:
        raise RuntimeError(_dead[python])
    w = _workers.get(python)
    if w is None or w.poll() is not None:
        try:   # utf-8 both ends: the worker runs with -I, which ignores PYTHONUTF8 / PYTHONIOENCODING
            w = subprocess.Popen([python, "-I", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 encoding="utf-8", errors="replace", bufsize=1)
        except OSError as e:
            _dead[python] = f"chatterbox worker can't start ({python}): {e}"
            raise RuntimeError(_dead[python]) from e
        w.replies = queue.Queue()
        threading.Thread(target=_reader, args=(w, w.replies), daemon=True).start()
        try:
            hello = _reply(w, timeout)
        except (TimeoutError, RuntimeError) as e:
            w.kill()
            hello = {"error": str(e)}
        if not hello.get("ready"):
            _dead[python] = f"chatterbox worker failed to start ({python}): {hello.get('error', hello)}"
            raise RuntimeError(_dead[python])
        if hello.get("device") == "cpu":
            print(f"  WARNING chatterbox worker is on the CPU (its torch sees no CUDA GPU: {python}); takes will be slow")
        _workers[python] = w
        atexit.register(w.terminate)
        from . import gpu_gate                        # the gate may stop it to give its VRAM back (restarts on the next line)
        gpu_gate.register_releaser("the Chatterbox worker", release_gpu)
    return w


def release_gpu() -> bool:
    """For cqf.gpu_gate: stop idle Chatterbox workers and drop Whisper models, so the VRAM they hold goes back
    (to shorts-factory, or to CheqUp's next ComfyUI job). Never while a take is rendering. True if anything went."""
    if not _lock.acquire(blocking=False):             # a job is running: leave it
        return False
    try:
        live = [w for w in _workers.values() if w.poll() is None]
        for w in live:
            w.terminate()
        _workers.clear()                              # not _dead/_crashes: the next line simply starts a fresh worker
        had_asr = bool(_asr)
        _asr.clear()
        return bool(live) or had_asr
    finally:
        _lock.release()


def chatterbox_job(job: dict, python: str, timeout: float | None = None, start_timeout: float | None = None) -> dict:
    """Send one job line to the worker, wait for its one reply line. Raises on failure or timeout;
    a timeout, a worker that won't start, or one that dies mid-job twice marks this env dead for the rest
    of the run (later lines go straight to Kokoro). A single mid-job death restarts the worker on the next line."""
    timeout = timeout or TAKE_TIMEOUT_S * max(1, len(job.get("outs") or [0]))
    with _lock:
        w = _worker(python, start_timeout or START_TIMEOUT_S)
        try:
            try:
                w.stdin.write(json.dumps(job) + "\n")
                w.stdin.flush()
                res = _reply(w, timeout)
            except TimeoutError as e:                 # (before OSError: TimeoutError is one)
                _dead[python] = str(e)
                raise
            except OSError as e:                      # broken pipe: it died between jobs
                raise RuntimeError(f"chatterbox worker exited ({e})") from e
        except RuntimeError as e:                     # died mid-job: the next line restarts it, but only once
            _crashes[python] = _crashes.get(python, 0) + 1
            if _crashes[python] >= MAX_CRASHES:
                _dead[python] = f"{e}; it died {_crashes[python]} times, so Kokoro for the rest of the run"
            raise
    if not res.get("ok"):
        raise RuntimeError(f"chatterbox: {res.get('error')}")
    return res


def free_comfy_vram(machines, never=()) -> list[str]:
    """Once per run, before the Chatterbox worker first loads: ask CheqUp's own ComfyUI on this PC (enabled farm
    machines on 127.0.0.1/localhost) to unload its models. Otherwise the b-roll models it keeps in VRAM can leave
    Chatterbox a CUDA out-of-memory at load, and every line goes to Kokoro. Best effort (POST /free, the same as
    ComfyUI's "Unload models" button); ComfyUI reloads what its next job needs. It never updates or restarts
    anything, and never POSTs to a URL in `never` (gpu_gate.protected: shorts-factory's ComfyUI)."""
    from .gpu_gate import _norm, protected
    skip = {_norm(u) for u in never or ()} | protected(None)      # + 127.0.0.1:8188 (shorts-factory's) always
    done = []
    for m in machines or []:
        if m.get("enabled") is False or str(m.get("host")) not in ("127.0.0.1", "localhost"):
            continue
        for k in range(max(1, int(m.get("gpus") or 1))):
            url = f"http://{m['host']}:{int(m.get('first_port') or 8288) + k}"
            if url in _freed or _norm(url) in skip:
                continue
            _freed.add(url)
            try:
                import requests
                requests.post(url + "/free", json={"unload_models": True, "free_memory": True}, timeout=5).raise_for_status()
                done.append(url)
            except Exception:  # noqa: BLE001 — ComfyUI down or old: Chatterbox just tries its luck
                pass
    if done:
        print(f"  voice: asked ComfyUI {', '.join(done)} to unload its models before Chatterbox loads")
        time.sleep(3)                                 # ComfyUI frees them on its worker thread
    return done


def shutdown():
    """Stop the workers and forget failures (tests, scripts/make_voice_refs.py)."""
    for w in _workers.values():
        if w.poll() is None:
            w.terminate()
    _workers.clear()
    _dead.clear()
    _crashes.clear()
    _freed.clear()


def _chatterbox(text, out, o, lexicon):
    """Render `takes` takes, keep them all (sNN_J.t1.wav … for picking by ear), copy the take Whisper
    hears best to `out`. Captions get Whisper word times when the word count matches the script.
    Takes are reused when text, reference and settings are unchanged (sNN_J.job.json).
    `tempo` (<1 slower) time-stretches the chosen take: Chatterbox itself has no speed control."""
    ref = o.get("ref")
    if not ref or not Path(ref).exists():
        raise FileNotFoundError(f"chatterbox reference clip missing: {ref}")
    takes = max(1, int(o.get("takes", 1)))
    paths = [out.with_name(f"{out.stem}.t{n + 1}.wav") for n in range(takes)]
    rp = Path(ref).resolve()
    job = {"text": respell(text, "chatterbox", lexicon), "outs": [str(p) for p in paths], "ref": str(rp),
           "ref_stat": [rp.stat().st_size, int(rp.stat().st_mtime)],
           "exaggeration": o.get("exaggeration", 0.7), "cfg_weight": o.get("cfg_weight", 0.5),
           "temperature": o.get("temperature", 0.8), "seed": o.get("seed", 1234),
           "tighten": o.get("tighten_pauses")}   # part of the cache key: changing it re-renders
    jf = out.with_suffix(".job.json")
    try:
        cached = jf.exists() and json.loads(jf.read_text(encoding="utf-8")) == job and all(p.exists() for p in paths)
    except (OSError, ValueError):                     # unreadable job file: render again
        cached = False
    if not cached:
        jf.unlink(missing_ok=True)                    # a half-finished job never counts as cached
        chatterbox_job(job, o.get("python") or "python",
                       float(o["timeout_s"]) * takes if o.get("timeout_s") else None, o.get("start_timeout_s"))
        tp = o.get("tighten_pauses")
        if tp:
            for p in paths:
                _tighten(p, float(tp.get("max_gap", 0.22)), float(tp.get("to", 0.15)))
        jf.write_text(json.dumps(job), encoding="utf-8")
    best, words, score = paths[0], None, None
    for p in paths:                                   # lowest misheard-word rate, ties broken by fewest pitch leaps
        s, ws = _asr_check(p, text, o.get("asr_model", "small.en"))
        s = (s if s is not None else 0.0) + _leaps(p)
        if score is None or s < score:
            best, words, score = p, ws, s
    shutil.copyfile(best, out)
    _trim_tail(out, words, _dur(out))
    tempo = float(o.get("tempo", 1.0) or 1.0)
    if abs(tempo - 1) > 0.01 and _stretch(out, tempo):
        words = words and [{**w, "s": round(w["s"] / tempo, 3), "e": round(w["e"] / tempo, 3)} for w in words]
    dur = _dur(out)
    return dur, words or estimate_words(text, 0.0, dur)


def _read(path: Path):
    """(float samples, sr) via soundfile, or the stdlib for 16-bit PCM wavs when soundfile isn't installed."""
    try:
        import soundfile as sf
        y, sr = sf.read(str(path))
        return y, sr
    except ImportError:
        import numpy as np
        with wave.open(str(path)) as f:
            sr, ch = f.getframerate(), f.getnchannels()
            y = np.frombuffer(f.readframes(f.getnframes()), "<i2").astype(np.float64) / 32768
        return (y if ch == 1 else y.reshape(-1, ch)), sr


def _write(path: Path, y, sr: int):
    try:
        import soundfile as sf
        sf.write(str(path), y, sr)
    except ImportError:
        import numpy as np
        pcm = (np.clip(y, -1, 32767 / 32768) * 32768).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1 if pcm.ndim == 1 else pcm.shape[1]), w.setsampwidth(2), w.setframerate(sr)
            w.writeframes(pcm.tobytes())


def _tighten(path: Path, max_gap: float = 0.22, to: float = 0.15):
    """Shorten inner pauses longer than max_gap to `to` seconds (keeps lead-in/tail): the 'fluid' fix.
    Runs on each take before Whisper, so caption word times match the tightened audio."""
    import numpy as np
    y, sr = _read(path)
    mono = y if y.ndim == 1 else y.mean(1)
    hop = max(1, int(0.01 * sr))
    n = len(mono) // hop
    if n < 10:
        return
    rms = np.sqrt(np.mean(mono[: n * hop].reshape(n, hop) ** 2, axis=1))
    quiet = rms < np.percentile(rms, 90) * 0.08
    if quiet.all():
        return
    first, last = int(np.argmax(~quiet)), n - int(np.argmax(~quiet[::-1]))
    keep = np.ones(len(y), bool)
    i = first
    while i < last:
        if quiet[i]:
            j = i
            while j < last and quiet[j]:
                j += 1
            if (j - i) * 0.01 > max_gap:
                keep[i * hop + int(to / 2 * sr): j * hop - int(to / 2 * sr)] = False
            i = j
        else:
            i += 1
    _write(path, y[keep], sr)


def _dur(path: Path) -> float:
    with wave.open(str(path)) as f:
        return f.getnframes() / f.getframerate()


def _stretch(path: Path, tempo: float) -> bool:
    """Formant-safe time-stretch in place (rubberband; atempo if this ffmpeg build lacks it). False = unchanged."""
    tmp = path.with_name(path.stem + ".stretch.wav")
    for flt in (f"rubberband=tempo={tempo:.4f}:pitchq=quality", f"atempo={tempo:.4f}"):
        try:
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-af", flt, "-c:a", "pcm_s16le", str(tmp)])
        except OSError:
            break
        if r.returncode == 0:
            tmp.replace(path)
            return True
    print(f"  WARNING tempo {tempo} not applied to {path.name} (ffmpeg has neither rubberband nor atempo?)")
    return False


def _leaps(path: Path) -> float:
    """Share of back-to-back voiced pitch frames (20 ms apart, no consonant gap between) that jump more
    than 9 semitones: squeaks and octave breaks, e.g. a take that flips up an octave on the CTA's final
    "you". Jumps across word gaps are normal intonation and don't count. 0 without praat-parselmouth."""
    try:
        import numpy as np
        import parselmouth
    except ImportError:
        return 0.0
    f = parselmouth.Sound(_wav16k(path).astype("float64"), 16000).to_pitch_ac(
        time_step=0.02, pitch_floor=70, pitch_ceiling=500).selected_array["frequency"]
    both = (f[1:] > 0) & (f[:-1] > 0)
    if both.sum() < 5:
        return 0.0
    jump = np.abs(12 * np.log2(f[1:][both] / f[:-1][both]))
    return float((jump > 9).mean())


def _norm(t: str) -> list[str]:
    t = re.sub(r"\bcheq", "check", t.lower().replace("’", "'").replace("-", " "))
    return re.findall(r"[a-z0-9']+", re.sub(r"\bcheck ?up\b", "checkup", t))


def _asr_check(path: Path, text: str, model: str):
    """(word error rate, caption words) via faster-whisper, or (None, None) if it isn't installed or fails."""
    try:
        return _asr_run(path, text, model)
    except Exception as e:  # ASR only ranks takes; it must never stop a render
        msg = f"  asr check skipped ({type(e).__name__}: {e})"
        if msg not in _said:                          # once per reason, not once per take
            _said.add(msg)
            print(msg)
        return None, None


def _wav16k(path: Path):
    """Mono float32 at 16 kHz without PyAV (faster-whisper's own decoder breaks on some av versions)."""
    import numpy as np
    with wave.open(str(path)) as f:
        sr, ch = f.getframerate(), f.getnchannels()
        a = np.frombuffer(f.readframes(f.getnframes()), "<i2").astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(1)
    n = int(len(a) * 16000 / sr)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)


def _asr_model(model: str, cpu: bool = False):
    from faster_whisper import WhisperModel
    if (model, cpu) not in _asr:
        dev, ct = "cpu", "int8"
        if not cpu:
            try:
                import torch
                dev, ct = ("cuda", "float16") if torch.cuda.is_available() else ("cpu", "int8")
            except ImportError:
                pass
        _asr[(model, cpu)] = WhisperModel(model, device=dev, compute_type=ct)
    return _asr[(model, cpu)]


def _asr_run(path: Path, text: str, model: str):
    audio = _wav16k(path)
    try:
        segs, _ = _asr_model(model).transcribe(audio, language="en", beam_size=1, word_timestamps=True)
        heard = [wd for sg in segs for wd in (sg.words or [])]
    except ImportError:
        raise
    except Exception:  # e.g. CTranslate2 can't find cuDNN on Windows: Whisper small.en on the CPU is fine
        segs, _ = _asr_model(model, cpu=True).transcribe(audio, language="en", beam_size=1, word_timestamps=True)
        heard = [wd for sg in segs for wd in (sg.words or [])]
    ref, hyp = _norm(text), _norm(" ".join(wd.word for wd in heard))
    d = list(range(len(hyp) + 1))                     # word-level Levenshtein
    for i in range(1, len(ref) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(hyp) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (ref[i - 1] != hyp[j - 1]))
    wer = d[-1] / max(1, len(ref))
    script = [w for w in text.split() if re.search(r"\w", w)]
    words = None
    if len(heard) == len(script):                     # same count: script spelling, Whisper timing
        words = [{"w": re.sub(r"[^\w’'-]+$", "", w), "s": round(h.start, 3), "e": round(h.end, 3)} for w, h in zip(script, heard)]
    return wer, words


def _trim_tail(path: Path, words, dur: float, keep: float = 0.25):
    """Chatterbox can leave breath/noise after the last word: cut 0.25 s after it."""
    if not words or words[-1]["e"] + keep >= dur:
        return
    with wave.open(str(path)) as f:
        prm, n = f.getparams(), int((words[-1]["e"] + keep) * f.getframerate())
        pcm = f.readframes(n)
    with wave.open(str(path), "wb") as f:
        f.setparams(prm)
        f.writeframes(pcm)


def pcm16(path: Path, sr: int) -> bytes:
    """A line's wav as mono 16-bit PCM at `sr`, for assembling vo_raw.wav from lines of either engine."""
    import numpy as np
    with wave.open(str(path)) as f:
        ch, sw, fr = f.getnchannels(), f.getsampwidth(), f.getframerate()
        raw = f.readframes(f.getnframes())
    if (ch, sw, fr) == (1, 2, sr):
        return raw
    if sw != 2:
        raise ValueError(f"{path}: {8 * sw}-bit wav, expected 16-bit PCM")
    a = np.frombuffer(raw, "<i2").astype(np.float32).reshape(-1, ch).mean(1)
    if fr != sr:
        a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * sr / fr)), np.arange(len(a)), a)
    return np.clip(np.round(a), -32768, 32767).astype("<i2").tobytes()


# ---------------------------------------------------------------- mastering
MASTER = ("highpass=f=80:poles=2,"
          "equalizer=f=250:t=q:w=1.0:g=-2,"
          "equalizer=f=3500:t=q:w=1.2:g=2.5,"
          "highshelf=f=9000:g=1.5,"
          "deesser=i=0.4:m=0.5:f=0.5,"
          "acompressor=threshold=0.1:ratio=3:attack=5:release=80:knee=4:makeup=1.5,"
          "loudnorm=I=-16:TP=-1.5:LRA=7")


def master(src: Path, dst: Path, cfg: dict | None = None) -> Path:
    """VO stem polish before the mix (render.mjs then takes the whole mix to -14 LUFS / -1 dBTP).
    cfg: filters (ffmpeg chain, default MASTER), pitch_st (formant-preserving lift, max +1)."""
    cfg = cfg or {}
    chain = cfg.get("filters") or MASTER
    st = min(float(cfg.get("pitch_st", 0) or 0), 1.0)
    run = lambda c: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", c + ",aresample=48000",  # noqa: E731
                                    "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)
    if st:
        try:
            run(f"rubberband=pitch={2 ** (st / 12):.5f}:formant=preserved:pitchq=quality," + chain)
            return dst
        except subprocess.CalledProcessError:   # this ffmpeg has no rubberband: keep the polish, skip the lift
            print(f"  WARNING voice master: pitch_st {st} skipped (ffmpeg without rubberband)")
    run(chain)
    return dst


def estimate_words(text: str, start: float, dur: float) -> list[dict]:
    """Fallback timing: spread words across the line by character length."""
    toks = [re.sub(r"[^\w’'-]+$", "", w) for w in text.split() if re.search(r"\w", w)]
    total = sum(len(w) + 1 for w in toks) or 1
    out, t = [], start
    for w in toks:
        d = dur * (len(w) + 1) / total
        out.append({"w": w, "s": round(t, 3), "e": round(t + d * 0.9, 3)})
        t += d
    return out
