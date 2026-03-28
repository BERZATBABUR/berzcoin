"""Unit tests for peer scoring penalties and eviction thresholds."""

import unittest

from node.p2p.peer_scoring import PeerScoringManager


class TestPeerScoringHardening(unittest.TestCase):
    def test_invalid_block_penalty_uses_strict_reason_key(self) -> None:
        scoring = PeerScoringManager()
        addr = "198.51.100.7:8333"
        scoring.record_invalid_block(addr)
        score = scoring.get_score(addr).score
        # -5 from generic failure + -35 strict invalid_block penalty.
        self.assertEqual(score, -40)

    def test_invalid_tx_penalty_uses_strict_reason_key(self) -> None:
        scoring = PeerScoringManager()
        addr = "198.51.100.8:8333"
        scoring.record_invalid_tx(addr)
        score = scoring.get_score(addr).score
        # -5 from generic failure + -15 strict invalid_transaction penalty.
        self.assertEqual(score, -20)


if __name__ == "__main__":
    unittest.main()
