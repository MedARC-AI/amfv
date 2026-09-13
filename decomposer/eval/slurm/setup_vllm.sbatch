#!/usr/bin/env bash
#SBATCH --job-name=amfv-setup-vllm
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --partition=main
#SBATCH --account=training
#SBATCH --export=ALL
#SBATCH --output=setup_vllm_%j.log
#SBATCH --error=setup_vllm_%j.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "${SCRIPT_DIR}/setup_vllm.sh" "${VLLM_REF:-main}"
