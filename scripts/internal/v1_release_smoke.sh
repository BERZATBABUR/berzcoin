#!/usr/bin/env bash
# v1 release smoke test:
# - boots v1 launcher in bootstrap mode
# - verifies dashboard and RPC
# - verifies mining is active
# - exercises send flow

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DATADIR="${BERZCOIN_V1_SMOKE_DATADIR:-${HOME}/.berzcoin_v1_smoke}"
KEEP_NODE="${BERZCOIN_V1_SMOKE_KEEP_NODE:-0}"

NODE_PID=""
RUN_INFO=""

cleanup() {
  if [[ "${KEEP_NODE}" == "1" ]]; then
    return 0
  fi

  if [[ -n "${NODE_PID}" ]] && kill -0 "${NODE_PID}" 2>/dev/null; then
    echo "[*] Stopping smoke node (pid ${NODE_PID})..."
    kill "${NODE_PID}" 2>/dev/null || true
    wait "${NODE_PID}" 2>/dev/null || true
  elif [[ -f "${DATADIR}/node.pid" ]]; then
    local pid
    pid="$(cat "${DATADIR}/node.pid" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      echo "[*] Stopping smoke node (pid ${pid})..."
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  fi
}
trap cleanup EXIT INT TERM

if command -v berzcoin-cli >/dev/null 2>&1; then
  BERZCOINCLI=(berzcoin-cli)
else
  export PYTHONPATH="${REPO_ROOT}"
  BERZCOINCLI=(python3 -m cli.main)
fi

rpc_cli() {
  "${BERZCOINCLI[@]}" -datadir "${DATADIR}" -rpcport "${RPC_PORT}" "$@"
}

expect_nonempty() {
  local value="$1"
  local label="$2"
  if [[ -z "${value}" ]]; then
    echo "error: ${label} is empty" >&2
    exit 1
  fi
}

echo "[*] Starting v1 smoke environment..."
"${REPO_ROOT}/scripts/run_v1_interface.sh" \
  --datadir "${DATADIR}" \
  --reset-datadir \
  --bootstrap-demo \
  --block-time-secs 1 \
  --coinbase-maturity 1 \
  --threads 1

RUN_INFO="${DATADIR}/run_info.env"
if [[ ! -f "${RUN_INFO}" ]]; then
  echo "error: missing run info file: ${RUN_INFO}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${RUN_INFO}"
expect_nonempty "${RPC_PORT:-}" "RPC_PORT"
expect_nonempty "${WEB_PORT:-}" "WEB_PORT"

if [[ -f "${DATADIR}/node.pid" ]]; then
  NODE_PID="$(cat "${DATADIR}/node.pid" 2>/dev/null || true)"
fi

echo "[*] Verifying dashboard URL..."
curl -fsS "http://127.0.0.1:${WEB_PORT}/" >/dev/null

echo "[*] Checking mining status..."
MINING_JSON="$(rpc_cli getminingstatus)"
if ! printf '%s' "${MINING_JSON}" | grep -q '"is_mining"[[:space:]]*:[[:space:]]*true'; then
  echo "error: mining is not active" >&2
  echo "${MINING_JSON}" >&2
  exit 1
fi

echo "[*] Exercising wallet send flow..."
START_HEIGHT="$(rpc_cli getblockcount | tail -1 | tr -d '\r\n')"
WALLET_INFO="$(rpc_cli getwalletinfo)"
ACTIVE_PRIVKEY="$(printf '%s\n' "${WALLET_INFO}" | sed -n 's/.*"private_key"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1 | tr -d '\r\n')"
expect_nonempty "${ACTIVE_PRIVKEY}" "active wallet private key"
TO_ADDR="$(rpc_cli getnewaddress | tail -1 | tr -d '\r\n')"
expect_nonempty "${TO_ADDR}" "destination address"
rpc_cli activatewallet "${ACTIVE_PRIVKEY}" >/dev/null

SEND_OUT="$(rpc_cli sendtoaddress "${TO_ADDR}" 1.0 2>&1 || true)"
TXID="$(printf '%s\n' "${SEND_OUT}" | sed -n 's/^Transaction sent:[[:space:]]*//p' | tail -1 | tr -d '\r\n')"
if [[ -z "${TXID}" ]]; then
  echo "error: sendtoaddress failed" >&2
  echo "${SEND_OUT}" >&2
  exit 1
fi

rpc_cli generate 1 >/dev/null
END_HEIGHT="$(rpc_cli getblockcount | tail -1 | tr -d '\r\n')"
if [[ -z "${START_HEIGHT}" || -z "${END_HEIGHT}" || "${END_HEIGHT}" -le "${START_HEIGHT}" ]]; then
  echo "error: block height did not increase after generate" >&2
  echo "start=${START_HEIGHT} end=${END_HEIGHT}" >&2
  exit 1
fi

echo "[OK] v1 smoke test passed."
echo "     dashboard=http://127.0.0.1:${WEB_PORT}/"
echo "     txid=${TXID}"
echo "     height=${END_HEIGHT}"
