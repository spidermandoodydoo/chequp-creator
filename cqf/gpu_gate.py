"""GPU gate: CheqUp yields Dan's 5090 to shorts-factory.

CheqUp has its own ComfyUI on the PC (scripts/install_comfy_cheq_pc.ps1, port 8288) next to shorts-factory's
(port 8188), on the same GPU. Before each GPU job on this PC (a b-roll still or clip prompt, an ACE-Step bed,
each board's Chatterbox lines) wait_for_gpu() waits while

  - any `gpu_gate.yield_to` ComfyUI has a job running or queued (GET /queue: read-only), or is listening but
    didn't answer within 5 s (stalled: busy; refused = not running = idle), or had work in the last quiet_s, or
  - nvidia-smi reports less free VRAM than the job needs (need_gb per job kind). What CheqUp's own ComfyUI
    holds (torch's reserved memory in its /system_stats) counts as free: it reuses or unloads that itself.

The first time a wait finds the GPU busy, CheqUp gives back what it holds itself (POST /free to CheqUp's own
ComfyUI, never to a yield_to one, plus the registered releasers, e.g. the Chatterbox worker): the factory then
gets that memory, and CheqUp never waits on its own idle models. After max_wait_s it raises GpuBusy and gives up
for the rest of the process (later calls fail at once, so a run doesn't wait max_wait_s per job). The only
contact with a yield_to server is GET /queue (and GET /system_stats, while VRAM is short, to say how much an idle
one keeps loaded): no POST, no /free, no /interrupt, ever. No gpu_gate block, or
enabled: false (config.yaml, the cloud VM) = a no-op.

While a b-roll still or clip runs on CheqUp's ComfyUI, yield_check() keeps reading the factory's queue (every
preempt_poll_s, default 10 s): when shorts-factory starts a job, CheqUp cancels its OWN job (by prompt_id, on its
own ComfyUI), frees its own VRAM and retries after the gate (farm.py), so the two never share the card for long.
preempt: false turns that off.

  gpu_gate:
    enabled: true
    yield_to: [{url: "http://127.0.0.1:8188", name: shorts-factory}]   # plain URL strings work too
    need_free_vram_gb: 22          # default for a job kind not in need_gb
    need_gb: {still: 22, clip: 26, ace: 12, chatterbox: 6}
    poll_s: 30
    quiet_s: 60                    # after shorts-factory was seen with work, wait this long without any (it queues
                                   # its jobs one after another: an idle moment between two is not a free GPU). 0 = off
    log_every_s: 300
    max_wait_s: 21600
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from urllib.parse import urlparse

import requests

LOCAL = ("127.0.0.1", "localhost", "::1")
# shorts-factory's ComfyUI on the PC (and Dan's own on mama's GPU0): never sent a POST even by a config that has
# no gpu_gate block (config.mama.yaml --use-5090, a hand-edited config). protected() always includes it.
ALWAYS_PROTECTED = ("http://127.0.0.1:8188",)
DEFAULT_NEED = {"still": 22, "clip": 26, "ace": 12, "chatterbox": 6}
SETTLE_S = 3                  # after a release: ComfyUI frees models on its worker thread

_now = time.monotonic         # tests swap these for a fake clock
_sleep = time.sleep
_waited = [0.0]               # seconds spent waiting in this process (state.json gpu_wait_s)
_gave_up: list = [None]       # the GpuBusy message once the gate has given up (sticky for the process)
_said: set = set()            # one-time notes (unreachable server, no nvidia-smi)
_releasers: dict = {}         # name -> fn(): frees VRAM CheqUp itself holds; True if it freed something
_busy_at: list = [None]       # _now() when a yield_to ComfyUI was last seen with work (gate or yield_check): quiet_s


class GpuBusy(RuntimeError):
    """The GPU stayed busy (shorts-factory's queue, or too little free VRAM) for gpu_gate.max_wait_s."""


class Yielded(RuntimeError):
    """CheqUp cancelled its own running job because a yield_to ComfyUI started work (the caller retries after the gate)."""


def settings(cfg: dict | None) -> dict | None:
    g = (cfg or {}).get("gpu_gate") or {}
    return g if g.get("enabled") else None


def _norm(url: str) -> str:
    u = urlparse(str(url).strip() if "://" in str(url) else "http://" + str(url).strip())
    host = (u.hostname or "").lower()
    host = "127.0.0.1" if host in LOCAL else host
    scheme = u.scheme or "http"
    return f"{scheme}://{host}:{u.port or (443 if scheme == 'https' else 80)}"


def yield_to(cfg: dict | None) -> list[dict]:
    """[{url, name}] of the ComfyUIs CheqUp yields to (from the config even when the gate is disabled)."""
    out = []
    for e in ((cfg or {}).get("gpu_gate") or {}).get("yield_to") or []:
        if isinstance(e, dict) and e.get("url"):
            out.append({"url": str(e["url"]).rstrip("/"), "name": str(e.get("name") or e["url"])})
        elif isinstance(e, str) and e.strip():
            out.append({"url": e.strip().rstrip("/"), "name": "ComfyUI " + e.strip().split("://")[-1].rstrip("/")})
    return out


def protected(cfg: dict | None) -> set[str]:
    """Normalised URLs CheqUp must never POST to (submit, upload, cancel, /free): every yield_to server, plus
    ALWAYS_PROTECTED (127.0.0.1:8188) whatever the config says."""
    return {_norm(y["url"]) for y in yield_to(cfg)} | {_norm(u) for u in ALWAYS_PROTECTED}


def is_protected(cfg: dict | None, url: str) -> bool:
    return _norm(url) in protected(cfg)


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in LOCAL


def cheq_url(cfg: dict | None) -> str | None:
    """CheqUp's own ComfyUI on this PC (comfy_cheq.url, or 127.0.0.1:comfy_cheq.port)."""
    c = (cfg or {}).get("comfy_cheq") or {}
    if c.get("url"):
        return str(c["url"]).rstrip("/")
    return f"http://127.0.0.1:{int(c['port'])}" if c.get("port") else None


def need_for(cfg: dict | None, kind: str | None = None) -> float:
    g = (cfg or {}).get("gpu_gate") or {}
    per = {**DEFAULT_NEED, **(g.get("need_gb") or {})}
    if kind and kind in per:
        return float(per[kind])
    return float(g.get("need_free_vram_gb", DEFAULT_NEED["still"]))


def _once(msg: str):
    if msg not in _said:
        _said.add(msg)
        print(f"  gpu_gate: {msg}")


QUEUE_TIMEOUT_S = 5.0
SLOW = "slow"                 # queue_state: listening but no reply in time (alive but stalled)


def queue_state(url: str, timeout: float | None = None) -> tuple[int, int] | str | None:
    """(running, pending) from GET <url>/queue (ComfyUI's queue_running / queue_pending lists). None = nothing
    answers there (connection refused, an error status, not a ComfyUI queue): not running, so idle. SLOW = no reply
    within `timeout` s although it took the request (or, on this PC, is listening but not accepting): a stalled
    ComfyUI is busy, not idle (wait_for_gpu waits).
    Read-only."""
    try:
        r = requests.get(url.rstrip("/") + "/queue", timeout=timeout or QUEUE_TIMEOUT_S)
        r.raise_for_status()
        q = r.json()
        if not isinstance(q, dict) or not ({"queue_running", "queue_pending"} & set(q)):
            return None
        return len(q.get("queue_running") or []), len(q.get("queue_pending") or [])
    except requests.Timeout as e:   # read: took the request, no reply; connect on this PC: listening but not accepting
        return SLOW if isinstance(e, requests.ReadTimeout) or is_local(url) else None
    except (requests.RequestException, ValueError, AttributeError, TypeError):
        return None


def _nvidia_smi() -> str | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        for p in (r"C:\Windows\System32\nvidia-smi.exe", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if os.path.isfile(p):
                return p
    return exe


def free_vram_gb(index: int = 0) -> float | None:
    """Free VRAM on GPU `index` per nvidia-smi, in GB (GiB). None = no nvidia-smi or no reading (check skipped)."""
    exe = _nvidia_smi()
    if not exe:
        _once("nvidia-smi not found: free VRAM not checked")
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True,
                           text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        _once(f"nvidia-smi gave no reading ({type(e).__name__}): free VRAM not checked")
        return None
    lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]    # one line per GPU, in nvidia-smi order
    try:
        if r.returncode != 0 or not 0 <= index < len(lines):
            raise ValueError
        return round(float(lines[index]) / 1024, 2)                          # MiB -> GiB; "[N/A]" -> ValueError
    except ValueError:
        got = lines[index] if 0 <= index < len(lines) else (r.stdout or r.stderr or "").strip()
        _once(f"nvidia-smi gave no reading for GPU {index} (exit {r.returncode}, {len(lines)} line(s): {got[:80]!r}): "
              "free VRAM not checked")
        return None


def status(cfg: dict | None, kind: str | None = None) -> dict:
    """A snapshot for cqf doctor and pc_run (never waits, never POSTs)."""
    g = settings(cfg)
    ys = [{**y, "state": queue_state(y["url"])} for y in yield_to(cfg)]
    return {"enabled": bool(g), "yield_to": ys, "free_gb": free_vram_gb(int((g or {}).get("gpu_index", 0))) if g else None,
            "need_gb": need_for(cfg, kind), "poll_s": (g or {}).get("poll_s", 30), "max_wait_s": (g or {}).get("max_wait_s", 21600),
            "cheq_url": cheq_url(cfg), "own_held_gb": own_held_gb(own_url(cfg)) if g else None}


def register_releaser(name: str, fn):
    """fn() frees GPU memory CheqUp itself holds (e.g. stops the Chatterbox worker); True if it freed something."""
    _releasers[name] = fn


def own_url(cfg: dict | None, url: str | None = None) -> str | None:
    """CheqUp's own ComfyUI: comfy_cheq, else the job's server when it is on this PC. Never a yield_to server."""
    for u in (cheq_url(cfg), url):
        if u and is_local(u) and not is_protected(cfg, u):
            return u.rstrip("/")
    return None


def own_held_gb(url: str | None) -> float:
    """VRAM the ComfyUI at `url` holds (torch's reserved memory, GET /system_stats: read-only). For CheqUp's own it
    counts as free for the next CheqUp job (it reuses or unloads that itself); for an idle yield_to one it only names
    who holds the VRAM in the wait message. 0 when it doesn't answer."""
    if not url:
        return 0.0
    try:
        devs = requests.get(url + "/system_stats", timeout=5).json().get("devices") or []
        return round(float(devs[0].get("torch_vram_total") or 0) / 2**30, 2) if devs else 0.0
    except (requests.RequestException, ValueError, AttributeError, TypeError, IndexError):
        return 0.0


def release_own(cfg: dict | None, url: str | None = None) -> list[str]:
    """Give back VRAM CheqUp holds: POST /free to CheqUp's own ComfyUI (never a yield_to one), then the releasers."""
    done = []
    own = own_url(cfg, url)
    if own and not is_protected(cfg, own):
        try:
            requests.post(own + "/free", json={"unload_models": True, "free_memory": True}, timeout=10).raise_for_status()
            done.append(f"CheqUp's ComfyUI ({own})")
        except requests.RequestException:
            pass
    for name, fn in list(_releasers.items()):
        try:
            if fn():
                done.append(name)
        except Exception:  # noqa: BLE001 — a releaser must never stop the gate
            pass
    return done


def yield_check(cfg: dict | None, url: str | None = None):
    """For comfy.Comfy.run (its yield_check attribute): fn() -> reason once a yield_to ComfyUI has a job, else None.
    At most one GET /queue per preempt_poll_s. None (no check) when the gate is off, preempt is false, there is
    nothing to yield to, or the job's server is not on this PC."""
    g = settings(cfg)
    ys = yield_to(cfg)
    if not g or not ys or g.get("preempt") is False or (url and not is_local(url)):
        return None
    every, last = float(g.get("preempt_poll_s", 10)), [None]

    def check():
        t = _now()
        if last[0] is not None and t - last[0] < every:
            return None
        last[0] = t
        for y in ys:
            st = queue_state(y["url"])
            if isinstance(st, tuple) and sum(st):      # a real job only: SLOW (stalled reply) never cancels ours
                _busy_at[0] = t
                return f"{y['name']} started work ({_jobs(sum(st))} queued, {st[0]} running)"
        return None
    return check


def waited_s() -> float:
    return round(_waited[0], 1)


def gave_up() -> str | None:
    return _gave_up[0]


def reset():
    """Forget waits, give-ups and notes (tests)."""
    _waited[0], _gave_up[0], _busy_at[0] = 0.0, None, None
    _said.clear()
    _releasers.clear()


def _mins(s: float) -> str:
    return f"{s / 60:.0f} min" if s >= 60 else f"{s:.0f} s"


def _jobs(n: int) -> str:
    return f"{n} job{'' if n == 1 else 's'}"


def wait_for_gpu(cfg: dict | None, need_gb: float | None = None, why: str = "", kind: str | None = None,
                 url: str | None = None) -> float:
    """Block until the GPU is free for one CheqUp job; returns the seconds waited. A no-op (0) when the gate is off,
    or when `url` (the server the job goes to) is not on this PC. Raises GpuBusy after max_wait_s."""
    g = settings(cfg)
    if not g or (url and not is_local(url)):
        return 0.0
    if _gave_up[0]:
        raise GpuBusy(f"gave up earlier in this run: {_gave_up[0]}")
    need = float(need_gb) if need_gb is not None else need_for(cfg, kind)
    poll, every, limit = float(g.get("poll_s", 30)), float(g.get("log_every_s", 300)), float(g.get("max_wait_s", 21600))
    what = why or kind or "a CheqUp GPU job"
    own, gpu = own_url(cfg, url), int(g.get("gpu_index", 0))
    t0 = _now()
    last_log, released = None, False
    while True:
        busy, idle = [], []
        for y in yield_to(cfg):
            st = queue_state(y["url"])
            if st is None:
                _once(f"{y['name']}'s ComfyUI ({y['url']}) not answering: treated as idle")
            elif st == SLOW:                   # alive but stalled (e.g. loading models): never read as idle
                busy.append(f"{y['name']}'s ComfyUI is listening but didn't answer GET /queue within "
                            f"{QUEUE_TIMEOUT_S:g} s (stalled: treated as busy)")
            elif sum(st):
                r, p = st
                busy.append(f"{y['name']} has {_jobs(r + p)} queued ({r} running)")
            else:
                idle.append(y)
        if busy:
            _busy_at[0] = _now()
        elif _busy_at[0] is not None and _now() - _busy_at[0] < float(g.get("quiet_s") or 0):
            # it had work moments ago: likely between two of its jobs (it keeps models loaded, queues the next)
            busy.append(f"{', '.join(y['name'] for y in idle) or 'the other ComfyUI'} had work {_mins(_now() - _busy_at[0])} "
                        f"ago (gpu_gate.quiet_s: waiting for {float(g.get('quiet_s') or 0):g} s without any)")
        free = None if busy else free_vram_gb(gpu)
        if free is not None:
            held = own_held_gb(own)            # fresh: ~0 after a release; if /free failed it is still CheqUp's own
            if free + held < need:             # name an idle yield_to ComfyUI that keeps models loaded (GET only)
                kept = [f"{y['name']}'s ComfyUI is idle but keeps {h:.1f} GB loaded" for y in idle
                        for h in [own_held_gb(y["url"])] if h >= 0.5]
                busy.append(f"{free:.1f} GB VRAM free" + (f" (+{held:.1f} GB held by CheqUp's ComfyUI)" if held else "")
                            + f", {what} needs {need:g} GB ({'; '.join(kept) or 'another program holds the rest'})")
        if busy and not released:
            released = True        # once per wait: give back what CheqUp itself holds (it is idle between its jobs)
            done = release_own(cfg, url)
            if done:
                print(f"  gpu_gate: {busy[0]}: freed {', '.join(done)}")
                _sleep(SETTLE_S)
                continue
        spent = _now() - t0
        if not busy:
            _waited[0] += spent
            if spent >= 1:
                print(f"  gpu_gate: GPU free after {_mins(spent)}; starting {what}")
            return spent
        reason = "; ".join(busy)
        if spent >= limit:
            _waited[0] += spent
            _gave_up[0] = (f"GPU still busy after {_mins(spent)} waiting to start {what}: {reason}. CheqUp gave up "
                           f"(gpu_gate.max_wait_s {limit:g}); nothing was sent to the other ComfyUI")
            print(f"  gpu_gate: {_gave_up[0]}")
            raise GpuBusy(_gave_up[0])
        if last_log is None or spent - last_log >= every:
            last_log = spent
            print(f"  GPU busy: {reason}; waiting ({_mins(spent)} so far)")
        _sleep(min(poll, max(0.01, limit - spent)))
