"""GPU gate (cqf/gpu_gate.py) and CheqUp's side-by-side ComfyUI: mock HTTP servers stand in for shorts-factory's
ComfyUI (busy/idle queue) and CheqUp's own, a fake nvidia-smi on PATH reports free VRAM, and a fake clock makes the
waiting instant. Checks waiting, logging cadence, the timeout error, that the factory mock never gets a POST, and
that the gate is a no-op when disabled. No GPU, no model. Run: python tests/test_gpu_gate.py"""
import contextlib
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from cqf import cli, comfy, config, farm, gpu_gate, music, pipeline, report, voice  # noqa: E402
import comfy_cheq_paths  # noqa: E402

GB = 2 ** 30


class Server:
    """A ComfyUI-ish mock that logs every request. queue: callable(n_queue_gets) -> (running, pending)."""

    def __init__(self, queue=lambda n: (0, 0), held_gb=0.0, version="0.39.2", argv=()):
        self.log, self.queue, self.held_gb, self.version, self.argv = [], queue, held_gb, version, list(argv)
        self.queue_gets = 0
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                me.log.append(("GET", self.path, None))
                if self.path == "/queue":
                    me.queue_gets += 1
                    r, p = me.queue(me.queue_gets)
                    return self._json({"queue_running": [[0, f"r{i}", {}, {}, []] for i in range(r)],
                                       "queue_pending": [[1, f"p{i}", {}, {}, []] for i in range(p)]})
                if self.path == "/system_stats":
                    return self._json({"system": {"comfyui_version": me.version, "argv": me.argv},
                                       "devices": [{"name": "cuda:0 fake", "vram_total": 32 * GB, "vram_free": 8 * GB,
                                                    "torch_vram_total": int(me.held_gb * GB), "torch_vram_free": 0}]})
                self._json({}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                me.log.append(("POST", self.path, body))
                self._json({})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def posts(self):
        return [x for x in self.log if x[0] == "POST"]

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@contextlib.contextmanager
def gate_env(free_mib=(32768,), with_smi=True):
    """Fresh gate state, a fake clock, and a fake nvidia-smi (free MiB per call: the last value repeats)."""
    tmp = Path(tempfile.mkdtemp())
    state = tmp / "smi.json"
    state.write_text(json.dumps({"seq": list(free_mib), "calls": 0}))
    if with_smi:
        smi = tmp / "nvidia-smi"
        smi.write_text(f"#!{sys.executable}\n"
                       "import json, os, sys\n"
                       "p = os.environ['FAKE_SMI_STATE']\n"
                       "s = json.load(open(p))\n"
                       "assert sys.argv[1:] == ['--query-gpu=memory.free', '--format=csv,noheader,nounits'], sys.argv\n"
                       "print(s['seq'][min(s['calls'], len(s['seq']) - 1)])\n"
                       "s['calls'] += 1\n"
                       "json.dump(s, open(p, 'w'))\n")
        smi.chmod(smi.stat().st_mode | stat.S_IEXEC)
    old_path, old_state = os.environ.get("PATH", ""), os.environ.get("FAKE_SMI_STATE")
    os.environ["PATH"] = str(tmp) if not with_smi else str(tmp) + os.pathsep + old_path
    if not with_smi:
        os.environ["PATH"] = str(tmp / "empty")
    os.environ["FAKE_SMI_STATE"] = str(state)
    clock = Clock()
    gpu_gate.reset()
    saved = gpu_gate._now, gpu_gate._sleep
    gpu_gate._now, gpu_gate._sleep = clock.now, clock.sleep
    try:
        yield clock, (lambda: json.loads(state.read_text())["calls"])
    finally:
        gpu_gate._now, gpu_gate._sleep = saved
        os.environ["PATH"] = old_path
        if old_state is None:
            os.environ.pop("FAKE_SMI_STATE", None)
        else:
            os.environ["FAKE_SMI_STATE"] = old_state
        gpu_gate.reset()
        shutil.rmtree(tmp, ignore_errors=True)


def gcfg(factory_url, own_url=None, enabled=True, **kw):
    g = {"enabled": enabled, "yield_to": [{"url": factory_url, "name": "shorts-factory"}], "need_free_vram_gb": 22,
         "need_gb": {"still": 22, "clip": 26, "ace": 12, "chatterbox": 6}, "poll_s": 30, "log_every_s": 300,
         "max_wait_s": 21600, **kw}
    cfg = {"gpu_gate": g}
    if own_url:
        cfg["comfy_cheq"] = {"url": own_url}
    return cfg


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------------------------------- the gate itself

def test_share_kinds_run_alongside_a_busy_factory_when_vram_allows_and_fall_back_without_sticking():
    fac = Server(queue=lambda n: (1, 3))                                             # busy for the whole test
    own = Server(held_gb=0)
    try:
        cfg = gcfg(fac.url, own.url, share_kinds=["chatterbox", "ace"], share_headroom_gb=4, share_max_wait_s=900)
        # enough VRAM for chatterbox (6) + headroom (4): starts at once although the factory has 4 jobs queued
        with gate_env(free_mib=(12000,)) as (clock, smi_calls):
            w, out = quiet(gpu_gate.wait_for_gpu, cfg, kind="chatterbox", why="the Chatterbox voice worker")
            assert w == 0.0 and smi_calls() == 1 and fac.posts() == [], out
        # 8 GB free: under 6 + 4, waits, then gives up after share_max_wait_s for this job only (not sticky)
        with gate_env(free_mib=(8000,)) as (clock, smi_calls):
            try:
                quiet(gpu_gate.wait_for_gpu, cfg, kind="chatterbox")
                raise AssertionError("expected GpuBusy")
            except gpu_gate.GpuBusy as e:
                assert "share_max_wait_s 900" in str(e) and "falling back for this job only" in str(e)
            assert gpu_gate.gave_up() is None                                        # the run carries on
            # a still is not a share kind: the busy factory still blocks it (no VRAM check while it is busy)
            calls = smi_calls()
            try:
                quiet(gpu_gate.wait_for_gpu, {**cfg, "gpu_gate": {**cfg["gpu_gate"], "max_wait_s": 60}}, kind="still")
                raise AssertionError("expected GpuBusy")
            except gpu_gate.GpuBusy:
                pass
            assert smi_calls() == calls and gpu_gate.gave_up()                       # stills: sticky give-up as before
        assert fac.posts() == []
    finally:
        fac.stop(), own.stop()


def test_noop_when_disabled_or_missing():
    fac = Server(queue=lambda n: (1, 5))
    try:
        with gate_env(free_mib=(100,)) as (clock, smi_calls):
            for cfg in ({}, {"gpu_gate": None}, gcfg(fac.url, enabled=False), config.load("config.yaml")):
                w, out = quiet(gpu_gate.wait_for_gpu, cfg, kind="still")
                assert w == 0.0 and out == "", (cfg, out)
            assert fac.log == [] and smi_calls() == 0 and clock.sleeps == []
            assert gpu_gate.settings(config.load("config.yaml")) is None             # the cloud VM: no gate
            # enabled, but the job goes to a remote server (not this PC's GPU): not gated
            w, _ = quiet(gpu_gate.wait_for_gpu, gcfg(fac.url), kind="still", url="http://100.75.169.5:8189")
            assert w == 0.0 and fac.log == []
    finally:
        fac.stop()


def test_waits_while_factory_busy_logs_every_log_every_s_and_never_posts_to_it():
    fac = Server(queue=lambda n: (1, 2) if n <= 25 else (0, 0))
    own = Server(held_gb=0)
    try:
        with gate_env(free_mib=(30000,)) as (clock, smi_calls):
            w, out = quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url), kind="still", why="b-roll still k1")
            lines = [ln for ln in out.splitlines() if ln.strip().startswith("GPU busy:")]
            # polls: t=0 busy -> CheqUp gives its own VRAM back (settle 3 s) -> t=3, 33, ... 693 busy -> t=723 idle
            assert abs(w - 723) < 1e-6, (w, clock.sleeps[:5])
            assert [re.search(r"waiting \((.*?) so far\)", ln).group(1) for ln in lines] == ["3 s", "5 min", "10 min"], lines
            assert lines[0].strip() == "GPU busy: shorts-factory has 3 jobs queued (1 running); waiting (3 s so far)"
            assert "GPU free after 12 min; starting b-roll still k1" in out
            assert fac.posts() == [] and {p for m, p, _ in fac.log} == {"/queue"}       # read-only: GET /queue only
            assert own.posts() == [("POST", "/free", {"unload_models": True, "free_memory": True})]   # once, ours
            assert abs(gpu_gate.waited_s() - 723) < 0.1 and smi_calls() == 1          # VRAM checked once it was idle
    finally:
        fac.stop(), own.stop()


def test_waits_for_free_vram_and_counts_own_comfy_memory_as_free():
    fac = Server()                                                                   # idle factory
    own = Server(held_gb=0)
    try:
        with gate_env(free_mib=(8192, 8192, 8192, 30720)) as (clock, smi_calls):     # 8 GB free, then 30 GB
            w, out = quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url), kind="still")
            assert w > 0 and smi_calls() == 4, (w, smi_calls(), out)
            assert "GPU busy: 8.0 GB VRAM free, still needs 22 GB" in out, out
            assert own.posts() == [("POST", "/free", {"unload_models": True, "free_memory": True})]
            assert fac.posts() == []
        own.held_gb = 16                                                             # CheqUp's ComfyUI holds 16 GB itself
        own.log.clear()
        with gate_env(free_mib=(8192,)) as (clock, smi_calls):
            w, out = quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url), kind="still")   # 8 + 16 >= 22: go
            assert w == 0 and own.posts() == [] and out == "" and clock.sleeps == []
            w, out = quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url), kind="chatterbox")   # 6 GB: go
            assert w == 0
            buf = io.StringIO()
            try:                                                             # explicit need_gb, never reached: gives up
                with contextlib.redirect_stdout(buf):
                    gpu_gate.wait_for_gpu(gcfg(fac.url, own.url, max_wait_s=60), need_gb=40, why="x")
                raise AssertionError("expected GpuBusy")
            except gpu_gate.GpuBusy as e:
                assert "x needs 40 GB" in str(e) and "needs 40 GB" in buf.getvalue() and gpu_gate.waited_s() >= 60
    finally:
        fac.stop(), own.stop()


def test_timeout_raises_gpubusy_then_fails_fast():
    fac = Server(queue=lambda n: (1, 0))                                             # busy for ever
    try:
        with gate_env() as (clock, smi_calls):
            try:
                quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, max_wait_s=120), kind="clip", why="b-roll clip k2")
                raise AssertionError("expected GpuBusy")
            except gpu_gate.GpuBusy as e:
                msg = str(e)
            assert "after 2 min waiting to start b-roll clip k2" in msg and "shorts-factory has 1 job queued" in msg, msg
            assert "nothing was sent to the other ComfyUI" in msg and gpu_gate.gave_up() == msg
            assert isinstance(gpu_gate.GpuBusy(msg), RuntimeError)                   # music/farm catch RuntimeError
            gets, t = fac.queue_gets, clock.t
            try:
                gpu_gate.wait_for_gpu(gcfg(fac.url), kind="still")
                raise AssertionError("a gate that gave up must fail at once")
            except gpu_gate.GpuBusy:
                pass
            assert fac.queue_gets == gets and clock.t == t and fac.posts() == []     # no new wait, no new poll
    finally:
        fac.stop()


def test_unreachable_factory_is_idle_and_missing_nvidia_smi_skips_vram():
    url = f"http://127.0.0.1:{closed_port()}"
    with gate_env(free_mib=(30000,)) as (clock, smi_calls):
        out = quiet(gpu_gate.wait_for_gpu, gcfg(url), kind="still")[1] + quiet(gpu_gate.wait_for_gpu, gcfg(url), kind="ace")[1]
        assert out.count("not answering: treated as idle") == 1, out                 # said once
        assert clock.sleeps == []
    fac = Server()
    try:
        with gate_env(with_smi=False) as (clock, smi_calls):
            w, out = quiet(gpu_gate.wait_for_gpu, gcfg(fac.url), kind="clip")
            assert w == 0 and "nvidia-smi not found: free VRAM not checked" in out
            assert gpu_gate.free_vram_gb() is None
    finally:
        fac.stop()


def test_stalled_factory_is_busy_not_idle_and_odd_queue_replies():
    """A factory ComfyUI that takes the connection but doesn't reply (stalled, e.g. loading models) is BUSY: the gate
    waits and gives up, never reads it as idle. It never triggers a mid-job cancel either. An error status or JSON
    that isn't a ComfyUI queue counts as not answering."""
    stalled = socket.socket()
    stalled.bind(("127.0.0.1", 0))
    stalled.listen(16)                                                      # accepts (backlog), never replies
    url = f"http://127.0.0.1:{stalled.getsockname()[1]}"
    saved = gpu_gate.QUEUE_TIMEOUT_S
    gpu_gate.QUEUE_TIMEOUT_S = 0.3
    odd = Server()
    try:
        assert gpu_gate.queue_state(url) == gpu_gate.SLOW
        with gate_env(free_mib=(30000,)) as (clock, smi_calls):
            try:
                quiet(gpu_gate.wait_for_gpu, gcfg(url, max_wait_s=60), kind="still", why="b-roll still s1")
                raise AssertionError("a stalled factory must not count as idle")
            except gpu_gate.GpuBusy as e:
                assert "didn't answer GET /queue" in str(e) and "treated as busy" in str(e), e
            assert smi_calls() == 0 and clock.t >= 1060
            assert gpu_gate.yield_check(gcfg(url, preempt_poll_s=0))() is None          # no cancel on a stall
            out = quiet(cli._comfy_cheq, gcfg(url))[1]
            assert "stalled: no reply to GET /queue" in out, out
        assert gpu_gate.queue_state(odd.url + "/nothing-here") is None     # 404 -> not answering
        assert gpu_gate.queue_state(odd.url) == (0, 0)
        assert gpu_gate.queue_state(f"http://127.0.0.1:{closed_port()}") is None        # refused -> not running
    finally:
        gpu_gate.QUEUE_TIMEOUT_S = saved
        stalled.close()
        odd.stop()


def _smi(tmp: Path, stdout: str, rc: int = 0) -> str:
    p = tmp / "nvidia-smi-fake"
    p.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({stdout!r})\nsys.exit({rc})\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_nvidia_smi_parsing_multi_gpu_non_numeric_and_errors():
    saved = gpu_gate._nvidia_smi
    tmp = Path(tempfile.mkdtemp())
    try:
        for out, rc, idx, want in (("30720\n1024\n", 0, 0, 30.0),             # two GPUs: index 0 ...
                                   ("30720\r\n1024\r\n", 0, 1, 1.0),         # ... index 1 (Windows line ends)
                                   ("[N/A]\n2048\n", 0, 1, 2.0),             # one GPU without a reading: the other still parses
                                   ("[N/A]\n", 0, 0, None),                  # non-numeric
                                   ("30720\n", 0, 3, None),                  # gpu_index beyond the GPUs: not the wrong GPU
                                   ("NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.\n", 9, 0, None),
                                   ("", 6, 0, None)):
            gpu_gate.reset()
            path = _smi(tmp, out, rc)
            gpu_gate._nvidia_smi = lambda path=path: path
            got, said = quiet(gpu_gate.free_vram_gb, idx)
            assert got == want, (out, rc, idx, got)
            assert (said == "") == (want is not None), (out, said)
            if want is None:
                assert "free VRAM not checked" in said and f"GPU {idx}" in said, said
        gpu_gate.reset()
        gpu_gate._nvidia_smi = lambda: str(tmp / "missing.exe")             # vanished between which() and run
        got, said = quiet(gpu_gate.free_vram_gb)
        assert got is None and "nvidia-smi gave no reading" in said, said
    finally:
        gpu_gate._nvidia_smi = saved
        gpu_gate.reset()
        shutil.rmtree(tmp, ignore_errors=True)


def test_quiet_s_does_not_start_in_the_gap_between_two_factory_jobs():
    fac, own = Server(), Server()
    try:
        with gate_env(free_mib=(30000,)) as (clock, smi_calls):
            assert quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url, quiet_s=60), kind="ace")[0] == 0   # never seen busy: go
            fac.queue, fac.queue_gets = (lambda n: (1, 0) if n <= 2 else (0, 0)), 0
            w, out = quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url, quiet_s=60), kind="still")
            # t=0 busy -> free ours, settle 3 s -> t=3 busy -> t=33 idle but only 30 s quiet -> t=63 idle 60 s: go
            assert abs(w - 63) < 1e-6 and fac.queue_gets == 4, (w, fac.queue_gets, out)
            assert smi_calls() == 2 and fac.posts() == []                     # VRAM read once per gate, after the quiet minute
            # yield_check saw the factory start a job mid-run: the next gate wants a quiet minute after it too
            fac.queue, fac.queue_gets = (lambda n: (1, 0) if n == 1 else (0, 0)), 0
            assert "started work" in gpu_gate.yield_check(gcfg(fac.url, own.url, quiet_s=60, preempt_poll_s=0))()
            w, out = quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url, quiet_s=60), kind="still")
            assert w >= 60 and "had work" in out and "quiet_s" in out, (w, out)
            assert quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url), kind="still")[0] == 0      # quiet_s unset = off
    finally:
        fac.stop(), own.stop()


def test_vram_wait_names_an_idle_factory_that_keeps_models_loaded():
    fac, own = Server(held_gb=18), Server(held_gb=0)                         # factory idle, 18 GB still loaded
    try:
        with gate_env(free_mib=(9216,)):
            try:
                quiet(gpu_gate.wait_for_gpu, gcfg(fac.url, own.url, max_wait_s=60), kind="still")
                raise AssertionError("expected GpuBusy")
            except gpu_gate.GpuBusy as e:
                assert "9.0 GB VRAM free" in str(e) and "shorts-factory's ComfyUI is idle but keeps 18.0 GB loaded" in str(e), e
        assert fac.posts() == [] and {m for m, p, _ in fac.log} == {"GET"} and {p for m, p, _ in fac.log} == {"/queue", "/system_stats"}
    finally:
        fac.stop(), own.stop()


def test_status_snapshot_is_read_only():
    fac, own = Server(queue=lambda n: (1, 3)), Server(held_gb=4)
    try:
        with gate_env(free_mib=(20480,)):
            st = gpu_gate.status(gcfg(fac.url, own.url), "still")
            assert st["enabled"] and st["yield_to"][0]["state"] == (1, 3) and st["free_gb"] == 20.0
            assert st["own_held_gb"] == 4.0 and st["need_gb"] == 22
            out = quiet(cli._comfy_cheq, {**gcfg(fac.url, own.url), "comfy_cheq": {"url": own.url, "dir": "C:/x"}})[1]
            assert "ok   CheqUp ComfyUI " + own.url + " v0.39.2" in out and "shorts-factory " + fac.url + " (1 running, 3 queued)" in out, out
            out = quiet(cli._comfy_cheq, gcfg(fac.url, enabled=False))[1]
            assert out.startswith("off  gpu_gate: disabled") and fac.url in out
        assert fac.posts() == [] and own.posts() == []
    finally:
        fac.stop(), own.stop()


# ----------------------------------------------------------------------- nothing CheqUp does POSTs to the factory

def test_cheqUp_never_sends_the_factory_anything_but_get_queue():
    fac = Server(queue=lambda n: (0, 0))
    try:
        cfg = {**gcfg(fac.url), "farm": {"machines": [{"name": "pc-5090", "host": "127.0.0.1", "first_port": fac.port, "gpus": 1,
                                                        "role": "hero", "enabled": True}], "probe_timeout_s": 2}}
        cfg["comfy_cheq"] = {"url": fac.url}                                         # misconfigured on purpose
        with gate_env(free_mib=(1000,)):
            live, out = quiet(farm.servers, cfg, True)
            assert live == [] and "not used (a gpu_gate.yield_to ComfyUI" in out
            srv, why = music.ace_server(cfg, music.ace_graph(cfg))
            assert srv is None and "shorts-factory's ComfyUI" in why
            assert gpu_gate.own_url(cfg) is None and gpu_gate.release_own(cfg) == []
            assert voice.free_comfy_vram(cfg["farm"]["machines"], never=gpu_gate.protected(cfg)) == []
            info = report.info({**cfg, "_preset": {}})
            assert info["farm_conflicts"] == [fac.url, fac.url], info["farm_conflicts"]   # farm port + comfy_cheq: refused
            try:                                                                      # VRAM short: still no POST to it
                quiet(gpu_gate.wait_for_gpu, {**cfg, "gpu_gate": {**cfg["gpu_gate"], "max_wait_s": 60}}, kind="still")
            except gpu_gate.GpuBusy:
                pass
        assert fac.posts() == [] and {p for m, p, _ in fac.log} <= {"/queue", "/system_stats"}, fac.log   # GETs only
        assert {m for m, p, _ in fac.log} == {"GET"}, fac.log
    finally:
        fac.stop()
        voice.shutdown()


def test_config_pc_points_farm_at_cheqUp_comfy():
    pc, cloud = config.load("config.pc.yaml"), config.load("config.yaml")
    hero = next(m for m in pc["farm"]["machines"] if m["name"] == "pc-5090")
    assert hero["first_port"] == pc["comfy_cheq"]["port"] == 8288 and hero["enabled"] is True
    assert gpu_gate.settings(pc) and gpu_gate.yield_to(pc) == [{"url": "http://127.0.0.1:8188", "name": "shorts-factory"}]
    assert gpu_gate.is_protected(pc, "http://localhost:8188") and not gpu_gate.is_protected(pc, "http://127.0.0.1:8288")
    assert gpu_gate.cheq_url(pc) == "http://127.0.0.1:8288" and pc["comfy_cheq"]["dir"] == r"C:\Users\white\ComfyUI-CheqUp"
    assert {k: gpu_gate.need_for(pc, k) for k in ("still", "clip", "ace", "chatterbox")} == {"still": 22, "clip": 26, "ace": 12, "chatterbox": 6}
    g = pc["gpu_gate"]
    assert (g["poll_s"], g["log_every_s"], g["max_wait_s"]) == (30, 300, 21600)
    info = report.info(pc)
    assert info["farm_conflicts"] == [] and info["comfy_cheq"]["url"] == "http://127.0.0.1:8288" and info["gpu_gate"]["enabled"]
    assert gpu_gate.settings(cloud) is None and "gpu_gate" not in cloud


# ----------------------------------------------------------------------------------------- wiring into the jobs

def _png(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    return p


def test_farm_gates_every_still_and_clip_job_on_this_pc():
    tmp = Path(tempfile.mkdtemp())
    calls, saved = [], gpu_gate.wait_for_gpu

    class Srv:
        url = "http://127.0.0.1:8288"

        def run(self, g, out_dir, stem, timeout=None):
            calls.append(("run", stem))
            return [_png(out_dir / f"{stem}_n20.png")]

        def upload(self, p):
            calls.append(("upload", p.name))
            return p.name
    try:
        gpu_gate.wait_for_gpu = lambda cfg, need_gb=None, why="", kind=None, url=None: calls.append(("gate", kind, url)) or 0.0
        b = {"look_positive": "", "negative": "n", "people_add_on": {"none": ""}, "negative_wan": "w", "negative_i2v_extra": "e"}
        cfg = {"_preset": {"broll": b}, "broll": {"candidates": 2, "max_rerolls": 0, "qa": "none"}, "models": {}, "farm": {}}
        shot = farm.Shot(key="k_9x16", prompt="hands slicing a lemon", motion="still", out_dir=tmp, people="none")
        farm._still_job(cfg, Srv(), shot)
        assert [c[0] for c in calls] == ["gate", "run", "gate", "run"] and calls[0] == ("gate", "still", Srv.url), calls
        calls.clear()
        _orig = farm._frames_ok
        farm._frames_ok = lambda cfg_, clip, s: (True, "ok")
        try:
            Srv.run = lambda self, g, out_dir, stem, timeout=None: calls.append(("run", stem)) or [_png(out_dir / f"{stem}.mp4")]
            farm._clip_job(cfg, Srv(), shot)
        finally:
            farm._frames_ok = _orig
        assert [c[0] for c in calls] == ["gate", "upload", "run"] and calls[0][1] == "clip", calls
        # the gate gives up: the shot fails (and falls back), it is never submitted
        calls.clear()

        def busy(*a, **k):
            raise gpu_gate.GpuBusy("GPU still busy after 360 min waiting to start b-roll still k: shorts-factory has 2 jobs queued")
        gpu_gate.wait_for_gpu = busy
        s2 = farm.Shot(key="k2_9x16", prompt="a bowl of lentils", motion="still", out_dir=tmp, people="none")
        quiet(farm._drain, cfg, [Srv()], [s2], farm._still_job, "still")
        assert s2.error.startswith("still: GPU still busy") and not s2.still and not any(c[0] == "run" for c in calls)
    finally:
        gpu_gate.wait_for_gpu = saved
        shutil.rmtree(tmp, ignore_errors=True)


def _wav(p: Path, secs=1.0):
    x = (0.2 * np.sin(2 * np.pi * 220 * np.arange(int(secs * 44100)) / 44100) * 32767).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(44100), w.writeframes(x.tobytes())
    return p


def test_ace_bed_waits_for_the_factory_before_submitting():
    if not shutil.which("ffmpeg"):
        print("skip test_ace_bed_waits_for_the_factory_before_submitting (no ffmpeg)")
        return
    from test_music import CKPT, Mock
    fac = Server(queue=lambda n: (1, 1) if n <= 3 else (0, 0))
    m = Mock()
    tmp = Path(tempfile.mkdtemp())
    seen, real_run = [], comfy.Comfy.run
    try:
        def fake_run(self, g, out_dir, stem, timeout=None):
            seen.append((self.url, fac.queue_gets))
            out_dir.mkdir(parents=True, exist_ok=True)
            return [_wav(out_dir / f"{stem}.wav")]
        comfy.Comfy.run = fake_run
        cfg = {**gcfg(fac.url), "out_dir": str(tmp / "out"), "models": {"ace_step": {"checkpoint": CKPT}},
               "farm": {"machines": [{"name": "pc-5090", "host": "127.0.0.1", "first_port": m.port, "gpus": 1, "role": "hero",
                                      "enabled": True}], "probe_timeout_s": 2, "job_timeout_s": 60}}
        with gate_env(free_mib=(30000,)):
            out, log = quiet(music.ace_bed, cfg, 9.5, tmp / "bed.wav")
        assert out and out.exists(), log
        assert seen == [(f"http://127.0.0.1:{m.port}", 4)], seen                                   # submitted only after the factory went idle
        assert "GPU busy: shorts-factory has 2 jobs queued (1 running)" in log and fac.posts() == []
        gpu_gate.reset()
        with gate_env(free_mib=(30000,)):                                   # the gate gives up: procedural bed, no submit
            fac.queue = lambda n: (1, 0)
            seen.clear()
            cfg["gpu_gate"]["max_wait_s"] = 60
            cfg["music"] = {"seed": 8}
            out, log = quiet(music.ace_bed, cfg, 9.5, tmp / "bed2.wav", "warm", 8)
            assert out is None and seen == [] and "ACE-Step failed (GpuBusy:" in log, log
    finally:
        comfy.Comfy.run = real_run
        fac.stop(), m.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_voice_gate_before_chatterbox_and_kokoro_reason_when_busy():
    from test_voice import cfg_for, board, fakes, run_voice
    fac = Server()
    saved = gpu_gate.wait_for_gpu
    seen = []
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            farm_cfg = {"machines": [{"name": "pc-5090", "host": "127.0.0.1", "first_port": fac.port, "gpus": 1, "enabled": True}]}
            with fakes(tmp) as (fk, count):
                gpu_gate.wait_for_gpu = lambda cfg, need_gb=None, why="", kind=None, url=None: seen.append((kind, voice.worker_ready(sys.executable))) or 0.0
                cfg = {**cfg_for(tmp, sys.executable), "farm": farm_cfg, **gcfg(fac.url)}
                run_voice(cfg, board(), tmp / "ep")
                assert seen == [("chatterbox", False)], seen                 # gated once, before the worker started
                assert fac.posts() == []                                     # free_comfy_vram skipped the factory port
                assert voice.worker_ready(sys.executable)                    # the worker outlives the board...
                run_voice(cfg, board(), tmp / "ep1b")
                assert seen == [("chatterbox", False), ("chatterbox", True)], seen   # ...and the next board is gated too

                def busy(*a, **k):
                    raise gpu_gate.GpuBusy("GPU still busy after 360 min waiting to start the Chatterbox voice worker")
                voice.shutdown()
                gpu_gate.wait_for_gpu = busy
                b = board()
                out = run_voice(cfg, b, tmp / "ep2")
                eng = b["audio"]["voice_engines"]
                cb = [e for e in eng if e["fallback"]]
                assert cb and all(e["engine"] == "kokoro" and "GPU busy" in e["fallback"] for e in cb), eng
                assert "Kokoro fallback" in out or "WARNING voice" in out
        assert fac.posts() == []
    finally:
        gpu_gate.wait_for_gpu = saved
        voice.shutdown()
        fac.stop()


def test_release_stops_idle_chatterbox_worker():
    from test_voice import fakes
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with fakes(tmp):
            gpu_gate.reset()
            w = voice._worker(sys.executable, 20)
            assert voice.worker_ready(sys.executable) and "the Chatterbox worker" in gpu_gate._releasers
            assert gpu_gate.release_own({}) == ["the Chatterbox worker"]
            w.wait(timeout=10)
            assert not voice.worker_ready(sys.executable) and sys.executable not in voice._dead   # restarts on the next line
            voice._worker(sys.executable, 20)
            voice._lock.acquire()
            try:
                assert voice.release_gpu() is False and voice.worker_ready(sys.executable)   # a take is rendering: left alone
            finally:
                voice._lock.release()
    gpu_gate.reset()


def test_state_and_report_record_gpu_wait_and_exit_12():
    tmp = Path(tempfile.mkdtemp())
    saved = {k: getattr(pipeline, k) for k in ("_broll", "_voice", "_music", "_sfx", "_render", "_probe")}
    saved_produce = pipeline.produce
    try:
        cfg = config.load("config.yaml")
        cfg["out_dir"], cfg["_config_name"] = str(tmp / "out"), "config.test.yaml"
        cfg["gpu_gate"] = {"enabled": True, "yield_to": ["http://127.0.0.1:8188"]}
        board = json.loads((ROOT / "concepts" / "made-simple-social.json").read_text(encoding="utf-8"))
        board.update(id="gw", formats=["9x16"])
        bp = tmp / "gw.json"
        bp.write_text(json.dumps(board))
        gpu_gate.reset()

        def fake_broll(cfg_, b, ep, use_5090, skip, formats=None):
            gpu_gate._waited[0] += 754.0                                     # the farm waited 12.6 min for the factory
            return False
        pipeline._broll = fake_broll
        pipeline._voice = lambda cfg_, b, ep, skip: None
        pipeline._music = lambda cfg_, b, ep: b.setdefault("audio", {}).update(music_engine="procedural")
        pipeline._sfx = lambda b, ep: None
        pipeline._render = lambda cfg_, b, ep, fmt: ep / "renders" / f"gw_{fmt}.mp4"
        pipeline._probe = lambda p: {"w": 1080, "h": 1920, "audio": True, "dur": 8.0, "mb": 1.0}
        quiet(pipeline.produce, cfg, bp)
        st = json.loads((tmp / "out" / "episodes" / "gw" / "state.json").read_text())
        assert st["status"] == "rendered" and st["gpu_wait_s"] == 754.0 and st["gpu_busy"] is None, st
        data = report.collect(cfg, ["gw"], make_exit=0)
        md = report.render_md(data)
        assert data["gpu_wait_s"] == 754.0 and "**GPU gate:** waited 13 min in total" in md and "GPU wait: 13 min" in md, md
        # the gate gives up during a board: recorded, the next boards are skipped, the run's exit code is 12
        msg = "GPU still busy after 360 min waiting to start b-roll still x: shorts-factory has 1 job queued (1 running)"

        def giving_up(cfg_, b, ep, use_5090, skip, formats=None):
            gpu_gate._gave_up[0] = msg
            return False
        pipeline._broll = giving_up
        calls = []

        def produce(cfg_, b, formats=None, **kw):
            calls.append(Path(b).stem)
            return saved_produce(cfg_, bp if Path(b).stem == "made-simple-social" else b, formats, **kw)
        pipeline.produce = produce
        real_load = config.load
        config.load = lambda path=None: cfg
        try:
            try:
                quiet(cli.main, ["make", "made-simple-social", "made-simple-live"])
                raise AssertionError("make must exit 1 when the gate gave up")
            except SystemExit as e:
                assert e.code == 1
        finally:
            config.load = real_load
        assert calls == ["made-simple-social"], calls                        # made-simple-live never started
        live = json.loads((tmp / "out" / "episodes" / "made-simple-live" / "state.json").read_text())
        assert live["status"] == "skipped" and live["gpu_busy"] == msg
        st = json.loads((tmp / "out" / "episodes" / "gw" / "state.json").read_text())
        assert st["gpu_busy"] == msg
        data = report.collect(cfg, ["gw", "made-simple-live"], make_exit=1)
        assert data["exit"] == 12 and data["meaning"] == "GPU busy (gate gave up)", data["exit"]
        md = report.render_md(data)
        assert "**Gave up:**" in md and "Skipped: not started: GPU still busy" in md and "GPU gate gave up during this board" in md
    finally:
        for k, v in saved.items():
            setattr(pipeline, k, v)
        pipeline.produce = saved_produce
        gpu_gate.reset()
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------ side-by-side install: model folders

STUB_FOLDER_PATHS = '''
import os
base_path = os.path.dirname(os.path.realpath(__file__))
models_dir = os.path.join(base_path, "models")
folder_names_and_paths = {}
folder_names_and_paths["checkpoints"] = ([os.path.join(models_dir, "checkpoints")], {".safetensors"})
folder_names_and_paths["text_encoders"] = ([os.path.join(models_dir, "text_encoders"), os.path.join(models_dir, "clip")], {".safetensors"})
folder_names_and_paths["diffusion_models"] = ([os.path.join(models_dir, "unet"), os.path.join(models_dir, "diffusion_models")], {".safetensors"})
folder_names_and_paths["vae"] = ([os.path.join(models_dir, "vae")], {".safetensors"})
folder_names_and_paths["frame_interpolation"] = ([os.path.join(models_dir, "frame_interpolation")], {".safetensors"})
folder_names_and_paths["custom_nodes"] = ([os.path.join(base_path, "custom_nodes")], set())
folder_names_and_paths["datasets"] = ([os.path.join(base_path, "datasets")], set())
folder_names_and_paths["brand_new_type"] = ([os.path.join(models_dir, "brand_new_type")], {".safetensors"})
'''


def _load_like_comfyui(yml: Path) -> dict:
    """utils/extra_config.load_extra_path_config of v0.39.2, minus the folder_paths call: {type: [paths]}."""
    conf_all, out = yaml.safe_load(yml.read_text(encoding="utf-8")), {}
    for c in conf_all.values():
        base = c.pop("base_path")
        assert c.pop("is_default") is False
        for k, v in c.items():
            out[k] = [os.path.normpath(os.path.join(base, y)) for y in v.split("\n") if y]
    return out


def test_extra_model_paths_yaml_maps_every_model_folder_type():
    tmp = Path(tempfile.mkdtemp())
    try:
        comfy_dir, shared = tmp / "ComfyUI-CheqUp", tmp / "shared models"
        comfy_dir.mkdir()
        (shared / "checkpoints").mkdir(parents=True)
        (shared / "checkpoints" / "ace.safetensors").write_bytes(b"x")
        (shared / "checkpoints" / "put_checkpoints_here").write_bytes(b"")
        (comfy_dir / "folder_paths.py").write_text(STUB_FOLDER_PATHS)
        (comfy_dir / "comfyui_version.py").write_text('__version__ = "0.39.2"\n')
        types, src = comfy_cheq_paths.folder_types(comfy_dir)
        assert "custom_nodes" not in types and "datasets" not in types and types["brand_new_type"] == ["brand_new_type"]
        assert types["diffusion_models"] == ["unet", "diffusion_models"] and types["text_encoders"] == ["text_encoders", "clip"]
        r, out = quiet(comfy_cheq_paths.main, ["yaml", "--comfy", str(comfy_dir), "--models", str(shared)])
        yml = comfy_dir / "extra_model_paths.yaml"
        assert r == 0 and json.loads(out)["types"] == 6 and not yml.read_bytes().startswith(b"\xef\xbb\xbf")
        got = _load_like_comfyui(yml)
        assert got["diffusion_models"] == [os.path.normpath(str(shared / "unet")), os.path.normpath(str(shared / "diffusion_models"))]
        assert set(got) == set(types)
        r, out = quiet(comfy_cheq_paths.main, ["summary", "--comfy", str(comfy_dir), "--models", str(shared)])
        s = json.loads(out)
        assert s["comfyui_version"] == "0.39.2" and s["types_with_files"] == ["checkpoints"] and s["model_files"] == 1
        # no folder_paths.py: the built-in v0.39.2 list (every type ComfyUI v0.39.2 knows under models/)
        types, src = comfy_cheq_paths.folder_types(tmp / "nowhere")
        assert types == comfy_cheq_paths.V0392 and "built-in" in src and len(types) == 25
        assert {"checkpoints", "diffusion_models", "text_encoders", "vae", "loras", "clip_vision", "upscale_models",
                "frame_interpolation", "controlnet", "embeddings", "audio_encoders", "model_patches"} <= set(types)
        y = yaml.safe_load(comfy_cheq_paths.yaml_text(types, r"C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI\models",
                                                      r"C:\Users\white\ComfyUI-CheqUp", "x"))
        assert y["cheq_shared_models"]["base_path"] == r"C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI\models"
        real = os.environ.get("COMFY_SRC")                                  # optional: a real ComfyUI checkout
        if real and (Path(real) / "folder_paths.py").exists():
            assert comfy_cheq_paths.folder_types(Path(real))[0] == comfy_cheq_paths.V0392
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------ PowerShell scripts (static)

def _code(p: Path) -> str:
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in p.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))


def test_ps1_side_by_side_comfy_rules():
    sd = ROOT / "scripts"
    inst, start, stop = (sd / n for n in ("install_comfy_cheq_pc.ps1", "comfy_cheq_start.ps1", "comfy_cheq_stop.ps1"))
    run, upd = (sd / "pc_run.ps1").read_text(), (sd / "update_comfy_pc.ps1").read_text()
    for p in (inst, start, stop):
        s = p.read_text(encoding="utf-8")
        assert s.isascii(), p.name
        code = _code(p)
        for bad in ("??", "&&", "||", "-Parallel", "$IsWindows", "-AsHashtable", "::new(", "-NoProxy"):
            assert bad not in code, (p.name, bad)
        assert "-Method Post" not in code and "/free" not in code and "/interrupt" not in code, p.name   # GETs only
        assert "$FactoryPort" in code and "$FactoryComfy" in code and "$FactoryVenv" in code, p.name     # guards
        assert "'C:\\Users\\white\\ComfyUI-CheqUp'" in s and "8288" in s, p.name
    ic = _code(inst)
    assert "'v0.39.2'" in ic and "'clone', '--depth', '1', '--branch'" in ic and "https://download.pytorch.org/whl/cu128" in ic
    assert "'venv', '--seed', '-p', '3.12'" in ic and "'pip', 'install', '--user', '--quiet', 'uv'" in ic
    assert "'-c', $Constraints" in ic and "comfy_cheq_paths.py" in ic and "'check'" in ic and "'summary'" in ic
    # cu130 when the driver supports CUDA 13 (v0.39.2 disables comfy-kitchen's CUDA ops below it), an explicit
    # -TorchIndex switch really reinstalls torch, and no torchaudio (unused by v0.39.2; its last release pins old torch)
    assert "https://download.pytorch.org/whl/cu130" in ic and "'CUDA Version:" in ic and "'--reinstall-package', 'torch'" in ic
    assert "'torchaudio'" not in ic
    assert "'C:\\Users\\white\\ComfyUI-Shared\\models'" in inst.read_text()
    sc = _code(start)
    for flag in ("'--listen', '127.0.0.1'", "'--port'", "'--extra-model-paths-config'", "'--output-directory'", "'--input-directory'",
                 "'--temp-directory'", "'--user-directory'", "'--disable-auto-launch'"):
        assert flag in sc, flag
    assert "-WindowStyle Hidden" in sc and "comfy_cheq_' +" in sc and "comfy_cheq.pid" in sc and "WaitSeconds = 180" in start.read_text()
    assert "Stop-Process" not in sc and "Stop-Process" in _code(stop)
    tc = _code(stop)
    assert "pc_run.lock" in tc and "exit 10" in tc and "$FactoryKeys" in tc and "Test-Ours" in tc
    # pc_run: exactly one POST, to CheqUp's own ComfyUI's /free, guarded against shorts-factory's port
    rc = _code(sd / "pc_run.ps1")
    posts = [ln for ln in rc.splitlines() if "-Method Post" in ln]
    assert len(posts) == 1 and "$script:CheqUrl + '/free'" in posts[0], posts
    assert "$script:FactoryPorts -contains $port" in rc and "$script:FactoryPorts = @(8188)" in rc
    assert "comfy_cheq_start.ps1" in rc and "install_comfy_cheq_pc.ps1" in rc and "12 = 'GPU busy" in rc
    assert "-Method Get" in rc and "/queue" in rc
    # update_comfy_pc.ps1 is kept, but marked not-for-CheqUp and refuses without an explicit switch
    assert upd.startswith("# NOT FOR CHEQUP") and "install_comfy_cheq_pc.ps1" in upd and "ConfirmSharedInstall" in upd
    pw = os.environ.get("PWSH") or shutil.which("pwsh")
    if pw:
        files = [str(p) for p in sorted(sd.glob("*.ps1"))]
        with tempfile.TemporaryDirectory() as d:
            chk = Path(d) / "parse.ps1"
            chk.write_text("$bad = 0\nforeach ($f in $args) {\n  $e = $null\n"
                           "  [void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$e)\n"
                           "  foreach ($x in $e) { $bad++; Write-Output ($f + ':' + $x.Extent.StartLineNumber + ' ' + $x.Message) }\n}\nexit $bad\n")
            r = subprocess.run([pw, "-NoProfile", "-NonInteractive", "-File", str(chk), *files], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
    else:
        print("  (no pwsh: PowerShell parse check skipped)")


def test_cheqUp_cancels_its_own_job_when_the_factory_starts_one():
    """Mid-job preemption: shorts-factory gets work while a CheqUp job runs on CheqUp's ComfyUI -> CheqUp interrupts
    ITS OWN job (by prompt_id, on its own server), raises Yielded, and farm retries after the gate. The factory gets
    only GET /queue."""
    from test_music import Mock
    fac, own = Server(queue=lambda n: (0, 0) if n <= 1 else (1, 0)), Mock()
    own.running = True                                                      # our job is executing (never finishes)
    try:
        cfg = gcfg(fac.url, preempt_poll_s=0)
        assert gpu_gate.yield_check({**cfg, "gpu_gate": {**cfg["gpu_gate"], "preempt": False}}) is None
        assert gpu_gate.yield_check(cfg, "http://100.75.169.5:8189") is None and gpu_gate.yield_check({}) is None
        with gate_env():
            c = comfy.Comfy(f"http://127.0.0.1:{own.port}", 60)
            c.yield_check = gpu_gate.yield_check(cfg, c.url)
            try:
                c.run(music.ace_graph({}), Path(tempfile.mkdtemp()), "x")
                raise AssertionError("no yield")
            except gpu_gate.Yielded as e:
                assert "shorts-factory started work" in str(e), e
        assert own.interrupt_bodies == [{"prompt_id": "p1"}] and fac.posts() == [] and fac.queue_gets == 2
    finally:
        fac.stop(), own.stop()
    # farm: a yielded job frees CheqUp's own VRAM and is retried after the gate; MAX_YIELDS+1 yields fail the shot
    tmp = Path(tempfile.mkdtemp())
    calls, saved_gate, saved_rel = [], gpu_gate.wait_for_gpu, gpu_gate.release_own
    tries = []

    class Srv:
        url = "http://127.0.0.1:8288"

        def run(self, g, out_dir, stem, timeout=None):
            calls.append("run")
            if len(tries) < 1:
                tries.append(1)
                raise gpu_gate.Yielded("shorts-factory started work")
            return [_png(out_dir / f"{stem}_n20.png")]
    try:
        gpu_gate.wait_for_gpu = lambda *a, **k: calls.append("gate") or 0.0
        gpu_gate.release_own = lambda cfg, url=None: calls.append("free") or []
        shot = farm.Shot(key="k_9x16", prompt="hands slicing a lemon", motion="still", out_dir=tmp, people="none")
        files, _ = quiet(farm._run, {}, Srv(), "still", shot, {}, "k_s1")
        assert calls == ["gate", "run", "free", "gate", "run"] and files, calls
        Srv.run = lambda self, g, out_dir, stem, timeout=None: calls.append("run") or (_ for _ in ()).throw(gpu_gate.Yielded("busy"))
        calls.clear()
        try:
            quiet(farm._run, {}, Srv(), "clip", shot, {}, "k_clip", gated=True, timeout=5)
            raise AssertionError("no give-up")
        except RuntimeError as e:
            assert "gave way to shorts-factory" in str(e) and not isinstance(e, gpu_gate.Yielded)
        assert calls.count("run") == farm.MAX_YIELDS + 1 and calls[0] == "run", calls       # gated=True: no first gate
    finally:
        gpu_gate.wait_for_gpu, gpu_gate.release_own = saved_gate, saved_rel
        shutil.rmtree(tmp, ignore_errors=True)


def test_factory_port_is_protected_without_any_gpu_gate_config():
    """127.0.0.1:8188 (shorts-factory's ComfyUI) gets no POST and no job even from a config with no gpu_gate block
    (config.mama.yaml --use-5090, a hand-edited config): comfy.Comfy refuses every POST to it, farm/music skip it,
    voice's /free skips it. Requests to it would hit the patched requests and fail the test."""
    import requests as rq
    hits = []
    real_get, real_post = rq.get, rq.post

    def get(url, *a, **k):
        if ":8188" in url:
            hits.append(("GET", url))
        return real_get(url, *a, **k)

    def post(url, *a, **k):
        hits.append(("POST", url))
        raise AssertionError(f"POST {url}")
    assert gpu_gate.is_protected(None, "http://localhost:8188") and gpu_gate.is_protected({}, "http://127.0.0.1:8188/")
    assert not gpu_gate.is_protected(None, "http://127.0.0.1:8288") and gpu_gate.own_url({}, "http://127.0.0.1:8188") is None
    rq.get, rq.post = get, post
    try:
        c = comfy.Comfy("http://localhost:8188")
        for fn in (lambda: c.run({}, Path(tempfile.mkdtemp()), "x"), lambda: c._post("/free", json={})):
            try:
                fn()
                raise AssertionError("no refusal")
            except RuntimeError as e:
                assert "shorts-factory" in str(e), e
        cfg = config.load("config.yaml")
        cfg.pop("gpu_gate", None)
        cfg["farm"]["machines"] = [{"name": "pc-5090", "host": "127.0.0.1", "first_port": 8188, "gpus": 1, "role": "hero", "enabled": True}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert farm.servers(cfg, use_5090=True) == []
            assert music.ace_server(cfg, music.ace_graph(cfg))[0] is None
            assert voice.free_comfy_vram(cfg["farm"]["machines"]) == []
            assert gpu_gate.release_own(cfg, "http://127.0.0.1:8188") == []
        assert report.info(cfg)["farm_conflicts"] == ["http://127.0.0.1:8188"]       # pc_run refuses it (exit 5)
    finally:
        rq.get, rq.post = real_get, real_post
    assert hits == [], hits
    # the scripts that could change files or processes near shorts-factory's install
    sd = ROOT / "scripts"
    fetch, stop, setup = ((sd / n).read_text(encoding="utf-8") for n in ("fetch_models_pc.ps1", "comfy_cheq_stop.ps1", "setup_pc.ps1"))
    assert "-o $t[2]" in fetch and "-o $out" not in fetch and ".cqpart" in fetch       # never appends to an existing file
    assert re.search(r"Move-Item -LiteralPath \$t\[2\] -Destination \$t\[1\]\s", fetch) and "Remove-Item" not in fetch
    assert "MinFreeGB" in fetch
    assert "Test-Under $Dest $f" in stop and "comfyui_version.py" in stop and "$FactoryDir" in stop
    assert "pyvenv.cfg" in setup and "VIRTUAL_ENV" in setup.split("pip install uv")[0]
    # pc_run creates shorts-factory's data\STOP only with -PauseFactory, never deletes it; cqf never mentions it
    rl = (sd / "pc_run.ps1").read_text(encoding="utf-8").splitlines()
    mk = [i for i, ln in enumerate(rl) if "$stop" in ln and re.search(r"New-Item|Set-Content|Out-File|WriteAll", ln)]
    assert len(mk) == 1 and rl[mk[0] - 1].strip() == "elseif ($PauseFactory) {", [rl[i] for i in mk]
    assert not any("$stop" in ln and re.search(r"Remove-Item|Move-Item|Rename-Item", ln) for ln in rl)
    assert not any("STOP" in p.read_text(encoding="utf-8") for p in (ROOT / "cqf").glob("*.py"))


def test_docs_name_the_side_by_side_comfy():
    puppet, readme = (ROOT / "PUPPET.md").read_text(encoding="utf-8"), (ROOT / "README.md").read_text(encoding="utf-8")
    for t in (puppet, readme):
        assert "install_comfy_cheq_pc.ps1" in t and "8288" in t and "gpu_gate" in t and "12" in t
    assert "comfy_cheq_stop.ps1" in puppet and "scripts/install_comfy_cheq_pc.ps1" in puppet
    assert "update_comfy_pc.ps1" not in puppet.replace("Never run `scripts/update_comfy_pc.ps1`", "")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
