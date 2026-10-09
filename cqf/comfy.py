"""ComfyUI API client + Wan 2.2 graphs (the same models and 4-step lightx2v setup
shorts-factory uses): a single-frame t2v still, then i2v from the chosen still."""
from __future__ import annotations

import json
import random
import time
import uuid
from pathlib import Path

import requests

from . import gpu_gate

GRAPHS = Path(__file__).resolve().parent / "graphs"
MIN_VERSION = (0, 39, 2)            # SeedVR2 + FrameInterpolate + nested SaveVideo codec keys
REQUIRED_NODES = ["SeedVR2Conditioning", "SeedVR2TemporalChunk", "FrameInterpolate", "ResizeImageMaskNode", "SaveVideo"]
_NUM = {"{{SEED}}": "seed", "{{WIDTH}}": "width", "{{HEIGHT}}": "height", "{{FRAMES}}": "frames",
        "{{SECONDS}}": "seconds", "{{BPM}}": "bpm"}           # SECONDS/BPM: ace_step_bed.json


def load_graph(name: str) -> str:
    return (GRAPHS / name).read_text(encoding="utf-8")


def fill(graph_json: str, **v) -> dict:
    """Fill {{PLACEHOLDERS}} after json.loads (never by text-replacing raw JSON).
    Numeric placeholders are quoted strings in the file and become ints here."""
    assert v.get("width", 16) % 16 == 0 and v.get("height", 16) % 16 == 0, "sizes must be multiples of 16"
    assert v.get("frames", 1) % 4 == 1, "frames must be 4n+1"

    def walk(x):
        if isinstance(x, dict):
            return {k: walk(y) for k, y in x.items()}
        if isinstance(x, list):
            return [walk(y) for y in x]
        if isinstance(x, str):
            if x in _NUM:
                return int(v[_NUM[x]])
            for k, y in v.items():
                x = x.replace("{{%s}}" % k.upper(), str(y))
        return x
    return walk(json.loads(graph_json))


def graph_models(graph: dict) -> list[str]:
    """Every model filename a graph loads (for the per-server model check)."""
    keys = ("unet_name", "clip_name", "vae_name", "lora_name", "model_name", "ckpt_name")
    return sorted({n["inputs"][k] for n in graph.values() for k in keys
                   if isinstance(n.get("inputs", {}).get(k), str) and "{{" not in n["inputs"][k]})


def combo_options(spec) -> list[str]:
    """The choices of one /object_info input spec: classic [[...], {...}] or V3 ["COMBO", {"options": [...]}]."""
    if isinstance(spec, list) and spec:
        if isinstance(spec[0], list):
            return [str(o) for o in spec[0]]
        if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
            return [str(o) for o in spec[1].get("options", [])]
    return []


class Comfy:
    def __init__(self, url: str, timeout: int = 1800, queue_timeout: float | None = None):
        """timeout bounds one job. queue_timeout (None = no separate bound) is how long a job may wait
        behind other work on this ComfyUI before it is taken off the queue; with it set, the job clock starts
        when ComfyUI picks the job up. (On the PC CheqUp has its own ComfyUI, so nothing else queues there;
        cqf.gpu_gate waits for shorts-factory's ComfyUI before a job is submitted.)"""
        self.url, self.timeout, self.client_id = url.rstrip("/"), timeout, uuid.uuid4().hex
        self.queue_timeout = queue_timeout
        self.yield_check = None     # gpu_gate.yield_check(): run() cancels its own job when shorts-factory starts one

    def alive(self, t: float = 5) -> bool:
        try:
            return requests.get(f"{self.url}/system_stats", timeout=t).ok
        except requests.RequestException:
            return False

    def node_info(self, node: str) -> dict:
        """/object_info/<node> for one node class ({} when this ComfyUI doesn't have it)."""
        return requests.get(f"{self.url}/object_info/{node}", timeout=15).json().get(node, {})

    def has_models(self, names: list[str]) -> list[str]:
        """Return the model files this server is missing."""
        info = requests.get(f"{self.url}/object_info", timeout=30).json()
        avail = set()
        for node in ("UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly", "FrameInterpolationModelLoader",
                     "CheckpointLoaderSimple"):
            for spec in info.get(node, {}).get("input", {}).get("required", {}).values():
                avail.update(combo_options(spec))          # classic and V3 node schemas
        return [n for n in names if n not in avail]

    def preflight(self, nodes: list[str] | None = None, min_version: tuple | None = MIN_VERSION) -> list[str]:
        """Problems that would make a graph fail on this server (empty = good). No arguments = the
        b-roll quality graphs (REQUIRED_NODES, ComfyUI >= MIN_VERSION); min_version=None skips the version gate."""
        problems = []
        if min_version:
            try:
                ver = requests.get(f"{self.url}/system_stats", timeout=10).json().get("system", {}).get("comfyui_version", "0")
                nums = tuple(int(p) for p in str(ver).lstrip("v").split("-")[0].split(".")[:3] if p.isdigit())
                if nums < tuple(min_version):
                    problems.append(f"ComfyUI {ver} < {'.'.join(map(str, min_version))} (update CheqUp's ComfyUI: scripts/install_comfy_cheq_pc.ps1)")
            except (requests.RequestException, ValueError) as e:
                problems.append(f"system_stats: {e}")
        for node in REQUIRED_NODES if nodes is None else nodes:
            try:
                if node not in requests.get(f"{self.url}/object_info/{node}", timeout=15).json():
                    problems.append(f"missing node {node} (update CheqUp's ComfyUI: scripts/install_comfy_cheq_pc.ps1)")
            except requests.RequestException as e:
                problems.append(f"object_info/{node}: {e}")
        return problems

    def _post(self, path: str, **kw):
        """Every POST this client sends (prompt, upload, cancel) goes through here: never to shorts-factory's
        ComfyUI (gpu_gate.ALWAYS_PROTECTED, 127.0.0.1:8188), whatever config built this client."""
        if gpu_gate.is_protected(None, self.url):
            raise RuntimeError(f"{self.url} is shorts-factory's ComfyUI: CheqUp never sends it anything (no POST {path})")
        return requests.post(f"{self.url}{path}", **kw)

    def _pending(self, pid: str) -> bool:
        q = requests.get(f"{self.url}/queue", timeout=15).json()
        return any(len(item) > 1 and item[1] == pid for item in q.get("queue_pending", []))

    def upload(self, path: Path) -> str:
        with open(path, "rb") as f:
            r = self._post("/upload/image", files={"image": (path.name, f, "image/png")}, data={"overwrite": "true"}, timeout=60)
        r.raise_for_status()
        return r.json()["name"]

    def run(self, graph: dict, out_dir: Path, stem: str, timeout: int | None = None) -> list[Path]:
        limit = timeout or self.timeout
        r = self._post("/prompt", json={"prompt": graph, "client_id": self.client_id}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"ComfyUI rejected graph: {r.text[:500]}")
        j = r.json()
        pid, t0 = j["prompt_id"], time.time()
        if j.get("node_errors"):   # ComfyUI queues the outputs that validated and silently drops the rest (e.g. the
            self._cancel(pid)      # SeedVR2 master of qwen_still.json): a half graph must fail, not pass off its native
            raise RuntimeError(f"ComfyUI dropped part of the graph: {json.dumps(j['node_errors'])[:500]}")
        waiting = self.queue_timeout is not None
        while waiting or time.time() - t0 < limit:
            h = requests.get(f"{self.url}/history/{pid}", timeout=30).json().get(pid)
            if h:
                status = h.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI error: {status.get('messages', [])[-1:]}")
                if status.get("completed", True):
                    return self._download(h, out_dir, stem)
            elif waiting and not self._pending(pid):
                waiting, t0 = False, time.time()           # picked up: the job clock starts now
            elif waiting and time.time() - t0 > self.queue_timeout:
                self._cancel(pid)                          # taken off the queue; the other job is never interrupted
                raise TimeoutError(f"{self.url} job {pid} still queued after {self.queue_timeout} s (another job holds this ComfyUI)")
            why = self.yield_check() if self.yield_check else None
            if why:                                        # the GPU is shorts-factory's first: stop OUR job, never its
                self._cancel(pid)
                raise gpu_gate.Yielded(f"{why}: CheqUp cancelled its own job {pid} on {self.url} to give it the GPU")
            time.sleep(2)
        self._cancel(pid)
        raise TimeoutError(f"{self.url} job {pid} timed out")

    def _cancel(self, pid: str):
        """Stop only our own job: delete it while it is queued, interrupt it (by prompt_id) only while it is
        the one running. A bare POST /interrupt stops whatever ComfyUI is running, and the PC's ComfyUI is
        shared with shorts-factory."""
        try:
            q = requests.get(f"{self.url}/queue", timeout=15).json()
            if any(len(i) > 1 and i[1] == pid for i in q.get("queue_running", [])):
                self._post("/interrupt", json={"prompt_id": pid}, timeout=10)
            else:
                self._post("/queue", json={"delete": [pid]}, timeout=10)
        except (requests.RequestException, ValueError):
            pass

    def _download(self, h: dict, out_dir: Path, stem: str) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for node_id, node_out in h.get("outputs", {}).items():
            for key in ("images", "videos", "gifs", "animated", "audio"):     # audio: SaveAudio (ace_step_bed.json)
                for f in node_out.get(key, []) if isinstance(node_out.get(key), list) else []:
                    if not isinstance(f, dict) or "filename" not in f:
                        continue
                    data = requests.get(f"{self.url}/view", params={"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}, timeout=120).content
                    p = out_dir / f"{stem}_n{node_id}{Path(f['filename']).suffix}"   # _n10 native, _n20 master
                    p.write_bytes(data)
                    files.append(p)
        if not files:
            raise RuntimeError("ComfyUI finished without output files")
        return files


def _two_pass(m: dict, model_hi: str, model_lo: str, lora_hi: str, lora_lo: str, pos: str, neg: str, latent_ref: list, seed: int) -> dict:
    """Shared Wan 2.2 MoE graph: high-noise expert for steps 0-2, low-noise for 2-4."""
    s, half = m["steps"], m["steps"] // 2
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model_hi, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": model_lo, "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": lora_hi, "strength_model": 1.0}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": lora_lo, "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": m["shift"]}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": m["shift"]}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["text_encoder"], "type": "wan", "device": "default"}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": pos}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": neg}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
        "20": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["5", 0], "add_noise": "enable", "noise_seed": seed, "steps": s, "cfg": 1.0,
               "sampler_name": "euler", "scheduler": "simple", "positive": None, "negative": None, "latent_image": latent_ref,
               "start_at_step": 0, "end_at_step": half, "return_with_leftover_noise": "enable"}},
        "21": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["6", 0], "add_noise": "disable", "noise_seed": seed, "steps": s, "cfg": 1.0,
               "sampler_name": "euler", "scheduler": "simple", "positive": None, "negative": None, "latent_image": ["20", 0],
               "start_at_step": half, "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "22": {"class_type": "VAEDecode", "inputs": {"samples": ["21", 0], "vae": ["10", 0]}},
    }


def still_graph(m: dict, prompt: str, negative: str, seed: int | None = None, prefix: str = "cq_still") -> dict:
    seed = seed if seed is not None else random.randint(0, 2**48)
    g = _two_pass(m, m["t2v_high"], m["t2v_low"], m["t2v_lora_high"], m["t2v_lora_low"], prompt, negative, ["11", 0], seed)
    g["11"] = {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": m["width"], "height": m["height"], "length": 1, "batch_size": 1}}
    for k in ("20", "21"):
        g[k]["inputs"]["positive"], g[k]["inputs"]["negative"] = ["8", 0], ["9", 0]
    g["30"] = {"class_type": "SaveImage", "inputs": {"images": ["22", 0], "filename_prefix": prefix}}
    return g


def clip_graph(m: dict, image_name: str, prompt: str, negative: str, seed: int | None = None, prefix: str = "cq_clip") -> dict:
    seed = seed if seed is not None else random.randint(0, 2**48)
    g = _two_pass(m, m["i2v_high"], m["i2v_low"], m["i2v_lora_high"], m["i2v_lora_low"], prompt, negative, ["12", 2], seed)
    g["11"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    g["12"] = {"class_type": "WanImageToVideo", "inputs": {"positive": ["8", 0], "negative": ["9", 0], "vae": ["10", 0], "start_image": ["11", 0],
               "width": m["width"], "height": m["height"], "length": m["frames"], "batch_size": 1}}
    for k in ("20", "21"):
        g[k]["inputs"]["positive"], g[k]["inputs"]["negative"] = ["12", 0], ["12", 1]
    g["30"] = {"class_type": "CreateVideo", "inputs": {"images": ["22", 0], "fps": float(m["fps"])}}
    g["31"] = {"class_type": "SaveVideo", "inputs": {"video": ["30", 0], "filename_prefix": prefix, "format": "mp4", "codec": "h264"}}
    return g


def model_files(m: dict) -> list[str]:   # legacy Wan-still/draft path only
    return [m[k] for k in ("t2v_high", "t2v_low", "t2v_lora_high", "t2v_lora_low", "i2v_high", "i2v_low",
                           "i2v_lora_high", "i2v_lora_low", "text_encoder", "vae")]
