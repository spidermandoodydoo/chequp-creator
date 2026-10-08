"""Chatterbox TTS worker (Resemble AI, MIT). Runs in its OWN Python env, because chatterbox-tts pins
torch==2.6.0 (no RTX 5090 support: on the PC install it with --no-deps on torch>=2.7 cu128).
cqf/voice.py starts this once per run and talks JSON lines over stdin/stdout:

  in : {"text", "outs": [take paths], "ref", "exaggeration", "cfg_weight", "temperature", "seed"}
  out: {"ok": true, "outs": [...], "sr": 24000, "secs": [...]}   or   {"ok": false, "error": "..."}

Only the original 0.5B English model honours exaggeration / cfg_weight (Turbo and Nano ignore them)."""
from __future__ import annotations

import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
# utf-8 both ways, set here: voice.py starts this with -I, which ignores PYTHONUTF8 / PYTHONIOENCODING.
_proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")   # protocol channel = the real stdout
try:
    os.dup2(2, 1)                                   # everything chatterbox/tqdm prints goes to stderr
except OSError:                                     # no usable stderr (detached parent): drop library output
    os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
if os.name == "nt":                                 # native code writing to the Win32 stdout handle, not fd 1
    try:
        import ctypes
        import msvcrt
        ctypes.windll.kernel32.SetStdHandle(-11, msvcrt.get_osfhandle(1))   # STD_OUTPUT_HANDLE -> stderr
    except Exception:  # noqa: BLE001
        pass
sys.stdout = sys.stderr if sys.stderr is not None else open(os.devnull, "w")
if sys.stdin is not None:
    sys.stdin.reconfigure(encoding="utf-8")


def main():
    try:   # a load failure (missing module, CUDA out of memory) is reported as the hello, so REPORT.md names it
        import numpy as np
        import soundfile as sf
        import torch
        from chatterbox.tts import ChatterboxTTS

        dev = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        if dev == "cpu":
            torch.set_num_threads(max(1, os.cpu_count() or 1))
        t0 = time.time()
        model = ChatterboxTTS.from_pretrained(device=dev)
    except Exception as e:
        _proto.write(json.dumps({"ready": False, "error": f"{type(e).__name__}: {e}"[:500]}) + "\n")
        raise
    print(f"chatterbox: loaded on {dev} in {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    for mod in ("chatterbox.models.t3.t3", "chatterbox.models.s3gen.flow_matching"):   # per-take progress bars:
        if hasattr(sys.modules.get(mod), "tqdm"):                                     # kilobytes of \r per take in
            sys.modules[mod].tqdm = lambda it=None, *a, **k: it                      # the PC log (download bars stay)
    _proto.write(json.dumps({"ready": True, "device": dev, "sr": model.sr}) + "\n")
    cond = None
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            job = json.loads(raw)
            ex = float(job.get("exaggeration", 0.5))
            key = (job["ref"], os.stat(job["ref"]).st_mtime_ns, ex)
            if cond != key:                      # conditioning is cached per (reference file, exaggeration)
                model.prepare_conditionals(job["ref"], exaggeration=ex)
                cond = key
            secs = []
            for n, out in enumerate(job["outs"]):
                torch.manual_seed(int(job.get("seed", 1234)) + n)
                wav = model.generate(job["text"], exaggeration=ex, cfg_weight=float(job.get("cfg_weight", 0.5)),
                                     temperature=float(job.get("temperature", 0.8)))
                a = wav.squeeze(0).detach().cpu().numpy().astype(np.float32)
                sf.write(out, a, model.sr)
                secs.append(round(len(a) / model.sr, 3))
            _proto.write(json.dumps({"ok": True, "outs": job["outs"], "sr": model.sr, "secs": secs}) + "\n")
        except Exception as e:  # report and keep serving
            _proto.write(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}) + "\n")


if __name__ == "__main__":
    main()
