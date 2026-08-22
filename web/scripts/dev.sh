#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PID=""

cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env"
  set +a
fi
export BACKEND_PORT="${BACKEND_PORT:-21783}"

"${SCRIPT_DIR}/backend-dev.sh" &
BACKEND_PID="$!"

python3 - <<PY
import time
import urllib.request

url = "http://127.0.0.1:${BACKEND_PORT}/api/v1/utils/health-check/"
deadline = time.time() + 30
last_error = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(0.5)

raise SystemExit(f"Backend did not become healthy: {last_error}")
PY

exec "${SCRIPT_DIR}/frontend-dev.sh"
