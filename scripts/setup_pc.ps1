# One-time setup on the Windows 5090 PC (PowerShell, from the chequp-creator folder).
# Uses CUDA torch so Kokoro voice runs on the GPU; b-roll goes to the PC's own ComfyUI (:8188).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { pip install uv }
if (-not (Test-Path .venv\Scripts\python.exe)) { uv venv -q -p 3.12 .venv }   # uv 0.11 exits 2 if it exists (re-runs)
$env:VIRTUAL_ENV = ".venv"
uv pip install -q --index-url https://download.pytorch.org/whl/cu128 `
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match `
  torch "kokoro>=0.9.4" "transformers>=4.44" "tokenizers>=0.19" "numpy<2.3" soundfile pyyaml requests `
  "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
# Chatterbox (MIT): own env. chatterbox-tts pins torch 2.6, which has no RTX 5090 (Blackwell) kernels,
# so install torch cu128 first and chatterbox with --no-deps, then its other deps.
# A failure here only warns: every Chatterbox line then falls back to Kokoro, named in REPORT.md.
$ErrorActionPreference = "Continue"    # uv/python progress on stderr must not stop setup; exit codes are checked
$cbFail = 0
if (-not (Test-Path .venv-chatterbox\Scripts\python.exe)) { uv venv -q -p 3.11 .venv-chatterbox; $cbFail += [int]($LASTEXITCODE -ne 0) }
$env:VIRTUAL_ENV = ".venv-chatterbox"
uv pip install -q --index-url https://download.pytorch.org/whl/cu128 "torch==2.7.1" "torchaudio==2.7.1"; $cbFail += [int]($LASTEXITCODE -ne 0)
uv pip install -q --no-deps "chatterbox-tts==0.1.7"; $cbFail += [int]($LASTEXITCODE -ne 0)
uv pip install -q "numpy<2" "librosa==0.11.0" s3tokenizer "transformers==5.2.0" "diffusers==0.29.0" "resemble-perth>=1.0.0" `
  "conformer==0.3.2" "safetensors==0.5.3" spacy-pkuseg "pykakasi==2.3.0" pyloudnorm omegaconf soundfile "setuptools<81"
$cbFail += [int]($LASTEXITCODE -ne 0)
if (Test-Path .venv-chatterbox\Scripts\python.exe) {    # imports only (no model load): is the env usable, on the GPU?
  .venv-chatterbox\Scripts\python.exe -c "import torch, perth, chatterbox.tts; assert torch.cuda.is_available(), 'torch sees no CUDA GPU'; assert perth.PerthImplicitWatermarker, 'resemble-perth watermarker did not import (setuptools<81 / pkg_resources?)'; print('ok   chatterbox env: torch', torch.__version__, 'on', torch.cuda.get_device_name(0))"
  $cbFail += [int]($LASTEXITCODE -ne 0)
} else { $cbFail += 1 }
if ($cbFail) { Write-Warning "Chatterbox env is not usable (see the errors above): VO lines will use the Kokoro fallback until setup_pc.ps1 runs cleanly." }
$env:VIRTUAL_ENV = ".venv"
uv pip install -q faster-whisper praat-parselmouth   # ranks Chatterbox takes + caption timings
if ($LASTEXITCODE -ne 0) { Write-Warning "faster-whisper / praat-parselmouth did not install: the first Chatterbox take is used and captions are estimated." }
$ErrorActionPreference = "Stop"
Push-Location render; if (-not (Test-Path node_modules)) { npm install --silent; npx playwright install chromium }; Pop-Location
# Synthetic voice references (Kokoro + Chatterbox on this PC's GPU; no real person is cloned). Skips files that exist.
$ErrorActionPreference = "Continue"
.venv\Scripts\python.exe scripts\make_voice_refs.py --config config.pc.yaml
if ($LASTEXITCODE -ne 0) { Write-Warning "make_voice_refs.py exited with code $($LASTEXITCODE): lines whose reference clip is missing are read by Kokoro (see above)." }
$ErrorActionPreference = "Stop"
Write-Host "ready: .venv\Scripts\python.exe -m cqf --config config.pc.yaml doctor"
