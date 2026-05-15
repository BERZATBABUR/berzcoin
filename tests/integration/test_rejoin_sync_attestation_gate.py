"""Integration-style checks for rejoin catch-up + attestation verifier gating."""

import asyncio
import unittest

from node.p2p.addrman import AddrMan
from node.p2p.connman import ConnectionManager
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.protocol.messages import JoinAttestMessage


class _Cfg:
    def get(self, key, default=None):
        if key == "authority_chain_enabled":
            return True
        if key == "authority_trusted_nodes":
            return []
        if key == "port":
            return 8333
        if key == "network_hardening":
            return False
        return default

    def get_admission_mode(self):
        return "strict"

    def get_min_verifier_votes(self):
        return 1


class _Peer:
    def __init__(self, address: str):
        self.address = address
        self.connected = True
        self.results = []

    async def send_join_result(self, msg) -> None:
        self.results.append(msg)

    async def disconnect(self) -> None:
        self.connected = False


class TestRejoinSyncAttestationGate(unittest.TestCase):
    def test_rejoin_requires_sync_then_valid_attestation(self) -> None:
        async def run() -> None:
            cm = ConnectionManager(AddrMan(), node_config=_Cfg())
            cm.authority_chain.verify_from_local("198.51.100.1:8333")
            cm.chainstate = type("_Chain", (), {"get_best_height": lambda self: 10})()
            remote_high = type("_Remote", (), {"peer_height": 30})()
            remote_synced = type("_Remote", (), {"peer_height": 10})()

            peer = _Peer("198.51.100.200:8333")
            candidate_priv = PrivateKey()
            candidate_pub = candidate_priv.public_key().to_bytes(compressed=True).hex()
            candidate_node_id = "198.51.100.77:8333"

            # Behind tip: verifier role should be blocked.
            cm.get_best_height_peer = lambda: remote_high
            cm._admission_challenges[peer.address] = {
                "challenge_id": 1,
                "challenge": b"challenge-a",
                "expires_at": 1_700_000_060,
                "created_at_ms": 1,
                "candidate_node_id": candidate_node_id,
                "candidate_pubkey": candidate_pub,
            }
            msg_hash_a = cm._build_join_attestation_hash(
                candidate_node_id=candidate_node_id,
                candidate_pubkey_hex=candidate_pub,
                challenge_id=1,
                challenge=b"challenge-a",
            )
            sig_a = sign_message_hash(candidate_priv, msg_hash_a)
            att_a = JoinAttestMessage(
                candidate_node_id=candidate_node_id,
                verifier_node_id="198.51.100.1:8333",
                verifier_pubkey=candidate_pub,
                challenge_id=1,
                signature=sig_a,
                timestamp=1_700_000_000,
            )
            with unittest.mock.patch("time.time", return_value=1_700_000_000.0):
                await cm.handle_join_attest(peer, att_a.serialize())
            self.assertTrue(peer.results)
            self.assertEqual(peer.results[-1].reason, "verifier_not_synced")

            # Caught up: valid attestation should now be accepted.
            cm.get_best_height_peer = lambda: remote_synced
            cm._admission_challenges[peer.address] = {
                "challenge_id": 2,
                "challenge": b"challenge-b",
                "expires_at": 1_700_000_060,
                "created_at_ms": 1,
                "candidate_node_id": candidate_node_id,
                "candidate_pubkey": candidate_pub,
            }
            msg_hash_b = cm._build_join_attestation_hash(
                candidate_node_id=candidate_node_id,
                candidate_pubkey_hex=candidate_pub,
                challenge_id=2,
                challenge=b"challenge-b",
            )
            sig_b = sign_message_hash(candidate_priv, msg_hash_b)
            att_b = JoinAttestMessage(
                candidate_node_id=candidate_node_id,
                verifier_node_id="198.51.100.1:8333",
                verifier_pubkey=candidate_pub,
                challenge_id=2,
                signature=sig_b,
                timestamp=1_700_000_001,
            )
            with unittest.mock.patch("time.time", return_value=1_700_000_001.0):
                await cm.handle_join_attest(peer, att_b.serialize())
            self.assertEqual(peer.results[-1].reason, "accepted")
            self.assertTrue(bool(cm.authority_chain.node_identities.get(candidate_node_id.lower())))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
