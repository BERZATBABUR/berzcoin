#!/usr/bin/env bash
# Developer/user bootstrap helper: install dependencies and editable package.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Error: Python is required but was not found in PATH."
  echo "Install Python 3.10+ and rerun this script."
  exit 1
fi

echo "BerzCoin setup"
echo "=============="
echo "Repo: ${REPO_ROOT}"
echo "Python: $(${PYTHON_BIN} --version 2>&1)"
echo

cd "${REPO_ROOT}"

echo "[1/3] Upgrading packaging tools..."
"${PYTHON_BIN}" -m pip install --upgrade pip setuptools wheel

echo "[2/3] Installing runtime dependencies..."
"${PYTHON_BIN}" -m pip install -r requirements.txt

echo "[3/3] Installing BerzCoin in editable mode..."
"${PYTHON_BIN}" -m pip install -e .

echo
echo "Setup complete."
echo
echo "Quick start (manual connect mode):"
echo "  ${PYTHON_BIN} -m cli.launcher node start --network mainnet --port 8333 --data-dir ~/.berzcoin_mainnet --connect <PEER_IP>:8333 --no-seeds"
echo
echo "Quick start (config mode):"
echo "  ${PYTHON_BIN} -m cli.launcher node start --config configs/node.toml"
echo
echo "Check CLI is available:"
echo "  ${PYTHON_BIN} -m cli.main -h"

