r"""Shared model folders for CheqUp's own ComfyUI (called by scripts/install_comfy_cheq_pc.ps1).

  python scripts/comfy_cheq_paths.py yaml    --comfy C:\Users\white\ComfyUI-CheqUp --models <shared models> [--out F]
  python scripts/comfy_cheq_paths.py check   --comfy C:\Users\white\ComfyUI-CheqUp --models <shared models> [--yaml F]
  python scripts/comfy_cheq_paths.py summary --comfy C:\Users\white\ComfyUI-CheqUp --models <shared models>

yaml     writes <comfy>/extra_model_paths.yaml: every model folder type this ComfyUI knows (read from its own
         folder_paths.py: each folder_names_and_paths entry under models_dir, e.g. diffusion_models = unet/ +
         diffusion_models/) points at the shared models folder (shorts-factory's), so no model file is copied or
         downloaded twice. custom_nodes and datasets are not shared. is_default is false: the install's own models
         folder stays first, so anything ComfyUI itself saves goes there, never into the shared folder.
check    loads that YAML with ComfyUI's own loader (utils/extra_config.py) and checks every type now searches the
         shared folder (after its own default folder) and custom_nodes is untouched. Imports only folder_paths and
         utils.extra_config (no torch); side effects stay inside <comfy>.
summary  one JSON line: ComfyUI version, torch / CUDA, shared folder types present and model file count.

Only reads the shared folder; writes only <comfy>/extra_model_paths.yaml. Stdlib only (check needs pyyaml, which
ComfyUI's requirements install)."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

# ComfyUI v0.39.2 folder_paths.py, model folder types under models_dir (the fallback if parsing finds nothing).
V0392 = {
    "checkpoints": ["checkpoints"], "configs": ["configs"], "loras": ["loras"], "vae": ["vae"],
    "text_encoders": ["text_encoders", "clip"], "diffusion_models": ["unet", "diffusion_models"],
    "clip_vision": ["clip_vision"], "style_models": ["style_models"], "embeddings": ["embeddings"],
    "diffusers": ["diffusers"], "vae_approx": ["vae_approx"], "controlnet": ["controlnet", "t2i_adapter"],
    "gligen": ["gligen"], "upscale_models": ["upscale_models"], "latent_upscale_models": ["latent_upscale_models"],
    "hypernetworks": ["hypernetworks"], "photomaker": ["photomaker"], "classifiers": ["classifiers"],
    "model_patches": ["model_patches"], "audio_encoders": ["audio_encoders"], "background_removal": ["background_removal"],
    "frame_interpolation": ["frame_interpolation"], "geometry_estimation": ["geometry_estimation"],
    "optical_flow": ["optical_flow"], "detection": ["detection"],
}
SECTION = "cheq_shared_models"


def folder_types(comfy: Path) -> tuple[dict[str, list[str]], str]:
    """{type: [subfolders]} for every folder_names_and_paths["type"] = ([os.path.join(models_dir, "sub"), ...], ...)
    in <comfy>/folder_paths.py (types under base_path, i.e. custom_nodes and datasets, are left out)."""
    src = Path(comfy) / "folder_paths.py"
    out: dict[str, list[str]] = {}
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return dict(V0392), "built-in v0.39.2 list (folder_paths.py unreadable)"
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript)):
            continue
        t = node.targets[0]
        if not (isinstance(t.value, ast.Name) and t.value.id == "folder_names_and_paths"):
            continue
        key = t.slice.value if isinstance(t.slice, ast.Constant) else getattr(getattr(t.slice, "value", None), "value", None)
        if not isinstance(key, str) or not isinstance(node.value, ast.Tuple) or not node.value.elts:
            continue
        paths = node.value.elts[0]
        if not isinstance(paths, ast.List) or not paths.elts:
            continue
        subs = []
        for call in paths.elts:
            if not (isinstance(call, ast.Call) and len(call.args) == 2 and isinstance(call.args[0], ast.Name)
                    and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str)):
                subs = []
                break
            if call.args[0].id != "models_dir":       # base_path: custom_nodes, datasets (not model folders)
                subs = []
                break
            subs.append(call.args[1].value)
        if subs:
            out[key] = subs
    if len(out) < 5:
        return dict(V0392), "built-in v0.39.2 list (folder_paths.py had no recognisable entries)"
    return out, f"{src}"


def _q(path: str) -> str:
    return "'" + str(path).replace("'", "''") + "'"         # YAML single quotes: backslashes stay literal


def yaml_text(types: dict[str, list[str]], models: str, comfy: str, source: str) -> str:
    lines = [
        f"# CheqUp's own ComfyUI ({comfy}) reads its models from the shared models folder below (shorts-factory's).",
        "# Written by scripts/install_comfy_cheq_pc.ps1 (scripts/comfy_cheq_paths.py yaml); re-running it rewrites this file.",
        f"# Folder types from {source}.",
        "# Read-only sharing: ComfyUI only lists and loads these files. is_default is false, so this install's own models",
        "# folder stays first and anything ComfyUI saves goes there, never here. custom_nodes and datasets are not shared.",
        f"{SECTION}:",
        f"  base_path: {_q(models)}",
        "  is_default: false",
    ]
    for k, subs in types.items():
        if len(subs) == 1:
            lines.append(f"  {k}: {subs[0]}/")
        else:
            lines.append(f"  {k}: |")
            lines += [f"    {s}/" for s in subs]
    return "\n".join(lines) + "\n"


def cmd_yaml(a) -> int:
    types, source = folder_types(Path(a.comfy))
    out = Path(a.out) if a.out else Path(a.comfy) / "extra_model_paths.yaml"
    text = yaml_text(types, os.path.normpath(a.models), os.path.normpath(a.comfy), source)
    out.write_text(text, encoding="utf-8")              # no BOM
    print(json.dumps({"ok": True, "yaml": str(out), "types": len(types), "source": source}))
    return 0


def cmd_check(a) -> int:
    comfy, models = os.path.abspath(a.comfy), os.path.abspath(a.models)
    yml = a.yaml or os.path.join(comfy, "extra_model_paths.yaml")
    sys.path.insert(0, comfy)
    os.chdir(comfy)
    import folder_paths                                   # noqa: E402  (ComfyUI's own; parses no argv by default)
    import utils.extra_config                             # noqa: E402
    before = {k: list(v[0]) for k, v in folder_paths.folder_names_and_paths.items()}
    utils.extra_config.load_extra_path_config(yml)
    types, _ = folder_types(Path(comfy))
    problems = []
    for k, subs in types.items():
        got = folder_paths.get_folder_paths(k)
        for sub in subs:
            want = os.path.normpath(os.path.join(models, sub))
            if want not in got:
                problems.append(f"{k}: {want} not searched")
        if before.get(k) and got[0] != before[k][0]:
            problems.append(f"{k}: the install's own folder is no longer first ({got[0]})")
    for k in ("custom_nodes", "datasets"):
        if k in before and folder_paths.get_folder_paths(k) != before[k]:
            problems.append(f"{k}: changed (must not be shared)")
    print(json.dumps({"ok": not problems, "types": len(types), "problems": problems[:20]}))
    return 0 if not problems else 1


def _version(comfy: Path) -> str | None:
    try:
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", (comfy / "comfyui_version.py").read_text(encoding="utf-8"))
        return m.group(1) if m else None
    except OSError:
        return None


def cmd_summary(a) -> int:
    comfy, models = Path(a.comfy), Path(a.models)
    types, _ = folder_types(comfy)
    present, files, size = [], 0, 0
    for k, subs in types.items():
        hit = False
        for sub in subs:
            d = models / sub
            if d.is_dir():
                for root, _dirs, names in os.walk(d):
                    for n in names:
                        if n.startswith("put_") or n.startswith("."):
                            continue                      # ComfyUI's placeholder files
                        files += 1
                        hit = True
                        try:
                            size += os.path.getsize(os.path.join(root, n))
                        except OSError:
                            pass
        if hit:
            present.append(k)
    info = {"comfyui_version": _version(comfy), "models": str(models), "models_exists": models.is_dir(),
            "folder_types": len(types), "types_with_files": present, "model_files": files, "model_gb": round(size / 2**30, 1)}
    try:
        import torch
        info.update(torch=torch.__version__, cuda=torch.version.cuda, cuda_available=bool(torch.cuda.is_available()))
        if info["cuda_available"]:
            info["device"] = torch.cuda.get_device_name(0)
    except Exception as e:  # noqa: BLE001 — summary must print whatever it can
        info.update(torch=None, cuda_available=False, torch_error=f"{type(e).__name__}: {e}"[:200])
    print(json.dumps(info))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="comfy_cheq_paths.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("yaml", "check", "summary"):
        p = sub.add_parser(name)
        p.add_argument("--comfy", required=True, help="CheqUp's ComfyUI folder (C:\\Users\\white\\ComfyUI-CheqUp)")
        p.add_argument("--models", required=True, help="the shared models folder (shorts-factory's)")
        if name == "yaml":
            p.add_argument("--out")
        if name == "check":
            p.add_argument("--yaml")
    a = ap.parse_args(argv)
    return {"yaml": cmd_yaml, "check": cmd_check, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
