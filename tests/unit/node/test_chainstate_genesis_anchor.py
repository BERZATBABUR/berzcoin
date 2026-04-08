"""Chainstate genesis-anchor initialization tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from node.chain.chainstate import ChainState
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from shared.consensus.params import ConsensusParams


def _open_migrated_db(data_dir: Path, network: str) -> Database:
    db = Database(data_dir, network)
    db.connect()
    migrations = Migrations(db)
    register_standard_migrations(migrations)
    migrations.migrate()
    return db


class TestChainStateGenesisAnchor(unittest.TestCase):
    def test_empty_mainnet_initializes_with_canonical_genesis_header_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db = _open_migrated_db(root, "mainnet")
            try:
                params = ConsensusParams.mainnet()
                chainstate = ChainState(db, params, str(root))
                chainstate.initialize()

                self.assertEqual(chainstate.get_best_height(), 0)
                self.assertEqual(
                    (chainstate.get_best_block_hash() or "").lower(),
                    params.genesis_block_hash.lower(),
                )
                header0 = chainstate.get_header(0)
                self.assertIsNotNone(header0)
                self.assertEqual(header0.hash_hex().lower(), params.genesis_block_hash.lower())
            finally:
                db.disconnect()

    def test_mainnet_fails_loudly_when_genesis_metadata_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bad_genesis = root / "bad_mainnet_genesis.json"
            bad_genesis.write_text(
                json.dumps(
                    {
                        "network": "mainnet",
                        "version": 1,
                        "prev_block_hash": "00" * 32,
                        "merkle_root": "00" * 32,
                        "timestamp": 1,
                        "bits": "0x207fffff",
                        "nonce": 1,
                        "hash": "00" * 32,
                    }
                ),
                encoding="utf-8",
            )

            db = _open_migrated_db(root, "mainnet")
            try:
                chainstate = ChainState(db, ConsensusParams.mainnet(), str(root))
                with patch.object(chainstate, "_genesis_metadata_path", return_value=bad_genesis):
                    with self.assertRaises(RuntimeError) as exc:
                        chainstate.initialize()
                self.assertIn("Genesis metadata mismatch", str(exc.exception))
            finally:
                db.disconnect()


if __name__ == "__main__":
    unittest.main()

