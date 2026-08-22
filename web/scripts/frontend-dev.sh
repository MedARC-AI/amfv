#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export PATH="${HOME}/.bun/bin:${PATH}"

if [[ ! -f "${ROOT_DIR}/.env" && -f "${ROOT_DIR}/.env.example" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
fi

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env"
  set +a
fi

export FRONTEND_PORT="${FRONTEND_PORT:-24861}"
export BACKEND_PORT="${BACKEND_PORT:-21783}"
export FRONTEND_BIND_HOST="${FRONTEND_BIND_HOST:-127.0.0.1}"
if [[ -z "${VITE_PUBLIC_APP_URL:-}" && -n "${PUBLIC_APP_URL:-}" ]]; then
  export VITE_PUBLIC_APP_URL="${PUBLIC_APP_URL%/}"
fi
if [[ -z "${VITE_ALLOWED_HOSTS:-}" ]]; then
  if [[ -n "${PUBLIC_APP_URL:-}" ]]; then
    public_app_host="${PUBLIC_APP_URL#http://}"
    public_app_host="${public_app_host#https://}"
    public_app_host="${public_app_host%%/*}"
    public_app_host="${public_app_host%%:*}"
    export VITE_ALLOWED_HOSTS="${public_app_host}"
  elif [[ -n "${CLOUDFLARE_HOSTNAME:-}" ]]; then
    export VITE_ALLOWED_HOSTS="${CLOUDFLARE_HOSTNAME}"
  fi
fi
if [[ -z "${VITE_API_URL:-}" || "${VITE_API_URL}" == "http://localhost:${BACKEND_PORT}" || "${VITE_API_URL}" == "http://127.0.0.1:${BACKEND_PORT}" ]]; then
  export VITE_API_URL=""
fi

if python3 - <<PY
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", ${FRONTEND_PORT})) == 0 else 1)
PY
then
  cat >&2 <<EOF
Frontend port ${FRONTEND_PORT} is already in use.
Stop the existing frontend before starting this one.
EOF
  exit 1
fi

cd "${ROOT_DIR}/frontend"
bun install
echo "Frontend: http://${FRONTEND_BIND_HOST}:${FRONTEND_PORT}"
if [[ -n "${VITE_PUBLIC_APP_URL:-}" ]]; then
  echo "Public:   ${VITE_PUBLIC_APP_URL}"
fi
echo "API:      same-origin /api proxy to http://127.0.0.1:${BACKEND_PORT}"
exec bun run dev --host "${FRONTEND_BIND_HOST}" --port "${FRONTEND_PORT}" --strictPort
