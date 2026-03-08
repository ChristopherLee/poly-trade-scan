#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PORT=8050
DASHBOARD_URL="http://localhost:${PORT}/api/summary"
PYTHON_EXE="${REPO_ROOT}/.venv/Scripts/python.exe"
DASHBOARD_SCRIPT="${REPO_ROOT}/dashboard.py"
LOG_DIR="${REPO_ROOT}/assets/logs"
STDOUT_LOG="${LOG_DIR}/dashboard.stdout.log"
STDERR_LOG="${LOG_DIR}/dashboard.stderr.log"
CMD_EXE="${CMD_EXE:-/mnt/c/Windows/System32/cmd.exe}"
NETSTAT_EXE="${NETSTAT_EXE:-/mnt/c/Windows/System32/netstat.exe}"
TASKKILL_EXE="${TASKKILL_EXE:-/mnt/c/Windows/System32/taskkill.exe}"
CURL_EXE="${CURL_EXE:-/mnt/c/Windows/System32/curl.exe}"

if [[ ! -f "${PYTHON_EXE}" ]]; then
  echo "Python interpreter not found at ${PYTHON_EXE}" >&2
  exit 1
fi

if [[ ! -f "${DASHBOARD_SCRIPT}" ]]; then
  echo "dashboard.py not found at ${DASHBOARD_SCRIPT}" >&2
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

get_listener_pids() {
  "${NETSTAT_EXE}" -ano -p tcp 2>/dev/null | tr -d '\r' | awk -v port=":${PORT}" '
    index($2, port) && $4 == "LISTENING" { print $5 }
  ' | sort -u
}

wait_for_port_state() {
  local should_be_listening="$1"
  local timeout_seconds="${2:-20}"
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

stop_dashboard_listeners() {
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

wait_for_dashboard() {
  local timeout_seconds="${1:-20}"
  local deadline=$((SECONDS + timeout_seconds))

  while (( SECONDS < deadline )); do
    if "${CURL_EXE}" --silent --show-error --fail --max-time 3 "${DASHBOARD_URL}" >/dev/null 2>/dev/null; then
      return 0
    fi

    sleep 0.5
  done

  return 1
}

stop_dashboard_listeners

if ! wait_for_port_state false 20; then
  remaining_pids="$(get_listener_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  echo "Port ${PORT} did not clear after stopping listeners. Remaining PIDs: ${remaining_pids}" >&2
  exit 1
fi

echo "Starting dashboard from ${DASHBOARD_SCRIPT}..."
python_exe_win="$(to_windows_path "${PYTHON_EXE}")"
dashboard_script_win="$(to_windows_path "${DASHBOARD_SCRIPT}")"
(
  cd "${REPO_ROOT}"
  "${CMD_EXE}" /c "${python_exe_win}" "${dashboard_script_win}" >"${STDOUT_LOG}" 2>"${STDERR_LOG}" &
)

if ! wait_for_port_state true 20; then
  echo "Dashboard process did not bind to port ${PORT}." >&2
  tail -n 20 "${STDERR_LOG}" 2>/dev/null || true
  exit 1
fi

if ! wait_for_dashboard 20; then
  echo "Dashboard failed to become healthy on ${DASHBOARD_URL}." >&2
  tail -n 20 "${STDERR_LOG}" 2>/dev/null || true
  exit 1
fi

listener_pids="$(get_listener_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
echo "Dashboard is healthy on http://localhost:${PORT}/ (PID ${listener_pids})."
echo "stdout: ${STDOUT_LOG}"
echo "stderr: ${STDERR_LOG}"
