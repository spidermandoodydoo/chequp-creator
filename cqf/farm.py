"""B-roll farm. Quality path from reference/broll-plan.md:

  phase 1 (all shots): Qwen-Image-2512 still at native size, 4 seeds, each upscaled 2x by
           SeedVR2 inside the same graph -> Claude vision scores every master -> best passing wins
  phase 2 (clip shots only): Wan 2.2 i2v (30 steps, cfg 3.5) -> SeedVR2 1.5x -> FILM 2x -> 30 fps

Phases run in that order so one GPU isn't reloading 20-50 GB of weights per shot.
AI plates never contain a recognisable face: every shot carries a people tag.
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import comfy, compliance
from .llm import extract_json, find_claude

PEOPLE_TAGS = {"none", "hands", "hands_pair", "back_view", "distant"}
DEFAULT_STILL_SIZES = {"9x16": [928, 1664], "16x9": [1664, 928]}
DEFAULT_CLIP_SIZES = {"9x16": [720, 1280], "16x9": [1280, 720]}


@dataclass
class Shot:
    key: str                  # cache key: <board>_<prompt hash>_<aspect>
    prompt: str               # still prompt (rewritten, positive-only)
    motion: str               # i2v motion prompt (motion + camera only)
    out_dir: Path
    aspect: str = "9x16"      # 9x16 | 16x9 (4:5 and 1:1 are crops of the 9:16 plate)
    people: str | None = None
    mode: str = "still"       # still (pushed in stage.js) | clip
    still: Path | None = None
    result: Path | None = None
    score: float = 0
    unverified: bool = False
    error: str | None = None
    log: list = field(default_factory=list)


def _sizes(cfg: dict, which: str) -> dict:
    m = cfg.get("models", {})
    if which == "still":
        return m.get("qwen_image", {}).get("sizes", DEFAULT_STILL_SIZES)
    return m.get("wan22", {}).get("clip", {}).get("sizes", DEFAULT_CLIP_SIZES)


def _graph(cfg: dict, which: str) -> str:
    m = cfg.get("models", {})
    name = (m.get("qwen_image", {}).get("graph") if which == "still" else m.get("wan22", {}).get("clip", {}).get("graph"))
    return comfy.load_graph(Path(name).name if name else ("qwen_still.json" if which == "still" else "wan_i2v_clip.json"))


def servers(cfg: dict, use_5090: bool = False, need_clips: bool = False) -> list[comfy.Comfy]:
    """Live ComfyUI servers that pass the version/node pre-flight and have every model file."""
    live, f = [], cfg["farm"]
    need = set(comfy.graph_models(_override(comfy.fill(_graph(cfg, "still"), seed=1, width=16, height=16), cfg)))
    if need_clips:
        need |= set(comfy.graph_models(_override(comfy.fill(_graph(cfg, "clip"), seed=1, width=16, height=16, frames=81), cfg)))
    for mc in f.get("machines", []):
        if mc.get("enabled") is False and not (use_5090 and mc.get("role") == "hero"):
            continue                      # mama is off for CheqUp (Dan: "not mama, mama is getting hot")
        if mc.get("role") == "hero" and not (use_5090 or mc.get("enabled")):
            continue
        for i in range(mc["gpus"]):
            c = comfy.Comfy(f"http://{mc['host']}:{mc['first_port'] + i}", f.get("job_timeout_s", 1800))
            if not c.alive(f.get("probe_timeout_s", 5)):
                continue
            problems = c.preflight() + [f"missing model {m}" for m in c.has_models(sorted(need))]
            if problems:
                print(f"  {c.url}: skipping: {'; '.join(problems[:4])} (see scripts/fetch_models_pc.ps1)")
                continue
            live.append(c)
    return live


QA_PROMPT = """Read the image at {image}. It is an AI-generated background plate for a UK weight-health ad aimed at women 35-64.
Brief: {brief}. People tag: {tag}. Aspect: {aspect}.
Reply with JSON only: {{"ok": true|false, "score": 1-10, "fails": ["..."], "notes": "..."}}
ok=false if ANY of:
1. A recognisable human face of any size (eyes, nose and mouth visible), whatever the tag; any face or head when the tag is none, hands or hands_pair; more people than the tag allows.
2. Anyone who looks under about 25, noticeably thin or underweight, or youth cues (school uniform, teen styling).
3. Framing that centres the belly, waist, thighs or body shape; bare midriff; a mirror or anyone checking their appearance.
4. A syringe, needle, injection pen or anything pen-shaped (marker pens too), pills, tablets, capsules, blister packs, vials, medicine boxes.
5. Scales of any kind, tape measures, scrubs, lab coat, stethoscope, lanyard, a clinic or hospital.
6. Legible text, numbers, logos or brand marks; a lit phone or screen showing content.
7. Extra, missing or fused fingers; hands that bend wrongly.
8. Card zone not plain: for 9x16 anything but plain background between 10% and 45% of the height from the top; for 16x9 anything but plain background in the left half.
9. Soft focus with no sharp point of focus; haze or washed-out low contrast; speckle, halftone or crosshatch texture; mushy or melted food.
10. It misses the brief.
Score 1-10 for: sharp fine detail; warm directional natural light with true colour and clean whites (editorial lifestyle photography, not graded); believable hands and food; brief match; clean card zone."""


def qa(cfg: dict, image: Path, shot: Shot) -> tuple[bool, float, str, bool]:
    """(ok, score, note, unverified). Without Claude the plate is UNVERIFIED: it can render as a
    draft, but the board goes to HOLD until a human has looked at it."""
    if cfg["broll"].get("qa") != "claude_vision":
        return True, 0, "qa off", True
    exe = find_claude()
    if not exe:
        return True, 0, "UNVERIFIED (no claude CLI)", True
    ask = QA_PROMPT.format(image=image, brief=shot.prompt[:400], tag=shot.people, aspect=shot.aspect)
    try:
        p = subprocess.run([exe, "-p", "--allowedTools", "Read", "--output-format", "text"], input=ask, capture_output=True,
                           text=True, encoding="utf-8", timeout=300)
        v = extract_json(p.stdout)
        return bool(v.get("ok")), float(v.get("score") or 0), "; ".join(v.get("fails") or []) or v.get("notes", ""), False
    except Exception as e:  # noqa: BLE001 — QA must never crash the farm
        return True, 0, f"UNVERIFIED ({e.__class__.__name__})", True


def _override(graph: dict, cfg: dict) -> dict:
    """Swap the SeedVR2 weights per config (fp8 on PCs with < 64 GB RAM)."""
    unet = cfg.get("models", {}).get("seedvr2", {}).get("unet")
    if unet:
        for n in graph.values():
            if n.get("class_type") == "UNETLoader" and str(n["inputs"].get("unet_name", "")).startswith("seedvr2_"):
                n["inputs"]["unet_name"] = unet
    return graph


def _seed(key: str, i: int) -> int:
    return (zlib.crc32(key.encode()) * 7919 + i * 104729) % (2**31)


def _still_job(cfg: dict, srv: comfy.Comfy, shot: Shot):
    final = shot.out_dir / f"{shot.key}.png"
    if final.exists():
        shot.still = final
        return
    look = cfg["_preset"]["broll"]["look_positive"]
    neg = cfg["_preset"]["broll"]["negative"] + ", " + cfg["_preset"]["broll"]["people_add_on"][shot.people]
    w, h = _sizes(cfg, "still")[shot.aspect]
    graph_src = _graph(cfg, "still")
    n, rerolls = cfg["broll"].get("candidates", 4), cfg["broll"].get("max_rerolls", 1)
    best = None
    for attempt in range(rerolls + 1):
        for c in range(n):
            seed = _seed(shot.key, attempt * n + c)
            g = _override(comfy.fill(graph_src, prompt=f"{shot.prompt} {look}", negative=neg, seed=seed, width=w, height=h,
                                     prefix=f"cq_{shot.key}"), cfg)
            files = srv.run(g, shot.out_dir / "candidates", f"{shot.key}_s{seed}")
            master = next((f for f in files if "_n20" in f.name), files[-1])
            ok, score, note, unv = qa(cfg, master, shot)
            shot.log.append({"file": master.name, "seed": seed, "ok": ok, "score": score, "note": note})
            if ok and (best is None or score > best[1]):
                best = (master, score, unv)
        if best:
            break
    if not best:
        raise RuntimeError("no still passed the vision check: " + " | ".join(str(x["note"]) for x in shot.log[-n:]))
    shutil.copy2(best[0], final)
    shot.still, shot.score, shot.unverified = final, best[1], best[2]


def _frames_ok(cfg: dict, clip: Path, shot: Shot) -> tuple[bool, str]:
    notes = []
    for frac in (0.0, 0.5, 0.98):
        img = clip.with_name(f"{clip.stem}_qa{int(frac * 100)}.png")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-sseof" if frac > 0.9 else "-ss",
                        "-0.2" if frac > 0.9 else str(frac * 5), "-i", str(clip), "-frames:v", "1", str(img)], check=False)
        if img.exists():
            ok, _, note, unv = qa(cfg, img, shot)
            if not ok:
                return False, f"frame {int(frac * 100)}%: {note}"
            notes.append(note)
            shot.unverified |= unv
    return True, "; ".join(notes)


def _clip_job(cfg: dict, srv: comfy.Comfy, shot: Shot):
    final = shot.out_dir / f"{shot.key}.mp4"
    if final.exists():
        shot.result = final
        return
    b = cfg["_preset"]["broll"]
    w, h = _sizes(cfg, "clip")[shot.aspect]
    frames = cfg.get("models", {}).get("wan22", {}).get("clip", {}).get("frames", 81)
    g = comfy.fill(_graph(cfg, "clip"), image=srv.upload(shot.still), prompt=shot.motion,
                   negative=b["negative_wan"] + "，" + b["negative_i2v_extra"], seed=_seed(shot.key, 999),
                   width=w, height=h, frames=frames, prefix=f"cq_{shot.key}")
    g = _override(g, cfg)
    clip = srv.run(g, shot.out_dir / "candidates", f"{shot.key}_clip", timeout=cfg["farm"].get("clip_timeout_s", 10800))[0]
    ok, note = _frames_ok(cfg, clip, shot)
    shot.log.append({"file": clip.name, "clip_ok": ok, "note": note})
    if not ok:
        raise RuntimeError(f"clip failed the vision check ({note}); the still will be used instead")
    shutil.move(clip, final)
    shot.result = final


def _drain(cfg: dict, live: list, items: list[Shot], job, label: str):
    q: "queue.Queue[Shot]" = queue.Queue()
    for s in items:
        q.put(s)
    lock = threading.Lock()

    def work(srv):
        while True:
            try:
                shot = q.get_nowait()
            except queue.Empty:
                return
            try:
                job(cfg, srv, shot)
                msg = f"ok (score {shot.score:g}{', UNVERIFIED' if shot.unverified else ''})"
            except Exception as e:  # noqa: BLE001
                shot.error = f"{label}: {e}"
                msg = "FAILED " + str(e)[:180]
            with lock:
                print(f"  [{srv.url.rsplit(':', 1)[-1]}] {label} {shot.key}: {msg}")
            q.task_done()
    threads = [threading.Thread(target=work, args=(srv,), daemon=True) for srv in live]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def render_shots(cfg: dict, shots: list[Shot], use_5090: bool = False) -> list[Shot]:
    bad = [s.key for s in shots if s.people not in PEOPLE_TAGS]
    if bad:
        raise ValueError(f"b-roll shots without a valid people tag {sorted(PEOPLE_TAGS)}: {bad}")
    for s in shots:
        hits = compliance.lint_prompt(s.prompt, s.key)
        if hits:
            raise ValueError("; ".join(map(str, hits)))
        s.out_dir.mkdir(parents=True, exist_ok=True)
        if (s.out_dir / f"{s.key}.png").exists():
            s.still = s.out_dir / f"{s.key}.png"
        if s.mode == "clip" and (s.out_dir / f"{s.key}.mp4").exists():
            s.result = s.out_dir / f"{s.key}.mp4"
    need_still = [s for s in shots if not s.still]
    need_clip = [s for s in shots if s.mode == "clip" and not s.result]
    if need_still or need_clip:
        live = servers(cfg, use_5090, need_clips=bool(need_clip))
        if not live:
            raise RuntimeError("no ComfyUI server passed the pre-flight (version, nodes, models)")
        if need_still:
            print(f"b-roll stills: {len(need_still)} shots x {cfg['broll'].get('candidates', 4)} candidates on {len(live)} GPU server(s)")
            _drain(cfg, live, need_still, _still_job, "still")
        need_clip = [s for s in need_clip if s.still]
        if need_clip:
            print(f"b-roll clips: {len(need_clip)} shots (Wan 2.2 30 steps + SeedVR2 + FILM; slow)")
            _drain(cfg, live, need_clip, _clip_job, "clip")
    for s in shots:
        if not s.result and s.still:
            s.result = s.still            # still with a push in stage.js
    if shots:
        log = shots[0].out_dir / "broll_log.json"
        prev = json.loads(log.read_text()) if log.exists() else {}
        prev.update({s.key: {"ok": bool(s.result), "score": s.score, "unverified": s.unverified, "error": s.error, "log": s.log} for s in shots})
        log.write_text(json.dumps(prev, indent=2))
    return shots
