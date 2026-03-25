#!/usr/bin/env bash
# Write ~/.berzcoin/berzcoin.conf for regtest, create wallets/default with a real coinbase address.
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

WALLET_PASSWORD="${BERZCOIN_WALLET_PASSWORD:-$(openssl rand -hex 32)}"

if [[ ! -f "${REPO_ROOT}/pyproject.toml" ]]; then
  echo "error: run this script from the BerzCoin repository (missing pyproject.toml)." >&2
  exit 1
fi

DEFAULT_WALLET="${DATADIR}/wallets/default"
if [[ -e "${DEFAULT_WALLET}" ]]; then
  echo "error: ${DEFAULT_WALLET} already exists. Remove it for a clean setup, or edit the config by hand." >&2
  exit 1
fi

export BERZCOIN_REPO_ROOT="${REPO_ROOT}"
export BERZCOIN_SETUP_PASS="${WALLET_PASSWORD}"
export BERZCOIN_DATADIR="${DATADIR}"

MINING_ADDR="$(PYTHONPATH="${REPO_ROOT}" python3 << 'PY'
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["BERZCOIN_REPO_ROOT"])
from node.wallet.core.wallet import Wallet

logging.getLogger("berzcoin").disabled = True

datadir = Path(os.environ["BERZCOIN_DATADIR"])
path = datadir / "wallets" / "default"
pw = os.environ["BERZCOIN_SETUP_PASS"]

w = Wallet(str(path), "regtest")
w.create(pw)
if not w.unlock(pw):
    print("error: unlock failed after create", file=sys.stderr)
    sys.exit(1)
addr = w.get_new_address()
if not addr:
    print("error: no address from wallet", file=sys.stderr)
    sys.exit(1)
print(addr)
PY
)"

cat > "${CONF}" << EOF
[main]
network = regtest
datadir = ${DATADIR}
wallet = default
walletpassphrase = ${WALLET_PASSWORD}
disablewallet = false

bind = 127.0.0.1
port = ${P2P_PORT}

rpcbind = 127.0.0.1
rpcport = ${RPC_PORT}
rpcallowip = 127.0.0.1

mining = true
miningaddress = ${MINING_ADDR}
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
echo "Passphrase: ${WALLET_PASSWORD}   (also in config file — store safely)"
echo "Regtest coinbase address: ${MINING_ADDR}"
echo ""
echo "Start node:"
echo "  berzcoind -conf ${CONF}"
echo ""
echo "CLI (cookie + port):"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} getblockcount"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} unlockwallet \"${WALLET_PASSWORD}\" --timeout 86400"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} setgenerate true --threads 2"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} generate 101"
echo "  berzcoin-cli -datadir ${DATADIR} -rpcport ${RPC_PORT} getwalletinfo"
echo ""
echo "Dashboard: http://127.0.0.1:8080/"
