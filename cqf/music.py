"""Music beds. Two backends (config music.backend):

  ace_step    ACE-Step 1.5 (ace_step_1.5_turbo_aio.safetensors) on CheqUp's own ComfyUI on the PC (port
              8288; cqf.gpu_gate waits for shorts-factory first): an instrumental bed per board look,
              cached in out/music/ by its inputs (ace_bed).
  procedural  numpy pad chords + a soft plucked arpeggio + a light pulse (bed): no GPU, no model,
              so the cloud VM and drafts are never silent. Also the fallback when ACE-Step can't run.

Either way a board can bring its own track with `"audio": {"music": "path.wav"}`."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import requests

SR = 48000

ACE_GRAPH = "ace_step_bed.json"
ACE_CKPT = "ace_step_1.5_turbo_aio.safetensors"     # Comfy-Org/ace_step_1.5_ComfyUI_files, models/checkpoints
ACE_INSTRUMENTAL = "[Instrumental]"                  # ACE-Step 1.5's own no-vocals lyric (its UI's Instrumental box)
ACE_MIN_SECONDS = 10      # ACE-Step 1.5 supports 10-600 s (acestep/constants.py DURATION_MIN; its LM raises shorter targets
                          # to 10). ComfyUI doesn't clamp, so a 6 s bumper would ask for 7 s; render.mjs trims the bed to the board.
# Captions in ACE-Step's style (genre, instruments, mood, tempo). warm = the design-system boards,
# bright = meta-live. bpm/keyscale also go to the LM planner as metadata.
ACE_STYLES = {
    "warm": {"tags": "warm acoustic, light piano, soft percussion, uplifting, gentle, british advert, instrumental, 90 bpm",
             "bpm": 90, "keyscale": "D major"},
    "bright": {"tags": "upbeat modern pop, plucked synth, light claps, optimistic, 100 bpm, instrumental",
               "bpm": 100, "keyscale": "G major"},
}
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


def _ace_ckpt(cfg: dict) -> str:
    return (cfg.get("models", {}).get("ace_step") or {}).get("checkpoint") or ACE_CKPT


def ace_graph(cfg: dict, mood: str = "warm", seconds: int = 10, seed: int = 7, prefix: str = "cq_music/ace") -> dict:
    """cqf/graphs/ace_step_bed.json filled for one bed. Whole seconds: the latent (25/s) and the LM's
    audio codes (5/s, duration rounded up) then cover exactly the same length."""
    from . import comfy
    st = ACE_STYLES.get(mood, ACE_STYLES["warm"])
    g = comfy.fill(comfy.load_graph(ACE_GRAPH), tags=st["tags"], lyrics=ACE_INSTRUMENTAL, seconds=int(seconds), seed=int(seed),
                   bpm=st["bpm"], keyscale=st["keyscale"], prefix=prefix)
    for n in g.values():
        if n["class_type"] == "CheckpointLoaderSimple":
            n["inputs"]["ckpt_name"] = _ace_ckpt(cfg)
    return g


def ace_server(cfg: dict, graph: dict):
    """(first live ComfyUI in farm.machines with the graph's nodes and checkpoint, None) or (None, why).
    Same machine rules as farm.servers() without --use-5090: enabled: false is skipped, and a hero
    only counts when enabled: true. No version gate: any ComfyUI with the ACE-Step 1.5 nodes
    (v0.12.0+) runs the graph; ace_bed() swaps language 'unknown' for 'en' on builds before v0.21.0."""
    from . import comfy, gpu_gate
    f = cfg.get("farm") or {}
    nodes = sorted({n["class_type"] for n in graph.values()})
    why = "no ComfyUI in farm.machines"
    for mc in f.get("machines", []):
        if mc.get("enabled") is False or (mc.get("role") == "hero" and mc.get("enabled") is not True):
            continue
        for i in range(mc.get("gpus", 1)):
            c = comfy.Comfy(f"http://{mc['host']}:{mc['first_port'] + i}", f.get("job_timeout_s", 1800), f.get("queue_timeout_s"))
            if gpu_gate.is_protected(cfg, c.url):      # shorts-factory's ComfyUI: never sent a job
                why = f"{c.url} is shorts-factory's ComfyUI (gpu_gate.yield_to), not used"
                continue
            if not c.alive(f.get("probe_timeout_s", 5)):
                why = f"{c.url} not responding"
                continue
            try:
                problems = c.preflight(nodes, min_version=None)
                gone = c.has_models(comfy.graph_models(graph))
                if gone:     # worded apart from farm's "missing model X" so pc_run never files it as a b-roll download
                    ckpts = comfy.combo_options(c.node_info("CheckpointLoaderSimple").get("input", {}).get("required", {}).get("ckpt_name"))
                    near = [n for n in ckpts if "ace" in n.lower()]
                    problems.append(f"checkpoint not in ComfyUI's checkpoints list: {', '.join(gone)}"
                                    + (f" (it lists: {', '.join(near[:4])})" if near else " (no ACE-Step checkpoint listed)"))
            except (requests.RequestException, ValueError) as e:
                problems = [str(e)]
            if not problems:
                return c, None
            why = f"{c.url}: {'; '.join(problems[:3])}"
    return None, why


def ace_bed(cfg: dict, seconds: float, out: Path, mood: str = "warm", seed: int = 7) -> Path | None:
    """ACE-Step 1.5 instrumental bed -> 48 kHz stereo 16-bit wav at `out` (sfx.silence_music edits it
    in place). Cached in <out_dir>/music/ by (style, seconds, seed, checkpoint), so boards with the
    same look and length share one bed. None (after a printed warning) when it can't be made: the
    caller then uses the procedural bed(). At least ACE_MIN_SECONDS long."""
    from . import comfy, gpu_gate
    from .config import path
    secs = max(ACE_MIN_SECONDS, math.ceil(seconds))
    style = ACE_STYLES.get(mood, ACE_STYLES["warm"])
    key = hashlib.sha1(json.dumps([style, secs, int(seed), _ace_ckpt(cfg)], sort_keys=True).encode()).hexdigest()[:10]
    cache = path(cfg, "out_dir") / "music" / f"ace_{mood}_{secs}s_{key}.wav"
    if not cache.exists():
        g = ace_graph(cfg, mood, secs, seed, prefix=f"cq_music/ace_{key}")
        srv, why = ace_server(cfg, g)
        if not srv:
            print(f"  music: ACE-Step unavailable ({why}); procedural bed instead")
            return None
        try:
            enc = srv.node_info("TextEncodeAceStepAudio1.5").get("input", {}).get("required", {})
            langs = comfy.combo_options(enc.get("language"))
            if langs and "unknown" not in langs:          # ComfyUI < 0.21: no 'unknown' (= instrumental) language
                for n in g.values():
                    if n["class_type"] == "TextEncodeAceStepAudio1.5":
                        n["inputs"]["language"] = "en"
            gpu_gate.wait_for_gpu(cfg, kind="ace", why=f"the ACE-Step {secs}s music bed", url=srv.url)   # shorts-factory first
            print(f"  music: ACE-Step 1.5 {secs}s {mood} bed on {srv.url}")
            raw = srv.run(g, cache.parent / "raw", f"ace_{key}")[0]
            tmp = cache.with_name(cache.stem + ".tmp.wav")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ar", str(SR), "-ac", "2",
                            "-c:a", "pcm_s16le", str(tmp)], check=True, capture_output=True)
            tmp.replace(cache)
        except (requests.RequestException, RuntimeError, OSError, subprocess.CalledProcessError) as e:
            print(f"  music: ACE-Step failed ({type(e).__name__}: {str(e)[:200]}); procedural bed instead")
            return None
    shutil.copyfile(cache, out)
    return out
