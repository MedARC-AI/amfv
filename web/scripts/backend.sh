#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "${ROOT_DIR}/.env" && -f "${ROOT_DIR}/.env.example" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
fi

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env"
  set +a
fi

export PROJECT_NAME="${PROJECT_NAME:-AMFV Web}"
export SECRET_KEY="${SECRET_KEY:-dev-only-secret-key-at-least-32-bytes}"
export FIRST_SUPERUSER="${FIRST_SUPERUSER:-admin@example.com}"
export FIRST_SUPERUSER_PASSWORD="${FIRST_SUPERUSER_PASSWORD:-changethis}"
export ENVIRONMENT="${ENVIRONMENT:-local}"
export FRONTEND_PORT="${FRONTEND_PORT:-24861}"
export BACKEND_PORT="${BACKEND_PORT:-21783}"
export BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-127.0.0.1}"
LOCAL_FRONTEND_HOST="http://localhost:${FRONTEND_PORT}"
LOCAL_FRONTEND_ORIGINS="${LOCAL_FRONTEND_HOST},http://127.0.0.1:${FRONTEND_PORT}"
if [[ -n "${PUBLIC_APP_URL:-}" && ( -z "${FRONTEND_HOST:-}" || "${FRONTEND_HOST}" == "${LOCAL_FRONTEND_HOST}" || "${FRONTEND_HOST}" == "http://127.0.0.1:${FRONTEND_PORT}" ) ]]; then
  export FRONTEND_HOST="${PUBLIC_APP_URL%/}"
else
  export FRONTEND_HOST="${FRONTEND_HOST:-${LOCAL_FRONTEND_HOST}}"
fi
if [[ -z "${BACKEND_CORS_ORIGINS:-}" || "${BACKEND_CORS_ORIGINS}" == "${LOCAL_FRONTEND_HOST}" || "${BACKEND_CORS_ORIGINS}" == "${LOCAL_FRONTEND_ORIGINS}" ]]; then
  if [[ -n "${PUBLIC_APP_URL:-}" ]]; then
    export BACKEND_CORS_ORIGINS="${PUBLIC_APP_URL%/},${LOCAL_FRONTEND_ORIGINS}"
  else
    export BACKEND_CORS_ORIGINS="${LOCAL_FRONTEND_ORIGINS}"
  fi
fi
if [[ -z "${SQLITE_DATABASE_URL:-}" || "${SQLITE_DATABASE_URL}" == "sqlite:///./app.db" ]]; then
  export SQLITE_DATABASE_URL="sqlite:///${ROOT_DIR}/data/app.db"
fi

mkdir -p "${ROOT_DIR}/data"

if python3 - <<PY
import urllib.request

url = "http://127.0.0.1:${BACKEND_PORT}/api/v1/utils/health-check/"
try:
    with urllib.request.urlopen(url, timeout=1) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
then
  cat >&2 <<EOF
Backend is already running on port ${BACKEND_PORT}.
Stop the existing backend before starting this one so it can use the current .env and data/app.db.
EOF
  exit 1
fi

cd "${ROOT_DIR}/backend"
uv run alembic upgrade head
uv run python -m app.initial_data
echo "Backend: http://${BACKEND_BIND_HOST}:${BACKEND_PORT}"
echo "Login:   ${FIRST_SUPERUSER}"
exec uv run fastapi run app/main.py --host "${BACKEND_BIND_HOST}" --port "${BACKEND_PORT}"
