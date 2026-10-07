"""Voiceover with Kokoro (same TTS as shorts-factory; British voices via lang_code 'b').
Returns per-scene wav files plus word timings for the on-brand caption layer."""
from __future__ import annotations

import re
import threading
import wave
from pathlib import Path

_lock = threading.Lock()      # Kokoro isn't thread-safe (shorts-factory serialises it too)
_pipes: dict = {}


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


def speak(text: str, out: Path, voice: str, speed: float = 0.95, lang: str = "b", sr: int = 24000) -> tuple[float, list[dict]]:
    """Synthesize one line. Returns (seconds, words[{w,s,e}]) with times relative to the line."""
    import numpy as np
    with _lock:
        chunks, words, t = [], [], 0.0
        for res in _pipeline(lang)(text, voice=voice, speed=speed):
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


def estimate_words(text: str, start: float, dur: float) -> list[dict]:
    """Fallback timing: spread words across the line by character length."""
    toks = text.split()
    total = sum(len(w) + 1 for w in toks) or 1
    out, t = [], start
    for w in toks:
        d = dur * (len(w) + 1) / total
        out.append({"w": w, "s": round(t, 3), "e": round(t + d * 0.9, 3)})
        t += d
    return out
