#!/usr/bin/env bash
# Demo: start a regtest node + web dashboard, create/unlock wallet, mine coins, then send.
#
# This matches this repo's behavior:
# - Config is INI (ConfigParser)
# - berzcoin-cli does NOT support --regtest and does NOT apply -conf to RPC settings
# - Use -datadir (cookie) and -rpcport (RPC) when calling berzcoin-cli

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DATADIR="${BERZCOIN_DEMO_DATADIR:-${HOME}/.berzcoin_demo}"
CONF="${DATADIR}/berzcoin.conf"
RPC_PORT="${BERZCOIN_DEMO_RPC_PORT:-18443}"
P2P_PORT="${BERZCOIN_DEMO_P2P_PORT:-18444}"
WEB_PORT="${BERZCOIN_DEMO_WEB_PORT:-8080}"

WALLET_NAME="demo"
WALLET_PASSWORD="${BERZCOIN_DEMO_WALLET_PASSWORD:-demo-$(openssl rand -hex 8)}"

cleanup() {
  if [[ -n "${NODE_PID:-}" ]] && kill -0 "${NODE_PID}" 2>/dev/null; then
    echo "[*] Stopping berzcoind (pid ${NODE_PID})..."
    kill "${NODE_PID}" 2>/dev/null || true
    wait "${NODE_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[*] BerzCoin dashboard demo (regtest)"
echo "    datadir: ${DATADIR}"

rm -rf "${DATADIR}"
mkdir -p "${DATADIR}"
chmod 700 "${DATADIR}" || true

cat > "${CONF}" << EOF
[main]
network = regtest
datadir = ${DATADIR}

wallet = ${WALLET_NAME}
walletpassphrase = ${WALLET_PASSWORD}
disablewallet = false

webdashboard = true
webhost = 127.0.0.1
webport = ${WEB_PORT}

rpcbind = 127.0.0.1
rpcport = ${RPC_PORT}
rpcallowip = 127.0.0.1

bind = 127.0.0.1
port = ${P2P_PORT}

# Keep miner disabled until we have a real address; we'll set it via RPC.
mining = false
autominer = false
miningaddress =
mining_threads = 1

dnsseed = false

debug = true
EOF
chmod 600 "${CONF}"

if command -v berzcoind >/dev/null 2>&1 && command -v berzcoin-cli >/dev/null 2>&1; then
  BERZCOIND=(berzcoind)
  BERZCOINCLI=(berzcoin-cli)
else
  export PYTHONPATH="${REPO_ROOT}"
  BERZCOIND=(python3 -m node.app.main)
  BERZCOINCLI=(python3 -m cli.main)
fi

rpc_cli() {
  "${BERZCOINCLI[@]}" -datadir "${DATADIR}" -rpcport "${RPC_PORT}" "$@"
}

wait_for_rpc() {
  local i
  for i in $(seq 1 80); do
    if rpc_cli getblockcount >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

echo "[*] Starting berzcoind..."
"${BERZCOIND[@]}" --regtest -conf "${CONF}" -datadir "${DATADIR}" &
NODE_PID=$!

echo "[*] Waiting for RPC on 127.0.0.1:${RPC_PORT} ..."
if ! wait_for_rpc; then
  echo "error: RPC did not become ready. Check ${DATADIR}/debug.log" >&2
  exit 1
fi

echo "[*] Unlocking wallet..."
rpc_cli unlockwallet "${WALLET_PASSWORD}" --timeout 86400 >/dev/null

echo "[*] Creating a mining/receive address..."
ADDR="$(rpc_cli getnewaddress | tail -1 | tr -d '\r')"
echo "    address: ${ADDR}"
rpc_cli setminingaddress "${ADDR}" >/dev/null

echo "[*] Mining 101 blocks to fund wallet..."
rpc_cli generate 101 --address "${ADDR}" >/dev/null

BAL="$(rpc_cli getbalance | tail -1 | tr -d '\r')"
echo "[*] Balance: ${BAL} BERZ"

echo ""
echo "[OK] Demo is ready"
echo "    Dashboard: http://127.0.0.1:${WEB_PORT}/"
echo "    Wallet password: ${WALLET_PASSWORD}"
echo ""
echo "Try sending to a new address:"
echo "  TO=\$(rpc_cli getnewaddress | tail -1)"
echo "  rpc_cli sendtoaddress \"\$TO\" 1.0"
echo ""
echo "Press Ctrl+C to stop the node."

wait "${NODE_PID}"
