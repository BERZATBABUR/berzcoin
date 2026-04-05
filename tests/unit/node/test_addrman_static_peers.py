"""AddrMan regressions for static/addnode retry behavior."""

import unittest

from node.p2p.addrman import AddrMan


class TestAddrManStaticPeers(unittest.TestCase):
    def test_static_peers_are_prioritized_even_after_failure(self) -> None:
        am = AddrMan()
        am.add_static_peer("127.0.0.1:18444")
        am.mark_failed("127.0.0.1:18444")

        # Static peers must still be returned immediately so connman can retry
        # quickly during startup races.
        addrs = am.get_addresses(1)
        self.assertIn("127.0.0.1:18444", addrs)

    def test_static_peer_priority_orders_sources(self) -> None:
        am = AddrMan()
        am.add_static_peer("198.51.100.10:8333", priority=20)  # bootstrap
        am.add_static_peer("198.51.100.11:8333", priority=10)  # addnode
        am.add_static_peer("198.51.100.12:8333", priority=0)   # connect

        addrs = am.get_addresses(3)
        self.assertEqual(
            addrs[:3],
            [
                "198.51.100.12:8333",
                "198.51.100.11:8333",
                "198.51.100.10:8333",
            ],
        )


if __name__ == "__main__":
    unittest.main()
