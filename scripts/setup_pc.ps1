# One-time setup on the Windows 5090 PC (PowerShell, from the chequp-creator folder).
# Uses CUDA torch so Kokoro voice runs on the GPU; b-roll goes to the PC's own ComfyUI (:8188).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { pip install uv }
uv venv -q -p 3.12 .venv
$env:VIRTUAL_ENV = ".venv"
uv pip install -q --index-url https://download.pytorch.org/whl/cu128 `
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match `
  torch "kokoro>=0.9.4" "transformers>=4.44" "tokenizers>=0.19" "numpy<2.3" soundfile pyyaml requests `
  "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
Push-Location render; if (-not (Test-Path node_modules)) { npm install --silent; npx playwright install chromium }; Pop-Location
Write-Host "ready: .venv\Scripts\python.exe -m cqf --config config.pc.yaml doctor"
