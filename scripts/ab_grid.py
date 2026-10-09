# ab_grid.py: one-evening A/B of b-roll settings on the 5090 (reference/broll-ab-test.md).
# Run from the repo root on the PC:  .venv\Scripts\python.exe scripts\ab_grid.py
# then, with the best native still:  .venv\Scripts\python.exe scripts\ab_grid.py --winner out\ab\C_s22_native.png
import copy, json, sys, yaml
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pathlib import Path
from cqf import comfy as cq, gpu_gate
G = Path("cqf/graphs"); OUT = Path("out/ab")
PC = yaml.safe_load(open("config.pc.yaml", encoding="utf-8"))
# CheqUp's own ComfyUI (comfy_cheq.port, 8288), never shorts-factory's (gpu_gate.yield_to, 8188)
PORT = int((PC.get("comfy_cheq") or {}).get("port") or next((m["first_port"] for m in PC["farm"]["machines"] if m.get("name") == "pc-5090"), 8288))
URL = gpu_gate.cheq_url(PC) or f"http://127.0.0.1:{PORT}"
if gpu_gate.is_protected(PC, URL):
    sys.exit(f"{URL} is shorts-factory's ComfyUI (gpu_gate.yield_to): ab_grid only runs on CheqUp's own (comfy_cheq.port)")
SRV = cq.Comfy(URL, timeout=10800)                               # clip-sized ceiling for every job
SRV.yield_check = gpu_gate.yield_check(PC, SRV.url)              # cancels OUR job if shorts-factory starts one
STILL, CLIP, UP = (json.loads((G / f).read_text(encoding="utf-8")) for f in ("qwen_still.json", "wan_i2v_clip.json", "seedvr2_upscale.json"))
P = yaml.safe_load(open("presets/chequp_meta.yaml", encoding="utf-8"))["broll"]
m = PC["models"]["wan22"]
BRIEF = "hands preparing a packed lunch of fresh vegetables in a calm kitchen at dawn"
PROMPT1 = "Close, high-angle view of a woman's hands packing a lunch into a clear glass container on a pale oak worktop in a home kitchen on a bright morning. Her right hand lays sugar snap peas beside a row of six halved cherry tomatoes, a handful of sliced cucumber rounds, two halved boiled eggs and a scoop of green lentil salad; her left hand steadies the container. Beside it: a small wooden board with a halved lemon and a few sprigs of flat-leaf parsley. Only her hands and forearms are in frame, a grey sweatshirt cuff pushed back, short unpainted nails, a woman in her fifties. Low sun from a sash window on the left throws crisp shadows across the worktop. Vertical frame: the upper half is plain sunlit white tiled wall, the hands and container sit just below the centre, the oak worktop fills the bottom. 50mm lens at f/5.6."
MOTION1 = "Her right hand slowly sets one more sugar snap pea into the container, then both hands rest still on the worktop. The sunlight stays steady. Fixed camera."
NEW = PROMPT1 + " " + P["look_positive"]
NEG = P["negative"] + ", " + P["people_add_on"]["hands"]
SEEDS = [11, 22, 33, 44]
NUM = {"{{SEED}}": "seed", "{{WIDTH}}": "width", "{{HEIGHT}}": "height", "{{FRAMES}}": "frames"}
def fill(g, **v):
    assert v.get("width", 16) % 16 == 0 and v.get("height", 16) % 16 == 0, "sizes must be multiples of 16"
    assert v.get("frames", 1) % 4 == 1, "frames must be 4n+1"
    def walk(x):
        if isinstance(x, dict): return {k: walk(y) for k, y in x.items()}
        if isinstance(x, list): return [walk(y) for y in x]
        if isinstance(x, str):
            if x in NUM: return int(v[NUM[x]])
            for k, y in v.items(): x = x.replace("{{%s}}" % k.upper(), str(y))
        return x
    return walk(copy.deepcopy(g))
def png_w(p): return int.from_bytes(p.read_bytes()[16:20], "big")
def run(name, g):
    while True:                            # shorts-factory first, before and during each job (GpuBusy ends it)
        gpu_gate.wait_for_gpu(PC, kind="clip" if name.startswith("F") else "still", why=f"A/B {name}", url=SRV.url)
        try:
            fs = SRV.run(g, OUT, name); break
        except gpu_gate.Yielded as e:
            print(name, e); gpu_gate.release_own(PC, SRV.url)
    if len(fs) == 2 and all(f.suffix == ".png" for f in fs):      # still graph: native + 2x master
        fs = [f.replace(f.with_name(f"{name}_{t}.png")) for f, t in zip(sorted(fs, key=png_w), ("native", "master"))]
    if len(fs) == 2 and all(f.suffix == ".mp4" for f in fs):      # clip + raw: raw 720p/16 fps file is the smaller one
        fs = [f.replace(f.with_name(f"{name}{t}.mp4")) for f, t in zip(sorted(fs, key=lambda p: p.stat().st_size), ("_raw720", ""))]
    print(name, *fs); return fs
def wan_fixed(seed):                       # B: installed Wan t2v, no LoRA
    g = cq.still_graph(m, NEW, NEG, seed=seed, prefix="ab_B"); del g["3"], g["4"]
    g["5"]["inputs"].update(model=["1", 0], shift=1.0); g["6"]["inputs"].update(model=["2", 0], shift=1.0)
    for k, cfg, s, e in (("20", 4.0, 0, 4), ("21", 3.0, 4, 10000)):
        g[k]["inputs"].update(steps=30, cfg=cfg, sampler_name="res_multistep", scheduler="sgm_uniform", start_at_step=s, end_at_step=e)
    return g
def zimage():                              # D: Z-Image base, native output only (upscalers are compared in E)
    g = copy.deepcopy(STILL)
    for k in map(str, range(11, 21)): del g[k]
    g["1"]["inputs"]["unet_name"] = "z_image_bf16.safetensors"
    g["2"]["inputs"].update(clip_name="qwen_3_4b.safetensors", type="lumina2")
    g["3"]["inputs"]["vae_name"] = "ae.safetensors"; g["4"]["inputs"]["shift"] = 3.0
    g["8"]["inputs"].update(steps=40, cfg=4.0, sampler_name="res_multistep", scheduler="simple")
    return g
def with_raw(g, tag):                      # also save the 720p/16 fps Wan output, to judge SeedVR2 vs plain resizing
    g = copy.deepcopy(g)
    g["40"] = {"class_type": "CreateVideo", "inputs": {"images": ["14", 0], "fps": 16.0}}
    g["41"] = {"class_type": "SaveVideo", "inputs": dict(g["29"]["inputs"], video=["40", 0], filename_prefix=f"ab_{tag}_raw")}
    return g
def draft_clip():                          # F2: installed 4-step LoRA, same upscale + FILM tail
    g = copy.deepcopy(CLIP)
    for nid, src, lora in (("30", "1", m["i2v_lora_high"]), ("31", "2", m["i2v_lora_low"])):
        g[nid] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": [src, 0], "lora_name": lora, "strength_model": 1.0}}
    g["3"]["inputs"]["model"], g["4"]["inputs"]["model"] = ["30", 0], ["31", 0]
    g["12"]["inputs"].update(steps=4, cfg=1.0, end_at_step=2); g["13"]["inputs"].update(steps=4, cfg=1.0, start_at_step=2)
    return g
if "--winner" not in sys.argv:
    old = f"{BRIEF}. {P['look_legacy_ab_only']}"
    for s in SEEDS: run(f"A_s{s}", cq.still_graph(m, old, P["negative"], seed=s, prefix="ab_A"))
    for s in SEEDS: run(f"B_s{s}", wan_fixed(s))
    for s in SEEDS: run(f"C_s{s}", fill(STILL, prompt=NEW, negative=NEG, seed=s, width=928, height=1664, prefix="ab_C"))
    for s in SEEDS: run(f"D_s{s}", fill(zimage(), prompt=NEW, negative=NEG, seed=s, width=1088, height=1920, prefix="ab_D"))
else:                                      # --winner = the NATIVE file of the best still, e.g. out\ab\C_s22_native.png
    name = SRV.upload(Path(sys.argv[sys.argv.index("--winner") + 1]))
    e1 = run("E1_seedvr2_7b", fill(UP, image=name, seed=1, prefix="ab_E1"))[0]
    sharp = copy.deepcopy(UP); sharp["5"]["inputs"]["unet_name"] = "seedvr2_7b_sharp_fp16.safetensors"
    run("E2_seedvr2_7b_sharp", fill(sharp, image=name, seed=1, prefix="ab_E2"))
    master = SRV.upload(e1)
    neg = P["negative_wan"] + "，" + P["negative_i2v_extra"]
    for tag, g in (("F1_full", CLIP), ("F2_draft", draft_clip())):
        run(tag, fill(with_raw(g, tag), image=master, prompt=MOTION1, negative=neg, seed=7, width=720, height=1280, frames=81, prefix=f"ab_{tag}"))

