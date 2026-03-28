#!/usr/bin/env bash
# Start 2 local regtest nodes, mine on node1, and verify node2 syncs the new tip.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BASE_DIR="${BERZCOIN_TWO_NODE_BASE:-${HOME}/.berzcoin-two-node}"

NODE1_DIR="${BASE_DIR}/node1"
NODE2_DIR="${BASE_DIR}/node2"
NODE1_CONF="${NODE1_DIR}/berzcoin.conf"
NODE2_CONF="${NODE2_DIR}/berzcoin.conf"

NODE1_RPC=19443
NODE2_RPC=19444
NODE1_P2P=19445
NODE2_P2P=19446

cleanup() {
  if [[ -n "${NODE1_PID:-}" ]] && kill -0 "${NODE1_PID}" 2>/dev/null; then
    kill "${NODE1_PID}" 2>/dev/null || true
    wait "${NODE1_PID}" 2>/dev/null || true
  fi
  if [[ -n "${NODE2_PID:-}" ]] && kill -0 "${NODE2_PID}" 2>/dev/null; then
    kill "${NODE2_PID}" 2>/dev/null || true
    wait "${NODE2_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if command -v berzcoind >/dev/null 2>&1 && command -v berzcoin-cli >/dev/null 2>&1; then
  BERZCOIND=(berzcoind)
  BERZCOINCLI=(berzcoin-cli)
else
  export PYTHONPATH="${REPO_ROOT}"
  BERZCOIND=(python3 -m node.app.main)
  BERZCOINCLI=(python3 -m cli.main)
fi

rpc1() {
  "${BERZCOINCLI[@]}" -datadir "${NODE1_DIR}" -rpcport "${NODE1_RPC}" "$@"
}

rpc2() {
  "${BERZCOINCLI[@]}" -datadir "${NODE2_DIR}" -rpcport "${NODE2_RPC}" "$@"
}

wait_rpc() {
  local which="$1"
  local i
  for i in $(seq 1 80); do
    if [[ "${which}" == "1" ]]; then
      if rpc1 getblockcount >/dev/null 2>&1; then
        return 0
      fi
    else
      if rpc2 getblockcount >/dev/null 2>&1; then
        return 0
      fi
    fi
    sleep 0.25
  done
  return 1
}

extract_json_field() {
  local key="$1"
  python3 - "$key" << 'PY'
import json
import sys

key = sys.argv[1]
obj = json.loads(sys.stdin.read())
print(obj.get(key, ""))
PY
}

rm -rf "${BASE_DIR}"
mkdir -p "${NODE1_DIR}" "${NODE2_DIR}"
chmod 700 "${BASE_DIR}" "${NODE1_DIR}" "${NODE2_DIR}" || true

cat > "${NODE1_CONF}" << CFG
[main]
network = regtest
datadir = ${NODE1_DIR}
disablewallet = false
rpcbind = 127.0.0.1
rpcport = ${NODE1_RPC}
rpcallowip = 127.0.0.1
bind = 127.0.0.1
port = ${NODE1_P2P}
addnode = 127.0.0.1:${NODE2_P2P}
mining = true
autominer = false
miningaddress =
debug = true
CFG

cat > "${NODE2_CONF}" << CFG
[main]
network = regtest
datadir = ${NODE2_DIR}
disablewallet = false
rpcbind = 127.0.0.1
rpcport = ${NODE2_RPC}
rpcallowip = 127.0.0.1
bind = 127.0.0.1
port = ${NODE2_P2P}
addnode = 127.0.0.1:${NODE1_P2P}
mining = false
debug = true
CFG

echo "[*] Starting node1..."
"${BERZCOIND[@]}" --regtest -datadir "${NODE1_DIR}" -conf "${NODE1_CONF}" &
NODE1_PID=$!
wait_rpc 1 || { echo "node1 RPC not ready" >&2; exit 1; }

echo "[*] Starting node2..."
"${BERZCOIND[@]}" --regtest -datadir "${NODE2_DIR}" -conf "${NODE2_CONF}" &
NODE2_PID=$!
wait_rpc 2 || { echo "node2 RPC not ready" >&2; exit 1; }

echo "[*] Creating and activating node1 wallet..."
WALLET_JSON="$(rpc1 createwallet default)"
PRIVKEY="$(printf '%s' "${WALLET_JSON}" | extract_json_field private_key)"
if [[ -z "${PRIVKEY}" ]]; then
  echo "createwallet did not return private_key" >&2
  exit 1
fi
rpc1 activatewallet "${PRIVKEY}" >/dev/null

ADDR="$(rpc1 getnewaddress | tr -d '\r')"
rpc1 setminingaddress "${ADDR}" >/dev/null

echo "[*] Mining 3 blocks on node1..."
rpc1 generate 3 --address "${ADDR}" >/dev/null

TIP1="$(rpc1 getblockcount | tr -d '\r')"
echo "[*] Node1 tip: ${TIP1}"

echo "[*] Waiting for node2 to catch up..."
for _ in $(seq 1 60); do
  TIP2="$(rpc2 getblockcount | tr -d '\r')"
  if [[ "${TIP2}" == "${TIP1}" ]]; then
    break
  fi
  sleep 0.5
done

TIP2="$(rpc2 getblockcount | tr -d '\r')"
echo "[*] Node2 tip: ${TIP2}"

if [[ "${TIP2}" != "${TIP1}" ]]; then
  echo "[FAIL] propagation check failed: node1=${TIP1}, node2=${TIP2}" >&2
  exit 1
fi

echo "[OK] 2-node propagation succeeded (node1=${TIP1}, node2=${TIP2})."
