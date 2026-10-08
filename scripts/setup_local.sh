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
echo "ready: .venv/bin/python -m cqf doctor"
