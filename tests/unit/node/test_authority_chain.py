"""Tests for transitive node-attestation authority chain."""

import unittest

from node.p2p.authority import NodeAuthorityChain


class TestNodeAuthorityChain(unittest.TestCase):
    def test_trusted_verifier_can_verify_and_delegate(self) -> None:
        chain = NodeAuthorityChain(trusted_nodes=["10.0.0.1:8333"])

        ok = chain.verify("10.0.0.1:12345", "10.0.0.2:9999")
        self.assertTrue(ok)
        self.assertIn("10.0.0.2", chain.verifiers)
        self.assertIn("10.0.0.2", chain.verified_nodes)

        # Newly verified node can verify another node (transitive authority).
        ok2 = chain.verify("10.0.0.2:8333", "10.0.0.3:8333")
        self.assertTrue(ok2)
        self.assertIn("10.0.0.3", chain.verifiers)

    def test_can_accept_when_connected_verifier_exists(self) -> None:
        chain = NodeAuthorityChain(trusted_nodes=["192.168.1.10"])
        self.assertTrue(chain.can_accept("203.0.113.1:19000", ["192.168.1.10:8333"]))
        self.assertFalse(chain.can_accept("203.0.113.1:19000", ["198.51.100.9:8333"]))


if __name__ == "__main__":
    unittest.main()
