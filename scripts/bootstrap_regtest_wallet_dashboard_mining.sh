#!/usr/bin/env bash
# One-command local bootstrap: regtest node + wallet + dashboard + background mining.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}"

DATADIR="${1:-/tmp/berzcoin-run/data}"
RPC_PORT="${RPC_PORT:-29443}"
P2P_PORT="${P2P_PORT:-29444}"
WEB_PORT="${WEB_PORT:-28080}"
WALLET_NAME="${WALLET_NAME:-default}"
WALLET_PRIVATE_KEY="${WALLET_PRIVATE_KEY:-}"
CONFIG_DIR="$(dirname "${DATADIR}")"
CONFIG_FILE="${CONFIG_DIR}/berzcoin.conf"
NODE_LOG="${CONFIG_DIR}/node.log"
NODE_PID_FILE="${CONFIG_DIR}/node.pid"

mkdir -p "${DATADIR}" "${CONFIG_DIR}"

cat > "${CONFIG_FILE}" <<EOF
[main]
network = regtest
datadir = ${DATADIR}
bind = 127.0.0.1
port = ${P2P_PORT}
rpcbind = 127.0.0.1
rpcport = ${RPC_PORT}
disablewallet = false
wallet = ${WALLET_NAME}
dnsseed = false
bootstrap_enabled = false
allow_missing_bootstrap = true
webdashboard = true
webhost = 127.0.0.1
webport = ${WEB_PORT}
mining = false
autominer = false
EOF

health_url="http://127.0.0.1:${RPC_PORT}/health"
if ! curl -sf "${health_url}" >/dev/null 2>&1; then
    nohup python3 -m node.app.main -conf "${CONFIG_FILE}" >"${NODE_LOG}" 2>&1 &
    echo $! > "${NODE_PID_FILE}"
fi

for _ in $(seq 1 60); do
    if curl -sf "${health_url}" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

if ! curl -sf "${health_url}" >/dev/null 2>&1; then
    echo "Node did not become healthy in time. Check log: ${NODE_LOG}"
    exit 1
fi

COOKIE_FILE="${DATADIR}/.cookie"
for _ in $(seq 1 20); do
    if [[ -f "${COOKIE_FILE}" ]]; then
        break
    fi
    sleep 0.25
done

if [[ ! -f "${COOKIE_FILE}" ]]; then
    echo "Missing RPC cookie at ${COOKIE_FILE}"
    exit 1
fi

COOKIE="$(cat "${COOKIE_FILE}")"
rpc() {
    local method="$1"
    local params="$2"
    curl -s --user "${COOKIE}" \
      -H "Content-Type: application/json" \
      --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"${method}\",\"params\":${params}}" \
      "http://127.0.0.1:${RPC_PORT}/"
}

if [[ -n "${WALLET_PRIVATE_KEY}" ]]; then
    rpc "activatewallet" "[\"${WALLET_PRIVATE_KEY}\"]" >/dev/null
fi

ADDR_JSON="$(rpc "get_new_address" "[]")"
if command -v jq >/dev/null 2>&1; then
    ADDR="$(echo "${ADDR_JSON}" | jq -r '.result // empty')"
else
    ADDR="$(echo "${ADDR_JSON}" | sed -n 's/.*"result"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
fi

if [[ -z "${ADDR}" ]]; then
    echo "Failed to derive mining address from wallet."
    echo "${ADDR_JSON}"
    exit 1
fi

rpc "setminingaddress" "[\"${ADDR}\"]" >/dev/null
rpc "generate" "[3]" >/dev/null
rpc "setgenerate" "[true,1]" >/dev/null

INFO_JSON="$(rpc "get_info" "[]")"
STATUS_JSON="$(rpc "getminingstatus" "[]")"

echo "Regtest bootstrap complete."
echo "RPC: http://127.0.0.1:${RPC_PORT}"
echo "Dashboard: http://127.0.0.1:${WEB_PORT}"
echo "Mining address: ${ADDR}"
echo "Node PID file: ${NODE_PID_FILE}"
echo "Node log: ${NODE_LOG}"
echo "get_info: ${INFO_JSON}"
echo "getminingstatus: ${STATUS_JSON}"
