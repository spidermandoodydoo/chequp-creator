"""A procedurally generated music bed, so drafts are never silent and nothing needs licensing.

Warm pad chords + a soft plucked arpeggio + a light pulse, in the "sparse, warm" register
Sophie's brief and the live ads use. It's a placeholder for a licensed track or an
ACE-Step bed from mama; swap with `"audio": {"music": "path.wav"}` on any board."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000
# I–V–vi–IV in D major (bright, warm); voicings in Hz-friendly MIDI numbers.
PROGRESSIONS = {
    "warm": [[50, 57, 62, 66, 69], [45, 57, 61, 64, 69], [47, 59, 62, 66, 71], [43, 55, 59, 62, 67]],
    "bright": [[50, 62, 66, 69, 74], [45, 61, 64, 69, 73], [47, 62, 66, 71, 74], [43, 59, 62, 67, 71]],
}


def _hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):          # one-pole; fine for a few hundred thousand samples
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def bed(seconds: float, out: Path, mood: str = "warm", bpm: float = 88, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR) + SR
    t = np.arange(n) / SR
    beat = 60 / bpm
    bar = beat * 4
    prog = PROGRESSIONS.get(mood, PROGRESSIONS["warm"])
    pad = np.zeros(n)
    pluck = np.zeros(n)
    pulse = np.zeros(n)
    nbars = int(np.ceil(n / SR / bar))
    for b in range(nbars):
        chord = prog[b % len(prog)]
        s0, s1 = int(b * bar * SR), min(n, int((b + 1) * bar * SR + 0.6 * SR))
        seg = t[s0:s1] - t[s0]
        env = np.minimum(1, seg / 0.8) * np.exp(-np.maximum(0, seg - bar) / 0.5)
        for m in chord:
            for det in (-0.06, 0.06):     # two detuned voices per note → chorus warmth
                f = _hz(m + det)
                pad[s0:s1] += env * (np.sin(2 * np.pi * f * seg) + 0.25 * np.sin(4 * np.pi * f * seg)) / len(chord)
        # plucked arpeggio on 8ths, up the top three chord tones
        for k in range(8):
            ps = int((b * bar + k * beat / 2) * SR)
            if ps >= n:
                break
            m = chord[2 + (k % 3)] + (12 if k in (3, 7) else 0)
            pe = min(n, ps + int(0.9 * SR))
            ts = t[ps:pe] - t[ps]
            pluck[ps:pe] += np.sin(2 * np.pi * _hz(m) * ts) * np.exp(-ts / 0.22) * (0.55 + 0.15 * rng.random())
        # soft low pulse on beats 1 and 3
        for k in (0, 2):
            ps = int((b * bar + k * beat) * SR)
            pe = min(n, ps + int(0.35 * SR))
            if ps >= n:
                break
            ts = t[ps:pe] - t[ps]
            pulse[ps:pe] += np.sin(2 * np.pi * (_hz(chord[0] - 12) * (1 + 0.5 * np.exp(-ts / 0.03))) * ts) * np.exp(-ts / 0.12)
    mix = 0.55 * _lowpass(pad, 1400) + 0.35 * _lowpass(pluck, 3200) + 0.4 * pulse
    fade = np.minimum(1, t / 1.2) * np.minimum(1, np.maximum(0, (seconds + 0.2 - t)) / 1.5)
    mix *= fade
    left = mix + 0.18 * np.roll(mix, int(0.011 * SR))
    right = mix + 0.18 * np.roll(mix, int(0.017 * SR))
    st = np.stack([left, right], axis=1)[: int(seconds * SR)]
    st /= max(1e-9, np.abs(st).max()) / 0.7
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes((st * 32767).astype("<i2").tobytes())
    return out
