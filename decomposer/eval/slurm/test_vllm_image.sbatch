#!/usr/bin/env bash
# Submits: import job (CPU, 2h) → GPU verification job (depends on import).
# Run this script directly with bash, not sbatch.
#
# Usage:
#   bash decomposer/eval/slurm/test_vllm_image.sbatch [docker-image-or-sqsh-path]
#
# Example:
#   bash decomposer/eval/slurm/test_vllm_image.sbatch docker://vllm/vllm-openai:latest

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VLLM_IMAGE="${1:-docker://vllm/vllm-openai:latest}"

echo "==> Image: ${VLLM_IMAGE}"

IMPORT_JOB=$(sbatch --parsable \
    --export="ALL,VLLM_IMAGE=${VLLM_IMAGE}" \
    "${SCRIPT_DIR}/test_vllm_import.sbatch")
echo "==> Import job: ${IMPORT_JOB}  (tail -f test_vllm_import_${IMPORT_JOB}.log)"

TEST_JOB=$(sbatch --parsable \
    --dependency="afterok:${IMPORT_JOB}" \
    --export="ALL,VLLM_IMAGE=${VLLM_IMAGE}" \
    "${SCRIPT_DIR}/test_vllm_check.sbatch")
echo "==> GPU test job: ${TEST_JOB}  (tail -f test_vllm_check_${TEST_JOB}.log)"
