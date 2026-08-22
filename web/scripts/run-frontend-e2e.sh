#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d /tmp/amfv-e2e-XXXXXX)"
BACKEND_LOG="${TMP_DIR}/backend.log"
BACKEND_PID=""
NODE_BIN=""

cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

export PATH="${HOME}/.bun/bin:${PATH}"

ensure_node() {
  if command -v node >/dev/null 2>&1; then
    NODE_BIN="$(command -v node)"
    return
  fi

  local node_version="v22.12.0"
  local node_dir="${TMP_DIR}/node-${node_version}"
  local node_archive="${TMP_DIR}/node-${node_version}.tar.xz"

  curl -fsSL "https://nodejs.org/dist/${node_version}/node-${node_version}-linux-x64.tar.xz" -o "${node_archive}"
  mkdir -p "${node_dir}"
  tar -xJf "${node_archive}" -C "${node_dir}" --strip-components=1
  NODE_BIN="${node_dir}/bin/node"
}

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env"
  set +a
elif [[ -f "${ROOT_DIR}/.env.example" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env.example"
  set +a
fi

export PROJECT_NAME="${PROJECT_NAME:-AMFV Web}"
export SECRET_KEY="${SECRET_KEY:-dev-only-secret-key-at-least-32-bytes}"
export FIRST_SUPERUSER="${FIRST_SUPERUSER:-admin@example.com}"
export FIRST_SUPERUSER_PASSWORD="${FIRST_SUPERUSER_PASSWORD:-changethis}"
export ENVIRONMENT="local"
export FRONTEND_HOST="http://localhost:5173"
export BACKEND_CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
export SQLITE_DATABASE_URL="sqlite:///${TMP_DIR}/e2e.db"
export VITE_API_URL="http://127.0.0.1:8000"
export PLAYWRIGHT_HTML_OPEN="never"

ensure_node

cd "${ROOT_DIR}/backend"
uv run alembic upgrade head
uv run python -m app.initial_data
uv run python -m app.scripts.seed_e2e
uv run python -m app.scripts.seed_e2e

uv run fastapi run app/main.py --host 127.0.0.1 --port 8000 >"${BACKEND_LOG}" 2>&1 &
BACKEND_PID="$!"

uv run python - <<'PY'
import time
import urllib.request

url = "http://127.0.0.1:8000/api/v1/utils/health-check/"
deadline = time.time() + 30
last_error: Exception | None = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        time.sleep(0.5)

raise SystemExit(f"Backend did not become healthy: {last_error}")
PY

rm -rf "${ROOT_DIR}/frontend/playwright/.auth" "${ROOT_DIR}/frontend/test-results"

cd "${ROOT_DIR}/frontend"
"${NODE_BIN}" ../node_modules/@playwright/test/cli.js install chromium
"${NODE_BIN}" ../node_modules/@playwright/test/cli.js test --config playwright.config.cjs
