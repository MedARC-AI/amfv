#!/usr/bin/env bash

# Shared environment and launch preparation for the web development and
# production entry points. This file is sourced; it does not launch a process.

amfv_load_web_environment() {
  local root_dir="$1"
  if [[ ! -f "${root_dir}/.env" && -f "${root_dir}/.env.example" ]]; then
    cp "${root_dir}/.env.example" "${root_dir}/.env"
  fi
  if [[ -f "${root_dir}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${root_dir}/.env"
    set +a
  fi
  export AMFV_WEB_ROOT="${root_dir}"
  export FRONTEND_PORT="${FRONTEND_PORT:-24861}"
  export BACKEND_PORT="${BACKEND_PORT:-21783}"
}

amfv_configure_backend_environment() {
  export PROJECT_NAME="${PROJECT_NAME:-AMFV Web}"
  export SECRET_KEY="${SECRET_KEY:-dev-only-secret-key-at-least-32-bytes}"
  export FIRST_SUPERUSER="${FIRST_SUPERUSER:-admin@example.com}"
  export FIRST_SUPERUSER_PASSWORD="${FIRST_SUPERUSER_PASSWORD:-changethis}"
  export ENVIRONMENT="${ENVIRONMENT:-local}"
  export BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-127.0.0.1}"

  local local_frontend_host="http://localhost:${FRONTEND_PORT}"
  local local_frontend_origins="${local_frontend_host},http://127.0.0.1:${FRONTEND_PORT}"
  if [[ -n "${PUBLIC_APP_URL:-}" && ( -z "${FRONTEND_HOST:-}" || "${FRONTEND_HOST}" == "${local_frontend_host}" || "${FRONTEND_HOST}" == "http://127.0.0.1:${FRONTEND_PORT}" ) ]]; then
    export FRONTEND_HOST="${PUBLIC_APP_URL%/}"
  else
    export FRONTEND_HOST="${FRONTEND_HOST:-${local_frontend_host}}"
  fi
  if [[ -z "${BACKEND_CORS_ORIGINS:-}" || "${BACKEND_CORS_ORIGINS}" == "${local_frontend_host}" || "${BACKEND_CORS_ORIGINS}" == "${local_frontend_origins}" ]]; then
    if [[ -n "${PUBLIC_APP_URL:-}" ]]; then
      export BACKEND_CORS_ORIGINS="${PUBLIC_APP_URL%/},${local_frontend_origins}"
    else
      export BACKEND_CORS_ORIGINS="${local_frontend_origins}"
    fi
  fi
  if [[ -z "${SQLITE_DATABASE_URL:-}" || "${SQLITE_DATABASE_URL}" == "sqlite:///./app.db" ]]; then
    export SQLITE_DATABASE_URL="sqlite:///${AMFV_WEB_ROOT}/data/app.db"
  fi
  mkdir -p "${AMFV_WEB_ROOT}/data"
}

amfv_configure_frontend_environment() {
  export FRONTEND_BIND_HOST="${FRONTEND_BIND_HOST:-127.0.0.1}"
  if [[ -z "${VITE_PUBLIC_APP_URL:-}" && -n "${PUBLIC_APP_URL:-}" ]]; then
    export VITE_PUBLIC_APP_URL="${PUBLIC_APP_URL%/}"
  fi
  if [[ -z "${VITE_ALLOWED_HOSTS:-}" ]]; then
    if [[ -n "${PUBLIC_APP_URL:-}" ]]; then
      local public_app_host="${PUBLIC_APP_URL#http://}"
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
}

amfv_require_port_available() {
  local label="$1"
  local port="$2"
  if python3 - "${port}" <<'PY'
import socket
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
  then
    printf '%s port %s is already in use. Stop the existing process first.\n' "${label}" "${port}" >&2
    return 1
  fi
}

amfv_prepare_backend() {
  (
    cd "${AMFV_WEB_ROOT}/backend"
    uv run python -m app.scripts.bootstrap wait-for-database
    uv run alembic upgrade head
    uv run python -m app.scripts.bootstrap seed-initial-data
  )
}
