#!/usr/bin/env bash
# Build vllm from source against CUDA 12.8 and install into .vllm_env.
# Run directly or via setup_vllm.sbatch on a compute node.
#
# Usage:
#   bash decomposer/eval/slurm/setup_vllm.sh [vllm-git-ref]
#
# Examples:
#   bash decomposer/eval/slurm/setup_vllm.sh          # latest main
#   bash decomposer/eval/slurm/setup_vllm.sh v0.26.0  # specific tag

set -euo pipefail

CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.8}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.vllm_env"
VLLM_REF="${1:-main}"
SRC_DIR="${SCRIPT_DIR}/.vllm_src"

echo "==> CUDA_HOME: ${CUDA_HOME}"
echo "==> venv:      ${VENV_DIR}"
echo "==> vllm ref:  ${VLLM_REF}"
echo "==> source:    ${SRC_DIR}"

export CUDA_HOME
export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

# Verify nvcc is reachable
nvcc --version

# --- venv -------------------------------------------------------------------
if [ ! -f "${VENV_DIR}/bin/python" ]; then
    echo "==> Creating Python 3.12 venv..."
    python3.12 -m venv "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip setuptools wheel

# --- torch (cu128) ----------------------------------------------------------
echo "==> Installing torch (cu128)..."
pip install torch \
    --index-url https://download.pytorch.org/whl/cu128

# --- clone / update vllm source --------------------------------------------
if [ -d "${SRC_DIR}/.git" ]; then
    echo "==> Updating existing vllm source..."
    git -C "${SRC_DIR}" fetch --tags
    git -C "${SRC_DIR}" checkout "${VLLM_REF}"
else
    echo "==> Cloning vllm..."
    git clone https://github.com/vllm-project/vllm.git "${SRC_DIR}"
    git -C "${SRC_DIR}" checkout "${VLLM_REF}"
fi

# --- build ------------------------------------------------------------------
echo "==> Building vllm (this takes 20-60 minutes)..."
# H100 = SM 9.0; limit arch list to speed up compilation
export TORCH_CUDA_ARCH_LIST="9.0"
# Limit parallel compile jobs to avoid OOM on the build node
export MAX_JOBS="${MAX_JOBS:-16}"

cd "${SRC_DIR}"
pip install -e . --no-build-isolation

echo ""
echo "==> Done. vllm installed at ${VENV_DIR}"
echo "    Binary: ${VENV_DIR}/bin/vllm"
"${VENV_DIR}/bin/vllm" --version
