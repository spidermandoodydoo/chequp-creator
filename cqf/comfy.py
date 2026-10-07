"""ComfyUI API client + Wan 2.2 graphs (the same models and 4-step lightx2v setup
shorts-factory uses): a single-frame t2v still, then i2v from the chosen still."""
from __future__ import annotations

import random
import time
import uuid
from pathlib import Path

import requests


class Comfy:
    def __init__(self, url: str, timeout: int = 1800):
        self.url, self.timeout, self.client_id = url.rstrip("/"), timeout, uuid.uuid4().hex

    def alive(self, t: float = 5) -> bool:
        try:
            return requests.get(f"{self.url}/system_stats", timeout=t).ok
        except requests.RequestException:
            return False

    def has_models(self, names: list[str]) -> list[str]:
        """Return the model files this server is missing."""
        info = requests.get(f"{self.url}/object_info", timeout=30).json()
        avail = set()
        for node in ("UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly"):
            for spec in info.get(node, {}).get("input", {}).get("required", {}).values():
                if isinstance(spec, list) and spec and isinstance(spec[0], list):
                    avail.update(spec[0])
        return [n for n in names if n not in avail]

    def upload(self, path: Path) -> str:
        with open(path, "rb") as f:
            r = requests.post(f"{self.url}/upload/image", files={"image": (path.name, f, "image/png")}, data={"overwrite": "true"}, timeout=60)
        r.raise_for_status()
        return r.json()["name"]

    def run(self, graph: dict, out_dir: Path, stem: str) -> list[Path]:
        r = requests.post(f"{self.url}/prompt", json={"prompt": graph, "client_id": self.client_id}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"ComfyUI rejected graph: {r.text[:500]}")
        pid, t0 = r.json()["prompt_id"], time.time()
        while time.time() - t0 < self.timeout:
            h = requests.get(f"{self.url}/history/{pid}", timeout=30).json().get(pid)
            if h:
                status = h.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI error: {status.get('messages', [])[-1:]}")
                if status.get("completed", True):
                    return self._download(h, out_dir, stem)
            time.sleep(2)
        requests.post(f"{self.url}/interrupt", timeout=10)
        raise TimeoutError(f"{self.url} job {pid} timed out")

    def _download(self, h: dict, out_dir: Path, stem: str) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for node_out in h.get("outputs", {}).values():
            for key in ("images", "videos", "gifs", "animated"):
                for f in node_out.get(key, []) if isinstance(node_out.get(key), list) else []:
                    if not isinstance(f, dict) or "filename" not in f:
                        continue
                    data = requests.get(f"{self.url}/view", params={"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}, timeout=120).content
                    p = out_dir / f"{stem}{'' if not files else '_' + str(len(files))}{Path(f['filename']).suffix}"
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


def model_files(m: dict) -> list[str]:
    return [m[k] for k in ("t2v_high", "t2v_low", "t2v_lora_high", "t2v_lora_low", "i2v_high", "i2v_low",
                           "i2v_lora_high", "i2v_lora_low", "text_encoder", "vae")]
