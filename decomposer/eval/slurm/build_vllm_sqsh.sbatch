#!/usr/bin/env bash
#SBATCH --job-name=amfv-vllm-sqsh
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --partition=c
#SBATCH --account=training
#SBATCH --export=ALL
#SBATCH --output=build_vllm_sqsh_%j.log
#SBATCH --error=build_vllm_sqsh_%j.log

set -euo pipefail

VLLM_IMAGE="${VLLM_IMAGE:-docker://vllm/vllm-openai:latest}"
OUT_DIR="${HOME}/.enroot"
OUT_SQS="${OUT_DIR}/vllm-openai-latest.sqsh"

# Point enroot at the existing layer cache so no downloading is needed.
export LABLESS_ENROOT_CACHE_PATH="${HOME}/.enroot/cache"
export LABLESS_ENROOT_TEMP_PATH="${TMPDIR:-/tmp}"
export LABLESS_ENROOT_RUNTIME_PATH="/tmp/enroot-runtime-${SLURM_JOB_ID}"
mkdir -p "${LABLESS_ENROOT_RUNTIME_PATH}"

echo "==> Assembling sqsh from cached layers..."
echo "    Source:      ${VLLM_IMAGE}"
echo "    Layer cache: ${LABLESS_ENROOT_CACHE_PATH}"
echo "    Output:      ${OUT_SQS}"

cd "${OUT_DIR}"
enroot import --output "${OUT_SQS}" "${VLLM_IMAGE}"

echo "==> Done."
ls -lh "${OUT_SQS}"
echo ""
echo "Use this in your config:"
echo "  vllm_image: ${OUT_SQS}"
