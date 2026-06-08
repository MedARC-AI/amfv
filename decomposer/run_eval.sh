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

# v1 engine ignores MULTIPROC_METHOD; force v0 + fork for Pyxis GPU subprocess access
export VLLM_USE_V1=0
export VLLM_WORKER_MULTIPROC_METHOD=fork
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1

pip3 install -e "$OUTPUT_DIR" --quiet

cd "$OUTPUT_DIR"

python3 evaluate.py \
    --data "$PROJECT_DIR/datasets/AskDocs.jsonl" \
    --decomposers factscore medscore veriscore \
    --output results/
