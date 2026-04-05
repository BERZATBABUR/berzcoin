"""Tests for consensus deployment registry helpers."""

import unittest

from shared.consensus.buried_deployments import (
    SOFTFORK_BIP34_STRICT,
    BuriedDeployment,
    get_buried_deployment_heights,
    get_custom_deployment_height,
    is_consensus_feature_active,
    is_custom_deployment_active,
    is_buried_deployment_active,
)
from shared.consensus.deployments import (
    get_deployment_definitions,
    get_versionbits_deployments,
)
from shared.consensus.params import ConsensusParams
from shared.consensus.versionbits import get_standard_deployments
from shared.consensus.versionbits import DeploymentState, VersionBitsDeployment, VersionBitsTracker


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

    def test_custom_deployment_helpers_use_custom_activation_map(self) -> None:
        params = ConsensusParams.regtest()
        params.custom_activation_heights = {
            "softfork_v2": 120,
            "hardfork_v2": 250,
        }

        self.assertEqual(get_custom_deployment_height(params, "softfork_v2"), 120)
        self.assertFalse(is_custom_deployment_active(params, "softfork_v2", 119))
        self.assertTrue(is_custom_deployment_active(params, "softfork_v2", 120))
        self.assertFalse(is_consensus_feature_active(params, "hardfork_v2", 249))
        self.assertTrue(is_consensus_feature_active(params, "hardfork_v2", 250))

    def test_versionbits_tracker_signaling_mask_and_block_version(self) -> None:
        d = VersionBitsDeployment("csv", bit=0, start_time=0, timeout=2_147_483_647)
        t = VersionBitsTracker([d])
        self.assertEqual(t.get_signaling_mask(), 0)

        d.state = DeploymentState.STARTED
        self.assertEqual(t.get_signaling_mask(), 1 << 0)
        self.assertEqual(t.get_block_version(0x20000000), 0x20000001)

        d.state = DeploymentState.ACTIVE
        self.assertEqual(t.get_signaling_mask(), 0)

    def test_is_consensus_feature_active_can_use_versionbits_tracker(self) -> None:
        params = ConsensusParams.regtest()
        d = VersionBitsDeployment(SOFTFORK_BIP34_STRICT, bit=7, start_time=0, timeout=2_147_483_647)
        t = VersionBitsTracker([d])
        d.state = DeploymentState.ACTIVE
        setattr(params, "versionbits_tracker", t)
        self.assertTrue(is_consensus_feature_active(params, SOFTFORK_BIP34_STRICT, 0))


if __name__ == "__main__":
    unittest.main()
