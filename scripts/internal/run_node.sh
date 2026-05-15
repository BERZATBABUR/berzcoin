#!/usr/bin/env bash
# Run BerzCoin node (repo checkout; sets PYTHONPATH).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}"

DATADIR="${HOME}/.berzcoin"
NETWORK="mainnet"
RPC_PORT=8332
P2P_PORT=8333
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --testnet)
            NETWORK="testnet"
            RPC_PORT=18332
            P2P_PORT=18333
            shift
            EXTRA_ARGS+=(--testnet)
            ;;
        --regtest)
            NETWORK="regtest"
            RPC_PORT=18443
            P2P_PORT=18444
            shift
            EXTRA_ARGS+=(--regtest)
            ;;
        --datadir)
            DATADIR="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo "Options:"
            echo "  --testnet    Run on testnet"
            echo "  --regtest    Run on regtest"
            echo "  --datadir DIR  Set data directory"
            echo "  --help       Show this help"
            exit 0
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

CONFIG_FILE="${DATADIR}/berzcoin.conf"
mkdir -p "${DATADIR}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
    RPC_PASS="$(openssl rand -hex 32)"
    cat > "${CONFIG_FILE}" << EOF
[main]
network=${NETWORK}
datadir=${DATADIR}
port=${P2P_PORT}
rpcbind=127.0.0.1
rpcport=${RPC_PORT}
rpcuser=berzcoin
rpcpassword=${RPC_PASS}
dnsseed=false
allow_missing_bootstrap=true
EOF
    chmod 600 "${CONFIG_FILE}"
    echo "Created config file: ${CONFIG_FILE}"
fi

echo "Starting BerzCoin node on ${NETWORK}..."
exec python3 -m node.app.main -conf "${CONFIG_FILE}" "${EXTRA_ARGS[@]}"
