# PUPPET.md: instructions for the Claude Code session on Dan's PC

Read this when the cloud session (or Dan) messages you about a CheqUp render.

## Who does what

- **The cloud session** has no GPU. It writes boards and code, sends you a message, and reads your reply. It never makes media.
- **You** are Claude Code on Dan's Windows RTX 5090 PC (user `white`). You run the render here with local models and report back what happened. You are the hands: run, check, report. Don't redesign anything, and don't edit cqf's code, boards or configs.
- **Every model runs on this PC.** That is the point of the setup:
  - b-roll: ComfyUI, with Qwen-Image 2512 + SeedVR2 stills. Wan 2.2 image-to-video clips only with `-Clips`.
  - voice: Chatterbox (Kokoro only as a reported fallback)
  - music: ACE-Step 1.5 on the same ComfyUI (cqf's procedural bed only as a reported fallback)
  - render: Playwright + ffmpeg
- cqf itself calls `claude -p` on this PC to vision-check each b-roll still. That only looks at the images and generates nothing, so it is expected.

## Where things are

| What | Where |
|---|---|
| cqf code (run everything from here) | `C:\Users\white\chequp-creator` |
| Python envs | `.venv` (Python 3.12, CUDA torch, Kokoro), `.venv-chatterbox` (Python 3.11, Chatterbox) |
| ComfyUI | `http://127.0.0.1:8188` · `C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI` · venv `C:\Users\white\ComfyUI-Factory-venv` |
| shorts-factory (shares this ComfyUI and GPU) | `C:\Users\white\heatmap\shorts-factory` (`shorts_factory.dir` in `config.pc.yaml`). Paused while `data\STOP` exists there. |
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

### Options (pass them to `pc_start.ps1`)

| Option | Meaning | When |
|---|---|---|
| `-Boards numan,made-simple` | Groups (`concepts\<group>-*.json`), `all`, board ids, paths or globs; commas allowed. Default: `concepts\numan-*.json`, or `concepts\made-simple-*.json` if there are no numan boards. | As the message says |
| `-Formats 9x16` | `9x16`, `4x5`, `1x1`, `16x9`. Default: each board's own formats. | As the message says |
| `-Strict` | Refuse HOLD boards instead of rendering drafts. | Only when the message asks |
| `-Clips` | Every AI b-roll shot becomes a Wan 2.2 image-to-video clip made from its chosen still, instead of a still with a slow push-in. Much slower, and needs the Wan 2.2 i2v files. Without it no AI video clip is made: every b-roll scene is a still, and REPORT.md says `AI video clips: 0`. | Only when the message asks (Dan's call) |
| `-SkipModels` | Accept fallbacks instead of stopping: CheqUp's own stills for b-roll, the procedural music bed, Kokoro for Chatterbox lines. The run then ends with exit 8 if anything fell back. The numan boards have no fallback stills on purpose, so a numan board whose b-roll can't be made fails instead (exit 1; REPORT.md's Errors name the scene). | Only when the message asks |
| `-Draft` | Don't stop on a ComfyUI older than 0.39.2 or on missing Qwen/SeedVR2 files. cqf has no Wan-only still path yet, so b-roll that needs those uses CheqUp's own stills (REPORT.md says so; exit 8), and boards without fallback stills (the numan ones) fail (exit 1). Voice and music still run locally. | Only when the message asks |
| `-PauseFactory` | Pause shorts-factory first by creating its `data\STOP`. pc_run never deletes it. | Only when the message asks |

pc_run always uses `config.pc.yaml`, where mama is disabled; it refuses (exit 5) any config that enables a ComfyUI other than this PC's.

## Exit codes: what to do

| Code | Meaning | What to do |
|---|---|---|
| 0 | ok | Reply with the template below. |
| 1 | a board failed | The other boards rendered. Reply with the template; REPORT.md's Errors say why. Don't fix code. |
| 2 | tools missing | The log names them and the `winget` command (ffmpeg, Node.js, Playwright). Reply; install only if the message says so. |
| 3 | ComfyUI down | Reply. Don't start or restart ComfyUI unless the message or Dan says so (shorts-factory uses it too). |
| 4 | ComfyUI too old | Reply with the version the log shows. The fix is `scripts/update_comfy_pc.ps1`, which pc_run only prints. **Never run it without Dan's explicit OK**: shorts-factory shares this ComfyUI. |
| 5 | Python env or config | If `.venv` is missing or broken, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_pc.ps1` once, then start again. If the config enables mama or another machine, don't touch it: reply. |
| 6 | models missing | The log lists them. Missing voice reference clips: run `.venv/Scripts/python.exe scripts/make_voice_refs.py --config config.pc.yaml` (local models, a few minutes) and start again; if it exits 4, torch sees no CUDA GPU: reply (don't use `--cpu` unless the message says so). Missing ComfyUI model files (b-roll, ACE-Step): reply; run `scripts/fetch_models_pc.ps1` only if the message says so. |
| 7 | unknown board or format | The log lists the groups and ids it knows. Reply; don't guess. |
| 8 | fallbacks used | Expected with `-SkipModels` / `-Draft`. Reply with the template; REPORT.md's Fallbacks section says what fell back and why. Without those options, say clearly that something fell back. |
| 9 | run crashed | Reply with the last 80 log lines. Don't rerun unless told. |
| 10 | already running | Don't start another. Wait for it with pc_wait. |
| 11 | still running (pc_wait) | Run pc_wait again. |

## Reply template

```
CheqUp run <run id>: exit <code> (<meaning>)
Command: scripts\pc_start.ps1 <options>
Started <time>, finished <time>. ComfyUI <version>. shorts-factory paused: <yes/no>.
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
- **Never update ComfyUI without Dan's OK** (`update_comfy_pc.ps1`, `git pull`, ComfyUI-Manager updates, new custom nodes). shorts-factory shares it.
- **Never use mama.** No `config.mama.yaml`, never set mama to `enabled: true`, never send jobs to 100.75.169.5. This PC's 5090 is the only render machine.
- **One run at a time. Never kill a run** (no `Stop-Process` / `taskkill` on pc_run, its python or ComfyUI). If it must stop, ask Dan.
- **Only resume shorts-factory if Dan says.** Resuming means deleting its `data\STOP`.
- **Every model runs on this PC**, with local open models. No cloud APIs for images, video, voice or music.
- Don't edit cqf code, boards or configs, and don't commit or push. Install or download only what the message or the first-time setup below says.

## First-time setup

1. `cd C:/Users/white/chequp-creator`
2. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_pc.ps1`: builds `.venv` and `.venv-chatterbox`, runs `npm install` and the Playwright Chromium download, and makes the synthetic voice reference clips (`scripts/make_voice_refs.py`). pc_run runs it by itself if `.venv` is missing.
3. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/fetch_models_pc.ps1`: about 47 GB of b-roll models plus the ACE-Step 1.5 checkpoint (9.3 GB) if it's missing. It's resumable. ComfyUI needs a restart to see new files; pause shorts-factory first and only restart with Dan's OK.
4. If the voice clips weren't made: `.venv/Scripts/python.exe scripts/make_voice_refs.py --config config.pc.yaml`.
5. Check: `.venv/Scripts/python.exe -m cqf --config config.pc.yaml doctor`.
6. The quality b-roll graphs need ComfyUI 0.39.2 or newer. If it's older, ask Dan (`scripts/update_comfy_pc.ps1`).
