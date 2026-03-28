"""Versionbits deployment registry and network profiles."""

from dataclasses import dataclass
from typing import List, Optional

from .params import ConsensusParams


@dataclass(frozen=True)
class DeploymentDefinition:
    """Declarative BIP9 deployment metadata."""

    name: str
    bit: int
    start_time: int
    timeout: int
    min_activation_height: int = 0


def get_deployment_definitions(network: str = "mainnet") -> List[DeploymentDefinition]:
    """Return deployment definitions for a given network profile."""
    normalized = str(network or "mainnet").strip().lower()

    # Keep regtest easy to signal in local testing while preserving
    # Bitcoin-style deployment descriptors (testdummy/csv/segwit).
    if normalized == "regtest":
        start = 0
        timeout = 2_147_483_647
        return [
            DeploymentDefinition("testdummy", 28, start, timeout),
            DeploymentDefinition("csv", 0, start, timeout),
            DeploymentDefinition("segwit", 1, start, timeout),
        ]

    # Mainnet/testnet default profile.
    return [
        DeploymentDefinition("testdummy", 28, 1199145601, 1230767999),
        DeploymentDefinition("csv", 0, 1462060800, 1493596800),
        DeploymentDefinition("segwit", 1, 1479168000, 1510704000),
    ]


def get_versionbits_deployments(
    params: Optional[ConsensusParams] = None,
    network: Optional[str] = None,
) -> List["VersionBitsDeployment"]:
    """Build ``VersionBitsDeployment`` objects from declarative definitions."""
    from .versionbits import VersionBitsDeployment

    resolved_network = network
    if resolved_network is None:
        resolved_network = params.get_network_name() if params else "mainnet"

    deployments: List[VersionBitsDeployment] = []
    for definition in get_deployment_definitions(resolved_network):
        deployments.append(
            VersionBitsDeployment(
                name=definition.name,
                bit=definition.bit,
                start_time=definition.start_time,
                timeout=definition.timeout,
                min_activation_height=definition.min_activation_height,
            )
        )
    return deployments
