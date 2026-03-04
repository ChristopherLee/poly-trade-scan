#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UI_DIR="${REPO_ROOT}/ui"
PACKAGE_JSON="${UI_DIR}/package.json"
NODE_MODULES_DIR="${UI_DIR}/node_modules"

PORT="${PORT:-3000}"
API_BASE="${DASHBOARD_API_BASE:-http://127.0.0.1:8050}"
USE_SAMPLE_DATA=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sample)
      USE_SAMPLE_DATA=1
      shift
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --api-base)
      API_BASE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--sample] [--port <port>] [--api-base <url>]" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "${PACKAGE_JSON}" ]]; then
  echo "UI package.json not found at ${PACKAGE_JSON}" >&2
  exit 1
fi

if [[ ! -d "${NODE_MODULES_DIR}" ]]; then
  echo "Installing UI dependencies..."
  (
    cd "${UI_DIR}"
    npm install
  )
fi

if [[ "${USE_SAMPLE_DATA}" == "1" ]]; then
  export UI_USE_SAMPLE_DATA=1
  unset DASHBOARD_API_BASE || true
  echo "Starting UI with sample data on http://127.0.0.1:${PORT}/"
else
  export UI_USE_SAMPLE_DATA=0
  export DASHBOARD_API_BASE="${API_BASE}"
  echo "Starting UI on http://127.0.0.1:${PORT}/ using API ${DASHBOARD_API_BASE}"
fi

cd "${UI_DIR}"
exec npm run dev -- --hostname 127.0.0.1 --port "${PORT}"
