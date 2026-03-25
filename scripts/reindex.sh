#!/usr/bin/env bash
# Rebuild UTXO set from block files (runs Reindexer only; does not start the node).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}"

DATADIR="${HOME}/.berzcoin"
NETWORK="mainnet"

while [[ $# -gt 0 ]]; do
    case $1 in
        --testnet)
            NETWORK="testnet"
            shift
            ;;
        --regtest)
            NETWORK="regtest"
            shift
            ;;
        --datadir)
            DATADIR="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [--testnet|--regtest] [--datadir DIR]"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

DB_PATH="${DATADIR}/${NETWORK}.db"
if [[ -f "${DB_PATH}" ]]; then
    cp "${DB_PATH}" "${DB_PATH}.bak"
    echo "Backed up database to ${DB_PATH}.bak"
fi

export BERZCOIN_REINDEX_DATADIR="${DATADIR}"
export BERZCOIN_REINDEX_NETWORK="${NETWORK}"

python3 << 'PY'
import asyncio
import os
import sys

from node.app.config import Config
from node.app.main import BerzCoinNode
from node.app.modes import ModeManager
from node.app.reindex import Reindexer


async def main() -> None:
    cfg = Config()
    cfg.set("datadir", os.environ["BERZCOIN_REINDEX_DATADIR"])
    cfg.set("network", os.environ["BERZCOIN_REINDEX_NETWORK"])
    node = BerzCoinNode()
    node.config = cfg
    node.mode_manager = ModeManager(cfg)
    node.network = cfg.get("network", "mainnet")
    if not await node.initialize():
        sys.exit(1)
    reindexer = Reindexer(
        node.chainstate,
        node.chainstate.blocks_store,
        node.chainstate.utxo_store,
    )
    ok = await reindexer.run()
    if node.db:
        node.db.disconnect()
    sys.exit(0 if ok else 1)


asyncio.run(main())
PY

echo "Reindex finished."
