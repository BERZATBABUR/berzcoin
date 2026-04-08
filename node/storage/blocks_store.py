"""Block storage management."""

import time
from typing import List, Optional, Dict, Union
from pathlib import Path
from shared.core.block import Block, BlockHeader
from shared.utils.logging import get_logger
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
        with open(block_file, 'wb') as f:
            f.write(block.serialize())

        with self.db.transaction():
            self.db.execute("""
                INSERT OR REPLACE INTO blocks
                (height, hash, version, prev_block_hash, merkle_root,
                 timestamp, bits, nonce, tx_count, size, weight, is_valid, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                height, block_hash, block.header.version,
                block.header.prev_block_hash.hex(),
                block.header.merkle_root.hex(),
                block.header.timestamp, block.header.bits, block.header.nonce,
                len(block.transactions), block.size(), block.weight(), True, int(time.time())
            ))

            self.db.execute("""
                INSERT OR REPLACE INTO block_headers
                (hash, height, version, prev_block_hash, merkle_root,
                 timestamp, bits, nonce, chainwork, is_valid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                block_hash, height, block.header.version,
                block.header.prev_block_hash.hex(),
                block.header.merkle_root.hex(),
                block.header.timestamp, block.header.bits, block.header.nonce,
                "0", True
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
        block_file = self.data_dir / f"{block_hash}.blk"
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
            block, _ = Block.deserialize(block_data)
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
