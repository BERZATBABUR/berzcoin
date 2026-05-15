#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF_PATH="${1:-/tmp/berz-live/berzcoin.conf}"
STATE_DIR="${2:-/tmp/berz-live}"
NODE_PID_FILE="${STATE_DIR}/node.pid"
WATCHDOG_PID_FILE="${STATE_DIR}/watchdog.pid"
WATCHDOG_LOG="${STATE_DIR}/watchdog.log"

mkdir -p "${STATE_DIR}"

echo $$ > "${WATCHDOG_PID_FILE}"

echo "[$(date -Is)] watchdog start conf=${CONF_PATH}" >> "${WATCHDOG_LOG}"

cleanup() {
  if [[ -f "${NODE_PID_FILE}" ]]; then
    PID="$(cat "${NODE_PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${PID}" ]] && kill -0 "${PID}" 2>/dev/null; then
      kill "${PID}" 2>/dev/null || true
      wait "${PID}" 2>/dev/null || true
    fi
    rm -f "${NODE_PID_FILE}"
  fi
  rm -f "${WATCHDOG_PID_FILE}"
  echo "[$(date -Is)] watchdog stop" >> "${WATCHDOG_LOG}"
}

trap cleanup EXIT INT TERM

while true; do
  echo "[$(date -Is)] starting node" >> "${WATCHDOG_LOG}"
  (
    cd "${REPO_ROOT}"
    env PYTHONPATH="${REPO_ROOT}" python3 -m node.app.main -conf "${CONF_PATH}"
  ) >> "${WATCHDOG_LOG}" 2>&1 &
  NODE_PID=$!
  echo "${NODE_PID}" > "${NODE_PID_FILE}"

  wait "${NODE_PID}"
  EXIT_CODE=$?
  echo "[$(date -Is)] node exited code=${EXIT_CODE}, restarting in 3s" >> "${WATCHDOG_LOG}"
  sleep 3

done
