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


if __name__ == "__main__":
    unittest.main()
