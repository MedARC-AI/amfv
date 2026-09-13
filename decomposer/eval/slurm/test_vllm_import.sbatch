#!/usr/bin/env bash
#SBATCH --job-name=amfv-vllm-import
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --partition=c
#SBATCH --account=training
#SBATCH --export=ALL
#SBATCH --output=test_vllm_import_%j.log
#SBATCH --error=test_vllm_import_%j.log

set -euo pipefail

# Store the .sqsh in home so all nodes can find it (home is on shared NFS).
ENROOT_CACHE="${ENROOT_CACHE:-${HOME}/.enroot/cache}"
mkdir -p "${ENROOT_CACHE}"
export LABLESS_ENROOT_CACHE_PATH="${ENROOT_CACHE}"

VLLM_IMAGE="${VLLM_IMAGE:-docker://vllm/vllm-openai:latest}"
echo "==> Importing container image: ${VLLM_IMAGE}"
echo "==> Cache path: ${ENROOT_CACHE}"

# NVIDIA_VISIBLE_DEVICES=void disables the GPU hook so the container starts on CPU nodes.
NVIDIA_VISIBLE_DEVICES=void srun --ntasks=1 \
     --container-image="${VLLM_IMAGE}" \
     echo "Image cached OK"

echo "==> Done. sqsh stored in ${ENROOT_CACHE}"
ls -lh "${ENROOT_CACHE}/"
