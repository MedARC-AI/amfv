#!/usr/bin/env bash

set -euo pipefail

WEB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${WEB_ROOT}/backend"
uv run python -m app.scripts.export_openapi --output "${WEB_ROOT}/frontend/openapi.json"

cd "${WEB_ROOT}/frontend"
bun run generate-client
perl -pi -e 's/[ \t]+$//' src/client/*.gen.ts
bun run check-client
