# chequp-creator

The shorts-factory workflow, rebuilt for **CheqUp's Meta ads**. It turns CheqUp's own account, ad-library and
search data into storyboards, then into finished 9:16 / 4:5 / 1:1 / 16:9 videos rendered **in the CheqUp Digital
Design System**. All media generation (b-roll, voice, music) runs on **Dan's Windows RTX 5090 PC** with local open
models (see [Running on the PC](#running-on-the-pc)); the cloud VM only writes boards and code. mama is not used.

```
data/insights.yaml ──► concepts/*.json ──► cqf plan (claude -p) ──► concepts/variants/*.json
   (account, proof,        (base storyboards,      hook variants, auto-linted, re-asked on failure)
    search, rulings)        one per concept)
                                   │
                                   ▼
   cqf make:  lint ─► b-roll (Qwen-Image on CheqUp's ComfyUI) ─► voice (Chatterbox or Kokoro) ─► music ─► render ─► QA
             (FAIL stops)  stills → Claude-vision check → i2v clips      design-system motion graphics
                                                                        (Playwright + ffmpeg)
                                   │
                                   ▼
   cqf outbox:  out/outbox/<date>/  mp4s + posters + ads_sheet.csv (copy, CTA, UTM URL, HOLD/READY) + index.html
```

Nothing is pushed to Meta automatically. CheqUp has two upheld ASA rulings and CAP/GPhC correspondence on
file, so every file passes a human with a HOLD/READY column.

## What the data says to make (from `data/insights.yaml`)

| # | Concept | Why (CheqUp's own numbers) | Storyboard |
|---|---|---|---|
| 1 | The Method in 20 seconds | METHOD B video = best ad in the account: **£62.51 CPA, 2.58 ROAS** (campaign paused) | `concepts/method-20.json` |
| 2 | WeightWatchers, included | "ww / weight watchers" ~94k UK searches/mo; a compliant trust hook | `concepts/ww-included.json` |
| 3 | Your Coach in your pocket | SUPPORT B – GOALS £128 CPA; IG Stories £176 | `concepts/coach-pocket.json` |
| 4 | 6-second bumpers ×4 | Reels overlay **£82 CPA**, Audience Network £93 | `concepts/bumper-{a..d}.json` |
| 5 | What happens when you start | How-to-start / eligibility demand; Numan's top hook | `concepts/consult-flow.json` |
| 6 | Six months, properly supported | Price in only 1 of 178 ads; Voy and Numan run time-bound offers | `concepts/six-months.json` |
| 7–10 | Real reviews · What if it lasted · Not just willpower · 65+ cut | see `insights.yaml` | via `cqf plan` |

Also from the data: **women 35–64 drive 83% of purchases**. Native FB Reels convert at £470 CPA against £82 for
Reels overlay, so every 9:16 cut puts its hook on screen from frame 0. Exclude **FB in-stream** (£3,628 CPA).
Pure lifestyle awareness video spent £44k for 2 purchases, so don't make it. Retire the "METHOD B_Oral pill" ad.

## Two looks: what's live, and the design system

`reference/meta-account/` holds CheqUp's own ad videos, pulled read-only from the Meta ad account:
- 23 small copies and their contact sheets
- metrics: LUFS, cuts, colours, timings
- spend joined to each creative
- **`LOOK.md`**, the measured spec

It turns out none of the live video ads follow the design system. They all use one "glass card" template: a deep purple
gradient, frosted cards, a cyan CTA pill and heavy type. Method B (about £37k, about £155 CPA) runs on it. So the renderer
has two themes:

- **`"theme": "meta-live"`** reproduces that template from the measured values. It adds a hook from frame 0, uses
  verbatim reviews only, and drops the medicine-jar hero. See `concepts/live-*.json`.
- **The default** is the design system, described below. See `concepts/method-20.json` and the others.

The brand team should choose. The data favours the template, and the brand guidelines favour the system.

## The design system is the renderer

`render/` builds every frame from `brand/design-system/tokens/*.css`, the files extracted from
*CheqUp Digital Design System.fig*. Nothing is restyled by hand:

- Instrument Sans 400/500/600 (never 700) and DM Mono, self-hosted in `brand/fonts/`.
- Sand, Dark Sand, Yellow Wash, Lavender, Midnight and Purple grounds. Purple is the action colour, and Sunflower is used only on eyebrows on Midnight.
- No gradients, textures or drop shadows. Borders are inset rings.
- Motion uses the two system durations, 160ms and 320ms, on one curve: `cubic-bezier(0.2, 0.8, 0.2, 1)`.
- Frames are laid out at the 390-wide mobile spec ("Mobile is the design") and rendered at 1080 px.
- Components are drawn from the spec: Button, ProgressStep, RadioRow and ProgressBar (the eligibility check), Card, Tag, RatingStars, the relief-rectangle crop and the PageDivider transitions (only the three defined pairs).
- Each scene type maps to the system:
  - `hook`: Display headline
  - `steps`: ProgressStep
  - `checklist`: line icons
  - `ui`: the eligibility check, animated
  - `chat`: Coach messages, always labelled illustrative
  - `stat`: refuses to render without a source
  - `quote`: refuses without a `verbatim_ref`
  - `media`: full bleed, or the relief crop
  - `endcard`: wordmark, CTA and regulatory line
- **The CheqUp Loop is never used.** The design system says it is not for digital advertising.
- Captions are a functional Midnight panel, with the current word in Lavender. They appear only on media scenes that have no other on-screen text.

## Compliance gate (`cqf/compliance.py`, tests in `tests/`)

**FAIL** (the board never renders):
- Names or indirect references to a prescription-only medicine: brand or generic names, GLP-1, jab, pen, injection, pill, tablet, oral, dose.
- Speed, superlative or kg claims ("2 minute consultation", "most effective").
- Personal attributes or body-image language.
- B-roll prompts asking for needles, pens, pills, scales, measuring tapes, body close-ups or clinical staff.
- AI people presented as members, clinicians or reviewers.
- Unsourced stats, or testimonials without a verbatim reference.
- Exclamation marks, emoji, title-case headings, or "Learn more"-style CTAs.

**HOLD** (renders as a draft, and the outbox marks it HOLD):
- Prices: needs legal sign-off.
- WeightWatchers mentions: needs partner approval.
- Stats: needs the source and sample size on file.
- Reviews: needs the verbatim review held with permission.

To clear a HOLD, record the sign-off in the board, e.g. `"approvals": {"price": "J. Smith, Legal, 2026-10-09"}`.

## Running it

```bash
pip install -r requirements.txt          # pyyaml, requests
# voice (on the PC only: the cloud VM never installs or runs a voice model): Python ≤3.12, then
pip install "kokoro>=0.9.4" "transformers>=4.44" soundfile \
  "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
# upbeat VO engine (Chatterbox, MIT) in its own env: scripts/setup_local.sh or scripts/setup_pc.ps1
cd render && npm install && npx playwright install chromium && cd ..

python -m cqf doctor                      # tools, LLM, ComfyUI + b-roll models, voice envs, ACE-Step
python -m cqf lint concepts/              # compliance only
python -m cqf plan method-20 -n 4         # 4 hook variants (claude -p)
python -m cqf make method-20              # full pipeline, all formats
python -m cqf batch --top 5 --variants    # top-5 concepts + their variants
python -m cqf outbox                      # build today's upload folder
```

Useful flags:
- `--no-broll`: uses each scene's `fallback_src` (CheqUp's own photography) instead of GPU clips.
- `--no-voice`: renders silently.
- `--format 9x16`: renders one ratio only.
- `--use-5090`: also queues b-roll on the PC's 5090 (CheqUp's own ComfyUI; the GPU gate yields to shorts-factory).
- `--strict`: refuses HOLD boards.
- `--clips`: every AI b-roll shot becomes a Wan 2.2 image-to-video clip (slow).

`make` carries on past a board that fails: that board's `state.json` says `status: error` with the error, and `make` exits 1 at the end. `python -m cqf report [boards...]` writes `out/REPORT.md` and `out/report.json` from the episodes' `state.json`: per board the verdict, the files (format, seconds, MB), each b-roll shot (Qwen plate or CheqUp still, score, UNVERIFIED), the engine that read each VO line (Chatterbox, or Kokoro with the fallback reason), the music engine (ACE-Step or the procedural bed), AI video clips, errors and the outbox. Runs from before engine recording get their engines guessed.

## Running on the PC

All generation happens on Dan's Windows RTX 5090 PC with local models; the cloud VM only writes boards and code. One command does a whole run there, always with `config.pc.yaml` (mama stays disabled):

```powershell
cd C:\Users\white\chequp-creator
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pc_start.ps1 -Boards numan -Formats 9x16   # returns in ~30 s
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pc_wait.ps1 -Minutes 100                  # waits; never kills
```

- `scripts\pc_run.ps1` does the run itself (Windows PowerShell 5.1 compatible). Pre-flight checks the tools (prints `winget` commands), `.venv` (runs `setup_pc.ps1` if it's missing), CheqUp's own ComfyUI at 127.0.0.1:8288 (starts it with `scripts\comfy_cheq_start.ps1` if it isn't answering; exit 4 with the install command if it isn't installed) and its version (0.39.2 or newer), `cqf doctor` (voice env and reference clips, b-roll models, ACE-Step, the GPU gate) and, read-only, shorts-factory's queue (`GET /queue` on 8188). shorts-factory doesn't need pausing. After the run it frees the VRAM of CheqUp's ComfyUI only (`POST /free` to 8288). Then `make` (carrying on past a failed board), `outbox` and `report`, all in `out\logs\pc_run_<ts>.log`. It ends with `out\REPORT.md`, a review zip `out\logs\review_<ts>.zip` (REPORT.md, the log, ads sheet, posters, a 12-frame contact sheet per board, b-roll plate thumbnails, state files) and, last of all, the marker `out\logs\pc_run_<ts>.done` holding the exit code.
- Options: `-Boards` (groups, `all`, ids, paths or globs; default `numan-*`, else `made-simple-*`), `-Formats`, `-Strict`, `-SkipModels` (accept fallbacks), `-Draft` (don't stop on an old CheqUp ComfyUI or missing Qwen/SeedVR2 files; that b-roll falls back to CheqUp stills, and a board with no `fallback_src`, like the numan ones, fails instead), `-Clips`, `-PauseFactory` (optional now, off by default).
- Exit codes: 0 ok, 1 a board failed, 2 tools, 3 ComfyUI down, 4 CheqUp ComfyUI missing or too old, 5 Python env or config, 6 models, 7 unknown board/format, 8 fallbacks used, 9 run crashed, 10 already running (lock file `out\logs\pc_run.lock`), 11 still running (pc_wait), 12 GPU busy (the GPU gate waited `gpu_gate.max_wait_s` for shorts-factory and gave up; later boards skipped).
- `scripts\pc_start.ps1` starts pc_run detached in a hidden PowerShell and returns; `scripts\pc_wait.ps1` waits for the done marker. A Claude Code session on the PC follows `PUPPET.md`.

### CheqUp's own ComfyUI, next to shorts-factory's

shorts-factory renders on the same 5090 with its own ComfyUI (`C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI`, venv `C:\Users\white\ComfyUI-Factory-venv`, port 8188), which is older than the v0.39.2 CheqUp's graphs need (SeedVR2, FrameInterpolate, the nested SaveVideo codec, ACE-Step 1.5). Updating it would restart it and change its packages, so CheqUp has its own install side by side, and shorts-factory's is never modified, restarted, stopped or sent anything but `GET /queue` and `GET /system_stats`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1   # once; safe to re-run
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_start.ps1        # pc_run does this itself
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_stop.ps1         # only CheqUp's; refuses during a run
```

- `install_comfy_cheq_pc.ps1` (`-Dest C:\Users\white\ComfyUI-CheqUp -Tag v0.39.2 -Models <shared models> -Port 8288`) clones ComfyUI at the tag, makes `<Dest>\.venv` with uv (Python 3.12, torch/torchvision from the cu130 index when the driver already supports CUDA 13, which v0.39.2 wants for its fast fp8 kernels, else cu128; a re-run keeps the installed build, then `requirements.txt` with torch pinned), and writes `<Dest>\extra_model_paths.yaml` (`scripts/comfy_cheq_paths.py`). That file points every model folder type v0.39.2 knows (read from its `folder_paths.py`: checkpoints, diffusion_models/unet, text_encoders/clip, vae, loras, clip_vision, upscale_models, frame_interpolation, controlnet, embeddings, audio_encoders, model_patches and the rest) at shorts-factory's models folder, read-only (`is_default: false`, so nothing is ever saved there), and ComfyUI's own loader checks it. No model is copied or downloaded twice. It refuses a `-Dest` inside the factory's install or venv and port 8188.
- `comfy_cheq_start.ps1` starts `<Dest>\.venv\Scripts\python.exe main.py --listen 127.0.0.1 --port 8288 --extra-model-paths-config <Dest>\extra_model_paths.yaml` with its own `--output-directory`, `--input-directory`, `--temp-directory` and `--user-directory` under `<Dest>` and `--disable-auto-launch`, hidden, logging to `out\logs\comfy_cheq_<ts>.log`, and waits up to 180 s for `/system_stats`.
- `config.pc.yaml`: `comfy_cheq: {dir, port: 8288}`; the `pc-5090` farm machine points at 8288; cqf refuses to send a job to a `gpu_gate.yield_to` server.
- **GPU gate** (`cqf/gpu_gate.py`, `gpu_gate` in `config.pc.yaml`): before each b-roll still or clip prompt, each ACE-Step bed and before each board's Chatterbox lines, cqf waits while shorts-factory's ComfyUI has jobs running or queued (a ComfyUI that takes the connection but doesn't answer `GET /queue` within 5 s counts as busy; a refused connection as idle), had any in the last `quiet_s` (60 s, so CheqUp doesn't start in the gap between two of its jobs), or while nvidia-smi shows less free VRAM than the job needs (`need_gb`: still 22, clip 26, ace 12, chatterbox 6; what CheqUp's own ComfyUI holds counts as free). The first time it finds the GPU busy it gives back what CheqUp itself holds (`POST /free` to 8288, and it stops an idle Chatterbox worker). It polls every `poll_s` (30 s), prints `GPU busy: shorts-factory has 2 jobs queued (1 running); waiting (12 min so far)` every `log_every_s` (5 min), and after `max_wait_s` (6 h) raises `GpuBusy`: the rest of the run is skipped and pc_run exits 12. Each board's wait is in `state.json` (`gpu_wait_s`) and REPORT.md. While a b-roll still or clip runs, cqf keeps reading shorts-factory's queue (every `preempt_poll_s`, 10 s): if shorts-factory starts a job, CheqUp cancels its own job (on 8288, by prompt_id), frees its VRAM and retries it after the gate (up to 3 times, then the shot falls back; `preempt: false` turns this off). `config.yaml` (the cloud VM) has no gate: it's a no-op there.
- `scripts/update_comfy_pc.ps1` updates the shared install and is **not** for CheqUp (it refuses without `-ConfirmSharedInstall`).

## B-roll (`cqf/farm.py`)

- Each media scene's `broll` prompt becomes a Qwen-Image-2512 still (4 seeds, each upscaled 2x by SeedVR2 7B in the same graph, `cqf/graphs/qwen_still.json`) on CheqUp's own ComfyUI (port 8288, v0.39.2; `scripts/fetch_models_pc.ps1` fetches the models into the shared models folder). The GPU gate waits for shorts-factory before each one. `claude -p` vision-checks every candidate and the best passing one wins. With no vision check the plate is UNVERIFIED and the board stays on HOLD.
- `--clips` (pc_run `-Clips`) turns each chosen still into a Wan 2.2 image-to-video clip (`cqf/graphs/wan_i2v_clip.json`: 30 steps, SeedVR2 1.5x, FILM to 30 fps). Without it every plate is a still with a slow push-in.
- Every shot carries a people tag (`none`, `hands`, `hands_pair`, `back_view`, `distant`): AI plates never show a recognisable face. 16:9 gets its own plate; 4:5 and 1:1 are crops of the 9:16 one. Plates are cached in `out/broll/` by prompt and aspect, so boards share them.
- If a plate can't be made, the scene uses its `fallback_src` (CheqUp's own photography). The numan boards have none on purpose (every brand photo shows a face), so there a missing plate fails the board and REPORT.md names the scene.

## Voice (`cqf/voice.py`)

- **Chatterbox** (Resemble AI, original 0.5B English model, MIT) is the default engine (`voice.engine` in `presets/chequp_meta.yaml`). It copies the delivery of a synthetic reference clip in `brand/voice/` (machine-made, so no real person is cloned: see `brand/voice/README.md`). The active "bouncy" profile is exaggeration 0.85, cfg_weight 0.4, temperature 0.9, with inner pauses tightened to 0.15 s.
- It runs in its own env (`.venv-chatterbox`: Python 3.11, torch 2.7.1 cu128; `voice.chatterbox_python` in the config) as one long-lived worker (`cqf/tts_chatterbox.py`, JSON lines over stdin/stdout, with timeouts). Each line gets 3 takes. Whisper (faster-whisper) picks the take it hears best and supplies the caption word timings, and takes with pitch squeaks lose (praat-parselmouth). All takes stay in `out/episodes/<id>/vo/sNN_J.tN.wav` so one can be swapped by ear, and they're reused while the text, settings and reference are unchanged.
- **Kokoro** (`bf_emma,bf_alice` blend at 1.05) is the fallback. If the env, a reference clip or the worker is missing or fails, that line is read by Kokoro and a warning names it. Each line's engine goes into `board.audio.voice_engines` for REPORT.md.
- Dialogue: scene `"vo": [{"voice": "customer"|"chequp"|"announcer"|"male", "text": "..."}]`. A line's settings layer as preset defaults < `voice.cast[role]` < the board's `voices[role]` < `voice.styles[style]` (e.g. `"style": "tagline"`: a beat, then slower) < keys on the line itself.
- The VO stem goes `vo_raw.wav` → `voice.MASTER` (EQ, de-ess, 3:1 compression, -16 LUFS) → `vo.wav`. `render.mjs` then takes the full mix to -14 LUFS.
- On the PC, `scripts/setup_pc.ps1` builds both envs and runs `scripts/make_voice_refs.py`, which makes any missing reference clip. It refuses to run without a CUDA GPU (exit 4) unless given `--cpu`, so it can't synthesise on the cloud VM by accident. The cloud VM never runs a voice model.

## Music (`cqf/music.py`)

`music.backend` picks the bed for boards that don't bring their own track (`"audio": {"music": "path.wav"}`):

- **`ace_step`** (`config.pc.yaml`): ACE-Step 1.5 makes an instrumental bed on CheqUp's own ComfyUI (port 8288; the GPU gate waits for shorts-factory first) with `cqf/graphs/ace_step_bed.json`. The graph is the Comfy-Org template `audio_ace_step_1_5_checkpoint.json`: `ace_step_1.5_turbo_aio.safetensors`, shift 3.0, 8 steps, cfg 1.0, euler/simple, lyrics `[Instrumental]`, language `unknown`. `scripts/fetch_models_pc.ps1` downloads the checkpoint (9.3 GB) if it's missing.
  - Design-system boards get the warm style (acoustic, light piano, 90 bpm, D major). `theme: meta-live` boards get the bright one (modern pop, plucked synth, 100 bpm, G major).
  - Each bed is the board's length plus 1 s, rounded up to whole seconds (10 s minimum). Beds are cached in `out/music/` by style, length, seed and checkpoint. Change `music.seed` for a new take.
  - It uses the first ComfyUI in `farm.machines` that has the ACE-Step 1.5 nodes (v0.12+) and the checkpoint, never a `gpu_gate.yield_to` one. mama is off, so on the PC that's CheqUp's 127.0.0.1:8288. On ComfyUI older than v0.21 the language becomes `en`.
  - If ACE-Step can't run, the board gets the procedural bed and a printed warning.
- **`procedural`** (default, `config.yaml`): numpy pad chords, a plucked arpeggio and a light pulse. It needs no GPU or model.

The board records `audio.music_engine` (`ace_step`, `procedural`, `file` or `none`) for REPORT.md, and `cqf doctor` says whether ACE-Step is available. `sfx` silence cues work on either bed.

## mama (7× 3090) — two phases, because both jobs want every card

**Not used for CheqUp.** mama is `enabled: false` in `config.pc.yaml` (Dan, 8 Oct: "not mama, mama is getting hot"), `pc_run.ps1` refuses any config that enables a ComfyUI other than the PC's, and `config.pc.yaml` has no LLM fallback to mama's LM Studio. This section and `config.mama.yaml` are kept only as a record of the earlier setup.

Qwen3-235B in LM Studio takes all 7 cards (~145 GB). Wan 2.2 needs one card per ComfyUI server. Switch between them on mama:

```bash
bash mama/cq_phase.sh plan      # load Qwen on :1234 → run `cqf plan` / `cqf batch` planning
bash mama/cq_phase.sh render    # unload Qwen, kill orphan llama-servers, check umt5, start ComfyUI on 8189-8194
bash mama/cq_phase.sh status
```

A typical batch:
1. `plan`, then write variants (no Claude limits are used, since Qwen does the writing).
2. `render`, then `cqf batch` (b-roll on 6 cards in parallel).
3. `outbox`.

If the LLM is down, `cqf plan` falls back to `claude -p`. If the farm is down, `cqf make` falls back to the brand photography.

`mama/cq_phase.sh render` also checks the size of the umt5 text encoder. That file arrived truncated on Oct 2 and failed every card at CLIPLoader. Fix it with `bash ~/fetch_models.sh /home/dan/ComfyUI-Installs/ComfyUI/ComfyUI`.

Throughput (shorts-factory figures for the same models):
- **One Wan 2.2 b-roll shot** (2 stills plus a 5 s clip) takes about 2.5 GPU-minutes on the 5090, and roughly 2× that on a 3090.
- **A typical 20 s concept** has 1–2 shots.
- **Six 3090s** should therefore produce about **40–70 b-roll shots an hour**.
- **Motion graphics** render on CPU at about 1 min per 20 s video per format.
