#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

DOCKER_BIN="${DOCKER_BIN:-docker}"
SUDO_BIN="${SUDO_BIN:-sudo}"

run_docker_compose() {
  if [[ "${AMFV_DOCKER_NO_SUDO:-}" == "1" ]]; then
    env AMFV_DOCKER_UID="$(id -u)" AMFV_DOCKER_GID="$(id -g)" \
      "${DOCKER_BIN}" compose "$@"
  else
    "${SUDO_BIN}" env AMFV_DOCKER_UID="$(id -u)" AMFV_DOCKER_GID="$(id -g)" \
      "${DOCKER_BIN}" compose "$@"
  fi
}

run_docker_compose up --build -d "$@"
run_docker_compose ps
