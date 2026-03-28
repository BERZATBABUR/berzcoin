"""Transitive node-attestation authority chain."""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Set


class NodeAuthorityChain:
    """Simple trust chain: verified nodes can verify new nodes."""

    def __init__(self, trusted_nodes: Optional[Iterable[str]] = None) -> None:
        self.verified_nodes: Set[str] = set()
        self.verifiers: Set[str] = set()
        self.verified_by: Dict[str, str] = {}
        for n in trusted_nodes or []:
            node = self._normalize(n)
            if not node:
                continue
            self.verified_nodes.add(node)
            self.verifiers.add(node)
            self.verified_by[node] = "bootstrap"

    def verify_from_local(self, target: str) -> bool:
        node = self._normalize(target)
        if not node:
            return False
        self.verified_nodes.add(node)
        self.verifiers.add(node)
        self.verified_by[node] = "local"
        return True

    def verify(self, verifier: str, target: str) -> bool:
        verifier_node = self._normalize(verifier)
        target_node = self._normalize(target)
        if not verifier_node or not target_node:
            return False
        if verifier_node not in self.verifiers:
            return False
        self.verified_nodes.add(target_node)
        # Verified node gets authority to verify next nodes.
        self.verifiers.add(target_node)
        self.verified_by[target_node] = verifier_node
        return True

    def can_accept(self, target: str, connected_peers: Iterable[str]) -> bool:
        target_node = self._normalize(target)
        if not target_node:
            return False
        if target_node in self.verified_nodes:
            return True
        verifier = self.pick_connected_verifier(connected_peers)
        return verifier is not None

    def verify_with_connected_verifier(
        self, target: str, connected_peers: Iterable[str]
    ) -> Optional[str]:
        verifier = self.pick_connected_verifier(connected_peers)
        if not verifier:
            return None
        if self.verify(verifier, target):
            return self._normalize(verifier)
        return None

    def pick_connected_verifier(self, connected_peers: Iterable[str]) -> Optional[str]:
        for peer in connected_peers:
            node = self._normalize(peer)
            if node in self.verifiers:
                return node
        return None

    def get_status(self) -> Dict[str, object]:
        return {
            "verified_nodes": sorted(self.verified_nodes),
            "verifiers": sorted(self.verifiers),
            "verified_by": dict(self.verified_by),
        }

    @staticmethod
    def _normalize(addr: str) -> str:
        raw = (addr or "").strip()
        if not raw:
            return ""
        return raw.rsplit(":", 1)[0] if ":" in raw else raw

