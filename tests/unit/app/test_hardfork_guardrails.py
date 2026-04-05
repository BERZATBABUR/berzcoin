"""Unit tests for startup hard-fork guardrails."""

import unittest

from node.app.main import BerzCoinNode
from shared.consensus.buried_deployments import HARDFORK_TX_V2


class _ChainStateStub:
    def __init__(self, height: int, custom_activation_heights):
        self._height = int(height)
        self.params = type(
            "_Params",
            (),
            {"custom_activation_heights": dict(custom_activation_heights)},
        )()

    def get_best_height(self):
        return self._height


class TestHardforkGuardrails(unittest.TestCase):
    def test_startup_blocked_when_consensus_version_too_old_after_activation(self) -> None:
        node = BerzCoinNode()
        node.config.set("enforce_hardfork_guardrails", True)
        node.config.set("node_consensus_version", 1)
        node.chainstate = _ChainStateStub(
            height=300,
            custom_activation_heights={HARDFORK_TX_V2: 250},
        )

        self.assertFalse(node._hardfork_guardrails_ok())

    def test_startup_allowed_when_consensus_version_is_upgraded(self) -> None:
        node = BerzCoinNode()
        node.config.set("enforce_hardfork_guardrails", True)
        node.config.set("node_consensus_version", 2)
        node.chainstate = _ChainStateStub(
            height=300,
            custom_activation_heights={HARDFORK_TX_V2: 250},
        )

        self.assertTrue(node._hardfork_guardrails_ok())

    def test_guardrails_can_be_disabled(self) -> None:
        node = BerzCoinNode()
        node.config.set("enforce_hardfork_guardrails", False)
        node.config.set("node_consensus_version", 1)
        node.chainstate = _ChainStateStub(
            height=300,
            custom_activation_heights={HARDFORK_TX_V2: 250},
        )

        self.assertTrue(node._hardfork_guardrails_ok())


if __name__ == "__main__":
    unittest.main()
