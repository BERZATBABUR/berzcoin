"""Sync checkpoints for light clients."""

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from node.chain.chainstate import ChainState

logger = get_logger()


class SyncCheckpoint:
    """Sync checkpoint for light client synchronization."""

    def __init__(
        self,
        height: int,
        block_hash: bytes,
        timestamp: int,
        filter_header: Optional[bytes] = None,
    ):
        self.height = height
        self.block_hash = block_hash
        self.timestamp = timestamp
        self.filter_header = filter_header or b"\x00" * 32

    def to_dict(self) -> Dict[str, Any]:
        return {
            "height": self.height,
            "block_hash": self.block_hash.hex(),
            "timestamp": self.timestamp,
            "filter_header": self.filter_header.hex(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncCheckpoint":
        fh = data.get("filter_header", "00" * 32)
        if not isinstance(fh, str) or len(fh) != 64:
            fh = "00" * 32
        try:
            fb = bytes.fromhex(fh)
        except ValueError:
            fb = b"\x00" * 32
        if len(fb) != 32:
            fb = b"\x00" * 32
        return cls(
            height=int(data["height"]),
            block_hash=bytes.fromhex(data["block_hash"]),
            timestamp=int(data["timestamp"]),
            filter_header=fb,
        )


class CheckpointManager:
    """Manage sync checkpoints."""

    def __init__(self, chainstate: "ChainState"):
        self.chainstate = chainstate
        self.checkpoints: Dict[int, SyncCheckpoint] = {}
        self._load_default_checkpoints()

    def _load_default_checkpoints(self) -> None:
        genesis = self.chainstate.get_block_by_height(0)
        if genesis:
            self.add_checkpoint(
                SyncCheckpoint(
                    height=0,
                    block_hash=genesis.header.hash(),
                    timestamp=genesis.header.timestamp,
                )
            )

    def add_checkpoint(self, checkpoint: SyncCheckpoint) -> None:
        self.checkpoints[checkpoint.height] = checkpoint
        logger.info("Added checkpoint at height %s", checkpoint.height)

    def get_checkpoint(self, height: int) -> Optional[SyncCheckpoint]:
        best_height: Optional[int] = None
        for h in self.checkpoints:
            if h <= height:
                if best_height is None or h > best_height:
                    best_height = h
        if best_height is not None:
            return self.checkpoints[best_height]
        return None

    def get_checkpoint_after(self, height: int) -> Optional[SyncCheckpoint]:
        for h in sorted(self.checkpoints.keys()):
            if h > height:
                return self.checkpoints[h]
        return None

    def get_checkpoint_before(self, height: int) -> Optional[SyncCheckpoint]:
        heights = [h for h in self.checkpoints if h < height]
        if not heights:
            return None
        return self.checkpoints[max(heights)]

    def get_next_checkpoint(self, current_height: int) -> Optional[SyncCheckpoint]:
        return self.get_checkpoint_after(current_height)

    def verify_checkpoint(self, height: int, block_hash: bytes) -> bool:
        checkpoint = self.checkpoints.get(height)
        if not checkpoint:
            return False
        return checkpoint.block_hash == block_hash

    def get_checkpoints_range(
        self, start_height: int, end_height: int
    ) -> List[SyncCheckpoint]:
        result: List[SyncCheckpoint] = []
        for h in sorted(self.checkpoints.keys()):
            if start_height <= h <= end_height:
                result.append(self.checkpoints[h])
        return result

    def get_last_checkpoint(self) -> Optional[SyncCheckpoint]:
        if not self.checkpoints:
            return None
        return self.checkpoints[max(self.checkpoints.keys())]

    def load_from_file(self, filename: str) -> bool:
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            for checkpoint_data in data.get("checkpoints", []):
                checkpoint = SyncCheckpoint.from_dict(checkpoint_data)
                self.add_checkpoint(checkpoint)
            logger.info("Loaded %s checkpoints from %s", len(self.checkpoints), filename)
            return True
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.error("Failed to load checkpoints: %s", e)
            return False

    def save_to_file(self, filename: str) -> bool:
        try:
            payload = {
                "checkpoints": [c.to_dict() for c in self.checkpoints.values()],
            }
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info("Saved %s checkpoints to %s", len(self.checkpoints), filename)
            return True
        except OSError as e:
            logger.error("Failed to save checkpoints: %s", e)
            return False

    def get_stats(self) -> Dict[str, int]:
        if not self.checkpoints:
            return {
                "total_checkpoints": 0,
                "oldest_height": 0,
                "newest_height": 0,
            }
        keys = list(self.checkpoints.keys())
        return {
            "total_checkpoints": len(self.checkpoints),
            "oldest_height": min(keys),
            "newest_height": max(keys),
        }
