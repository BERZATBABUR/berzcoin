#!/usr/bin/env bash
# Write ~/.berzcoin/berzcoin.conf for regtest private-key wallet flow.
#
# Usage (from repo):
#   chmod +x scripts/setup_regtest.sh
#   ./scripts/setup_regtest.sh
#
# Override datadir:
#   BERZCOIN_REGTEST_DATADIR=/path ./scripts/setup_regtest.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATADIR="${BERZCOIN_REGTEST_DATADIR:-${HOME}/.berzcoin}"
CONF="${DATADIR}/berzcoin.conf"
RPC_PORT=18443
P2P_PORT=18444

echo "BerzCoin regtest setup"
echo "======================="

mkdir -p "${DATADIR}/wallets"
chmod 700 "${DATADIR}" || true

if [[ ! -f "${REPO_ROOT}/pyproject.toml" ]]; then
  echo "error: run this script from the BerzCoin repository (missing pyproject.toml)." >&2
  exit 1
fi

cat > "${CONF}" << EOF
[main]
network = regtest
datadir = ${DATADIR}
disablewallet = false

bind = 127.0.0.1
port = ${P2P_PORT}

rpcbind = 127.0.0.1
rpcport = ${RPC_PORT}
rpcallowip = 127.0.0.1

mining = true
miningaddress =
autominer = false
mining_threads = 1

webdashboard = true
webhost = 127.0.0.1
webport = 8080

debug = true
logfile = debug.log
EOF

chmod 600 "${CONF}"

echo ""
echo "Config:     ${CONF}"
echo ""
echo "Start node:"
echo "  berzcoind -conf ${CONF}"
echo ""
echo "CLI (cookie + port):"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} getblockcount"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} createwallet default"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} activatewallet \"<private_key_hex>\""
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} setminingaddress \"<active_wallet_address>\""
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} setgenerate true --threads 2"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} generate 101"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} getwalletinfo"
echo ""
echo "Dashboard: http://127.0.0.1:8080/"
