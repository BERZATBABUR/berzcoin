"""Unit tests for control RPC observability endpoints."""

import asyncio
import unittest

from node.rpc.handlers.control import ControlHandlers


class _HealthStub:
    async def check(self):
        return {"status": "healthy", "checks": {}}

    def is_ready(self):
        return True


class _MetricsStub:
    def get_metrics(self):
        return {"node": {"best_height": 10}}

    def get_rate(self):
        return {"blocks_per_second": 1.0}


class _NodeStub:
    def __init__(self):
        self.health_checker = _HealthStub()
        self.metrics_collector = _MetricsStub()
        self.mempool = type(
            "_Mempool",
            (),
            {
                "last_reject_reason": "empty_output_script",
                "reject_reason_counts": {
                    "empty_output_script": 2,
                    "zero_output": 1,
                    "script_verification_failed": 4,
                },
            },
        )()


class _ConnmanStub:
    def __init__(self):
        self.peers = {"a": object(), "b": object()}
        self.inbound_peers = {"a": object()}
        self.outbound_peers = {"b": object()}
        self.authority_chain_enabled = True
        self.authority_chain = type(
            "_Authority",
            (),
            {
                "get_status": lambda self: {"verified_nodes": ["n1"], "verifiers": ["v1"], "verified_by": {"n1": "v1"}},
            },
        )()

    def get_admission_metrics(self):
        return {
            "pending_join_count": 2,
            "verify_latency_ms_avg": 14.5,
            "verify_latency_samples": 4,
            "rejection_reasons": {"insufficient_attestations": 1},
            "verifier_activity": {"node:v1": 3},
        }


class _ChainStub:
    params = None

    def get_best_block_hash(self):
        return None

    def get_best_height(self):
        return 0


class TestControlObservability(unittest.TestCase):
    def test_health_readiness_metrics(self) -> None:
        async def run() -> None:
            handlers = ControlHandlers(_NodeStub())
            health = await handlers.get_health()
            ready = await handlers.get_readiness()
            metrics = await handlers.get_metrics()

            self.assertEqual(health.get("status"), "healthy")
            self.assertTrue(ready.get("ready"))
            self.assertIn("metrics", metrics)
            self.assertIn("rates", metrics)

        asyncio.run(run())

    def test_get_network_info_includes_admission_metrics(self) -> None:
        async def run() -> None:
            node = _NodeStub()
            node.connman = _ConnmanStub()
            node.chainstate = _ChainStub()
            handlers = ControlHandlers(node)
            info = await handlers.get_network_info()
            self.assertIn("admission_metrics", info)
            self.assertEqual(info["admission_metrics"].get("pending_join_count"), 2)
            self.assertIn("authority_chain", info)
            self.assertIn("mempool_observability", info)
            tx_rejects = info["mempool_observability"]["tx_validation_rejects"]
            self.assertEqual(tx_rejects.get("zero_output"), 1)
            self.assertEqual(tx_rejects.get("empty_output_script"), 2)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
