"""Compact block filters for light clients (BIP158-style stubs)."""

import struct
from typing import List, Set, Tuple

from shared.core.block import Block
from shared.core.hashes import hash256


class GCSFilter:
    """Golomb-coded set filter (simplified implementation for prototyping)."""

    def __init__(self, P: int = 19, M: int = 1 << 20):
        self.P = P
        self.M = M
        self.N = 0
        self._body = b""

    def build(self, elements: Set[bytes]) -> bytes:
        if not elements:
            self.N = 0
            self._body = b""
            return struct.pack("<B", self.P) + struct.pack("<Q", 0)

        self.N = len(elements)
        sorted_elements = sorted(elements)
        values = [int.from_bytes(e, "little") % self.M for e in sorted_elements]

        diffs: List[int] = []
        prev = 0
        for v in sorted(values):
            diff = (v - prev) % self.M
            diffs.append(diff)
            prev = v

        encoded: List[int] = []
        for diff in diffs:
            encoded.extend(self._golomb_encode(diff))

        self._body = bytes(encoded)
        return struct.pack("<B", self.P) + struct.pack("<Q", self.N) + self._body

    def match(self, element: bytes) -> bool:
        if not self._body and self.N == 0:
            return False
        _ = int.from_bytes(element, "little") % self.M
        return self._check_match(_)

    def _golomb_encode(self, value: int) -> List[int]:
        q = value >> self.P
        r = value & ((1 << self.P) - 1)
        result: List[int] = []
        result.extend([1] * q)
        result.append(0)
        for i in range(self.P - 1, -1, -1):
            result.append((r >> i) & 1)

        bytes_result: List[int] = []
        for i in range(0, len(result), 8):
            byte_val = 0
            for j in range(8):
                if i + j < len(result) and result[i + j]:
                    byte_val |= 1 << (7 - j)
            bytes_result.append(byte_val)
        return bytes_result

    def _check_match(self, _value: int) -> bool:
        return True


def _parse_filter_blob(data: bytes) -> Tuple[GCSFilter, int]:
    if len(data) < 9:
        gcs = GCSFilter()
        gcs.N = 0
        gcs._body = b""
        return gcs, 0
    P = data[0]
    N = struct.unpack_from("<Q", data, 1)[0]
    body = data[9:]
    gcs = GCSFilter(P=P)
    gcs.N = N
    gcs._body = body
    return gcs, len(data)


class CompactFilter:
    """Compact block filter for light clients."""

    FILTER_TYPE_BASIC = 0

    def __init__(self, block_hash: bytes, filter_type: int = FILTER_TYPE_BASIC):
        self.block_hash = block_hash
        self.filter_type = filter_type
        self._gcs = GCSFilter()
        self._serialized_payload: bytes = b""

    def build_from_block(self, block: Block) -> bytes:
        elements: Set[bytes] = set()
        for tx in block.transactions:
            for i, _txout in enumerate(tx.vout):
                outpoint = tx.txid() + struct.pack("<I", i)
                elements.add(hash256(outpoint))
        self._serialized_payload = self._gcs.build(elements)
        return self._serialized_payload

    def serialize(self) -> bytes:
        return (
            struct.pack("<B", self.filter_type)
            + self.block_hash
            + self._serialized_payload
        )

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple["CompactFilter", int]:
        filter_type = data[offset]
        offset += 1
        block_hash = data[offset : offset + 32]
        offset += 32
        rest = data[offset:]
        cf = cls(block_hash, filter_type)
        cf._serialized_payload = rest
        cf._gcs, _ = _parse_filter_blob(rest)
        return cf, len(data)


def filter_matches(filter_data: bytes, element: bytes) -> bool:
    gcs, _ = _parse_filter_blob(filter_data)
    return gcs.match(element)
