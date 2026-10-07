"""B-roll farm: one worker per live ComfyUI server (mama's 3090s, optionally the PC 5090).
Each shot = N candidate stills (Wan 2.2 t2v, 1 frame) → vision QA → i2v clip from the best."""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import comfy, compliance
from .llm import find_claude


@dataclass
class Shot:
    key: str                 # episode/scene id, used for file names and caching
    prompt: str
    motion: str
    out_dir: Path
    result: Path | None = None
    error: str | None = None
    log: list = field(default_factory=list)


def servers(cfg: dict, use_5090: bool = False) -> list[comfy.Comfy]:
    live, f = [], cfg["farm"]
    for mc in f["machines"]:
        if mc.get("role") == "hero" and not (use_5090 or mc.get("enabled")):
            continue
        for i in range(mc["gpus"]):
            c = comfy.Comfy(f"http://{mc['host']}:{mc['first_port'] + i}", f.get("job_timeout_s", 1800))
            if not c.alive(f.get("probe_timeout_s", 5)):
                continue
            missing = c.has_models(comfy.model_files(cfg["models"]["wan22"]))
            if missing:
                print(f"  {c.url}: skipping, missing {missing} (re-run mama/fetch_models.sh)")
                continue
            live.append(c)
    return live


def qa_still(cfg: dict, image: Path, shot: Shot) -> tuple[bool, str]:
    """Ask Claude to look at the still. Fails closed only for banned imagery; if Claude is
    unavailable the still passes as UNVERIFIED (render now, verify later — as shorts-factory)."""
    if cfg["broll"].get("qa") != "claude_vision":
        return True, "qa off"
    exe = find_claude()
    if not exe:
        return True, "UNVERIFIED (no claude)"
    ask = (f"Read the image at {image}. It is B-roll for a UK weight-health ad. Brief: {shot.prompt}\n"
           "Reject it if it shows ANY of: syringe, needle, pen injector, pills, tablets, medicine packaging, vials, weighing scales, "
           "measuring tape, a body close-up or bare midriff, before/after framing, anyone in medical clothing, legible text or logos, "
           "distorted hands/faces, or if it plainly misses the brief. Reply with JSON only: {\"ok\": true|false, \"reason\": \"...\"}")
    try:
        p = subprocess.run([exe, "-p", "--allowedTools", "Read", "--output-format", "text"], input=ask, capture_output=True,
                           text=True, encoding="utf-8", timeout=300)
        from .llm import extract_json
        v = extract_json(p.stdout)
        return bool(v.get("ok")), v.get("reason", "")
    except Exception as e:  # noqa: BLE001 — QA must never crash the farm
        return True, f"UNVERIFIED ({e.__class__.__name__})"


def _work(cfg: dict, srv: comfy.Comfy, q: "queue.Queue[Shot]", lock: threading.Lock):
    m, neg = cfg["models"]["wan22"], cfg["_preset"]["broll"]["negative"]
    look = cfg["_preset"]["broll"]["look"]
    while True:
        try:
            shot = q.get_nowait()
        except queue.Empty:
            return
        try:
            final = shot.out_dir / f"{shot.key}.mp4"
            if final.exists():
                shot.result = final
                continue
            prompt = f"{shot.prompt}. {look}"
            bad = compliance.lint_prompt(shot.prompt, shot.key)
            if bad:
                raise ValueError("; ".join(map(str, bad)))
            best = None
            for attempt in range(cfg["broll"].get("max_rerolls", 2) + 1):
                for c in range(cfg["broll"].get("candidates", 2)):
                    still = srv.run(comfy.still_graph(m, prompt, neg, prefix=f"cq_{shot.key}"), shot.out_dir / "stills", f"{shot.key}_a{attempt}c{c}")[0]
                    ok, why = qa_still(cfg, still, shot)
                    shot.log.append(f"{still.name}: {'ok' if ok else 'REJECT'} {why}")
                    if ok:
                        best = still
                        break
                if best:
                    break
            if not best:
                raise RuntimeError("no still passed QA: " + " | ".join(shot.log[-3:]))
            name = srv.upload(best)
            clip = srv.run(comfy.clip_graph(m, name, f"{shot.motion}. {prompt}", neg, prefix=f"cq_{shot.key}"), shot.out_dir, shot.key)[0]
            if clip != final:
                shutil.move(clip, final)
            shot.result = final
        except Exception as e:  # noqa: BLE001
            shot.error = f"{srv.url}: {e}"
        finally:
            with lock:
                print(f"  [{srv.url.rsplit(':', 1)[-1]}] {shot.key}: {'ok' if shot.result else 'FAILED ' + str(shot.error)[:160]}")
            q.task_done()


def render_shots(cfg: dict, shots: list[Shot], use_5090: bool = False) -> list[Shot]:
    todo = [s for s in shots if not (s.out_dir / f"{s.key}.mp4").exists()]
    for s in shots:
        if s not in todo:
            s.result = s.out_dir / f"{s.key}.mp4"
    if not todo:
        return shots
    live = servers(cfg, use_5090)
    if not live:
        raise RuntimeError("no ComfyUI servers up — on mama run: bash mama/cq_phase.sh render")
    print(f"b-roll: {len(todo)} shots on {len(live)} GPU servers")
    q: "queue.Queue[Shot]" = queue.Queue()
    for s in todo:
        s.out_dir.mkdir(parents=True, exist_ok=True)
        q.put(s)
    lock = threading.Lock()
    threads = [threading.Thread(target=_work, args=(cfg, srv, q, lock), daemon=True) for srv in live]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    (todo[0].out_dir / "broll_log.json").write_text(json.dumps({s.key: {"ok": bool(s.result), "error": s.error, "log": s.log} for s in todo}, indent=2))
    return shots
