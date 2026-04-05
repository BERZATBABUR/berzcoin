"""Version bits deployment tracking (BIP9)."""

from typing import Dict, List, Optional
from enum import IntEnum

from .params import ConsensusParams

class DeploymentState(IntEnum):
    """Deployment state for version bits."""
    DEFINED = 0
    STARTED = 1
    LOCKED_IN = 2
    ACTIVE = 3
    FAILED = 4

class VersionBitsDeployment:
    """Version bits deployment tracking."""

    def __init__(self, name: str, bit: int, start_time: int, timeout: int,
                 min_activation_height: int = 0):
        self.name = name
        self.bit = bit
        self.start_time = start_time
        self.timeout = timeout
        self.min_activation_height = min_activation_height
        self.state = DeploymentState.DEFINED
        self.since_height = 0

    def is_supported(self, version: int) -> bool:
        return (version >> self.bit) & 1 == 1

class VersionBitsTracker:
    """Track version bits state across blocks."""

    def __init__(self, deployments: List[VersionBitsDeployment]):
        self.deployments = {d.name: d for d in deployments}
        self.window_size = 2016
        self.threshold = 1916

    def update_state(self, height: int, time: int, versions: List[int]) -> None:
        for deployment in self.deployments.values():
            self._update_deployment_state(deployment, height, time, versions)

    def _update_deployment_state(self, deployment: VersionBitsDeployment,
                                 height: int, time: int, versions: List[int]) -> None:
        if deployment.state == DeploymentState.DEFINED:
            if time >= deployment.start_time and time < deployment.timeout:
                deployment.state = DeploymentState.STARTED
                deployment.since_height = height

        elif deployment.state == DeploymentState.STARTED:
            if time >= deployment.timeout:
                deployment.state = DeploymentState.FAILED
                return
            if len(versions) < self.window_size:
                return
            count = sum(1 for v in versions if deployment.is_supported(v))
            if count >= self.threshold:
                deployment.state = DeploymentState.LOCKED_IN
                deployment.since_height = height

        elif deployment.state == DeploymentState.LOCKED_IN:
            activation_ready = max(
                deployment.since_height + self.window_size,
                int(deployment.min_activation_height),
            )
            if height >= activation_ready:
                deployment.state = DeploymentState.ACTIVE

    def get_state(self, name: str) -> Optional[DeploymentState]:
        deployment = self.deployments.get(name)
        return deployment.state if deployment else None

    def is_active(self, name: str) -> bool:
        return self.get_state(name) == DeploymentState.ACTIVE

    def get_mask(self) -> int:
        mask = 0
        for deployment in self.deployments.values():
            if deployment.state == DeploymentState.ACTIVE:
                mask |= (1 << deployment.bit)
        return mask

    def get_signaling_mask(self) -> int:
        """Bits miners should set while deployment is progressing."""
        mask = 0
        for deployment in self.deployments.values():
            if deployment.state in (DeploymentState.STARTED, DeploymentState.LOCKED_IN):
                mask |= (1 << deployment.bit)
        return mask

    def get_block_version(self, base_version: int = 0x20000000) -> int:
        """Compose next block version with current versionbits signaling mask."""
        return int(base_version) | int(self.get_signaling_mask())

def get_standard_deployments(params: Optional[ConsensusParams] = None) -> List[VersionBitsDeployment]:
    """Get standard version bits deployments for the active network profile."""
    from .deployments import get_versionbits_deployments

    return get_versionbits_deployments(params=params)
