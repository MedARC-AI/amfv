#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "${ROOT_DIR}/scripts/common.sh"
amfv_load_web_environment "${ROOT_DIR}"
amfv_configure_backend_environment
amfv_require_port_available "Backend" "${BACKEND_PORT}"
amfv_prepare_backend

cd "${ROOT_DIR}/backend"
echo "Backend dev: http://${BACKEND_BIND_HOST}:${BACKEND_PORT}"
echo "Login:       ${FIRST_SUPERUSER}"
exec uv run fastapi dev app/main.py --host "${BACKEND_BIND_HOST}" --port "${BACKEND_PORT}"
