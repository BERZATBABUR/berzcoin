"""Version bits deployment tracking (BIP9)."""

from typing import Dict, List, Optional
from enum import IntEnum

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
            if height >= deployment.since_height + self.window_size:
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

def get_standard_deployments() -> List[VersionBitsDeployment]:
    """Get standard version bits deployments."""
    return [
        VersionBitsDeployment(
            name="testdummy",
            bit=28,
            start_time=1199145601,
            timeout=1230767999
        ),
        VersionBitsDeployment(
            name="csv",
            bit=0,
            start_time=1462060800,
            timeout=1493596800
        ),
        VersionBitsDeployment(
            name="segwit",
            bit=1,
            start_time=1479168000,
            timeout=1510704000
        )
    ]
