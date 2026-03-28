"""Buried deployment activation helpers."""

from enum import Enum
from typing import Dict, Union

from .params import ConsensusParams


class BuriedDeployment(str, Enum):
    """Consensus deployments activated by height."""

    BIP34 = "bip34"
    BIP65 = "bip65"
    BIP66 = "bip66"
    CSV = "csv"
    SEGWIT = "segwit"


def get_buried_deployment_heights(params: ConsensusParams) -> Dict[BuriedDeployment, int]:
    """Map deployment identifiers to activation heights from consensus params."""
    return {
        BuriedDeployment.BIP34: int(params.bip34_height),
        BuriedDeployment.BIP65: int(params.bip65_height),
        BuriedDeployment.BIP66: int(params.bip66_height),
        BuriedDeployment.CSV: int(params.csv_height),
        BuriedDeployment.SEGWIT: int(params.segwit_height),
    }


def get_buried_deployment_height(
    params: ConsensusParams,
    deployment: Union[BuriedDeployment, str],
) -> int:
    """Return activation height for a buried deployment name."""
    key = deployment if isinstance(deployment, BuriedDeployment) else BuriedDeployment(str(deployment).lower())
    return get_buried_deployment_heights(params)[key]


def is_buried_deployment_active(
    params: ConsensusParams,
    deployment: Union[BuriedDeployment, str],
    height: int,
) -> bool:
    """Return whether deployment is active at ``height``."""
    activation_height = get_buried_deployment_height(params, deployment)
    return int(height) >= int(activation_height)
