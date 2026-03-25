#!/usr/bin/env bash
# Regtest one-command mining: creates datadir, conf, starts node, unlocks wallet,
# sets a real mining address, starts one CPU mining thread.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DATADIR="${BERZCOIN_EASY_DATADIR:-${HOME}/.berzcoin-easy-mine}"
CONF="${DATADIR}/berzcoin.conf"
RPC_PORT=18443
P2P_PORT=18444
WALLET_NAME="miner_wallet"
WALLET_PASS="$(openssl rand -hex 16)"

rpc_password() {
  local line
  line="$(grep '^berzcoin:' "${DATADIR}/.cookie" 2>/dev/null | head -1 || true)"
  if [[ -z "$line" ]]; then
    echo "error: no RPC cookie at ${DATADIR}/.cookie (is the node running?)" >&2
    return 1
  fi
  printf '%s' "${line#berzcoin:}"
}

rpc_cli() {
  local pw
  pw="$(rpc_password)" || return 1
  "${BERZCOINCLI[@]}" -rpcconnect=127.0.0.1 -rpcport="${RPC_PORT}" -rpcpassword="${pw}" "$@"
}

wait_for_rpc() {
  local i
  for i in $(seq 1 60); do
    if rpc_cli getblockcount >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "error: RPC did not become ready on 127.0.0.1:${RPC_PORT}" >&2
  return 1
}

cleanup() {
  if [[ -n "${NODE_PID:-}" ]] && kill -0 "${NODE_PID}" 2>/dev/null; then
    echo "[*] Stopping node (pid ${NODE_PID})..."
    kill "${NODE_PID}" 2>/dev/null || true
    wait "${NODE_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[*] BerzCoin easy mining (regtest)"
echo "    Data directory: ${DATADIR}"

mkdir -p "${DATADIR}"
chmod 700 "${DATADIR}" || true

# INI sections required (ConfigParser).
cat > "${CONF}" << EOF
[main]
network = regtest
datadir = ${DATADIR}
bind = 127.0.0.1
port = ${P2P_PORT}
rpcbind = 127.0.0.1
rpcport = ${RPC_PORT}
rpcallowip = 127.0.0.1
wallet = ${WALLET_NAME}
walletpassphrase = ${WALLET_PASS}
mining = true
autominer = false
miningaddress =
mining_threads = 1
debug = true
disablewallet = false
webdashboard = false
EOF
chmod 600 "${CONF}"

if command -v berzcoind >/dev/null 2>&1 && command -v berzcoin-cli >/dev/null 2>&1; then
  BERZCOIND=(berzcoind)
  BERZCOINCLI=(berzcoin-cli)
else
  export PYTHONPATH="${REPO_ROOT}"
  BERZCOIND=(python3 -m node.app.main)
  BERZCOINCLI=(python3 -m cli.main)
  echo "[*] Using repo interpreter (install with: pip install -e ${REPO_ROOT})"
fi

echo "[*] Wallet passphrase (saved only in ${CONF}; chmod 600):"
echo "    ${WALLET_PASS}"
echo ""

echo "[*] Starting berzcoind..."
"${BERZCOIND[@]}" --regtest -datadir "${DATADIR}" -conf "${CONF}" &
NODE_PID=$!

if ! wait_for_rpc; then
  exit 1
fi

echo "[*] Unlocking wallet..."
rpc_cli unlockwallet "${WALLET_PASS}" --timeout 86400

echo "[*] Getting coinbase address..."
ADDR="$(rpc_cli getnewaddress | tail -1 | tr -d '\r')"
if [[ -z "$ADDR" ]]; then
  echo "error: getnewaddress returned empty" >&2
  exit 1
fi
echo "[*] Mining reward address: ${ADDR}"

rpc_cli setminingaddress "${ADDR}"

echo "[*] Starting mining (1 thread). Press Ctrl+C to stop node."
rpc_cli setgenerate true --threads 1

echo "[*] Tip: check height with: ${BERZCOINCLI[*]} -rpcport ${RPC_PORT} -rpcpassword \"\$(cut -d: -f2 ${DATADIR}/.cookie)\" getblockcount"
wait "${NODE_PID}"
