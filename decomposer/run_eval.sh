#!/bin/bash

#SBATCH --job-name=amfv-decomposer-eval
#SBATCH --ntasks=1
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-gpu=8
#SBATCH --account=root
#SBATCH --qos=normal
#SBATCH --nice=0
#SBATCH --container-image=/data/pyxis/vllm/latest.sqsh
#SBATCH --container-mount-home
#SBATCH --container-mounts=/data/hf_cache:/data/hf_cache
#SBATCH --container-writable
#SBATCH --output="/admin/home/%u/amfv/decomposer/slurm/job_%j.log"
#SBATCH --error="/admin/home/%u/amfv/decomposer/slurm/job_%j.log"

set -euo pipefail

export PROJECT_DIR="${PROJECT_DIR:-$HOME/amfv}"
export OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_DIR/decomposer}"
export HF_HOME="${HF_HOME:-/data/hf_cache}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

mkdir -p "$OUTPUT_DIR/slurm" "$OUTPUT_DIR/results"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a; source "$PROJECT_DIR/.env"; set +a
fi

export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1

pip3 install -e "$OUTPUT_DIR" --quiet

# ── vLLM server ───────────────────────────────────────────────────────────────
# Start vllm serve as a background process — the Python evaluation code calls
# it via the OpenAI-compatible API, so there is no Python subprocess spawning
# on our side (fixes CUDA init failure inside Pyxis containers).

VLLM_PORT=8000
VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen3-8B}"
VLLM_TP="${VLLM_TP:-${SLURM_GPUS_PER_TASK:-1}}"

python3 -m vllm.entrypoints.openai.api_server \
    --model "$VLLM_MODEL" \
    --port "$VLLM_PORT" \
    --tensor-parallel-size "$VLLM_TP" \
    --dtype bfloat16 \
    --max-model-len 16384 \
    &
VLLM_PID=$!

# Kill server on any exit (clean or error)
trap 'kill "$VLLM_PID" 2>/dev/null || true' EXIT

echo "Waiting for vLLM server (pid $VLLM_PID)..."
for i in $(seq 1 60); do
    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM server process died before becoming healthy" >&2
        exit 1
    fi
    if curl -sf "http://localhost:${VLLM_PORT}/health" >/dev/null 2>&1; then
        echo "vLLM server ready (${i}x5s = $((i * 5))s)"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: vLLM server did not become healthy within 5 minutes" >&2
        exit 1
    fi
    sleep 5
done

export VLLM_BASE_URL="http://localhost:${VLLM_PORT}/v1"

# ── evaluation ────────────────────────────────────────────────────────────────
cd "$OUTPUT_DIR"

DATASET="${DATASET:-$PROJECT_DIR/datasets/AskDocs.jsonl}"
MODEL_SHORTNAME="${VLLM_MODEL##*/}"
DATASET_STEM="$(basename "$DATASET" .jsonl)"
RUN_TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

python3 evaluate.py \
    --data "$DATASET" \
    --decomposers factscore medscore veriscore \
    --output "results/${MODEL_SHORTNAME}/${DATASET_STEM}/${RUN_TIMESTAMP}"
