#!/usr/bin/env bash
#SBATCH --job-name=amfv-vllm-check
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
#SBATCH --partition=n
#SBATCH --account=training
#SBATCH --export=ALL
#SBATCH --output=test_vllm_check_%j.log
#SBATCH --error=test_vllm_check_%j.log

set -euo pipefail

# Use the same shared cache path as the import job.
ENROOT_CACHE="${ENROOT_CACHE:-${HOME}/.enroot/cache}"
mkdir -p "${ENROOT_CACHE}"
export LABLESS_ENROOT_CACHE_PATH="${ENROOT_CACHE}"

VLLM_IMAGE="${VLLM_IMAGE:-docker://vllm/vllm-openai:latest}"
echo "==> Checking container: ${VLLM_IMAGE}"
echo "==> Cache path: ${ENROOT_CACHE}"

srun --ntasks=1 \
     --container-image="${VLLM_IMAGE}" \
     python3 -c "
import torch, vllm
print('torch:     ', torch.__version__)
print('CUDA:      ', torch.version.cuda)
print('vllm:      ', vllm.__version__)
print('GPU avail: ', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU:       ', torch.cuda.get_device_name(0))

from vllm.model_executor.models import ModelRegistry
supported = ModelRegistry.get_supported_archs()
qwen3 = [a for a in supported if 'qwen3' in a.lower()]
print('Qwen3 archs:', qwen3)
print('qwen3_5_moe:', 'Qwen3MoeForCausalLM' in supported or 'qwen3_5_moe' in str(supported))
"
