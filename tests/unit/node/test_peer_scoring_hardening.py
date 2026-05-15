"""Unit tests for peer scoring penalties and eviction thresholds."""

import tempfile
import unittest
from pathlib import Path

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

    def test_repeated_malformed_messages_trigger_ban(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scoring = PeerScoringManager()
            scoring.configure_persistence(Path(tmp))
            addr = "198.51.100.9:8333"
            for _ in range(10):
                scoring.record_bad(addr, "protocol_violation")
            self.assertTrue(scoring.is_banned(addr))

    def test_single_duplicate_like_event_does_not_ban(self) -> None:
        scoring = PeerScoringManager()
        addr = "198.51.100.10:8333"
        scoring.record_bad(addr, "relay_spam")
        self.assertFalse(scoring.is_banned(addr))


if __name__ == "__main__":
    unittest.main()
