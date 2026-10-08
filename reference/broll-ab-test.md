# A/B grid: one brief, one evening on the 5090

**Brief:** the packed lunch that failed.
- Variants B–D use rewritten prompt #1 plus `look_positive`, with the `hands` negative add-on.
- Variant A uses today's brief with the old look string, now stored as `look_legacy_ab_only`.

**Seeds:** 11, 22, 33 and 44 for every still variant.

## Variants

| ID | What it tests | Settings | Output | Time (estimate) |
|---|---|---|---|---|
| **A** | Control: today's pipeline, unchanged | `comfy.still_graph`: Wan t2v single frame, 4-step LoRA, cfg 1, shift 8, 720x1280, old prompt | 4 stills | ~2 min |
| **B** | Today's models done properly, with no download | Same Wan t2v without the LoRA: shift 1, 30 steps, cfg 4.0 then 3.0, res_multistep/sgm_uniform, switch at step 4 | 4 stills | ~8–15 min |
| **C** | Primary: Qwen-Image-2512 | `qwen_still.json` at 928x1664, 50 steps, cfg 4, shift 3.1, with SeedVR2 inside | 4 natives + 4 masters | ~15–30 min |
| **D** | Fallback: Z-Image base | Same graph with the Z-Image files and the SeedVR2 tail removed: 1088x1920, 40 steps, cfg 4, res_multistep, shift 3 | 4 stills | ~5–10 min |
| **E** | Upscaler | The winning native → SeedVR2 7B fp16 (E1) vs 7B sharp fp16 (E2), both 2x | 2 masters | ~3–5 min |
| **F** | Motion | E1 → full clip graph (F1: 30 steps, cfg 3.5) vs the installed 4-step LoRA draft (F2: shift 5, 2/2, cfg 1). Same seed, same SeedVR2 1.5x + FILM tail. Each also saves its raw 720p 16 fps Wan output | 2 clips + 2 raw | F1 ~30–75 min, F2 ~10–25 min |

**Total:** about 1.5–3 hours, plus a few minutes for each model's first load. These are estimates, not measured on your PC. The F1 run is also the "time one real clip" check: set `farm.clip_timeout_s` from it.

## Setup
1. Update ComfyUI to v0.39.2, and start it with `--disable-metadata`.
2. Download the core files, plus `seedvr2_7b_sharp_fp16`, `z_image_bf16`, `qwen_3_4b` and `ae`.
3. Save the three graphs to `cqf/graphs/`:
   - `qwen_still.json` (= `still_graph_json`);
   - `wan_i2v_clip.json` (= `clip_graph_json`);
   - `seedvr2_upscale.json` (the standalone graph in the stack, section 4).
4. Replace the preset's `broll` block with the one in the prompt rules. That renames `look` to `look_legacy_ab_only` and adds `people_add_on`, `negative_wan` and `negative_i2v_extra`.
5. Save the script below as `ab_grid.py` in the repo root. Paste prompt #1's still and motion text in place of the two `<…>` placeholders.

## Running it
- **Stage 1:** `.venv\Scripts\python.exe ab_grid.py` makes 16 stills. C writes `C_sNN_native.png` and `C_sNN_master.png`.
- **Stage 2:** `.venv\Scripts\python.exe ab_grid.py --winner out\ab\C_s22_native.png` makes the upscales and clips. Pass the **native** file of the best still.

**Checked:** I ran all 20 jobs through ComfyUI's validator on v0.39.2 and on master d91ed5f5b, with the real `cqf/comfy.py`, a stand-in server and dummy models. All 20 passed. I haven't run them on a GPU.

The script is `scripts/ab_grid.py`.

## How to compare (about 30 minutes)
1. **Look at the stills blind.** Make a contact sheet of the 16 stills at native size, using the `_native` files for C, with the labels hidden. View each two ways:
   - at 100%, cropped to the hands and vegetables;
   - at phone size, with a mock card drawn from 10.7% to 43% of the height.
2. **Score each still from 1 to 5 on:**
   - **Brief match:** hands only, the named vegetables, no face.
   - **Detail:** vegetable edges, nails, wood grain.
   - **Hands:** five fingers, a believable grip, a believable age (40s to 60s).
   - **Light and colour against the brand photos:** warm directional sun, clean whites, no haze or speckle.
   - **Clear card zone:** the upper half is plain.
   - **Would Sophie sign it off?**
3. **Run the new vision check on all 16.** Note any still it passes that you'd reject, and the reverse. Tighten the prompt wherever it disagrees with you.
4. **Decide:**
   - **Stills model:** pick the highest-scoring variant. If C and D are within 2 points, take C, because it follows the prompt more closely. If B beats C, skip the downloads; I'd be surprised.
   - **Upscaler:** compare C's master, E1 and E2 at 100%. Use E1 (plain 7B) unless E2 adds real detail without crunchy edges or halos.
   - **Motion:** play these at phone size:
     - F1, against `F1_full_raw720.mp4` upscaled by the renderer. Does SeedVR2 earn its 5–15 minutes?
     - F1, against F2.
     - F1, against the E1 still under a 1.00→1.05 push.
   - **Warping check:** look at frames at 0%, 50% and 100% for warping hands.
   - **Then:**
     - If F1 is clean and better than the pushed still, use image-to-video for environmental-motion shots. Otherwise use the still with a push everywhere.
     - If F1's SeedVR2 output adds texture to the hands that wasn't there, switch the clip graph's colour correction to `wavelet`, or drop to SeedVR2 3B (`seedvr2_3b_fp16`), and re-test.
     - F2 tells you whether draft mode is good enough for animatics.
