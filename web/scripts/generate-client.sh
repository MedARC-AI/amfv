#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export PATH="${HOME}/.bun/bin:${PATH}"

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

cd "${ROOT_DIR}/backend"
uv run python -c "import app.main; import json; print(json.dumps(app.main.app.openapi()))" > "${ROOT_DIR}/frontend/openapi.json"

cd "${ROOT_DIR}/frontend"
bun run generate-client
perl -pi -e 's/[ \t]+$//' src/client/*.gen.ts
bun run check-client
