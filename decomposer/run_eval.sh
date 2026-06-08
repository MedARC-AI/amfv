#!/bin/bash

#SBATCH --job-name=amfv-decomposer-eval
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-gpu=8
#SBATCH --export=ALL
#SBATCH --container-image=/data/pyxis/vllm/latest.sqsh
#SBATCH --container-mounts=/admin/home/aymane.ouraq:/admin/home/aymane.ouraq
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

# Install decomposer package into the container's Python environment
pip install -e "$OUTPUT_DIR" --quiet

cd "$OUTPUT_DIR"

python evaluate.py \
    --data "$PROJECT_DIR/datasets/AskDocs.jsonl" \
    --decomposers factscore medscore veriscore \
    --output results/
