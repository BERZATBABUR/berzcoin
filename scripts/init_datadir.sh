#!/usr/bin/env bash
# Initialize BerzCoin data directory and config.

set -euo pipefail

DATADIR="${HOME}/.berzcoin"
NETWORK="mainnet"
RPC_PORT=8332
P2P_PORT=8333

while [[ $# -gt 0 ]]; do
    case $1 in
        --testnet)
            NETWORK="testnet"
            RPC_PORT=18332
            P2P_PORT=18333
            shift
            ;;
        --regtest)
            NETWORK="regtest"
            RPC_PORT=18443
            P2P_PORT=18444
            shift
            ;;
        --datadir)
            DATADIR="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo "  --testnet    Initialize testnet directory"
            echo "  --regtest    Initialize regtest directory"
            echo "  --datadir DIR"
            echo "  --help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

mkdir -p "${DATADIR}/blocks" "${DATADIR}/wallets" "${DATADIR}/backups"

RPC_PASS="$(openssl rand -hex 32)"
cat > "${DATADIR}/berzcoin.conf" << EOF
# BerzCoin configuration
network=${NETWORK}
datadir=${DATADIR}
bind=0.0.0.0
port=${P2P_PORT}
rpcbind=127.0.0.1
rpcport=${RPC_PORT}
rpcuser=berzcoin
rpcpassword=${RPC_PASS}
txindex=1
EOF

chmod 600 "${DATADIR}/berzcoin.conf"

echo "Initialized BerzCoin data directory: ${DATADIR}"
echo "Network: ${NETWORK}"
