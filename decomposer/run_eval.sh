#!/bin/bash
#SBATCH --job-name=amfv-decomposer-eval
#SBATCH --output=logs/%j.out
#SBATCH --error=logs/%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=04:00:00

set -euo pipefail

DECOMPOSER_DIR="$HOME/amfv/decomposer"
PYTHON="$DECOMPOSER_DIR/.venv313/bin/python"
DATA="/path/to/AskDocs.jsonl"   # <-- update this

mkdir -p "$DECOMPOSER_DIR/logs"
cd "$DECOMPOSER_DIR"

$PYTHON evaluate.py \
    --data "$DATA" \
    --decomposers factscore medscore veriscore \
    --output results/
