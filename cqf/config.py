"""Config + preset loading. Paths in config.yaml are relative to the project root."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(path: str | Path | None = None) -> dict:
    cfg = yaml.safe_load((ROOT / (path or "config.yaml")).read_text(encoding="utf-8"))
    cfg["_preset"] = yaml.safe_load((ROOT / cfg["preset"]).read_text(encoding="utf-8"))
    cfg["_insights"] = yaml.safe_load((ROOT / cfg["data_dir"] / "insights.yaml").read_text(encoding="utf-8"))
    return cfg


def path(cfg: dict, key: str) -> Path:
    p = ROOT / cfg[key]
    p.mkdir(parents=True, exist_ok=True)
    return p
