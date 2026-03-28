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


if __name__ == "__main__":
    unittest.main()

