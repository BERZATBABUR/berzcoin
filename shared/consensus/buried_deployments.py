"""Buried deployment activation helpers."""

from enum import Enum
from typing import Dict, Union

from .params import ConsensusParams

# Canonical project-specific deployment identifiers.
SOFTFORK_BIP34_STRICT = "berz_softfork_bip34_strict"
HARDFORK_TX_V2 = "berz_hardfork_tx_v2"

# Backward-compat aliases for older config/test names.
_CUSTOM_DEPLOYMENT_ALIASES = {
    "softfork_strict_bip34": SOFTFORK_BIP34_STRICT,
    "hardfork_v2_tx_version": HARDFORK_TX_V2,
}


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


def get_custom_deployment_height(params: ConsensusParams, deployment: str) -> int:
    """Return activation height for project-specific consensus deployment."""
    normalized = normalize_custom_deployment_name(deployment)
    if not normalized:
        raise ValueError("deployment name is required")

    custom = getattr(params, "custom_activation_heights", {}) or {}
    if normalized not in custom:
        raise KeyError(f"unknown custom deployment: {deployment}")
    return int(custom[normalized])


def is_custom_deployment_active(
    params: ConsensusParams,
    deployment: str,
    height: int,
) -> bool:
    """Return whether a project-specific deployment is active at ``height``."""
    activation_height = get_custom_deployment_height(params, deployment)
    return int(height) >= int(activation_height)


def is_consensus_feature_active(
    params: ConsensusParams,
    deployment: Union[BuriedDeployment, str],
    height: int,
) -> bool:
    """Unified activation check for buried + project-specific deployments."""
    try:
        return is_buried_deployment_active(params, deployment, height)
    except ValueError:
        tracker = getattr(params, "versionbits_tracker", None)
        deployment_name = normalize_custom_deployment_name(deployment)
        if tracker is not None and deployment_name:
            try:
                if bool(tracker.is_active(deployment_name)):
                    return True
            except Exception:
                pass
        try:
            return is_custom_deployment_active(params, deployment_name, height)
        except (ValueError, KeyError):
            return False


def normalize_custom_deployment_name(deployment: Union[BuriedDeployment, str]) -> str:
    """Normalize deployment identifier and map legacy aliases to canonical names."""
    normalized = str(deployment or "").strip().lower()
    return _CUSTOM_DEPLOYMENT_ALIASES.get(normalized, normalized)
