#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UI_DIR="${REPO_ROOT}/ui"
LOG_DIR="${REPO_ROOT}/assets/logs"
CMD_EXE="${CMD_EXE:-/mnt/c/Windows/System32/cmd.exe}"
CURL_EXE="${CURL_EXE:-/mnt/c/Windows/System32/curl.exe}"

UI_PORT=3000
DASHBOARD_PORT=8050
EXPECTED_HEADING="Marked-to-market copied PnL"
SCREENSHOT_PATH="${LOG_DIR}/wallet-detail-smoke.png"
WALLET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wallet)
      WALLET="$2"
      shift 2
      ;;
    --ui-port)
      UI_PORT="$2"
      shift 2
      ;;
    --dashboard-port)
      DASHBOARD_PORT="$2"
      shift 2
      ;;
    --heading)
      EXPECTED_HEADING="$2"
      shift 2
      ;;
    --screenshot)
      SCREENSHOT_PATH="$2"
      shift 2
      ;;
    *)
      if [[ -z "${WALLET}" ]]; then
        WALLET="$1"
        shift
      else
        echo "Unknown argument: $1" >&2
        echo "Usage: $0 --wallet <address> [--ui-port <port>] [--dashboard-port <port>] [--heading <text>] [--screenshot <path>]" >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "${WALLET}" ]]; then
  echo "Wallet address is required." >&2
  echo "Usage: $0 --wallet <address> [--ui-port <port>] [--dashboard-port <port>] [--heading <text>] [--screenshot <path>]" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"

to_windows_path() {
  if command -v wslpath >/dev/null 2>&1; then
    wslpath -w "$1"
    return
  fi

  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
    return
  fi

  printf '%s\n' "$1"
}

API_URL="http://localhost:${DASHBOARD_PORT}/api/wallet_detail?wallet=${WALLET}"
PAGE_URL="http://localhost:${UI_PORT}/wallets/${WALLET}"

echo "Checking dashboard API: ${API_URL}"
api_body="$("${CURL_EXE}" --silent --show-error --fail --max-time 10 "${API_URL}")"

echo "Checking UI route: ${PAGE_URL}"
page_body="$("${CURL_EXE}" --silent --show-error --fail --max-time 10 "${PAGE_URL}" | tr -d '\r')"

if ! grep -Fq "${EXPECTED_HEADING}" <<< "${page_body}"; then
  echo "Expected heading '${EXPECTED_HEADING}' was not present in the server-rendered wallet page. The UI may be stale." >&2
  exit 1
fi

echo "Capturing wallet detail screenshot..."
screenshot_win="$(to_windows_path "${SCREENSHOT_PATH}")"
ui_dir_win="$(to_windows_path "${UI_DIR}")"
(
  cd "${UI_DIR}"
  "${CMD_EXE}" /c npx.cmd playwright screenshot \
    --browser chromium \
    --viewport-size "1600,1200" \
    --wait-for-selector ".panel" \
    --wait-for-timeout 2000 \
    "${PAGE_URL}" \
    "${screenshot_win}"
)

if [[ ! -f "${SCREENSHOT_PATH}" ]]; then
  echo "Screenshot was not created at ${SCREENSHOT_PATH}" >&2
  exit 1
fi

echo "Wallet detail smoke test passed."
echo "Screenshot: ${SCREENSHOT_PATH}"
