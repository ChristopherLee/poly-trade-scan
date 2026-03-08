#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UI_DIR="${REPO_ROOT}/ui"
PACKAGE_JSON="${UI_DIR}/package.json"
NODE_MODULES_DIR="${UI_DIR}/node_modules"
LOG_DIR="${REPO_ROOT}/assets/logs"
STDOUT_LOG="${LOG_DIR}/ui.stdout.log"
STDERR_LOG="${LOG_DIR}/ui.stderr.log"
CMD_EXE="${CMD_EXE:-/mnt/c/Windows/System32/cmd.exe}"
NETSTAT_EXE="${NETSTAT_EXE:-/mnt/c/Windows/System32/netstat.exe}"
TASKKILL_EXE="${TASKKILL_EXE:-/mnt/c/Windows/System32/taskkill.exe}"
CURL_EXE="${CURL_EXE:-/mnt/c/Windows/System32/curl.exe}"

PORT=3000
API_BASE="${DASHBOARD_API_BASE:-http://127.0.0.1:8050}"
SKIP_BUILD=0
HEALTH_URL="http://127.0.0.1:${PORT}/"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    --api-base)
      API_BASE="$2"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--port <port>] [--api-base <url>] [--skip-build]" >&2
      exit 1
      ;;
  esac
done

HEALTH_URL="http://127.0.0.1:${PORT}/"

if [[ ! -f "${PACKAGE_JSON}" ]]; then
  echo "UI package.json not found at ${PACKAGE_JSON}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"

get_listener_pids() {
  "${NETSTAT_EXE}" -ano -p tcp 2>/dev/null | tr -d '\r' | awk -v port=":${PORT}" '
    index($2, port) && $4 == "LISTENING" { print $5 }
  ' | sort -u
}

wait_for_port_state() {
  local should_be_listening="$1"
  local timeout_seconds="${2:-30}"
  local deadline=$((SECONDS + timeout_seconds))

  while (( SECONDS < deadline )); do
    local has_listeners=0
    if [[ -n "$(get_listener_pids)" ]]; then
      has_listeners=1
    fi

    if [[ "${should_be_listening}" == "true" && ${has_listeners} -eq 1 ]]; then
      return 0
    fi

    if [[ "${should_be_listening}" == "false" && ${has_listeners} -eq 0 ]]; then
      return 0
    fi

    sleep 0.5
  done

  return 1
}

stop_ui_listeners() {
  local pids
  local saw_listener=0

  while true; do
    pids="$(get_listener_pids)"

    if [[ -z "${pids}" ]]; then
      if [[ "${saw_listener}" -eq 0 ]]; then
        echo "No listeners on port ${PORT}."
      fi
      return 0
    fi

    saw_listener=1

    while IFS= read -r listener_pid; do
      [[ -z "${listener_pid}" ]] && continue
      echo "Stopping PID ${listener_pid} on port ${PORT}..."
      "${TASKKILL_EXE}" /PID "${listener_pid}" /F >/dev/null
    done <<< "${pids}"

    sleep 1
  done
}

wait_for_ui() {
  local ui_pid="$1"
  local timeout_seconds="${2:-30}"
  local deadline=$((SECONDS + timeout_seconds))

  while (( SECONDS < deadline )); do
    if ! kill -0 "${ui_pid}" 2>/dev/null; then
      return 1
    fi

    if "${CURL_EXE}" --silent --show-error --fail --max-time 3 "${HEALTH_URL}" >/dev/null 2>/dev/null; then
      return 0
    fi

    sleep 0.5
  done

  return 1
}

if [[ ! -d "${NODE_MODULES_DIR}" ]]; then
  echo "Installing UI dependencies..."
  (
    cd "${UI_DIR}"
    "${CMD_EXE}" /c npm.cmd install
  )
fi

if [[ "${SKIP_BUILD}" != "1" ]]; then
  echo "Building UI production bundle..."
  (
    cd "${UI_DIR}"
    "${CMD_EXE}" /c npm.cmd run build
  )
fi

stop_ui_listeners

if ! wait_for_port_state false 30; then
  remaining_pids="$(get_listener_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  echo "Port ${PORT} did not clear after stopping listeners. Remaining PIDs: ${remaining_pids}" >&2
  exit 1
fi

if [[ "${API_BASE}" != "http://127.0.0.1:8050" ]]; then
  echo "Custom --api-base is not supported in restart_ui.sh. Use the default dashboard API on 127.0.0.1:8050." >&2
  exit 1
fi

echo "Starting UI on ${HEALTH_URL} using API ${API_BASE}..."
(
  cd "${UI_DIR}"
  "${CMD_EXE}" /c npm.cmd run start -- --hostname 127.0.0.1 --port "${PORT}" >"${STDOUT_LOG}" 2>"${STDERR_LOG}" &
  ui_pid=$!
  echo "${ui_pid}" >"${LOG_DIR}/ui.pid"
)
ui_pid="$(cat "${LOG_DIR}/ui.pid")"

if ! wait_for_port_state true 30; then
  echo "UI process did not bind to port ${PORT}. PID=${ui_pid}" >&2
  tail -n 20 "${STDERR_LOG}" 2>/dev/null || true
  exit 1
fi

if ! wait_for_ui "${ui_pid}" 30; then
  echo "UI failed to become healthy on ${HEALTH_URL}. PID=${ui_pid}" >&2
  tail -n 20 "${STDERR_LOG}" 2>/dev/null || true
  exit 1
fi

echo "UI is healthy on ${HEALTH_URL} (PID ${ui_pid})."
echo "stdout: ${STDOUT_LOG}"
echo "stderr: ${STDERR_LOG}"
