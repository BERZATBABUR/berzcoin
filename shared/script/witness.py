"""Witness data handling for SegWit."""

from typing import List, Optional
from ..core.serialization import Serializer

class Witness:
    """SegWit witness data."""

    def __init__(self, items: Optional[List[bytes]] = None):
        self.items = items if items is not None else []

    def push(self, item: bytes) -> None:
        self.items.append(item)

    def pop(self) -> Optional[bytes]:
        if not self.items:
            return None
        return self.items.pop()

    def get(self, index: int) -> Optional[bytes]:
        if index < 0 or index >= len(self.items):
            return None
        return self.items[index]

    def size(self) -> int:
        return len(self.items)

    def is_empty(self) -> bool:
        return len(self.items) == 0

    def serialize(self) -> bytes:
        if self.is_empty():
            return b''
        result = Serializer.write_varint(len(self.items))
        for item in self.items:
            result += Serializer.write_bytes(item)
        return result

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> tuple:
        if offset >= len(data):
            return cls(), offset
        n_items, offset = Serializer.read_varint(data, offset)
        items = []
        for _ in range(n_items):
            length, offset = Serializer.read_varint(data, offset)
            item, offset = Serializer.read_bytes(data, offset, length)
            items.append(item)
        return cls(items), offset

    def __len__(self) -> int:
        return len(self.items)

    def __repr__(self) -> str:
        items_hex = [item.hex()[:16] for item in self.items]
        return f"Witness({items_hex})"
