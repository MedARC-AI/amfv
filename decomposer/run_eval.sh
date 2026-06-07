#!/bin/bash

#SBATCH --job-name=amfv-decomposer-eval
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-gpu=8
#SBATCH --exclude=n-6,n-7,n-8
#SBATCH --export=ALL
#SBATCH --output="/admin/home/aymane.ouraq/amfv/decomposer/slurm/job_%j.log"
#SBATCH --error="/admin/home/aymane.ouraq/amfv/decomposer/slurm/job_%j.log"

set -euo pipefail

export PROJECT_DIR="/admin/home/aymane.ouraq/amfv"
export OUTPUT_DIR="$PROJECT_DIR/decomposer"
export HF_HOME="/admin/home/aymane.ouraq/.cache/huggingface"

mkdir -p "$OUTPUT_DIR/slurm" "$OUTPUT_DIR/results"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

export CUDA_DEVICE_ORDER=PCI_BUS_ID
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1

# Start vLLM server via Singularity container
apptainer exec --nv \
    --bind "$HF_HOME:$HF_HOME" \
    /data/containers/vllm-openai-latest.sqfs \
    python -m vllm.entrypoints.openai.api_server \
        --model Qwen/Qwen3-8B \
        --port 8000 \
        --max-model-len 8192 &
VLLM_PID=$!

# Wait for server to be ready
echo "Waiting for vLLM server..."
until curl -s http://localhost:8000/health > /dev/null 2>&1; do
    sleep 5
done
echo "vLLM server ready."

# Run evaluation using our venv
source "$OUTPUT_DIR/.venv312/bin/activate"
cd "$OUTPUT_DIR"

python evaluate.py \
    --data "$PROJECT_DIR/datasets/AskDocs.jsonl" \
    --decomposers factscore medscore veriscore \
    --output results/

# Shut down vLLM server
kill $VLLM_PID
