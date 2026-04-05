"""Mempool persistence storage."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from shared.core.transaction import Transaction
from shared.utils.logging import get_logger
from node.mempool.pool import MempoolEntry

logger = get_logger()

class MempoolStore:
    SNAPSHOT_MAGIC = "berzcoin-mempool"
    SNAPSHOT_VERSION = 2

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.mempool_file = data_dir / "mempool.dat"
        self.backup_file = data_dir / "mempool.dat.bak"

    @staticmethod
    def _canonical_json(value: Any) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def _checksum_payload(cls, payload: Dict[str, Any]) -> str:
        return hashlib.sha256(cls._canonical_json(payload)).hexdigest()

    def _build_payload(
        self,
        transactions: Dict[str, MempoolEntry],
        *,
        network: str,
        tip_hash: Optional[str],
        tip_height: int,
        rules_fingerprint: str,
    ) -> Dict[str, Any]:
        entries = []
        for txid in sorted(transactions.keys()):
            entry = transactions[txid]
            entries.append(
                {
                    "txid": txid,
                    "transaction": entry.tx.serialize(include_witness=True).hex(),
                    "fee": int(entry.fee),
                    "fee_rate": float(entry.fee_rate),
                    "time_added": float(entry.time_added),
                    "height_added": int(entry.height_added),
                }
            )
        return {
            "metadata": {
                "created_at": int(time.time()),
                "network": str(network or "unknown"),
                "tip_hash": str(tip_hash or ""),
                "tip_height": int(tip_height),
                "rules_fingerprint": str(rules_fingerprint or ""),
            },
            "entries": entries,
        }

    def _write_snapshot(self, payload: Dict[str, Any]) -> bool:
        envelope = {
            "magic": self.SNAPSHOT_MAGIC,
            "version": self.SNAPSHOT_VERSION,
            "checksum": self._checksum_payload(payload),
            "payload": payload,
        }
        try:
            if self.mempool_file.exists():
                import shutil

                shutil.copy(self.mempool_file, self.backup_file)
            tmp_file = self.mempool_file.with_suffix(".dat.tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(envelope, f, sort_keys=True, separators=(",", ":"))
                f.flush()
            tmp_file.replace(self.mempool_file)
            return True
        except Exception as e:
            logger.error(f"Failed to save mempool: {e}")
            return False

    def save(
        self,
        transactions: Dict[str, MempoolEntry],
        *,
        network: str = "unknown",
        tip_hash: Optional[str] = None,
        tip_height: int = -1,
        rules_fingerprint: str = "",
    ) -> bool:
        payload = self._build_payload(
            transactions,
            network=network,
            tip_hash=tip_hash,
            tip_height=tip_height,
            rules_fingerprint=rules_fingerprint,
        )
        ok = self._write_snapshot(payload)
        if ok:
            logger.info("Saved %s transactions to mempool.dat", len(payload.get("entries", [])))
        return ok

    def _read_snapshot_file(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                envelope = json.load(f)
            if not isinstance(envelope, dict):
                raise ValueError("invalid envelope")
            if envelope.get("magic") != self.SNAPSHOT_MAGIC:
                raise ValueError("invalid magic")
            if int(envelope.get("version", -1)) != int(self.SNAPSHOT_VERSION):
                raise ValueError("unsupported snapshot version")
            payload = envelope.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("missing payload")
            expected = str(envelope.get("checksum", ""))
            actual = self._checksum_payload(payload)
            if expected != actual:
                raise ValueError("checksum mismatch")
            return payload
        except Exception as e:
            logger.warning("Failed to read mempool snapshot %s: %s", path, e)
            return None

    def load_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.mempool_file.exists() and not self.backup_file.exists():
            logger.info("No mempool snapshot found")
            return None
        snapshot = self._read_snapshot_file(self.mempool_file)
        if snapshot is not None:
            return snapshot
        if self.backup_file.exists():
            logger.info("Trying backup mempool snapshot")
            return self._read_snapshot_file(self.backup_file)
        return None

    def load(self) -> Dict[str, MempoolEntry]:
        snapshot = self.load_snapshot()
        if snapshot is None:
            return {}
        transactions: Dict[str, MempoolEntry] = {}
        entries = snapshot.get("entries", [])
        if not isinstance(entries, list):
            return {}
        for tx_data in entries:
            try:
                tx_bytes = bytes.fromhex(str(tx_data["transaction"]))
                tx, _ = Transaction.deserialize(tx_bytes)
                entry = MempoolEntry(
                    tx=tx,
                    txid=str(tx_data["txid"]),
                    size=len(tx.serialize(include_witness=True)),
                    vsize=max(1, (tx.weight() + 3) // 4),
                    weight=tx.weight(),
                    fee=int(tx_data.get("fee", 0)),
                    fee_rate=float(tx_data.get("fee_rate", 0.0)),
                    time_added=float(tx_data.get("time_added", time.time())),
                    height_added=int(tx_data.get("height_added", -1)),
                    ancestors=set(),
                    descendants=set(),
                )
                transactions[str(tx_data["txid"])] = entry
            except Exception as e:
                logger.warning("Skipping malformed mempool entry during load: %s", e)
        logger.info("Loaded %s transactions from mempool.dat", len(transactions))
        return transactions

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
