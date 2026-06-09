#!/bin/bash

#SBATCH --job-name=amfv-decomposer-eval
#SBATCH --ntasks=1
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-gpu=8
#SBATCH --account=root
#SBATCH --qos=normal
#SBATCH --container-image=/data/pyxis/vllm/latest.sqsh
#SBATCH --container-mount-home
#SBATCH --container-mounts=/data/hf_cache:/data/hf_cache
#SBATCH --container-writable
#SBATCH --output="/admin/home/%u/amfv/decomposer/slurm/job_%j.log"
#SBATCH --error="/admin/home/%u/amfv/decomposer/slurm/job_%j.log"

set -euo pipefail

# ── parse args ────────────────────────────────────────────────────────────────
# --model and --tp are consumed here to start the vLLM server.
# Everything else is forwarded verbatim to evaluate.py.
VLLM_MODEL=""
VLLM_TP=1
EVAL_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) VLLM_MODEL="$2"; shift 2 ;;
        --tp)    VLLM_TP="$2";    shift 2 ;;
        --data)  EVAL_ARGS+=("--data" "$(realpath "$2")"); shift 2 ;;
        *)       EVAL_ARGS+=("$1"); shift ;;
    esac
done

if [[ -z "$VLLM_MODEL" ]]; then
    echo "ERROR: --model is required" >&2
    echo "Usage: sbatch run_eval.sh --model <model> [--tp <N>] [evaluate.py flags...]" >&2
    exit 1
fi

# ── env ───────────────────────────────────────────────────────────────────────
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
# Derive the port from the job ID so two jobs sharing a node don't collide.
VLLM_PORT="${VLLM_PORT:-$((8000 + ${SLURM_JOB_ID:-0} % 1000))}"

# VLLM_EXTRA_ARGS: extra server flags, e.g. "--gpu-memory-utilization 0.95".
# shellcheck disable=SC2086
python3 -m vllm.entrypoints.openai.api_server \
    --model "$VLLM_MODEL" \
    --served-model-name "$VLLM_MODEL" \
    --port "$VLLM_PORT" \
    --tensor-parallel-size "$VLLM_TP" \
    --dtype bfloat16 \
    --max-model-len 16384 \
    ${VLLM_EXTRA_ARGS:-} \
    &
VLLM_PID=$!

trap 'kill "$VLLM_PID" 2>/dev/null || true' EXIT

VLLM_WAIT_ITERS="${VLLM_WAIT_ITERS:-240}"
echo "Waiting for vLLM server (pid $VLLM_PID, timeout $((VLLM_WAIT_ITERS * 5))s)..."
for i in $(seq 1 "$VLLM_WAIT_ITERS"); do
    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM server process died before becoming healthy" >&2
        exit 1
    fi
    if curl -sf "http://localhost:${VLLM_PORT}/health" >/dev/null 2>&1; then
        echo "vLLM server ready (${i}x5s = $((i * 5))s)"
        break
    fi
    if [ "$i" -eq "$VLLM_WAIT_ITERS" ]; then
        echo "ERROR: vLLM server did not become healthy within $((VLLM_WAIT_ITERS * 5)) seconds" >&2
        exit 1
    fi
    sleep 5
done

export VLLM_BASE_URL="http://localhost:${VLLM_PORT}/v1"

# ── evaluation ────────────────────────────────────────────────────────────────
cd "$OUTPUT_DIR"

# evaluate.py defaults to the vLLM-backed decomposers and --output results.
python3 evaluate.py ${EVAL_ARGS[@]+"${EVAL_ARGS[@]}"}
