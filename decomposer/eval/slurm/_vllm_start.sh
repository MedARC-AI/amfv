#!/usr/bin/env bash
# Source this file to start a vLLM server and export VLLM_BASE_URL.
#
# Usage (enroot/pyxis mode — recommended):
#   source _vllm_start.sh <model> <tensor_parallel> <container_image> [port]
#
# Usage (direct binary mode — fallback):
#   source _vllm_start.sh <model> <tensor_parallel> <vllm_bin_path> [port]
#
# The third argument is auto-detected: if it ends in .sqsh or starts with
# docker:// it is treated as a container image; otherwise as a binary path.
#
# Sets:
#   VLLM_BASE_URL  — OpenAI-compatible base URL for the started server
#   VLLM_PID       — PID of the background vllm process

_VLLM_MODEL="${1:?model argument required}"
_VLLM_TP="${2:-4}"
_VLLM_ARG3="${3:-vllm}"
_VLLM_PORT="${4:-8000}"
_VLLM_TIMEOUT="${VLLM_TIMEOUT:-3600}"

_VLLM_LOG_DIR="${SLURM_LOG_DIR:-/tmp}"
_VLLM_LOG="${_VLLM_LOG_DIR}/vllm_${SLURM_JOB_ID:-$$}_${SLURM_ARRAY_TASK_ID:-0}.log"

echo "[vllm] Starting ${_VLLM_MODEL} (tp=${_VLLM_TP}, port=${_VLLM_PORT})…"
echo "[vllm] Server log: ${_VLLM_LOG}"

if [[ "${_VLLM_ARG3}" == *.sqsh || "${_VLLM_ARG3}" == docker://* ]]; then
    # enroot/pyxis mode: run vllm inside a container via srun.
    # Redirect vllm output from *inside* the container (pyxis doesn't forward
    # container stdout through srun's fds), mounting the log dir explicitly.
    _ENROOT_CACHE="${ENROOT_CACHE:-${HOME}/.enroot/cache}"
    mkdir -p "${_ENROOT_CACHE}"
    export LABLESS_ENROOT_CACHE_PATH="${_ENROOT_CACHE}"
    # Mount sources must exist before enroot will accept them
    mkdir -p "${HF_HOME}" "${_VLLM_LOG_DIR}"

    # Route Triton's kernel cache into the container's ephemeral /tmp so stale
    # .cubin/.ptx entries from killed jobs never persist to the next run.
    export TRITON_CACHE_DIR=/tmp/triton_cache

    echo "[vllm] Using container: ${_VLLM_ARG3}"
    srun --ntasks=1 \
         --container-image="${_VLLM_ARG3}" \
         --container-mounts="${HF_HOME}:${HF_HOME},${_VLLM_LOG_DIR}:${_VLLM_LOG_DIR},/dev/shm:/dev/shm" \
         --container-env="HF_HOME,HUGGING_FACE_HUB_TOKEN,TRITON_CACHE_DIR" \
         bash -c "vllm serve '${_VLLM_MODEL}' \
             --tensor-parallel-size '${_VLLM_TP}' \
             --port '${_VLLM_PORT}' \
             --enforce-eager \
             >> '${_VLLM_LOG}' 2>&1" \
         &
else
    # direct binary mode
    echo "[vllm] Using binary: ${_VLLM_ARG3}"
    "${_VLLM_ARG3}" serve "${_VLLM_MODEL}" \
        --tensor-parallel-size "${_VLLM_TP}" \
        --port "${_VLLM_PORT}" \
        >> "${_VLLM_LOG}" 2>&1 &
fi
VLLM_PID=$!

trap 'echo "[vllm] Stopping vLLM (pid ${VLLM_PID})…"; kill "${VLLM_PID}" 2>/dev/null' EXIT

export VLLM_BASE_URL="http://localhost:${_VLLM_PORT}/v1"
_VLLM_HEALTH_URL="http://localhost:${_VLLM_PORT}/health"

echo "[vllm] Waiting for server to be ready (timeout ${_VLLM_TIMEOUT}s)…"
for i in $(seq 1 "${_VLLM_TIMEOUT}"); do
    if curl -sf "${_VLLM_HEALTH_URL}" > /dev/null 2>&1; then
        echo "[vllm] Ready after ${i}s"
        break
    fi
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "[vllm] Process died unexpectedly — last lines of ${_VLLM_LOG}:" >&2
        tail -20 "${_VLLM_LOG}" >&2
        exit 1
    fi
    if (( i % 30 == 0 )); then
        echo "[vllm] Still waiting… (${i}s) — tail of server log:"
        tail -3 "${_VLLM_LOG}" 2>/dev/null || true
    fi
    sleep 1
done

if ! curl -sf "${_VLLM_HEALTH_URL}" > /dev/null 2>&1; then
    echo "[vllm] Server did not become healthy within ${_VLLM_TIMEOUT}s — last lines of ${_VLLM_LOG}:" >&2
    tail -20 "${_VLLM_LOG}" >&2
    exit 1
fi
