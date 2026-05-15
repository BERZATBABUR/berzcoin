"""Transitive node-attestation authority chain with persisted attestations."""

from __future__ import annotations

import time
from typing import Dict, Iterable, Optional, Set, Any

from node.storage.authority_store import AuthorityStore


class NodeAuthorityChain:
    """Trust chain where trusted verifiers attest candidate nodes."""

    def __init__(
        self,
        trusted_nodes: Optional[Iterable[str]] = None,
        *,
        min_verifier_votes: int = 1,
        admission_mode: str = "open",
        store: Optional[AuthorityStore] = None,
    ) -> None:
        self.min_verifier_votes = max(1, int(min_verifier_votes))
        self.admission_mode = self._normalize_mode(admission_mode)
        self.store = store
        self.verified_nodes: Set[str] = set()
        self.verifiers: Set[str] = set()
        self.verifier_ids: Set[str] = set()
        self.verified_by: Dict[str, str] = {}
        # Stable node identity mapping used for rejoin continuity checks.
        self.node_identities: Dict[str, str] = {}
        # candidate -> verifier_id -> attestation metadata
        self.attestations: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._load()
        for n in trusted_nodes or []:
            node = self._normalize(n)
            if not node:
                continue
            self.verified_nodes.add(node)
            self.verifiers.add(node)
            vid = self._default_verifier_id(node)
            self.verifier_ids.add(vid)
            self.verified_by[node] = "bootstrap"
            self.attestations.setdefault(node, {})
            self.attestations[node][vid] = {
                "verifier_id": vid,
                "verifier_node": node,
                "source": "bootstrap",
                "timestamp": int(time.time()),
            }
        self._persist()

    def verify_from_local(self, target: str) -> bool:
        node = self._normalize(target)
        if not node:
            return False
        return self.attest(
            target=node,
            verifier=node,
            verifier_identity=self._default_verifier_id(node),
            source="local",
            force_accept=True,
        )

    def verify(
        self,
        verifier: str,
        target: str,
        verifier_identity: Optional[str] = None,
    ) -> bool:
        return self.attest(
            target=target,
            verifier=verifier,
            verifier_identity=verifier_identity,
            source="transitive",
            force_accept=False,
        )

    def attest(
        self,
        *,
        target: str,
        verifier: str,
        verifier_identity: Optional[str] = None,
        source: str = "attestation",
        force_accept: bool = False,
    ) -> bool:
        verifier_node = self._normalize(verifier)
        target_node = self._normalize(target)
        if not verifier_node or not target_node:
            return False
        verifier_id = self._normalize_verifier_id(
            verifier_identity or self._default_verifier_id(verifier_node)
        )
        if not verifier_id:
            return False
        if (not force_accept) and verifier_node not in self.verifiers and verifier_id not in self.verifier_ids:
            return False
        self.verifier_ids.add(verifier_id)

        bucket = self.attestations.setdefault(target_node, {})
        if verifier_id not in bucket:
            bucket[verifier_id] = {
                "verifier_id": verifier_id,
                "verifier_node": verifier_node,
                "source": source,
                "timestamp": int(time.time()),
            }
        accepted = force_accept or self._attestation_vote_count(target_node) >= self.min_verifier_votes
        if accepted:
            self._accept_verified(target_node, verifier_id)
        self._persist()
        return accepted

    def register_node_identity(self, node_id: str, pubkey_hex: str) -> bool:
        """Register/revalidate stable node identity pubkey for rejoin continuity."""
        nid = str(node_id or "").strip().lower()
        pkh = str(pubkey_hex or "").strip().lower()
        if not nid or not pkh:
            return False
        existing = self.node_identities.get(nid)
        if existing and existing != pkh:
            return False
        self.node_identities[nid] = pkh
        self._persist()
        return True

    def validate_rejoin_identity(self, node_id: str, pubkey_hex: str) -> bool:
        """If node_id is known, enforce same historical pubkey; otherwise allow."""
        nid = str(node_id or "").strip().lower()
        pkh = str(pubkey_hex or "").strip().lower()
        if not nid or not pkh:
            return False
        existing = self.node_identities.get(nid)
        if existing is None:
            return True
        return existing == pkh

    def can_accept(self, target: str, connected_peers: Iterable[str]) -> bool:
        target_node = self._normalize(target)
        if not target_node:
            return False
        if self.admission_mode == "open":
            return True
        if target_node in self.verified_nodes:
            return True
        if self.admission_mode == "strict":
            return self._attestation_vote_count(target_node) >= self.min_verifier_votes
        verifier = self.pick_connected_verifier(connected_peers)
        return verifier is not None

    def verify_with_connected_verifier(
        self,
        target: str,
        connected_peers: Iterable[str],
        verifier_identity: Optional[str] = None,
    ) -> Optional[str]:
        verifier = self.pick_connected_verifier(connected_peers)
        if not verifier:
            return None
        if self.verify(verifier, target, verifier_identity=verifier_identity):
            return self._normalize_verifier_id(verifier_identity or self._default_verifier_id(verifier))
        return None

    def pick_connected_verifier(self, connected_peers: Iterable[str]) -> Optional[str]:
        for peer in connected_peers:
            node = self._normalize(peer)
            if node in self.verifiers:
                return node
        return None

    def get_status(self) -> Dict[str, object]:
        return {
            "admission_mode": self.admission_mode,
            "min_verifier_votes": int(self.min_verifier_votes),
            "verified_nodes": sorted(self.verified_nodes),
            "verifiers": sorted(self.verifiers),
            "verifier_ids": sorted(self.verifier_ids),
            "verified_by": dict(self.verified_by),
            "attestations": {
                node: sorted(votes.keys())
                for node, votes in sorted(self.attestations.items())
            },
            "attestation_counts": {
                node: len(votes)
                for node, votes in sorted(self.attestations.items())
            },
            "node_identities_count": len(self.node_identities),
        }

    @staticmethod
    def _normalize(addr: str) -> str:
        raw = (addr or "").strip()
        if not raw:
            return ""
        return raw.rsplit(":", 1)[0] if ":" in raw else raw

    @staticmethod
    def _normalize_verifier_id(verifier_id: str) -> str:
        return str(verifier_id or "").strip().lower()

    def _default_verifier_id(self, node: str) -> str:
        return f"node:{self._normalize(node)}"

    def _accept_verified(self, node: str, verified_by_id: str) -> None:
        self.verified_nodes.add(node)
        self.verifiers.add(node)
        self.verifier_ids.add(self._default_verifier_id(node))
        self.verified_by.setdefault(node, verified_by_id)

    def _attestation_vote_count(self, node: str) -> int:
        return len(self.attestations.get(node, {}))

    def get_attestation_vote_count(self, target: str) -> int:
        """Return current vote count for a candidate target node."""
        node = self._normalize(target)
        if not node:
            return 0
        return self._attestation_vote_count(node)

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = str(mode or "open").strip().lower()
        if normalized in {"open", "assisted", "strict"}:
            return normalized
        return "open"

    def _load(self) -> None:
        if self.store is None:
            return
        payload = self.store.load()
        if not isinstance(payload, dict):
            return
        self.verified_nodes.update(
            self._normalize(v) for v in payload.get("verified_nodes", []) if self._normalize(v)
        )
        self.verifiers.update(
            self._normalize(v) for v in payload.get("verifiers", []) if self._normalize(v)
        )
        self.verifier_ids.update(
            self._normalize_verifier_id(v) for v in payload.get("verifier_ids", []) if self._normalize_verifier_id(v)
        )
        raw_verified_by = payload.get("verified_by", {})
        if isinstance(raw_verified_by, dict):
            self.verified_by.update(
                {
                    self._normalize(k): self._normalize_verifier_id(v)
                    for k, v in raw_verified_by.items()
                    if self._normalize(k) and self._normalize_verifier_id(v)
                }
            )
        raw_attestations = payload.get("attestations", {})
        if isinstance(raw_attestations, dict):
            for node, by_verifier in raw_attestations.items():
                normalized_node = self._normalize(node)
                if not normalized_node or not isinstance(by_verifier, dict):
                    continue
                bucket: Dict[str, Dict[str, Any]] = {}
                for vid, meta in by_verifier.items():
                    normalized_id = self._normalize_verifier_id(vid)
                    if not normalized_id:
                        continue
                    if not isinstance(meta, dict):
                        meta = {}
                    bucket[normalized_id] = {
                        "verifier_id": normalized_id,
                        "verifier_node": self._normalize(meta.get("verifier_node", "")),
                        "source": str(meta.get("source", "loaded")),
                        "timestamp": int(meta.get("timestamp", int(time.time()))),
                    }
                if bucket:
                    self.attestations[normalized_node] = bucket
        raw_identities = payload.get("node_identities", {})
        if isinstance(raw_identities, dict):
            for node_id, pubkey in raw_identities.items():
                nid = str(node_id or "").strip().lower()
                pkh = str(pubkey or "").strip().lower()
                if nid and pkh:
                    self.node_identities[nid] = pkh

    def _persist(self) -> None:
        if self.store is None:
            return
        payload = {
            "verified_nodes": sorted(self.verified_nodes),
            "verifiers": sorted(self.verifiers),
            "verifier_ids": sorted(self.verifier_ids),
            "verified_by": dict(self.verified_by),
            "attestations": self.attestations,
            "node_identities": dict(self.node_identities),
            "admission_mode": self.admission_mode,
            "min_verifier_votes": int(self.min_verifier_votes),
        }
        self.store.save(payload)
