"""Mempool persistence storage."""

import json
import time
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Set
from shared.core.transaction import Transaction
from shared.utils.logging import get_logger
from node.mempool.pool import MempoolEntry

logger = get_logger()

class MempoolStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.mempool_file = data_dir / "mempool.dat"
        self.backup_file = data_dir / "mempool.dat.bak"

    def save(self, transactions: Dict[str, MempoolEntry]) -> bool:
        try:
            data = []
            for txid, entry in transactions.items():
                data.append({
                    'txid': txid,
                    'transaction': entry.tx.serialize().hex(),
                    'fee': entry.fee,
                    'fee_rate': entry.fee_rate,
                    'time_added': entry.time_added,
                    'height_added': entry.height_added,
                    'ancestors': list(entry.ancestors),
                    'descendants': list(entry.descendants),
                })
            if self.mempool_file.exists():
                import shutil
                shutil.copy(self.mempool_file, self.backup_file)
            with open(self.mempool_file, 'wb') as f:
                pickle.dump(data, f)
            logger.info(f"Saved {len(data)} transactions to mempool.dat")
            return True
        except Exception as e:
            logger.error(f"Failed to save mempool: {e}")
            return False

    def load(self) -> Dict[str, MempoolEntry]:
        if not self.mempool_file.exists():
            logger.info("No mempool.dat found")
            return {}
        try:
            with open(self.mempool_file, 'rb') as f:
                data = pickle.load(f)
            transactions = {}
            for tx_data in data:
                tx_bytes = bytes.fromhex(tx_data['transaction'])
                tx, _ = Transaction.deserialize(tx_bytes)
                entry = MempoolEntry(
                    tx=tx,
                    txid=tx_data['txid'],
                    size=len(tx.serialize()),
                    vsize=max(1, (tx.weight() + 3) // 4),
                    weight=tx.weight(),
                    fee=tx_data['fee'],
                    fee_rate=tx_data['fee_rate'],
                    time_added=tx_data['time_added'],
                    height_added=tx_data['height_added'],
                    ancestors=set(tx_data['ancestors']),
                    descendants=set(tx_data['descendants']),
                )
                transactions[tx_data['txid']] = entry
            logger.info(f"Loaded {len(transactions)} transactions from mempool.dat")
            return transactions
        except Exception as e:
            logger.error(f"Failed to load mempool: {e}")
            if self.backup_file.exists():
                logger.info("Trying backup file...")
                try:
                    with open(self.backup_file, 'rb') as f:
                        data = pickle.load(f)
                    logger.info("Loaded from backup")
                except Exception:
                    pass
            return {}

    def save_json(self, transactions: Dict[str, MempoolEntry]) -> bool:
        try:
            data = {}
            for txid, entry in transactions.items():
                data[txid] = {
                    'txid': txid,
                    'fee': entry.fee,
                    'fee_rate': entry.fee_rate,
                    'size': entry.size,
                    'vsize': entry.vsize,
                    'weight': entry.weight,
                    'time_added': entry.time_added,
                    'height_added': entry.height_added,
                    'ancestors': list(entry.ancestors),
                    'descendants': list(entry.descendants),
                }
            json_file = self.data_dir / "mempool.json"
            with open(json_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved JSON mempool to {json_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save JSON mempool: {e}")
            return False

    def clear(self) -> None:
        try:
            if self.mempool_file.exists():
                self.mempool_file.unlink()
            if self.backup_file.exists():
                self.backup_file.unlink()
            logger.info("Cleared mempool files")
        except Exception as e:
            logger.error(f"Failed to clear mempool files: {e}")

    def get_size(self) -> int:
        if self.mempool_file.exists():
            return self.mempool_file.stat().st_size
        return 0

    def backup(self) -> bool:
        try:
            if self.mempool_file.exists():
                import shutil
                backup_file = self.data_dir / f"mempool_{int(time.time())}.bak"
                shutil.copy(self.mempool_file, backup_file)
                logger.info(f"Created mempool backup: {backup_file}")
                return True
        except Exception as e:
            logger.error(f"Failed to backup mempool: {e}")
        return False

    def restore_backup(self) -> bool:
        try:
            backups = list(self.data_dir.glob("mempool_*.bak"))
            if not backups:
                logger.warning("No backups found")
                return False
            latest = max(backups, key=lambda p: p.stat().st_mtime)
            import shutil
            shutil.copy(latest, self.mempool_file)
            logger.info(f"Restored mempool from {latest}")
            return True
        except Exception as e:
            logger.error(f"Failed to restore mempool: {e}")
            return False

    def cleanup_old_backups(self, keep: int = 5) -> int:
        try:
            backups = sorted(self.data_dir.glob("mempool_*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
            removed = 0
            for backup in backups[keep:]:
                backup.unlink()
                removed += 1
            if removed:
                logger.info(f"Removed {removed} old backups")
            return removed
        except Exception as e:
            logger.error(f"Failed to cleanup backups: {e}")
            return 0
