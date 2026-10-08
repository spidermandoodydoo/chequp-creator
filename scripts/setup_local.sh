#!/usr/bin/env bash
# One-time setup for running chequp-creator on this machine (CPU only).
# Creates .venv with Python 3.12 (Kokoro doesn't build on 3.13) and installs voice + render deps.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || pip install uv
uv venv -q -p 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -q --index-url https://download.pytorch.org/whl/cpu \
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match \
  torch "kokoro>=0.9.4" "transformers>=4.44" "tokenizers>=0.19" "numpy<2.3" soundfile pyyaml requests \
  "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
(cd render && { [ -d node_modules ] || npm install --silent; })
# Optional, opt-in (CHATTERBOX=1): the Chatterbox VO engine in its own env (it pins old torch). Production voice runs on
# the 5090 PC (setup_pc.ps1); never on the cloud VM. Without it every Chatterbox line falls back to Kokoro.
if [ "${CHATTERBOX:-0}" = 1 ]; then
  [ -x .venv-chatterbox/bin/python ] || uv venv -q -p 3.11 .venv-chatterbox
  VIRTUAL_ENV=.venv-chatterbox uv pip install -q --index-url https://download.pytorch.org/whl/cpu "torch==2.7.1" "torchaudio==2.7.1"
  VIRTUAL_ENV=.venv-chatterbox uv pip install -q --no-deps "chatterbox-tts==0.1.7"
  VIRTUAL_ENV=.venv-chatterbox uv pip install -q "numpy<2" "librosa==0.11.0" s3tokenizer "transformers==5.2.0" "diffusers==0.29.0" "resemble-perth>=1.0.0" \
    "conformer==0.3.2" "safetensors==0.5.3" spacy-pkuseg "pykakasi==2.3.0" pyloudnorm omegaconf soundfile "setuptools<81"
  VIRTUAL_ENV=.venv uv pip install -q faster-whisper praat-parselmouth
fi
echo "ready: .venv/bin/python -m cqf doctor"
