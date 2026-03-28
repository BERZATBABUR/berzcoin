"""Shared validation-limit policy for block/transaction checks."""

from dataclasses import dataclass

from shared.consensus.params import MAX_MONEY


@dataclass(frozen=True)
class ValidationLimits:
    """Centralized validation guardrails used by chain validation."""

    coinbase_script_sig_min_len: int = 2
    coinbase_script_sig_max_len: int = 100
    dust_threshold: int = 546
    max_future_block_time_seconds: int = 2 * 60 * 60
    median_time_past_window: int = 11
    coinbase_maturity: int = 100
    max_money: int = int(MAX_MONEY)

    @classmethod
    def from_params(cls, params) -> "ValidationLimits":
        """Build validation limits from consensus params with safe defaults."""
        return cls(
            coinbase_maturity=int(getattr(params, "coinbase_maturity", 100)),
            max_money=int(getattr(params, "max_money", MAX_MONEY)),
        )

    def is_coinbase_script_length_valid(self, script_len: int) -> bool:
        """Return True when coinbase scriptSig size is in consensus range."""
        return self.coinbase_script_sig_min_len <= int(script_len) <= self.coinbase_script_sig_max_len

    def is_dust_output(self, value: int, script_pubkey: bytes) -> bool:
        """Return True for spendable outputs below dust threshold.

        OP_RETURN outputs are excluded from this warning helper.
        """
        if len(script_pubkey) > 0 and script_pubkey[0] == 0x6A:
            return False
        return int(value) < self.dust_threshold
