"""Tests for consensus deployment registry helpers."""

import unittest

from shared.consensus.buried_deployments import (
    BuriedDeployment,
    get_buried_deployment_heights,
    is_buried_deployment_active,
)
from shared.consensus.deployments import (
    get_deployment_definitions,
    get_versionbits_deployments,
)
from shared.consensus.params import ConsensusParams
from shared.consensus.versionbits import get_standard_deployments


class TestDeploymentRegistry(unittest.TestCase):
    def test_mainnet_definitions_include_expected_deployments(self) -> None:
        definitions = get_deployment_definitions("mainnet")
        self.assertEqual([d.name for d in definitions], ["testdummy", "csv", "segwit"])
        self.assertEqual([d.bit for d in definitions], [28, 0, 1])

    def test_regtest_has_open_signal_window(self) -> None:
        definitions = get_deployment_definitions("regtest")
        self.assertTrue(all(d.start_time == 0 for d in definitions))
        self.assertTrue(all(d.timeout == 2_147_483_647 for d in definitions))

    def test_standard_deployments_delegate_to_registry(self) -> None:
        params = ConsensusParams.regtest()
        from_standard = get_standard_deployments(params)
        from_registry = get_versionbits_deployments(params=params)

        self.assertEqual([d.name for d in from_standard], [d.name for d in from_registry])
        self.assertEqual([d.bit for d in from_standard], [d.bit for d in from_registry])

    def test_buried_deployment_helpers_use_consensus_params(self) -> None:
        params = ConsensusParams.regtest()
        heights = get_buried_deployment_heights(params)
        self.assertEqual(heights[BuriedDeployment.BIP34], params.bip34_height)
        self.assertEqual(heights[BuriedDeployment.SEGWIT], params.segwit_height)
        self.assertFalse(is_buried_deployment_active(params, "bip34", 1))
        self.assertTrue(is_buried_deployment_active(params, BuriedDeployment.SEGWIT, 0))


if __name__ == "__main__":
    unittest.main()
