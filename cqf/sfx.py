"""Procedural sound effects for comic/dialogue beats (Numan-style restraint: a ding, a chime,
a flat beep, tinny hold music, or a deliberate silence that ducks the music bed).
Generated, so there's nothing to license."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / decay)


def _tone(freqs, dur, attack=0.004, decay=0.25, amp=0.5):
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * f * t) * (0.6 ** i) for i, f in enumerate(freqs))
    return amp * x * _env(n, attack, decay) / max(1e-9, np.abs(x).max())


def make(kind: str, dur: float = 1.0) -> np.ndarray:
    if kind == "ding":                                  # message notification
        return _tone([1318.5, 2637, 3951], 0.9, decay=0.22, amp=0.45)
    if kind == "chime":                                 # auto-reply: three soft notes
        parts = [_tone([f, f * 2], 0.5, decay=0.18, amp=0.35) for f in (880, 1108.7, 1318.5)]
        out = np.zeros(int(1.2 * SR))
        for i, p in enumerate(parts):
            o = int(i * 0.16 * SR)
            out[o:o + len(p)] += p
        return out
    if kind == "beep":                                  # flat machine beep
        n = int(0.16 * SR)
        x = 0.35 * np.sign(np.sin(2 * np.pi * 440 * np.arange(n) / SR)) * np.minimum(1, np.arange(n) / 200)
        return np.convolve(x, np.ones(6) / 6, mode="same")
    if kind == "hold_music":                            # tinny phone-line hold loop
        n = int(dur * SR)
        t = np.arange(n) / SR
        notes = [523.3, 659.3, 784, 659.3, 587.3, 698.5, 880, 698.5]
        idx = (t / 0.3).astype(int) % len(notes)
        f = np.array(notes)[idx]
        x = np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)) * 0.12
        x = np.convolve(x, np.ones(24) / 24, mode="same")        # crude band-limit: phone line
        return x * np.minimum(1, t / 0.05) * np.minimum(1, (dur - t) / 0.1)
    return np.zeros(int(0.01 * SR))                     # 'silence' is handled on the music bed


def track(cues: list[dict], seconds: float, out: Path) -> Path:
    """Mix cues [{at, kind, dur?}] into one stereo wav aligned to the board timeline."""
    n = int(seconds * SR) + SR
    mix = np.zeros(n)
    for c in cues:
        if c.get("kind") == "silence":
            continue
        x = make(c["kind"], float(c.get("dur", 2.0)))
        o = int(float(c["at"]) * SR)
        mix[o:o + len(x)] += x[: max(0, n - o)]
    mix = mix[: int(seconds * SR)]
    st = np.stack([mix, mix], axis=1)
    peak = np.abs(st).max()
    if peak > 0.95:
        st *= 0.95 / peak
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes((st * 32767).astype("<i2").tobytes())
    return out


def silence_music(music_wav: Path, windows: list[tuple[float, float]]):
    """Duck the music bed to silence in [start, end] windows with 60 ms fades (comic dead air)."""
    with wave.open(str(music_wav)) as w:
        ch, sr, frames = w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())
    x = np.frombuffer(frames, "<i2").astype(np.float32).reshape(-1, ch)
    gain = np.ones(len(x), np.float32)
    f = int(0.06 * sr)
    for a, b in windows:
        i, j = int(a * sr), min(len(x), int(b * sr))
        if j <= i:
            continue
        gain[i:j] = 0
        gain[max(0, i - f):i] = np.minimum(gain[max(0, i - f):i], np.linspace(1, 0, i - max(0, i - f)))
        gain[j:j + f] = np.minimum(gain[j:j + f], np.linspace(0, 1, len(gain[j:j + f])))
    x = (x * gain[:, None]).astype("<i2")
    with wave.open(str(music_wav), "wb") as w:
        w.setnchannels(ch), w.setsampwidth(2), w.setframerate(sr), w.writeframes(x.tobytes())
