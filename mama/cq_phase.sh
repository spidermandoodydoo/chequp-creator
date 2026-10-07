#!/usr/bin/env bash
# Switch mama's 7x RTX 3090 between the two jobs that both want every card.
#
#   bash cq_phase.sh plan     Qwen3-235B in LM Studio across all 7 cards (~145 GB VRAM) — write scripts
#   bash cq_phase.sh render   unload Qwen, start one ComfyUI per card on 8189-8194 — Wan 2.2 b-roll
#   bash cq_phase.sh status   what is holding the GPUs right now
#
# Run it on mama itself (or ask the mama Claude session to). Nothing here SSHes anywhere.
set -euo pipefail
COMFY="${COMFY:-/home/dan/ComfyUI-Installs/ComfyUI/ComfyUI}"
MODEL="${QWEN_MODEL:-qwen/qwen3-235b-a22b-2507}"
FIRST_GPU="${FIRST_GPU:-1}"      # GPU0 / :8188 stays Dan's own ComfyUI
LAST_GPU="${LAST_GPU:-6}"
LOGS="${LOGS:-$HOME/cq_logs}"; mkdir -p "$LOGS"

stop_comfy_farm() {
  for i in $(seq "$FIRST_GPU" "$LAST_GPU"); do
    pkill -f "main.py.*--port $((8188 + i))" 2>/dev/null || true
  done
}

free_llama() {
  # LM Studio leaves orphaned llama-server processes holding VRAM after a restart (Oct 5 incident).
  lms unload --all 2>/dev/null || true
  pkill -f llama-server 2>/dev/null || true
  sleep 3
}

check_models() {
  # The umt5 encoder arrived truncated once and failed every card at CLIPLoader (Oct 2).
  local te="$COMFY/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
  local sz; sz=$(stat -c %s "$te" 2>/dev/null || echo 0)
  if [ "$sz" -lt 6000000000 ]; then
    echo "umt5 text encoder missing or truncated ($sz bytes) — run: bash ~/fetch_models.sh $COMFY" >&2
    exit 1
  fi
}

case "${1:-status}" in
  plan)
    stop_comfy_farm; free_llama
    sudo systemctl start lms-qwen.service 2>/dev/null || lms load "$MODEL" --gpu max -y
    echo "plan phase: $MODEL loading on :1234 (watch: nvidia-smi)"
    ;;
  render)
    sudo systemctl stop lms-qwen.service 2>/dev/null || true
    free_llama; check_models
    cd "$COMFY"
    for i in $(seq "$FIRST_GPU" "$LAST_GPU"); do
      port=$((8188 + i))
      CUDA_VISIBLE_DEVICES=$i nohup .venv/bin/python main.py --listen 0.0.0.0 --port "$port" \
        --disable-all-custom-nodes ${COMFY_EXTRA:-} > "$LOGS/comfy-$port.log" 2>&1 &
      echo "GPU$i -> :$port"
    done
    echo "render phase: ComfyUI farm starting (first load of Wan 2.2 takes ~1 min per card)"
    ;;
  status)
    nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu --format=csv,noheader
    pgrep -af "llama-server|main.py --listen" | cut -c1-140 || true
    ;;
  *) echo "usage: $0 plan|render|status" >&2; exit 2 ;;
esac
