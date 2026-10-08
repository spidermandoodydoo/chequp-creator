#!/usr/bin/env python3
"""Make the synthetic voice reference clips Chatterbox clones, in brand/voice/. RUN THIS ON THE 5090 PC:
every model here (Kokoro, Chatterbox) runs locally on the PC, never on the cloud VM.

  .venv\\Scripts\\python.exe scripts\\make_voice_refs.py [--config config.pc.yaml] [--force]

  announcer_ref.wav         ~8.6 s of Kokoro bf_emma reading a neutral news paragraph
  customer_ref.wav          ~8 s of Kokoro bf_isabella, conversational (the dialogue "customer" role)
  announcer_bouncy_ref.wav  Chatterbox reading a promo paragraph FROM announcer_ref.wav at high energy
                            (exaggeration 1.0, cfg 0.35, temperature 0.9), seeds 11/22/33; keeps the take with
                            the highest pitch SD (the 8 Oct pick was seed 11, 3.81 st). Default preset reference.

Idempotent: files that exist are kept (their pitch SD is printed) unless --force. Every reference is
synthetic (Kokoro-82M Apache-2.0 / Chatterbox MIT), so no real person's voice is cloned; see brand/voice/README.md.
Refuses to make anything unless this Python's torch (and, for the bouncy clip, the Chatterbox env's torch) sees a
CUDA GPU, so it can't run by accident on a machine without one, such as the cloud VM. --cpu overrides that.
Exit: 0 all three present, 1 a model step failed, 2 Kokoro missing, 3 Chatterbox env missing (bouncy ref not made),
4 refused: no CUDA GPU (and no --cpu)."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cqf import voice  # noqa: E402  (stdlib-only at import: no kokoro/torch until a clip is made)

BRAND = ROOT / "brand" / "voice"
KOKORO_REFS = {   # file: (text, Kokoro voice, speed)
    "announcer_ref.wav": ("Councils across England have agreed a new plan for local libraries, with longer opening "
                          "hours from next spring and more room for community groups to meet.", "bf_emma", 1.0),
    "customer_ref.wav": ("So I finally sorted the garden this weekend. It took ages, honestly, but it looks lovely "
                         "now, and the kids actually want to sit out there.", "bf_isabella", 1.0),
}
BOUNCY_TEXT = ("Good news for your week ahead. There's a brand new way to feel more in control, with real people on "
               "your side, and it fits around the life you've already got. Here's how it works.")
BOUNCY = {"exaggeration": 1.0, "cfg_weight": 0.35, "temperature": 0.9}
SEEDS = (11, 22, 33)


def _f0_numpy(y, sr: int, fmin: float = 70, fmax: float = 500):
    """Plain autocorrelation pitch track (10 ms hop), used only when praat-parselmouth isn't installed."""
    import numpy as np
    n, hop, lo, hi = int(0.04 * sr), int(0.01 * sr), int(sr / fmax), int(sr / fmin)
    frames = [y[i:i + n] for i in range(0, len(y) - n, hop)]
    if not frames:
        return np.zeros(0)
    rms = np.array([np.sqrt(np.mean(f ** 2)) for f in frames])
    thr = np.percentile(rms, 90) * 0.1
    out = np.zeros(len(frames))
    for k, f in enumerate(frames):
        if rms[k] < thr:
            continue
        f = (f - f.mean()) * np.hanning(n)
        ac = np.fft.irfft(np.abs(np.fft.rfft(f, 2 * n)) ** 2)[:n]
        if ac[0] <= 0:
            continue
        lag = lo + int(np.argmax(ac[lo:hi + 1]))
        if ac[lag] / ac[0] < 0.4:
            continue
        if lo < lag < hi:                                   # parabolic peak refinement
            a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
            lag = lag + 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else lag
        out[k] = sr / lag
    return out


def pitch_stats(path: Path) -> tuple[float, float, float]:
    """(pitch SD in semitones around the median, median Hz, seconds): the 'bounce' measure of the 8 Oct sweep."""
    import numpy as np
    y, sr = voice._wav16k(path), 16000
    try:
        import parselmouth
        f0 = parselmouth.Sound(y.astype("float64"), sr).to_pitch_ac(time_step=0.01, pitch_floor=70,
                                                                   pitch_ceiling=500).selected_array["frequency"]
    except ImportError:
        f0 = _f0_numpy(y.astype("float64"), sr)
    f = f0[f0 > 0]
    if len(f) < 10:
        return 0.0, 0.0, round(len(y) / sr, 2)
    st = 12 * np.log2(f / np.median(f))
    return round(float(np.std(st)), 2), round(float(np.median(f)), 1), round(len(y) / sr, 2)


def _show(tag: str, p: Path, extra: str = ""):
    sd, hz, secs = pitch_stats(p)
    rel = p.relative_to(ROOT) if p.is_relative_to(ROOT) else p
    print(f"{tag} {rel}: pitch SD {sd} st, median {hz} Hz, {secs} s{extra}" + ("  (over 10 s: Chatterbox only uses the first 10 s)" if secs > 10 else ""))


def cuda_here() -> tuple[bool, str]:
    """Does this Python's torch see a CUDA GPU? (ok, GPU name or why not)."""
    try:
        import torch
    except ImportError:
        return False, "torch isn't installed in this Python"
    try:
        return (True, torch.cuda.get_device_name(0)) if torch.cuda.is_available() else (False, "torch sees no CUDA GPU")
    except Exception as e:  # noqa: BLE001 — a broken CUDA install counts as none
        return False, f"{type(e).__name__}: {e}"


def cuda_in(python: str) -> tuple[bool, str]:
    """The same question for another env (the Chatterbox venv), asked in a subprocess."""
    code = ("import sys, torch; ok = torch.cuda.is_available(); "
            "print(torch.cuda.get_device_name(0) if ok else 'torch sees no CUDA GPU'); sys.exit(0 if ok else 1)")
    try:
        r = subprocess.run([python, "-I", "-c", code], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"
    said = (r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}").splitlines()[-1][:200]
    return r.returncode == 0, said


REFUSED = ("REFUSED {what}: no CUDA GPU in {where} ({why}). Voice references are made on Dan's 5090 PC "
           "(.venv\\Scripts\\python.exe scripts\\make_voice_refs.py), never on a machine without a GPU. "
           "Pass --cpu only if a CPU run is really meant.")


def make_kokoro(dst: Path, text: str, vid: str, speed: float, lang: str = "b", sr: int = 24000):
    tmp = dst.with_name(dst.stem + ".tmp.wav")       # write then rename: a crash never leaves a 'present' half file
    voice.speak(text, tmp, vid, speed, lang, sr, engine="kokoro")
    tmp.replace(dst)


def make_bouncy(dst: Path, ref: Path, python: str, cand_dir: Path, timeout: float = 900) -> Path:
    """Three high-energy Chatterbox reads of BOUNCY_TEXT cloned from `ref`; keeps the one with the most pitch movement."""
    cand_dir.mkdir(parents=True, exist_ok=True)
    scored = []
    for seed in SEEDS:
        out = cand_dir / f"announcer_bouncy_s{seed}.wav"
        voice.chatterbox_job({"text": voice.respell(BOUNCY_TEXT, "chatterbox"), "outs": [str(out)],
                              "ref": str(ref.resolve()), **BOUNCY, "seed": seed}, python, timeout)
        sd, hz, secs = pitch_stats(out)
        print(f"  candidate seed {seed}: pitch SD {sd} st, median {hz} Hz, {secs} s  ({out})")
        scored.append((sd, seed, out))
    sd, seed, best = max(scored)
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    shutil.copyfile(best, tmp)
    tmp.replace(dst)
    print(f"  kept seed {seed} (highest pitch SD, {sd} st); the other candidates stay in {cand_dir} to swap by ear")
    return best


def run(brand: Path, chatterbox_python: str | None, lang: str = "b", sr: int = 24000, force: bool = False,
        cand_dir: Path | None = None, cpu: bool = False) -> int:
    todo = [n for n in (*KOKORO_REFS, "announcer_bouncy_ref.wav") if force or not (brand / n).exists()]
    if todo and not cpu:
        ok, why = cuda_here()
        if not ok:                                    # before any model loads: nothing is made here
            print(REFUSED.format(what=", ".join(todo), where="this Python", why=why))
            return 4
        print(f"GPU: {why}")
    brand.mkdir(parents=True, exist_ok=True)
    status = 0
    for name, (text, vid, speed) in KOKORO_REFS.items():
        dst = brand / name
        if dst.exists() and not force:
            _show("ok  ", dst, " (kept; --force remakes it)")
            continue
        try:
            import kokoro  # noqa: F401
        except ImportError:
            print(f"MISS {name}: kokoro isn't installed in this Python (run scripts/setup_pc.ps1, then use .venv's python)")
            status = 2
            continue
        try:
            make_kokoro(dst, text, vid, speed, lang, sr)
            _show("made", dst, f" (Kokoro {vid})")
        except Exception as e:
            print(f"FAIL {name}: Kokoro {type(e).__name__}: {e}")
            status = status or 1
    dst, ref = brand / "announcer_bouncy_ref.wav", brand / "announcer_ref.wav"
    if dst.exists() and not force:
        _show("ok  ", dst, " (kept; --force remakes it)")
    elif not ref.exists():
        print(f"MISS {dst.name}: needs {ref.name} first")
        status = status or 2
    elif not chatterbox_python or not Path(chatterbox_python).exists():
        print(f"MISS {dst.name}: Chatterbox env {chatterbox_python} not found (scripts/setup_pc.ps1); lines use Kokoro until it exists")
        status = 3
    elif not cpu and not (cb := cuda_in(chatterbox_python))[0]:
        print(REFUSED.format(what=dst.name, where=f"the Chatterbox env {chatterbox_python}", why=cb[1]))
        status = 4
    else:
        print(f"making {dst.name} with Chatterbox from {ref.name} (exaggeration 1.0, cfg 0.35, temperature 0.9, seeds {SEEDS})")
        try:
            make_bouncy(dst, ref, chatterbox_python, cand_dir or ROOT / "out" / "voice_refs")
            _show("made", dst, " (Chatterbox)")
        except Exception as e:
            print(f"FAIL {dst.name}: {type(e).__name__}: {e}")
            status = status or 1
    voice.shutdown()
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.pc.yaml" if os.name == "nt" else "config.yaml")
    ap.add_argument("--force", action="store_true", help="remake all three even if they exist")
    ap.add_argument("--cpu", action="store_true", help="allow a run without a CUDA GPU (refused otherwise)")
    a = ap.parse_args(argv)
    from cqf import config
    vcfg = config.load(a.config)["voice"]
    cb = vcfg.get("chatterbox_python")
    cb = str(ROOT / cb) if cb and not Path(cb).is_absolute() else cb
    return run(BRAND, cb, vcfg.get("lang_code", "b"), int(vcfg.get("sample_rate", 24000)), a.force, cpu=a.cpu)


if __name__ == "__main__":
    sys.exit(main())
