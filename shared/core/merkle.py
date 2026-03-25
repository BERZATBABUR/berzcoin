"""Merkle tree calculations for BerzCoin."""

import hashlib
from typing import List, Optional
from .hashes import hash256

class MerkleTree:
    """Merkle tree implementation."""

    def __init__(self, hashes: List[bytes]):
        """Initialize Merkle tree.

        Args:
            hashes: List of transaction hashes (32 bytes each)
        """
        self.hashes = hashes
        self.levels = self._build_tree(hashes)

    def _build_tree(self, hashes: List[bytes]) -> List[List[bytes]]:
        """Build the Merkle tree levels.

        Args:
            hashes: Leaf hashes

        Returns:
            List of levels (root at level 0)
        """
        if not hashes:
            return []

        levels = [hashes]
        current = hashes

        while len(current) > 1:
            # Duplicate last if odd number
            if len(current) % 2 == 1:
                current = current + [current[-1]]

            # Calculate next level
            next_level = []
            for i in range(0, len(current), 2):
                combined = current[i] + current[i + 1]
                next_level.append(hash256(combined))

            levels.append(next_level)
            current = next_level

        return levels

    def root(self) -> Optional[bytes]:
        """Get Merkle root.

        Returns:
            Merkle root (32 bytes) or None if empty
        """
        if not self.levels:
            return None
        return self.levels[-1][0] if self.levels[-1] else None

    def depth(self) -> int:
        """Get tree depth."""
        return len(self.levels)

    def get_level(self, level: int) -> List[bytes]:
        """Get specific level of the tree.

        Args:
            level: Level index (0 = leaves, root at depth-1)

        Returns:
            List of hashes at that level
        """
        if level < 0 or level >= len(self.levels):
            raise IndexError(f"Level {level} out of range")
        return self.levels[level]

    def get_proof(self, index: int) -> List[bytes]:
        """Get Merkle proof for a leaf.

        Args:
            index: Leaf index

        Returns:
            List of sibling hashes for proof
        """
        if index < 0 or index >= len(self.hashes):
            raise IndexError(f"Index {index} out of range")

        proof = []
        current_index = index

        for level in range(len(self.levels) - 1):
            current_level = self.levels[level]

            # Determine sibling
            if current_index % 2 == 0:
                sibling_index = current_index + 1
                if sibling_index >= len(current_level):
                    sibling_index = current_index
            else:
                sibling_index = current_index - 1

            # Add sibling to proof
            if sibling_index < len(current_level):
                proof.append(current_level[sibling_index])

            # Move to next level
            current_index //= 2

        return proof

    @staticmethod
    def verify_proof(leaf: bytes, proof: List[bytes], root: bytes, index: int) -> bool:
        """Verify a Merkle proof.

        Args:
            leaf: Leaf hash
            proof: List of sibling hashes
            root: Expected Merkle root
            index: Leaf index

        Returns:
            True if proof is valid
        """
        current = leaf
        current_index = index

        for sibling in proof:
            if current_index % 2 == 0:
                combined = current + sibling
            else:
                combined = sibling + current

            current = hash256(combined)
            current_index //= 2

        return current == root

def merkle_root(hashes: List[bytes]) -> Optional[bytes]:
    """Calculate Merkle root from list of hashes.

    Args:
        hashes: List of transaction hashes

    Returns:
        Merkle root or None if empty
    """
    if not hashes:
        return None

    if len(hashes) == 1:
        return hashes[0]

    # Duplicate last if odd
    if len(hashes) % 2 == 1:
        hashes = hashes + [hashes[-1]]

    # Calculate next level
    next_level = []
    for i in range(0, len(hashes), 2):
        combined = hashes[i] + hashes[i + 1]
        next_level.append(hash256(combined))

    return merkle_root(next_level)
