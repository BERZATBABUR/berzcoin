"""Unit tests for connection manager anti-eclipse behavior."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from node.p2p.addrman import AddrMan
from node.p2p.connman import ConnectionManager
from node.p2p.limits import OutboundClass
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.protocol.messages import JoinAttestMessage


class _Cfg:
    def __init__(
        self,
        hardening: bool,
        connect=None,
        addnode=None,
        bootstrap=None,
        bind: str = "0.0.0.0",
        port: int = 8333,
        admission_mode: str = "open",
    ):
        self._hardening = bool(hardening)
        self._connect = list(connect or [])
        self._addnode = list(addnode or [])
        self._bootstrap = list(bootstrap or [])
        self._bind = str(bind)
        self._port = int(port)
        self._admission_mode = str(admission_mode)

    def get(self, key, default=None):
        if key == "network_hardening":
            return self._hardening
        if key == "bootstrap_enabled":
            return bool(self._bootstrap)
        if key == "bind":
            return self._bind
        if key == "port":
            return self._port
        return default

    def get_connect_peers(self):
        return list(self._connect)

    def get_addnode_peers(self):
        return list(self._addnode)

    def get_bootstrap_nodes(self):
        return list(self._bootstrap)

    def get_admission_mode(self):
        return self._admission_mode

    def get_min_verifier_votes(self):
        return 1


class _FakePeer:
    def __init__(self, address: str, connected_at: float):
        host, port = address.rsplit(":", 1)
        self.host = host
        self.port = int(port)
        self.address = address
        self.connected_at = connected_at
        self.connected = True
        self.disconnected = False

    async def disconnect(self, reason: str = "") -> None:
        self.disconnect_reason = reason or getattr(self, "disconnect_reason", "")
        self.connected = False
        self.disconnected = True


class _AttestPeer:
    def __init__(self, address: str):
        self.address = address
        self.connected = True
        self.results = []
        self.disconnected = False

    async def send_join_result(self, msg) -> None:
        self.results.append(msg)

    async def disconnect(self, reason: str = "") -> None:
        _ = reason
        self.disconnected = True
        self.connected = False


class _ViolationPeer:
    def __init__(self, address: str):
        self.address = address
        self.disconnected = False

    async def disconnect(self, reason: str = "") -> None:
        _ = reason
        self.disconnected = True


class TestConnmanHardening(unittest.TestCase):
    def test_network_hardening_flag_defaults_to_false(self) -> None:
        cm = ConnectionManager(AddrMan())
        self.assertFalse(cm.network_hardening)
        self.assertFalse(cm.peer_scores.network_hardening)

    def test_network_hardening_flag_propagates_from_config(self) -> None:
        cm = ConnectionManager(AddrMan(), node_config=_Cfg(True))
        self.assertTrue(cm.network_hardening)
        self.assertTrue(cm.peer_scores.network_hardening)

    def test_anchor_peers_persist_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp)
            addrman1 = AddrMan(data_dir=datadir)
            anchors = ["198.51.100.10:8333", "203.0.113.20:8333"]
            addrman1.set_anchor_peers(anchors)

            addrman2 = AddrMan(data_dir=datadir)
            self.assertEqual(addrman2.get_anchor_peers(), set(anchors))

    def test_hardened_outbound_class_plan_prefers_anchors_and_block_relay(self) -> None:
        cm = ConnectionManager(AddrMan(), max_outbound=6, node_config=_Cfg(True))
        plan = cm._desired_outbound_classes()
        self.assertEqual(plan.count(OutboundClass.ANCHOR), 2)
        self.assertEqual(plan.count(OutboundClass.BLOCK_RELAY_ONLY), 2)
        self.assertEqual(plan.count(OutboundClass.FULL_RELAY), 2)

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

    def test_anchor_protection_keeps_two_anchor_netgroups(self) -> None:
        cm = ConnectionManager(AddrMan(), node_config=_Cfg(True))
        a1 = _FakePeer("198.51.100.9:2001", connected_at=10)
        a2 = _FakePeer("198.51.100.10:2002", connected_at=11)
        b1 = _FakePeer("203.0.113.7:2003", connected_at=12)
        c1 = _FakePeer("192.0.2.5:2004", connected_at=13)
        cm.outbound_peers = {p.address: p for p in (a1, a2, b1, c1)}
        cm.outbound_classes = {
            a1.address: OutboundClass.ANCHOR,
            a2.address: OutboundClass.ANCHOR,
            b1.address: OutboundClass.ANCHOR,
            c1.address: OutboundClass.FULL_RELAY,
        }
        cm.peer_scores.get_score(a1.address).score = -20
        cm.peer_scores.get_score(a2.address).score = 5
        cm.peer_scores.get_score(b1.address).score = 4
        cm.peer_scores.get_score(c1.address).score = -1
        protected = cm._protected_outbound_addresses()
        # Must keep at least one anchor from each of two distinct netgroups.
        self.assertTrue(
            (a1.address in protected or a2.address in protected)
            and (b1.address in protected)
        )

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

    def test_config_source_priority_connect_only_wins(self) -> None:
        cfg = _Cfg(
            True,
            connect=["203.0.113.1:8333"],
            addnode=["198.51.100.2:8333"],
            bootstrap=["192.0.2.3:8333"],
        )
        cm = ConnectionManager(AddrMan(), node_config=cfg, connect_only=True)
        cm._load_peers_from_config()
        addrs = cm.addrman.get_addresses(5)
        self.assertEqual(addrs[:1], ["203.0.113.1:8333"])
        self.assertNotIn("198.51.100.2:8333", addrs)
        self.assertNotIn("192.0.2.3:8333", addrs)

    def test_listener_uses_configured_bind(self) -> None:
        async def run() -> None:
            cfg = _Cfg(False, bind="127.0.0.1", port=18444)
            cm = ConnectionManager(AddrMan(), node_config=cfg)

            fake_server = mock.AsyncMock()
            fake_server.close = mock.Mock()
            fake_server.wait_closed = mock.AsyncMock()

            with mock.patch("asyncio.start_server", new=mock.AsyncMock(return_value=fake_server)) as start_server:
                await cm.start()
                start_server.assert_awaited_once()
                args = start_server.await_args.args
                self.assertEqual(args[1], "127.0.0.1")
                self.assertEqual(args[2], 18444)
                await cm.stop()

        asyncio.run(run())

    def test_strict_mode_outbound_connect_does_not_auto_verify_local(self) -> None:
        async def run() -> None:
            cfg = _Cfg(False, admission_mode="strict")
            cm = ConnectionManager(AddrMan(), node_config=cfg)

            class _OutboundPeer:
                def __init__(self, host: str, port: int, is_outbound: bool = True):
                    self.host = host
                    self.port = int(port)
                    self.is_outbound = bool(is_outbound)
                    self.connected = True
                    self.on_message = None
                    self.on_disconnect = None
                    self.connected_at = 0.0

                @property
                def address(self) -> str:
                    return f"{self.host}:{self.port}"

                async def connect(self) -> bool:
                    return True

            with mock.patch("node.p2p.connman.Peer", _OutboundPeer):
                with mock.patch.object(cm.authority_chain, "verify_from_local", wraps=cm.authority_chain.verify_from_local) as verify_local:
                    ok = await cm._connect_outbound_address("198.51.100.10:8333", OutboundClass.FULL_RELAY)
                    self.assertTrue(ok)
                    verify_local.assert_not_called()

        asyncio.run(run())

    def test_protocol_violation_threshold_disconnects_peer(self) -> None:
        async def run() -> None:
            cm = ConnectionManager(AddrMan())
            peer = _ViolationPeer("198.51.100.50:8333")
            await cm._on_peer_protocol_violation(peer, "invalid_command")
            self.assertFalse(peer.disconnected)
            await cm._on_peer_protocol_violation(peer, "malformed_message")
            self.assertFalse(peer.disconnected)
            await cm._on_peer_protocol_violation(peer, "oversized_payload")
            self.assertTrue(peer.disconnected)

        asyncio.run(run())

    def test_disconnect_reason_is_tracked(self) -> None:
        async def run() -> None:
            cm = ConnectionManager(AddrMan())
            peer = _FakePeer("198.51.100.80:8333", connected_at=1.0)
            peer.disconnect_reason = "idle_timeout"
            cm.peers[peer.address] = peer
            await cm._on_peer_disconnect(peer)
            self.assertEqual(cm._disconnect_reasons.get("idle_timeout"), 1)

        asyncio.run(run())

    def test_idle_timeout_disconnects_stale_peer(self) -> None:
        async def run() -> None:
            cm = ConnectionManager(AddrMan())
            cm._idle_timeout_secs = 1
            peer = _FakePeer("198.51.100.81:8333", connected_at=1.0)
            peer.last_message_at = asyncio.get_event_loop().time() - 5
            cm.peers[peer.address] = peer
            await cm._drop_idle_peers()
            self.assertTrue(peer.disconnected)

        asyncio.run(run())

    def test_dead_peer_cleanup_removes_all_indexes(self) -> None:
        async def run() -> None:
            cm = ConnectionManager(AddrMan())
            peer = _FakePeer("198.51.100.60:8333", connected_at=1.0)
            cm.peers[peer.address] = peer
            cm.outbound_peers[peer.address] = peer
            cm.outbound_classes[peer.address] = OutboundClass.FULL_RELAY
            cm._pending_outbound_class[peer.address] = OutboundClass.FULL_RELAY
            cm._peer_violation_counts[peer.address] = 2
            await cm._on_peer_disconnect(peer)
            self.assertNotIn(peer.address, cm.peers)
            self.assertNotIn(peer.address, cm.outbound_peers)
            self.assertNotIn(peer.address, cm.outbound_classes)
            self.assertNotIn(peer.address, cm._pending_outbound_class)
            self.assertNotIn(peer.address, cm._peer_violation_counts)

        asyncio.run(run())

    def test_verifier_readiness_blocks_attestation_when_node_is_behind(self) -> None:
        async def run() -> None:
            cfg = _Cfg(False, admission_mode="strict")
            cm = ConnectionManager(AddrMan(), node_config=cfg)
            cm.authority_chain_enabled = True
            cm.chainstate = type("_Chain", (), {"get_best_height": lambda self: 10})()
            best_peer = type("_Peer", (), {"peer_height": 25})()
            cm.get_best_height_peer = lambda: best_peer

            peer = _AttestPeer("198.51.100.10:8333")
            cm._admission_challenges[peer.address] = {
                "challenge_id": 111,
                "challenge": b"abc",
                "expires_at": 1_700_000_060,
                "created_at_ms": 1,
                "candidate_node_id": "node:candidate",
                "candidate_pubkey": "02" + ("11" * 32),
            }
            att = JoinAttestMessage(
                candidate_node_id="node:candidate",
                verifier_node_id="node:verifier",
                verifier_pubkey="02" + ("11" * 32),
                challenge_id=111,
                signature=b"\x30\x06\x02\x01\x01\x02\x01\x01",
                timestamp=1_700_000_000,
            )
            with mock.patch("time.time", return_value=1_700_000_000.0):
                await cm.handle_join_attest(peer, att.serialize())
            self.assertTrue(peer.results)
            self.assertEqual(peer.results[-1].reason, "verifier_not_synced")

        asyncio.run(run())

    def test_attestation_replay_and_time_skew_are_rejected(self) -> None:
        async def run() -> None:
            cfg = _Cfg(False, admission_mode="strict")
            cm = ConnectionManager(AddrMan(), node_config=cfg)
            cm.authority_chain_enabled = True
            cm.chainstate = type("_Chain", (), {"get_best_height": lambda self: 100})()
            cm.get_best_height_peer = lambda: type("_Peer", (), {"peer_height": 100})()
            cm.authority_chain.verify_from_local("198.51.100.1:8333")

            peer = _AttestPeer("198.51.100.10:8333")
            candidate_key = PrivateKey()
            candidate_pub = candidate_key.public_key().to_bytes(compressed=True).hex()
            candidate_node_id = "198.51.100.77:8333"

            now = 1_700_000_000
            cm._admission_challenges[peer.address] = {
                "challenge_id": 42,
                "challenge": b"challenge-x",
                "expires_at": now + 60,
                "created_at_ms": now * 1000,
                "candidate_node_id": candidate_node_id,
                "candidate_pubkey": candidate_pub,
            }
            msg_hash = cm._build_join_attestation_hash(
                candidate_node_id=candidate_node_id,
                candidate_pubkey_hex=candidate_pub,
                challenge_id=42,
                challenge=b"challenge-x",
            )
            sig = sign_message_hash(candidate_key, msg_hash)
            good_att = JoinAttestMessage(
                candidate_node_id=candidate_node_id,
                verifier_node_id="198.51.100.1:8333",
                verifier_pubkey=candidate_pub,
                challenge_id=42,
                signature=sig,
                timestamp=now,
            )
            with mock.patch("time.time", return_value=float(now)):
                await cm.handle_join_attest(peer, good_att.serialize())
            self.assertTrue(peer.results)
            self.assertEqual(peer.results[-1].reason, "accepted")

            # Reuse same challenge/attestation => replay reject.
            cm._admission_challenges[peer.address] = {
                "challenge_id": 42,
                "challenge": b"challenge-x",
                "expires_at": now + 60,
                "created_at_ms": now * 1000,
                "candidate_node_id": candidate_node_id,
                "candidate_pubkey": candidate_pub,
            }
            with mock.patch("time.time", return_value=float(now)):
                await cm.handle_join_attest(peer, good_att.serialize())
            self.assertEqual(peer.results[-1].reason, "attestation_replay")

            # New challenge but stale timestamp => skew reject.
            cm._admission_challenges[peer.address] = {
                "challenge_id": 43,
                "challenge": b"challenge-y",
                "expires_at": now + 60,
                "created_at_ms": now * 1000,
                "candidate_node_id": candidate_node_id,
                "candidate_pubkey": candidate_pub,
            }
            msg_hash2 = cm._build_join_attestation_hash(
                candidate_node_id=candidate_node_id,
                candidate_pubkey_hex=candidate_pub,
                challenge_id=43,
                challenge=b"challenge-y",
            )
            sig2 = sign_message_hash(candidate_key, msg_hash2)
            skew_att = JoinAttestMessage(
                candidate_node_id=candidate_node_id,
                verifier_node_id="198.51.100.1:8333",
                verifier_pubkey=candidate_pub,
                challenge_id=43,
                signature=sig2,
                timestamp=now - 10_000,
            )
            with mock.patch("time.time", return_value=float(now)):
                await cm.handle_join_attest(peer, skew_att.serialize())
            self.assertEqual(peer.results[-1].reason, "attestation_time_skew")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
