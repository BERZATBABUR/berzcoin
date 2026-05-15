"""Tests for transitive node-attestation authority chain."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from node.p2p.authority import NodeAuthorityChain
from node.storage.authority_store import AuthorityStore


class TestNodeAuthorityChain(unittest.TestCase):
    def test_trusted_verifier_can_attest_and_delegate_in_strict_mode(self) -> None:
        chain = NodeAuthorityChain(
            trusted_nodes=["10.0.0.1:8333"],
            admission_mode="strict",
            min_verifier_votes=1,
        )

        ok = chain.verify(
            "10.0.0.1:12345",
            "10.0.0.2:9999",
            verifier_identity="pubkey:aaaaaaaa",
        )
        self.assertTrue(ok)
        self.assertIn("10.0.0.2", chain.verifiers)
        self.assertIn("10.0.0.2", chain.verified_nodes)

        # Newly verified node can verify another node (transitive authority).
        ok2 = chain.verify(
            "10.0.0.2:8333",
            "10.0.0.3:8333",
            verifier_identity="node:10.0.0.2",
        )
        self.assertTrue(ok2)
        self.assertIn("10.0.0.3", chain.verifiers)

    def test_assisted_mode_accepts_when_connected_verifier_exists(self) -> None:
        chain = NodeAuthorityChain(trusted_nodes=["192.168.1.10"], admission_mode="assisted")
        self.assertTrue(chain.can_accept("203.0.113.1:19000", ["192.168.1.10:8333"]))
        self.assertFalse(chain.can_accept("203.0.113.1:19000", ["198.51.100.9:8333"]))

    def test_strict_mode_requires_vote_threshold(self) -> None:
        chain = NodeAuthorityChain(
            trusted_nodes=["10.0.0.1"],
            admission_mode="strict",
            min_verifier_votes=2,
        )
        # One vote only: candidate not yet accepted.
        self.assertFalse(
            chain.verify("10.0.0.1:8333", "10.0.0.5:8333", verifier_identity="pubkey:v1")
        )
        self.assertFalse(chain.can_accept("10.0.0.5:8333", []))
        # Second distinct verifier attestation reaches threshold.
        chain.verify_from_local("10.0.0.2")
        self.assertTrue(
            chain.verify("10.0.0.2:8333", "10.0.0.5:8333", verifier_identity="pubkey:v2")
        )
        self.assertTrue(chain.can_accept("10.0.0.5:8333", []))

    def test_persists_attestations_and_verifier_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            store = AuthorityStore(Path(tmp))
            chain = NodeAuthorityChain(
                trusted_nodes=["198.51.100.10:8333"],
                admission_mode="strict",
                min_verifier_votes=1,
                store=store,
            )
            chain.verify(
                "198.51.100.10:8333",
                "198.51.100.22:8333",
                verifier_identity="pubkey:deadbeef",
            )

            restored = NodeAuthorityChain(
                trusted_nodes=[],
                admission_mode="strict",
                min_verifier_votes=1,
                store=store,
            )
            status = restored.get_status()
            self.assertIn("198.51.100.22", status["verified_nodes"])
            self.assertIn("pubkey:deadbeef", status["verifier_ids"])

    def test_duplicate_verifier_vote_does_not_increment_threshold(self) -> None:
        chain = NodeAuthorityChain(
            trusted_nodes=["10.10.0.1"],
            admission_mode="strict",
            min_verifier_votes=2,
        )
        first = chain.verify(
            "10.10.0.1:8333",
            "10.10.0.9:8333",
            verifier_identity="pubkey:same-verifier",
        )
        self.assertFalse(first)
        votes_after_first = chain.get_attestation_vote_count("10.10.0.9:8333")
        second = chain.verify(
            "10.10.0.1:9000",
            "10.10.0.9:8333",
            verifier_identity="pubkey:same-verifier",
        )
        self.assertFalse(second)
        votes_after_second = chain.get_attestation_vote_count("10.10.0.9:8333")
        self.assertEqual(votes_after_first, 1)
        self.assertEqual(votes_after_second, 1)
        self.assertFalse(chain.can_accept("10.10.0.9:8333", []))

    def test_untrusted_verifier_is_rejected(self) -> None:
        chain = NodeAuthorityChain(
            trusted_nodes=["192.0.2.10"],
            admission_mode="strict",
            min_verifier_votes=1,
        )
        ok = chain.verify(
            "203.0.113.50:8333",
            "203.0.113.60:8333",
            verifier_identity="pubkey:malicious",
        )
        self.assertFalse(ok)
        self.assertEqual(chain.get_attestation_vote_count("203.0.113.60:8333"), 0)
        self.assertFalse(chain.can_accept("203.0.113.60:8333", []))

    def test_rejoin_identity_must_match_known_pubkey(self) -> None:
        chain = NodeAuthorityChain(
            trusted_nodes=["198.51.100.9"],
            admission_mode="strict",
            min_verifier_votes=1,
        )
        self.assertTrue(chain.register_node_identity("node:abc", "02" + ("22" * 32)))
        self.assertTrue(chain.validate_rejoin_identity("node:abc", "02" + ("22" * 32)))
        self.assertFalse(chain.validate_rejoin_identity("node:abc", "02" + ("33" * 32)))


if __name__ == "__main__":
    unittest.main()
