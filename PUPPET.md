# PUPPET.md: instructions for the Claude Code session on Dan's PC

Read this when the cloud session (or Dan) messages you about a CheqUp render.

## Who does what

- **The cloud session** has no GPU. It writes boards and code, sends you a message, and reads your reply. It never makes media.
- **You** are Claude Code on Dan's Windows RTX 5090 PC (user `white`). You run the render here with local models and report back what happened. You are the hands: run, check, report. Don't redesign anything, and don't edit cqf's code, boards or configs.
- **Every model runs on this PC.** That is the point of the setup:
  - b-roll: CheqUp's own ComfyUI (port 8288), with Qwen-Image 2512 + SeedVR2 stills. Wan 2.2 image-to-video clips only with `-Clips`.
  - voice: Chatterbox (Kokoro only as a reported fallback)
  - music: ACE-Step 1.5 on the same CheqUp ComfyUI (cqf's procedural bed only as a reported fallback)
  - render: Playwright + ffmpeg
- **shorts-factory shares this GPU and keeps rendering.** It has its own ComfyUI (port 8188). CheqUp never touches that ComfyUI, its venv or its files, and only ever reads its queue (`GET /queue`, plus `GET /system_stats` while VRAM is short). Before every CheqUp GPU job, cqf's GPU gate (`gpu_gate` in `config.pc.yaml`) waits while shorts-factory has jobs running or queued, or while too little VRAM is free, and if shorts-factory starts a job while a CheqUp b-roll job runs, CheqUp cancels its own job and redoes it later. So a CheqUp run can take longer than its own work; REPORT.md says how long it waited. shorts-factory doesn't need pausing.
- cqf itself calls `claude -p` on this PC to vision-check each b-roll still. That only looks at the images and generates nothing, so it is expected.

## Where things are

| What | Where |
|---|---|
| cqf code (run everything from here) | `C:\Users\white\chequp-creator` |
| Python envs | `.venv` (Python 3.12, CUDA torch, Kokoro), `.venv-chatterbox` (Python 3.11, Chatterbox) |
| CheqUp's ComfyUI (ours) | `http://127.0.0.1:8288` · `C:\Users\white\ComfyUI-CheqUp` (ComfyUI v0.39.2, own venv `.venv`) · log `out\logs\comfy_cheq_<ts>.log` · models read from shorts-factory's models folder through `C:\Users\white\ComfyUI-CheqUp\extra_model_paths.yaml` |
| shorts-factory's ComfyUI (**not ours: never touch**) | `http://127.0.0.1:8188` · `C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI` · venv `C:\Users\white\ComfyUI-Factory-venv` · models `C:\Users\white\ComfyUI-Shared\models` (shared read-only with CheqUp's; all CheqUp models were downloaded there on 9 Oct) |
| shorts-factory (shares this GPU) | `C:\Users\white\heatmap\shorts-factory` (`shorts_factory.dir` in `config.pc.yaml`). Paused while `data\STOP` exists there (not needed for CheqUp any more). |
| Outputs | `out\episodes\<board>\`, `out\outbox\<date>\`, `out\REPORT.md` (+ `report.json`), `out\logs\` |

## Running a render

A run takes roughly 1-3 hours (not yet timed on this PC). Your shell tool stops a foreground command after 10 minutes, and a `run_in_background` command after 30 minutes by default (2 hours at most). So **never run `pc_run.ps1` under the shell tool**. Start it detached and wait for it separately:

Write every command with forward slashes, exactly as below: they work the same from your shell tool (Git Bash) and from PowerShell. In Git Bash an unquoted backslash is eaten, so `scripts\pc_wait.ps1` would become `scriptspc_wait.ps1`.

1. **Start** (returns within about 30 seconds):
   ```bash
   cd C:/Users/white/chequp-creator
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pc_start.ps1 [options]
   ```
   It runs `scripts/pc_run.ps1` in its own hidden PowerShell and prints the run id (`<ts>`), PID, log and done marker, then watches the first 30 seconds, where pre-flight problems show up. Note the run id. pc_start's exit code:
   - **0**: started and still running. Go to step 2.
   - **10**: a run is already going. Don't start another; just wait for it (step 2).
   - **9**: pc_run died at start without a done marker. Reply with what pc_start printed.
   - **Anything else**: the run already ended in pre-flight with that code (table below). Nothing was rendered.
2. **Wait**: run this with `run_in_background` and `timeout: 7200000`:
   ```bash
   cd C:/Users/white/chequp-creator && powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pc_wait.ps1 -Minutes 100
   ```
   You are re-invoked when it returns:
   - **11**: still running after 100 minutes. Run `pc_wait` again.
   - **9**: the run's process is gone and it never wrote its `.done` marker. It was killed or crashed, or the PC lost power. `out\REPORT.md` is **not** its result. Reply with the last 80 log lines (pc_wait prints them).
   - **Anything else** is the run's own exit code (table below).

   Stopping `pc_wait` never stops the run, and pc_wait never kills anything.
3. **Done marker.** The very last thing a run does is write `out\logs\pc_run_<ts>.done`, a JSON file with `exit`, `finished`, `report`, `log` and `pack`. While it's missing, the run hasn't ended. To look in by hand: `tail -n 30 out/logs/pc_run_<ts>.log` (PowerShell: `Get-Content out/logs/pc_run_<ts>.log -Tail 30`).
4. **One run at a time.** `out\logs\pc_run.lock` names the running run; pc_start and pc_run refuse a second one (exit 10). Never kill a running pc_run: CheqUp jobs it has queued in ComfyUI would keep running anyway. If it must stop, ask Dan.

The first run also downloads open voice weights from Hugging Face: Kokoro, Chatterbox and Whisper. That's expected and happens once.

### CheqUp's own ComfyUI

pc_run starts CheqUp's ComfyUI itself when port 8288 isn't answering, and checks it is v0.39.2 or newer. After the run it frees that ComfyUI's VRAM (`POST /free` to 8288 only), so shorts-factory gets the memory back. You may run these yourself, because none of them touches shorts-factory's ComfyUI, its venv or port 8188:

- **Install or repair** (first time, or after exit 4). It takes a while: torch is a few GB. Re-running it is safe.
  ```bash
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1
  ```
  It refuses to run while CheqUp's ComfyUI is running: stop that first (below). It ends with a summary: version, torch + CUDA, shared model folders. Its exit codes: 0 ok, 2 git/uv/Python missing (it prints the `winget` command), 3 refused, 4 git failed, 5 install failed, 6 torch sees no CUDA GPU (never update or reinstall the NVIDIA driver for it: that resets the GPU under shorts-factory; tell Dan, or retry with `-TorchIndex https://download.pytorch.org/whl/cu128` if it picked cu130), 7 model-folder check failed. ComfyUI logging "you need pytorch with cu130" means it got cu128 because the driver predates CUDA 13: it still renders, just without the fast fp8 kernels.
- **Start** (pc_run does this itself): `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_start.ps1`. Exit 0 = running, 3 = didn't come up (it prints the log tail), 4 = not installed.
- **Stop** (only when no CheqUp run is going, e.g. before re-installing or after new model files): `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_stop.ps1`. It stops only processes started from `C:\Users\white\ComfyUI-CheqUp`, and refuses (exit 10) while a run is going.

### Options (pass them to `pc_start.ps1`)

| Option | Meaning | When |
|---|---|---|
| `-Boards numan,made-simple` | Groups (`concepts\<group>-*.json`), `all`, board ids, paths or globs; commas allowed. Default: `concepts\numan-*.json`, or `concepts\made-simple-*.json` if there are no numan boards. | As the message says |
| `-Formats 9x16` | `9x16`, `4x5`, `1x1`, `16x9`. Default: each board's own formats. | As the message says |
| `-Strict` | Refuse HOLD boards instead of rendering drafts. | Only when the message asks |
| `-Clips` | Every AI b-roll shot becomes a Wan 2.2 image-to-video clip made from its chosen still, instead of a still with a slow push-in. Much slower, and needs the Wan 2.2 i2v files. Without it no AI video clip is made: every b-roll scene is a still, and REPORT.md says `AI video clips: 0`. | Only when the message asks (Dan's call) |
| `-SkipModels` | Accept fallbacks instead of stopping: CheqUp's own stills for b-roll, the procedural music bed, Kokoro for Chatterbox lines. The run then ends with exit 8 if anything fell back. The numan boards have no fallback stills on purpose, so a numan board whose b-roll can't be made fails instead (exit 1; REPORT.md's Errors name the scene). | Only when the message asks |
| `-Draft` | Don't stop on a CheqUp ComfyUI older than 0.39.2 or on missing Qwen/SeedVR2 files. cqf has no Wan-only still path yet, so b-roll that needs those uses CheqUp's own stills (REPORT.md says so; exit 8), and boards without fallback stills (the numan ones) fail (exit 1). Voice and music still run locally. | Only when the message asks |
| `-PauseFactory` | Pause shorts-factory first by creating its `data\STOP`. Not needed any more: CheqUp has its own ComfyUI and the GPU gate yields to shorts-factory. pc_run never deletes it. | Only when the message asks |

pc_run always uses `config.pc.yaml`, where mama is disabled; it refuses (exit 5) any config that enables a ComfyUI other than this PC's, or that would send CheqUp jobs to shorts-factory's ComfyUI (port 8188).

## Exit codes: what to do

| Code | Meaning | What to do |
|---|---|---|
| 0 | ok | Reply with the template below. |
| 1 | a board failed | The other boards rendered. Reply with the template; REPORT.md's Errors say why. Don't fix code. |
| 2 | tools missing | The log names them and the `winget` command (ffmpeg, Node.js, Playwright). Reply; install only if the message says so. |
| 3 | ComfyUI down | CheqUp's ComfyUI didn't come up although pc_run started it. Run `scripts/comfy_cheq_start.ps1` once yourself and look at what it prints (the log tail). If it comes up, start the run again; if not, reply with that tail. Never start, restart or stop shorts-factory's ComfyUI. |
| 4 | CheqUp ComfyUI missing or too old | Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1` yourself (first `scripts/comfy_cheq_stop.ps1` if it says CheqUp's ComfyUI is running), then start the run again. It never touches shorts-factory's ComfyUI. If the install fails, reply with its output. |
| 5 | Python env or config | If `.venv` is missing or broken, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_pc.ps1` once, then start again. If the config enables mama or another machine, don't touch it: reply. |
| 6 | models missing | The log lists them. Missing voice reference clips: run `.venv/Scripts/python.exe scripts/make_voice_refs.py --config config.pc.yaml` (local models, a few minutes; its Chatterbox step waits for shorts-factory first, like a run) and start again; if it exits 4, torch sees no CUDA GPU: reply (don't use `--cpu` unless the message says so). Missing ComfyUI model files (b-roll, ACE-Step): reply; run `scripts/fetch_models_pc.ps1` only if the message says so. |
| 7 | unknown board or format | The log lists the groups and ids it knows. Reply; don't guess. |
| 8 | fallbacks used | Expected with `-SkipModels` / `-Draft`. Reply with the template; REPORT.md's Fallbacks section says what fell back and why. Without those options, say clearly that something fell back. |
| 9 | run crashed | Reply with the last 80 log lines. Don't rerun unless told. |
| 10 | already running | Don't start another. Wait for it with pc_wait. |
| 11 | still running (pc_wait) | Run pc_wait again. |
| 12 | GPU busy | The GPU gate waited `gpu_gate.max_wait_s` (6 h) for shorts-factory, or for free VRAM, and gave up. The boards after that were skipped and REPORT.md says why (shorts-factory busy, or VRAM held while its queue was empty: the reason names shorts-factory's ComfyUI if it is idle but keeps models loaded). Nothing was sent to shorts-factory. Reply with the template; don't rerun unless told. |

## Reply template

```
CheqUp run <run id>: exit <code> (<meaning>)
Command: scripts\pc_start.ps1 <options>
Started <time>, finished <time>. CheqUp ComfyUI <version>. GPU gate waited <from REPORT.md>. shorts-factory paused: <yes/no>.
What I did / problems: <one line each, or "none">

REPORT.md (verbatim):
<paste out\REPORT.md in full, unedited>

Files: out\logs\review_<run id>.zip <sent / path>. Outbox: out\outbox\<date>\ (<n> files), not uploaded anywhere.
```

If the run stopped before rendering (codes 2-7) there is no REPORT.md for it: paste the lines pc_start/pc_wait printed instead.

## Files to send

- **Always:** `out\logs\review_<run id>.zip`: REPORT.md, report.json, the log, the ads sheet, posters, a 12-frame contact sheet per board, thumbnails of the b-roll plates and the state files.
- **Only if the message asks:** specific videos from `out\outbox\<date>\`.
- Send them with your own file-sending tool. If you have none, give the full paths. Never upload them anywhere else: not Meta, not a cloud drive, not GitHub.

## Hard rules

- **Never touch Meta.** No Meta tools or MCP servers, no Ads Manager, no uploads, no posting. The outbox is for Dan to upload by hand.
- **Never touch shorts-factory's ComfyUI** (port 8188, `C:\Users\white\ComfyUI-Installs`, venv `C:\Users\white\ComfyUI-Factory-venv`): no update, `git pull`, pip install, restart, stop, `/free`, `/interrupt` or any other POST. The only thing CheqUp does there is read `GET /queue` (and `GET /system_stats`). Never run `scripts/update_comfy_pc.ps1`: it changes that shared install. CheqUp's own ComfyUI is updated only with `scripts/install_comfy_cheq_pc.ps1`, and no custom nodes or ComfyUI-Manager go into either. Never update the NVIDIA driver either: it is shared, and installing one resets the GPU under shorts-factory.
- **Never use mama.** No `config.mama.yaml`, never set mama to `enabled: true`, never send jobs to 100.75.169.5. This PC's 5090 is the only render machine.
- **One run at a time. Never kill a run** (no `Stop-Process` / `taskkill` on pc_run, its python or any ComfyUI). If it must stop, ask Dan. CheqUp's own ComfyUI is stopped only with `scripts/comfy_cheq_stop.ps1`, and only when no run is going.
- **Only resume shorts-factory if Dan says.** Resuming means deleting its `data\STOP`.
- **Every model runs on this PC**, with local open models. No cloud APIs for images, video, voice or music.
- Don't edit cqf code, boards or configs, and don't commit or push. Install or download only what the message or the first-time setup below says.

## First-time setup

1. `cd C:/Users/white/chequp-creator`
2. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_pc.ps1`: builds `.venv` and `.venv-chatterbox`, runs `npm install` and the Playwright Chromium download, and makes the synthetic voice reference clips (`scripts/make_voice_refs.py`). pc_run runs it by itself if `.venv` is missing.
3. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1`: CheqUp's own ComfyUI v0.39.2 in `C:\Users\white\ComfyUI-CheqUp` (its own venv, port 8288), reading shorts-factory's model files without copying them. pc_run stops with exit 4 and this command while it's missing.
4. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/fetch_models_pc.ps1`: about 47 GB of b-roll models plus the ACE-Step 1.5 checkpoint (9.3 GB) if it's missing, into the shared models folder. It's resumable (partial files are `<name>.cqpart`). It never changes, appends to or deletes a file already in that folder (shorts-factory loads from it): a file that differs from upstream is listed as NOT TOUCHED and the script exits 1, so reply with that list instead of moving anything yourself. It won't start if the downloads would leave less than 30 GB free on the disk. If CheqUp's ComfyUI doesn't list the new files afterwards, restart it (`scripts/comfy_cheq_stop.ps1`; pc_run starts it again). Never restart shorts-factory's.
5. If the voice clips weren't made: `.venv/Scripts/python.exe scripts/make_voice_refs.py --config config.pc.yaml`.
6. Check: `.venv/Scripts/python.exe -m cqf --config config.pc.yaml doctor` (it shows CheqUp's ComfyUI and its version, and the GPU gate with shorts-factory's queue).
