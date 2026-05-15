"""Integration-style admission rollout tests for authority-chain node onboarding."""

import unittest

from node.p2p.authority import NodeAuthorityChain


class TestAuthorityAdmissionRollout(unittest.TestCase):
    def test_second_node_becomes_verifier_and_admits_third(self) -> None:
        """starter -> second verified -> second verifies third."""
        chain = NodeAuthorityChain(
            trusted_nodes=["198.51.100.1:8333"],
            admission_mode="strict",
            min_verifier_votes=1,
        )

        second_joined = chain.verify(
            "198.51.100.1:8333",
            "198.51.100.2:8333",
            verifier_identity="node:198.51.100.1",
        )
        self.assertTrue(second_joined)
        self.assertIn("198.51.100.2", chain.verifiers)

        third_joined = chain.verify(
            "198.51.100.2:8333",
            "198.51.100.3:8333",
            verifier_identity="node:198.51.100.2",
        )
        self.assertTrue(third_joined)
        self.assertTrue(chain.can_accept("198.51.100.3:8333", []))

    def test_duplicate_vote_replay_is_ignored(self) -> None:
        chain = NodeAuthorityChain(
            trusted_nodes=["203.0.113.10:8333"],
            admission_mode="strict",
            min_verifier_votes=2,
        )

        self.assertFalse(
            chain.verify(
                "203.0.113.10:8333",
                "203.0.113.20:8333",
                verifier_identity="pubkey:dup-voter",
            )
        )
        self.assertFalse(
            chain.verify(
                "203.0.113.10:8333",
                "203.0.113.20:8333",
                verifier_identity="pubkey:dup-voter",
            )
        )
        self.assertEqual(chain.get_attestation_vote_count("203.0.113.20:8333"), 1)
        self.assertFalse(chain.can_accept("203.0.113.20:8333", []))

    def test_malicious_verifier_cannot_attest(self) -> None:
        chain = NodeAuthorityChain(
            trusted_nodes=["192.0.2.11:8333"],
            admission_mode="strict",
            min_verifier_votes=1,
        )
        accepted = chain.verify(
            "198.18.0.66:8333",
            "192.0.2.22:8333",
            verifier_identity="pubkey:malicious",
        )
        self.assertFalse(accepted)
        self.assertEqual(chain.get_attestation_vote_count("192.0.2.22:8333"), 0)

    def test_offline_verifier_prevents_assisted_admission(self) -> None:
        chain = NodeAuthorityChain(
            trusted_nodes=["203.0.113.1:8333"],
            admission_mode="assisted",
            min_verifier_votes=1,
        )
        # No connected verifier available.
        self.assertFalse(chain.can_accept("203.0.113.55:8333", []))
        # Connected verifier available.
        self.assertTrue(chain.can_accept("203.0.113.55:8333", ["203.0.113.1:8333"]))


if __name__ == "__main__":
    unittest.main()

