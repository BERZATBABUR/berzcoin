#!/usr/bin/env bash
# One-click installer for BerzCoin (dev: editable install from repo; fallback: PyPI)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "BerzCoin installer"
echo "===================="

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3.10+ is required."
    exit 1
fi

if [[ -f "${REPO_ROOT}/pyproject.toml" ]]; then
    echo "Installing from source (editable) at ${REPO_ROOT} ..."
    (cd "${REPO_ROOT}" && pip install -e ".")
else
    echo "Installing from PyPI ..."
    pip install berzcoin || {
        echo "Error: pip install berzcoin failed. Clone the BerzCoin repo and run this script from it."
        exit 1
    }
fi

echo ""
echo "Installation complete."
echo ""
echo "One-command local v1 run (node + dashboard, neutral wallet/mining):"
echo "  scripts/run_v1_interface.sh --reset-datadir"
echo "Optional demo bootstrap (auto wallet + mining):"
echo "  scripts/run_v1_interface.sh --reset-datadir --bootstrap-demo"
echo ""
echo "Important: createwallet / getnewaddress require a running node (JSON-RPC)."
echo "Typical flow:"
echo "  1. Write ~/.berzcoin/berzcoin.conf (see configs/secure_mainnet.toml and docs/QUICK_START.md)."
echo "  2. Start: berzcoind -datadir ~/.berzcoin -conf ~/.berzcoin/berzcoin.conf"
echo "  3. Then: berzcoin-cli createwallet default"
echo "           berzcoin-cli activatewallet \"<private_key_hex>\""
echo "           berzcoin-cli getnewaddress"
echo ""
echo "Or use the lightweight helper (local simple-wallet create + RPC for address/send/balance):"
echo "  berzcoin-wallet create"
echo "  berzcoin-wallet address   # needs step 2 running"
echo ""
echo "Check balance: berzcoin-cli getbalance"
echo "Local CPU mining walkthrough: docs/QUICK_START.md (regtest; setgenerate is not for testnet/mainnet)."
echo ""
echo "Documentation: file://${REPO_ROOT}/docs/QUICK_START.md (or your install prefix)."
