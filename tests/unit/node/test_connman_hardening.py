"""Unit tests for connection manager anti-eclipse behavior."""

import asyncio
import unittest

from node.p2p.addrman import AddrMan
from node.p2p.connman import ConnectionManager


class _FakePeer:
    def __init__(self, address: str, connected_at: float):
        host, port = address.rsplit(":", 1)
        self.host = host
        self.port = int(port)
        self.address = address
        self.connected_at = connected_at
        self.connected = True
        self.disconnected = False

    async def disconnect(self) -> None:
        self.connected = False
        self.disconnected = True


class TestConnmanHardening(unittest.TestCase):
    def test_netgroup_reduction(self) -> None:
        cm = ConnectionManager(AddrMan())
        self.assertEqual(cm._netgroup_for_address("203.0.113.7:8333"), "203.0")
        host, port = cm._split_host_port("[2001:db8::1]:18444", 8333)
        self.assertEqual(host, "2001:db8::1")
        self.assertEqual(port, 18444)

    def test_evicts_worst_inbound_for_better_candidate(self) -> None:
        async def run() -> None:
            cm = ConnectionManager(AddrMan())
            bad = _FakePeer("198.51.100.9:2001", connected_at=10)
            good = _FakePeer("203.0.113.7:2002", connected_at=20)
            cm.inbound_peers = {bad.address: bad, good.address: good}
            cm.peers = dict(cm.inbound_peers)

            cm.peer_scores.get_score(bad.address).score = -30
            cm.peer_scores.get_score(good.address).score = 5
            cm.peer_scores.get_score("192.0.2.77:3000").score = 0

            self.assertTrue(await cm._evict_worst_inbound_for("192.0.2.77:3000"))
            self.assertTrue(bad.disconnected)

        asyncio.run(run())

    def test_filter_and_add_addrs_rejects_invalid_and_penalizes_spam(self) -> None:
        cm = ConnectionManager(AddrMan())
        addrs = [
            "8.8.8.8:8333",   # valid global
            "8.8.8.8:8333",   # duplicate
            "10.1.1.1:8333",  # private
            "127.0.0.1:8333", # loopback
            "invalid:port",   # malformed
        ]
        added = cm.filter_and_add_addrs("198.51.100.20:8333", addrs)
        self.assertEqual(added, 1)

        spam_addrs = [f"1.1.1.{i % 250}:8333" for i in range(1200)]
        before = cm.peer_scores.get_score("198.51.100.20:8333").score
        cm.filter_and_add_addrs("198.51.100.20:8333", spam_addrs)
        after = cm.peer_scores.get_score("198.51.100.20:8333").score
        self.assertLess(after, before)

    def test_outbound_eviction_respects_diversity_protection(self) -> None:
        cm = ConnectionManager(AddrMan())
        a_bad = _FakePeer("198.51.100.9:2001", connected_at=10)
        a_good = _FakePeer("198.51.100.10:2002", connected_at=20)
        b_bad = _FakePeer("203.0.113.7:2003", connected_at=30)
        cm.outbound_peers = {p.address: p for p in (a_bad, a_good, b_bad)}

        cm.peer_scores.get_score(a_bad.address).score = -60
        cm.peer_scores.get_score(a_good.address).score = 25
        cm.peer_scores.get_score(b_bad.address).score = -55

        victim = cm._select_outbound_eviction_candidate()
        self.assertIsNotNone(victim)
        self.assertEqual(victim.address, a_bad.address)

    def test_inbound_eviction_prefers_redundant_netgroup(self) -> None:
        async def run() -> None:
            cm = ConnectionManager(AddrMan())
            p1 = _FakePeer("198.51.100.10:2001", connected_at=10)
            p2 = _FakePeer("198.51.100.11:2002", connected_at=20)
            p3 = _FakePeer("203.0.113.7:2003", connected_at=30)
            cm.inbound_peers = {p.address: p for p in (p1, p2, p3)}
            cm.peers = dict(cm.inbound_peers)

            cm.peer_scores.get_score(p1.address).score = 5
            cm.peer_scores.get_score(p2.address).score = 2
            cm.peer_scores.get_score(p3.address).score = -1
            cm.peer_scores.get_score("192.0.2.77:3000").score = -10

            self.assertTrue(await cm._evict_worst_inbound_for("192.0.2.77:3000"))
            self.assertTrue(p2.disconnected)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
