#!/bin/bash

#SBATCH --job-name=amfv-decomposer-eval
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-gpu=8
#SBATCH --export=ALL
#SBATCH --output="/admin/home/aymane.ouraq/amfv/decomposer/slurm/job_%j.log"
#SBATCH --error="/admin/home/aymane.ouraq/amfv/decomposer/slurm/job_%j.log"

set -euo pipefail

export PROJECT_DIR="/admin/home/aymane.ouraq/amfv"
export OUTPUT_DIR="$PROJECT_DIR/decomposer"

mkdir -p "$OUTPUT_DIR/slurm" "$OUTPUT_DIR/results"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

source "$OUTPUT_DIR/.venv313/bin/activate"

export CUDA_DEVICE_ORDER=PCI_BUS_ID
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1

cd "$OUTPUT_DIR"

python evaluate.py \
    --data "$PROJECT_DIR/datasets/AskDocs.jsonl" \
    --decomposers factscore medscore veriscore \
    --output results/
