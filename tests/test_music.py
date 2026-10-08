"""Music backends against a mock ComfyUI (no GPU, no model; the "ACE-Step output" is a numpy sine).
Run: python -m pytest tests  (or python tests/test_music.py). Needs ffmpeg."""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cqf import comfy, music, pipeline, sfx  # noqa: E402

CKPT = "ace_step_1.5_turbo_aio.safetensors"
LANGS_NEW = ["de", "en", "fr", "ja", "zh", "unknown"]      # ComfyUI >= 0.21 (trimmed)
LANGS_OLD = ["de", "en", "fr", "ja", "zh"]                  # ComfyUI 0.12-0.20: no "unknown"
ACE_NODES = ["CheckpointLoaderSimple", "ModelSamplingAuraFlow", "TextEncodeAceStepAudio1.5", "ConditioningZeroOut",
             "EmptyAceStep1.5LatentAudio", "KSampler", "VAEDecodeAudio", "SaveAudio"]


def _flac(seconds: float) -> bytes:
    """A 44.1 kHz mono sine as FLAC, standing in for SaveAudio's file."""
    with tempfile.TemporaryDirectory() as d:
        w, f = Path(d) / "s.wav", Path(d) / "s.flac"
        x = (0.3 * np.sin(2 * np.pi * 220 * np.arange(int(seconds * 44100)) / 44100) * 32767).astype("<i2")
        with wave.open(str(w), "wb") as o:
            o.setnchannels(1), o.setsampwidth(2), o.setframerate(44100), o.writeframes(x.tobytes())
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(w), str(f)], check=True)
        return f.read_bytes()


class Mock:
    """Just enough of ComfyUI's HTTP API: system_stats, object_info, prompt, history, view, queue."""

    def __init__(self, version="0.39.2", langs=LANGS_NEW, ckpts=(CKPT,), pending=False):
        self.version, self.langs, self.ckpts, self.pending = version, list(langs), list(ckpts), pending
        self.prompts, self.hits, self.deleted, self.interrupts = [], [], [], 0
        self.running, self.interrupt_bodies = False, []          # running: our job is the one executing (never finishes)
        self.node_errors = {}                                    # /prompt reply: outputs ComfyUI dropped
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code), self.send_header("Content-Type", "application/json"), self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                u = urlparse(self.path)
                mock.hits.append(u.path)
                if u.path == "/system_stats":
                    return self._json({"system": {"comfyui_version": mock.version}})
                if u.path == "/object_info":
                    return self._json(mock.info())
                if u.path.startswith("/object_info/"):
                    n = u.path.split("/", 2)[2]
                    return self._json({n: mock.info()[n]} if n in mock.info() else {})
                if u.path == "/queue":
                    pend = [[0, p["id"], {}, {}, []] for p in mock.prompts] if mock.pending else []
                    run = [[0, mock.prompts[-1]["id"], {}, {}, []]] if mock.running and mock.prompts else [[0, "other-job", {}, {}, []]]
                    return self._json({"queue_running": run, "queue_pending": pend})
                if u.path.startswith("/history/"):
                    pid = u.path.split("/")[2]
                    if mock.pending or mock.running or not any(p["id"] == pid for p in mock.prompts):
                        return self._json({})
                    secs = next(p for p in mock.prompts if p["id"] == pid)["graph"]["5"]["inputs"]["seconds"]
                    mock.secs = secs
                    return self._json({pid: {"status": {"status_str": "success", "completed": True},
                                             "outputs": {"8": {"audio": [{"filename": "ace_00001_.flac", "subfolder": "cq_music", "type": "output"}]}}}})
                if u.path == "/view":
                    q = parse_qs(u.query)
                    assert q["filename"] == ["ace_00001_.flac"] and q["type"] == ["output"], q
                    b = _flac(mock.secs)
                    self.send_response(200), self.end_headers()
                    return self.wfile.write(b)
                self._json({}, 404)

            def do_POST(self):
                u = urlparse(self.path)
                mock.hits.append(u.path)
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if u.path == "/prompt":
                    g = body["prompt"]
                    bad = [n["class_type"] for n in g.values() if n["class_type"] not in mock.info()]
                    if bad:
                        return self._json({"error": f"unknown nodes {bad}"}, 400)
                    pid = f"p{len(mock.prompts) + 1}"
                    mock.prompts.append({"id": pid, "graph": g})
                    return self._json({"prompt_id": pid, "number": len(mock.prompts), "node_errors": mock.node_errors})
                if u.path == "/queue":
                    mock.deleted += body.get("delete", [])
                    return self._json({})
                if u.path == "/interrupt":
                    mock.interrupts += 1
                    mock.interrupt_bodies.append(body)
                    return self._json({})
                self._json({}, 404)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def info(self):
        """Real /object_info shapes: classic [[...], {}] combos for V1 nodes, ["COMBO", {"options"}] for V3."""
        keys = [f"{r} {q}" for q in ("major", "minor") for r in ("C", "D", "E", "F", "G", "A", "B")]
        i = {n: {"input": {"required": {}}} for n in ACE_NODES}
        i["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [self.ckpts, {"tooltip": "x"}]
        i["TextEncodeAceStepAudio1.5"]["input"]["required"].update(
            language=["COMBO", {"default": "en", "multiselect": False, "options": self.langs}],
            keyscale=["COMBO", {"multiselect": False, "options": keys}])
        return i

    def stop(self):
        self.srv.shutdown(), self.srv.server_close()


def _cfg(tmp: Path, *ports, backend="ace_step", mama_port=None) -> dict:
    machines = [{"name": "mama", "host": "127.0.0.1", "first_port": mama_port or 1, "gpus": 1, "role": "broll", "enabled": False}]
    machines += [{"name": "pc-5090", "host": "127.0.0.1", "first_port": p, "gpus": 1, "role": "hero", "enabled": True} for p in ports]
    return {"out_dir": str(tmp / "out"), "farm": {"machines": machines, "probe_timeout_s": 2, "job_timeout_s": 60},
            "models": {"ace_step": {"checkpoint": CKPT}},
            "music": {"generate": True, "backend": backend, "seed": 7, "gain_db_under_vo": -21, "gain_db_solo": -6}}


def _wav(p: Path):
    with wave.open(str(p)) as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes() / w.getframerate()


def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


def _board(theme=None, secs=(4, 5, 3)):
    b = {"id": "t", "scenes": [{"dur": s} for s in secs]}
    if theme:
        b["theme"] = theme
    return b


def test_graph_fill():
    g = music.ace_graph({"models": {"ace_step": {"checkpoint": "my_ace.safetensors"}}}, "bright", 13, 42, "cq_music/ace_k")
    assert "{{" not in json.dumps(g)
    enc = g["3"]["inputs"]
    assert enc["seed"] == 42 and enc["bpm"] == 100 and enc["duration"] == 13 and isinstance(enc["duration"], int)
    assert enc["keyscale"] == "G major" and enc["language"] == "unknown" and enc["lyrics"] == "[Instrumental]"
    assert enc["timesignature"] == "4" and enc["generate_audio_codes"] is True and enc["top_k"] == 0
    assert g["5"]["inputs"]["seconds"] == 13 and g["6"]["inputs"]["seed"] == 42 and g["6"]["inputs"]["steps"] == 8
    assert g["2"]["inputs"]["shift"] == 3.0 and g["8"]["class_type"] == "SaveAudio" and g["8"]["inputs"]["filename_prefix"] == "cq_music/ace_k"
    assert comfy.graph_models(g) == ["my_ace.safetensors"]
    w = music.ace_graph({}, "warm")
    assert w["1"]["inputs"]["ckpt_name"] == CKPT and w["3"]["inputs"]["keyscale"] == "D major" and w["3"]["inputs"]["bpm"] == 90


def test_combo_options():
    assert comfy.combo_options([["a", "b"], {}]) == ["a", "b"]
    assert comfy.combo_options(["COMBO", {"options": ["x"]}]) == ["x"]
    assert comfy.combo_options(None) == [] and comfy.combo_options(["INT", {"min": 0}]) == []


def test_ace_bed_on_mock_comfy_then_cache():
    tmp, m, mama = Path(tempfile.mkdtemp()), Mock(), Mock()
    try:
        cfg = _cfg(tmp, m.port, mama_port=mama.port)
        out, log = _quiet(music.ace_bed, cfg, 12.2, tmp / "music.wav", "warm", 7)
        assert out == tmp / "music.wav", log
        sr, ch, sw, dur = _wav(out)
        assert (sr, ch, sw) == (48000, 2, 2) and abs(dur - 13) < 0.05, (sr, ch, sw, dur)   # ceil(12.2) = 13 s
        g = m.prompts[0]["graph"]
        assert g["3"]["inputs"]["language"] == "unknown" and g["3"]["inputs"]["duration"] == 13
        assert g["8"]["inputs"]["filename_prefix"].startswith("cq_music/ace_")
        cached = list((tmp / "out" / "music").glob("ace_warm_13s_*.wav"))
        assert len(cached) == 1 and not mama.hits, (cached, mama.hits)           # mama (enabled: false) never asked
        m.stop()                                                                     # cache hit: no server needed
        again, _ = _quiet(music.ace_bed, cfg, 12.9, tmp / "music2.wav", "warm", 7)
        assert again and again.read_bytes() == out.read_bytes()
        short, log = _quiet(music.ace_bed, cfg, 3, tmp / "m3.wav", "warm", 7)      # 10 s minimum -> new key, server down
        assert short is None and "not responding" in log, log
    finally:
        mama.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_old_comfy_gets_language_en():
    tmp, m = Path(tempfile.mkdtemp()), Mock(version="0.14.0", langs=LANGS_OLD)
    try:
        out, log = _quiet(music.ace_bed, _cfg(tmp, m.port), 10, tmp / "music.wav", "bright", 7)
        assert out and m.prompts[0]["graph"]["3"]["inputs"]["language"] == "en", log
        assert m.prompts[0]["graph"]["3"]["inputs"]["keyscale"] == "G major"
    finally:
        m.stop(), shutil.rmtree(tmp, ignore_errors=True)


def test_missing_checkpoint_or_down_falls_back_to_procedural():
    tmp, m = Path(tempfile.mkdtemp()), Mock(ckpts=["ace_step_v1_3.5b.safetensors"])
    try:
        out, log = _quiet(music.ace_bed, _cfg(tmp, m.port), 10, tmp / "music.wav")
        assert out is None and "checkpoint not in ComfyUI's checkpoints list" in log and "ace_step_v1_3.5b" in log, log
        assert "missing model" not in log and not m.prompts
        board, ep = _board(), tmp / "ep"
        ep.mkdir()
        _, log = _quiet(pipeline._music, _cfg(tmp, m.port), board, ep)
        assert board["audio"]["music_engine"] == "procedural" and Path(board["audio"]["music"]).exists(), log
        assert "procedural bed instead" in log
        down = _cfg(tmp, 9)                                                          # nothing listens on port 9
        board = _board()
        _, log = _quiet(pipeline._music, down, board, ep)
        assert board["audio"]["music_engine"] == "procedural" and "not responding" in log, log
    finally:
        m.stop(), shutil.rmtree(tmp, ignore_errors=True)


def test_pipeline_music_engine_and_silence_cue():
    tmp, m = Path(tempfile.mkdtemp()), Mock()
    try:
        cfg, ep = _cfg(tmp, m.port), tmp / "ep"
        ep.mkdir()
        board = _board(theme="meta-live", secs=(4, 5, 3))
        board["audio"] = {"vo": "vo.wav"}
        _quiet(pipeline._music, cfg, board, ep)
        a = board["audio"]
        assert a["music_engine"] == "ace_step" and a["music"] == str(ep / "music.wav") and a["music_gain_db"] == -21
        assert m.prompts[0]["graph"]["3"]["inputs"]["bpm"] == 100                 # meta-live -> bright
        assert abs(_wav(ep / "music.wav")[3] - 13) < 0.05                           # 12 s board + 1 s
        board["scenes"][1]["sfx"] = [{"at": 1.0, "kind": "silence", "dur": 1.5}]   # sfx.silence_music on the ACE wav
        pipeline._sfx(board, ep)
        with wave.open(str(ep / "music.wav")) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2)
        assert np.abs(x[int(5.2 * 48000):int(6.3 * 48000)]).max() == 0 and np.abs(x[48000:96000]).max() > 1000
    finally:
        m.stop(), shutil.rmtree(tmp, ignore_errors=True)


def test_backend_choices():
    tmp = Path(tempfile.mkdtemp())
    try:
        ep = tmp / "ep"
        ep.mkdir()
        cfg = _cfg(tmp, backend="procedural")
        board = _board()
        pipeline._music(cfg, board, ep)
        assert board["audio"]["music_engine"] == "procedural" and _wav(ep / "music.wav")[:3] == (48000, 2, 2)
        cfg["music"].pop("backend")                                                  # default = procedural
        board = _board()
        pipeline._music(cfg, board, ep)
        assert board["audio"]["music_engine"] == "procedural"
        board = _board()
        board["audio"] = {"music": "brand/music/track.wav"}
        pipeline._music(cfg, board, ep)
        assert board["audio"]["music_engine"] == "file" and board["audio"]["music"] == "brand/music/track.wav"
        cfg["music"]["generate"] = False
        board = _board()
        pipeline._music(cfg, board, ep)
        assert board["audio"] == {"music_engine": "none"}
        cfg["music"].update(generate=True, backend="acestep")
        try:
            pipeline._music(cfg, _board(), ep)
            raise AssertionError("a misspelt backend must stop the board")
        except ValueError as e:
            assert "acestep" in str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preflight_and_queue_timeout():
    m = Mock(version="0.38.0")
    try:
        c = comfy.Comfy(f"http://127.0.0.1:{m.port}", 60)
        p = c.preflight()                                                            # farm.py's no-argument call
        assert any("0.38.0 < 0.39.2" in s for s in p) and any("missing node SeedVR2Conditioning" in s for s in p), p
        assert c.preflight(ACE_NODES, min_version=None) == []
        assert any("missing node Foo" in s for s in c.preflight(["Foo"], min_version=None))
        assert c.has_models([CKPT, "nope.safetensors"]) == ["nope.safetensors"]
        assert c.node_info("TextEncodeAceStepAudio1.5")["input"]["required"]["language"][0] == "COMBO" and c.node_info("Nope") == {}
        m.pending = True                                                             # another job holds the GPU
        q = comfy.Comfy(f"http://127.0.0.1:{m.port}", 60, queue_timeout=1)
        try:
            q.run(music.ace_graph({}), Path(tempfile.mkdtemp()), "x")
            raise AssertionError("expected TimeoutError")
        except TimeoutError as e:
            assert "still queued" in str(e)
        assert m.deleted == [m.prompts[-1]["id"]] and m.interrupts == 0             # taken off the queue, nothing interrupted
    finally:
        m.stop()


def test_timeout_only_cancels_our_own_job():
    """A job that runs out of time is deleted while queued and interrupted by prompt_id only while it is the
    one running: a bare /interrupt would stop shorts-factory's job on the shared ComfyUI."""
    m = Mock()
    try:
        c = comfy.Comfy(f"http://127.0.0.1:{m.port}", 1)                         # no queue_timeout: 1 s job clock
        m.pending = True                                                             # still queued at the deadline
        try:
            c.run(music.ace_graph({}), Path(tempfile.mkdtemp()), "x")
            raise AssertionError("expected TimeoutError")
        except TimeoutError:
            pass
        assert m.deleted == [m.prompts[-1]["id"]] and m.interrupts == 0, (m.deleted, m.interrupt_bodies)
        m.pending, m.running = False, True                                           # ours is the running job
        try:
            c.run(music.ace_graph({}), Path(tempfile.mkdtemp()), "y")
            raise AssertionError("expected TimeoutError")
        except TimeoutError:
            pass
        assert m.interrupt_bodies == [{"prompt_id": m.prompts[-1]["id"]}], m.interrupt_bodies
    finally:
        m.stop()


def test_half_validated_graph_fails():
    """validate_prompt passes a graph when ANY output validates and ComfyUI then runs only those outputs, listing
    the rest in node_errors: run() must fail (and dequeue it) instead of returning the native still as the master."""
    m = Mock()
    try:
        m.node_errors = {"20": {"errors": [{"message": "Value not in list"}], "class_type": "SaveImage"}}
        try:
            comfy.Comfy(f"http://127.0.0.1:{m.port}", 60).run(music.ace_graph({}), Path(tempfile.mkdtemp()), "x")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "dropped part of the graph" in str(e) and "Value not in list" in str(e), e
        assert m.deleted == [m.prompts[-1]["id"]] and m.interrupts == 0
    finally:
        m.stop()


def test_unforeseen_ace_error_still_gets_procedural_bed():
    tmp = Path(tempfile.mkdtemp())
    real = music.ace_bed
    try:
        def boom(*a, **k):
            raise AttributeError("'list' object has no attribute 'get'")      # e.g. an odd /object_info reply
        music.ace_bed = boom
        ep = tmp / "ep"
        ep.mkdir()
        board = _board()
        _, said = pipeline._tee_call(pipeline._music, _cfg(tmp, 9), board, ep)
        assert board["audio"]["music_engine"] == "procedural" and Path(board["audio"]["music"]).exists()
        assert "AttributeError" in pipeline._reasons(said).get("music", ""), said
    finally:
        music.ace_bed = real
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
