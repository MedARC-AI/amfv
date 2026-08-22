#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "${ROOT_DIR}/scripts/common.sh"
amfv_load_web_environment "${ROOT_DIR}"
amfv_configure_frontend_environment
amfv_require_port_available "Frontend" "${FRONTEND_PORT}"

cd "${ROOT_DIR}/frontend"
bun install
bun run build
echo "Frontend: http://${FRONTEND_BIND_HOST}:${FRONTEND_PORT}"
if [[ -n "${VITE_PUBLIC_APP_URL:-}" ]]; then
  echo "Public:           ${VITE_PUBLIC_APP_URL}"
fi
echo "API:              same-origin /api proxy to http://127.0.0.1:${BACKEND_PORT}"
exec bun run preview --host "${FRONTEND_BIND_HOST}" --port "${FRONTEND_PORT}" --strictPort
