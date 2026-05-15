"""Block storage management."""

import time
from typing import List, Optional, Dict, Union
from pathlib import Path
from shared.core.block import Block, BlockHeader
from shared.utils.logging import get_logger
from node.utils.crash_injection import maybe_crash
from .db import Database

logger = get_logger()

class BlocksStore:
    """Block storage manager."""

    def __init__(self, db: Database, data_dir: Union[Path, str], cache_size: int = 100):
        self.db = db
        root = Path(data_dir)
        self.data_dir = root / "blocks"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._block_cache: Dict[str, Block] = {}
        self._header_cache: Dict[str, BlockHeader] = {}
        self._cache_size = max(8, int(cache_size))

    def write_block(self, block: Block, height: int) -> None:
        block_hash = block.header.hash_hex()
        block_file = self.data_dir / f"{block_hash}.blk"
        block_bytes = block.serialize()
        with open(block_file, 'wb') as f:
            f.write(block_bytes)
        maybe_crash("during_block_write")
        file_size = len(block_bytes)
        rel_file_path = str(block_file.relative_to(self.data_dir.parent))

        with self.db.transaction():
            self.db.execute("""
                INSERT OR REPLACE INTO blocks
                (height, hash, file_path, file_number, file_offset, version, prev_block_hash, merkle_root,
                 timestamp, bits, nonce, tx_count, size, weight, is_valid, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                height, block_hash, rel_file_path, -1, 0, block.header.version,
                block.header.prev_block_hash.hex(),
                block.header.merkle_root.hex(),
                block.header.timestamp, block.header.bits, block.header.nonce,
                len(block.transactions), file_size, block.weight(), True, int(time.time())
            ))

            self.db.execute("""
                INSERT OR REPLACE INTO block_headers
                (hash, height, version, prev_block_hash, merkle_root,
                 timestamp, bits, nonce, chainwork, is_valid, status_flags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                block_hash, height, block.header.version,
                block.header.prev_block_hash.hex(),
                block.header.merkle_root.hex(),
                block.header.timestamp, block.header.bits, block.header.nonce,
                "0", True, (1 << 0) | (1 << 1) | (1 << 2)
            ))

            for i, tx in enumerate(block.transactions):
                txid = tx.txid().hex()
                self.db.execute("""
                    INSERT INTO transactions
                    (txid, block_hash, height, "index", version, locktime, size, weight, is_coinbase)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    txid,
                    block_hash,
                    height,
                    i,
                    tx.version,
                    tx.locktime,
                    len(tx.serialize()),
                    tx.weight(),
                    tx.is_coinbase(),
                ))

                for j, txin in enumerate(tx.vin):
                    witness_data = txin.witness.serialize() if txin.witness else b''
                    self.db.execute("""
                        INSERT INTO inputs
                        (txid, "index", prev_txid, prev_index, script_sig, sequence, witness)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (txid, j, txin.prev_tx_hash.hex(), txin.prev_tx_index, txin.script_sig, txin.sequence, witness_data))

                for j, txout in enumerate(tx.vout):
                    self.db.execute("""
                        INSERT INTO outputs
                        (txid, "index", value, script_pubkey, spent)
                        VALUES (?, ?, ?, ?, ?)
                    """, (txid, j, txout.value, txout.script_pubkey, False))

        self._update_cache(block_hash, block)
        logger.debug(f"Block {height} ({block_hash[:16]}) written to storage")

    def read_block(self, height: int) -> Optional[Block]:
        block_hash = self.get_block_hash(height)
        if not block_hash:
            return None
        return self.read_block_by_hash(block_hash)

    def read_block_by_hash(self, block_hash: str) -> Optional[Block]:
        if block_hash in self._block_cache:
            return self._block_cache[block_hash]
        row = self.db.fetch_one(
            "SELECT file_path, size FROM blocks WHERE hash = ? ORDER BY processed_at DESC LIMIT 1",
            (block_hash,),
        )
        block_file = self.data_dir / f"{block_hash}.blk"
        expected_size = None
        if row and row.get("file_path"):
            block_file = self.data_dir.parent / str(row["file_path"])
            expected_size = int(row.get("size") or 0)
        if not block_file.exists():
            # Backward compatibility for old height-keyed block files.
            h = self.get_block_height(block_hash)
            legacy = self.data_dir / f"{h:08d}.blk" if h is not None else None
            if not legacy or not legacy.exists():
                return None
            block_file = legacy
        try:
            with open(block_file, 'rb') as f:
                block_data = f.read()
            if expected_size is not None and expected_size > 0 and len(block_data) != expected_size:
                logger.error(
                    "Block file size mismatch for %s: expected=%s got=%s",
                    block_hash[:16],
                    expected_size,
                    len(block_data),
                )
                return None
            block, _ = Block.deserialize(block_data)
            computed_hash = block.header.hash_hex()
            if computed_hash != block_hash:
                logger.error(
                    "Block hash consistency check failed: requested=%s computed=%s",
                    block_hash[:16],
                    computed_hash[:16],
                )
                return None
            self._update_cache(block_hash, block)
            return block
        except Exception as e:
            logger.error(f"Failed to read block {block_hash[:16]}: {e}")
            return None

    def read_header(self, height: int) -> Optional[BlockHeader]:
        block_hash = self.get_block_hash(height)
        if not block_hash:
            return None
        if block_hash in self._header_cache:
            return self._header_cache[block_hash]
        result = self.db.fetch_one("SELECT * FROM block_headers WHERE hash = ?", (block_hash,))
        if not result:
            return None
        header = BlockHeader(
            version=result['version'],
            prev_block_hash=bytes.fromhex(result['prev_block_hash']),
            merkle_root=bytes.fromhex(result['merkle_root']),
            timestamp=result['timestamp'],
            bits=result['bits'],
            nonce=result['nonce']
        )
        self._update_header_cache(block_hash, header)
        return header

    def read_header_by_hash(self, block_hash: str) -> Optional[BlockHeader]:
        if block_hash in self._header_cache:
            return self._header_cache[block_hash]
        result = self.db.fetch_one("SELECT * FROM block_headers WHERE hash = ?", (block_hash,))
        if not result:
            return None
        header = BlockHeader(
            version=result['version'],
            prev_block_hash=bytes.fromhex(result['prev_block_hash']),
            merkle_root=bytes.fromhex(result['merkle_root']),
            timestamp=result['timestamp'],
            bits=result['bits'],
            nonce=result['nonce']
        )
        self._update_header_cache(block_hash, header)
        return header

    def get_height(self) -> int:
        result = self.db.fetch_one("SELECT MAX(height) as max_height FROM blocks WHERE is_valid = 1")
        return result['max_height'] if result and result['max_height'] else -1

    def get_best_block_hash(self) -> Optional[str]:
        result = self.db.fetch_one(
            "SELECT hash FROM blocks WHERE is_valid = 1 ORDER BY height DESC, processed_at DESC LIMIT 1"
        )
        return result['hash'] if result else None

    def get_headers_range(self, start_height: int, count: int) -> List[BlockHeader]:
        results = self.db.fetch_all("SELECT * FROM block_headers WHERE height >= ? AND height < ? ORDER BY height", (start_height, start_height + count))
        headers = []
        for result in results:
            headers.append(BlockHeader(
                version=result['version'],
                prev_block_hash=bytes.fromhex(result['prev_block_hash']),
                merkle_root=bytes.fromhex(result['merkle_root']),
                timestamp=result['timestamp'],
                bits=result['bits'],
                nonce=result['nonce']
            ))
        return headers

    def block_exists(self, height: int) -> bool:
        result = self.db.fetch_one(
            "SELECT 1 FROM blocks WHERE height = ? LIMIT 1",
            (height,),
        )
        return result is not None

    def get_block_hash(self, height: int) -> Optional[str]:
        result = self.db.fetch_one(
            "SELECT hash FROM blocks WHERE height = ? ORDER BY processed_at DESC LIMIT 1",
            (height,),
        )
        return result['hash'] if result else None

    def get_block_height(self, block_hash: str) -> Optional[int]:
        result = self.db.fetch_one("SELECT height FROM blocks WHERE hash = ?", (block_hash,))
        return result['height'] if result else None

    def scan_raw_block_files(self) -> Dict[str, object]:
        """Scan raw block files and classify storage/index consistency."""
        report: Dict[str, object] = {
            "ok": True,
            "verified_count": 0,
            "indexed_count": 0,
            "raw_only": [],
            "corrupt_files": [],
            "index_missing_file": [],
        }
        hashes_on_disk = set()
        for path in sorted(self.data_dir.glob("*.blk")):
            stem = path.stem.strip().lower()
            if len(stem) != 64:
                continue
            hashes_on_disk.add(stem)
            try:
                blob = path.read_bytes()
                block, _ = Block.deserialize(blob)
                computed = block.header.hash_hex().lower()
                if computed != stem:
                    report["ok"] = False
                    report["corrupt_files"].append(
                        {"path": str(path), "expected_hash": stem, "computed_hash": computed}
                    )
                    continue
                report["verified_count"] = int(report["verified_count"]) + 1
                exists = self.db.fetch_one(
                    "SELECT 1 FROM block_headers WHERE hash = ? LIMIT 1",
                    (computed,),
                )
                if not exists:
                    report["raw_only"].append({"hash": computed, "path": str(path)})
            except Exception as e:
                report["ok"] = False
                report["corrupt_files"].append({"path": str(path), "error": str(e)})

        idx_rows = self.db.fetch_all("SELECT hash, file_path FROM blocks")
        report["indexed_count"] = len(idx_rows)
        for row in idx_rows:
            h = str(row.get("hash", "")).strip().lower()
            file_path = str(row.get("file_path") or "").strip()
            if file_path:
                p = self.data_dir.parent / file_path
            else:
                p = self.data_dir / f"{h}.blk"
            if not p.exists():
                report["ok"] = False
                report["index_missing_file"].append({"hash": h, "file_path": str(p)})
        return report

    def _update_cache(self, block_hash: str, block: Block) -> None:
        self._block_cache[block_hash] = block
        self._header_cache[block_hash] = block.header
        if len(self._block_cache) > self._cache_size:
            oldest = next(iter(self._block_cache))
            self._block_cache.pop(oldest, None)
        if len(self._header_cache) > self._cache_size:
            oldest = next(iter(self._header_cache))
            self._header_cache.pop(oldest, None)

    def _update_header_cache(self, block_hash: str, header: BlockHeader) -> None:
        self._header_cache[block_hash] = header
        if len(self._header_cache) > self._cache_size:
            oldest = next(iter(self._header_cache))
            self._header_cache.pop(oldest, None)
