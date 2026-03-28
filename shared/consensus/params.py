"""Consensus parameters for BerzCoin."""

from dataclasses import dataclass
from typing import Dict, Any

SATOSHI = 1
COIN = 100_000_000  # 1 BERZ = 100,000,000 sat (Bitcoin-style base unit)
MAX_SUPPLY_BERZ = 2 ** 24
MAX_MONEY = MAX_SUPPLY_BERZ * COIN
HALVING_YEARS = 4

# BerzCoin network magic values (message-start bytes).
MAINNET_MAGIC = b"\xb7\xd9\xc4\xef"
TESTNET_MAGIC = b"\xce\xe2\xca\xff"
REGTEST_MAGIC = b"\xfa\xce\xb0\x0c"

# BerzCoin network-owned genesis anchors (derived March 27, 2026).
MAINNET_GENESIS_HASH = "0000a2b00a878937fe2431db054cc73784721f63ee8bacffe1e0aa0612f01f25"
MAINNET_GENESIS_TIME = 1774569600  # 2026-03-27 00:00:00 UTC
MAINNET_GENESIS_NONCE = 24409
MAINNET_GENESIS_BITS = 0x207FFFFF
MAINNET_GENESIS_MERKLE_ROOT = "2ed9e25352bd4cdbd52a7d5afc3b780f9dd5b2dd3425c66d8b8f1c45e72d74e2"

TESTNET_GENESIS_HASH = "0000a21d5c6616e56712e4e80ab1a3fb8d859bc386a54bf8e3cb3e104c96726f"
TESTNET_GENESIS_TIME = 1774569660
TESTNET_GENESIS_NONCE = 17146
TESTNET_GENESIS_BITS = 0x207FFFFF
TESTNET_GENESIS_MERKLE_ROOT = "50e9ba90db5c87178f43191d5d39f048593789a3f167b59b14887626479a5dbb"

REGTEST_GENESIS_HASH = "0000febb790a74d4063fecb92bca9797baf04f499f1b0679cf942d746a4ff974"
REGTEST_GENESIS_TIME = 1774569720
REGTEST_GENESIS_NONCE = 25343
REGTEST_GENESIS_BITS = 0x207FFFFF
REGTEST_GENESIS_MERKLE_ROOT = "dc11f6262a71d2abf20588f8f537f8264f07432d3d539a482ade6756da7db514"

@dataclass
class ConsensusParams:
    """Consensus parameters for different networks."""
    
    # Block parameters
    max_block_size: int
    max_block_weight: int
    max_block_sigops: int
    
    # Difficulty adjustment
    pow_target_spacing: int  # Target block time in seconds
    pow_target_timespan: int  # Difficulty adjustment interval
    pow_limit: int  # Maximum proof of work target
    pow_no_retargeting: bool  # Disable difficulty retargeting
    
    # Subsidy
    initial_subsidy: int  # Initial block reward in satoshis
    subsidy_halving_interval: int  # Blocks between halvings
    
    # Activation heights
    bip34_height: int  # Block height for BIP34
    bip66_height: int  # Block height for BIP66
    bip65_height: int  # Block height for BIP65 (OP_CHECKLOCKTIMEVERIFY)
    csv_height: int  # Block height for BIP68/BIP112/BIP113 (OP_CHECKSEQUENCEVERIFY)
    segwit_height: int  # Block height for SegWit activation
    
    # Genesis block
    genesis_block_hash: str
    genesis_time: int
    genesis_nonce: int
    genesis_bits: int
    genesis_version: int
    genesis_merkle_root: str
    
    # Network parameters
    message_magic: bytes
    default_port: int
    dns_seeds: list
    checkpoint_data: Dict[int, str]
    max_money: int

    def retarget_interval_blocks(self) -> int:
        """Blocks between difficulty adjustments (e.g. 2016 when span 2 weeks / 10 min spacing)."""
        spacing = max(1, self.pow_target_spacing)
        return max(1, self.pow_target_timespan // spacing)

    @staticmethod
    def _four_year_halving_interval(block_spacing_seconds: int) -> int:
        seconds = HALVING_YEARS * 365 * 24 * 60 * 60
        return max(1, seconds // max(1, int(block_spacing_seconds)))

    @staticmethod
    def _initial_subsidy_for_cap(max_money: int, halving_interval: int) -> int:
        # For an infinite halving series, total ~= 2 * interval * subsidy.
        # We floor to sat units and keep issuance <= cap.
        return max(1, int(max_money) // max(1, 2 * int(halving_interval)))

    @classmethod
    def mainnet(cls) -> 'ConsensusParams':
        """Mainnet consensus parameters."""
        spacing = 120  # 2 minutes
        halving_interval = cls._four_year_halving_interval(spacing)
        return cls(
            max_block_size=1000000,
            max_block_weight=4000000,
            max_block_sigops=20000,
            pow_target_spacing=spacing,
            pow_target_timespan=1209600,  # 2 weeks
            # CPU-friendly pow limit for BerzCoin's standalone network profile.
            pow_limit=0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff,
            pow_no_retargeting=False,
            initial_subsidy=cls._initial_subsidy_for_cap(MAX_MONEY, halving_interval),
            subsidy_halving_interval=halving_interval,
            bip34_height=227931,
            bip66_height=363725,
            bip65_height=388381,
            csv_height=419328,
            segwit_height=481824,
            genesis_block_hash=MAINNET_GENESIS_HASH,
            genesis_time=MAINNET_GENESIS_TIME,
            genesis_nonce=MAINNET_GENESIS_NONCE,
            genesis_bits=MAINNET_GENESIS_BITS,
            genesis_version=1,
            genesis_merkle_root=MAINNET_GENESIS_MERKLE_ROOT,
            message_magic=MAINNET_MAGIC,
            default_port=8333,
            dns_seeds=[
                "seed1.berzcoin.org",
                "seed2.berzcoin.org",
                "seed3.berzcoin.org",
                "dnsseed.berzcoin.org",
            ],
            checkpoint_data={
                0: MAINNET_GENESIS_HASH,
            },
            max_money=MAX_MONEY,
        )
    
    @classmethod
    def testnet(cls) -> 'ConsensusParams':
        """Testnet consensus parameters."""
        params = cls.mainnet()
        params.pow_limit = 0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        # Keep 4-year halving and cap-derived subsidy across networks.
        params.pow_no_retargeting = False
        params.initial_subsidy = cls._initial_subsidy_for_cap(params.max_money, params.subsidy_halving_interval)
        params.genesis_block_hash = TESTNET_GENESIS_HASH
        params.genesis_time = TESTNET_GENESIS_TIME
        params.genesis_nonce = TESTNET_GENESIS_NONCE
        params.genesis_bits = TESTNET_GENESIS_BITS
        params.genesis_merkle_root = TESTNET_GENESIS_MERKLE_ROOT
        params.bip34_height = 21111
        params.bip66_height = 330776
        params.bip65_height = 330776
        params.csv_height = 330776
        params.segwit_height = 0  # Always active on testnet
        params.message_magic = TESTNET_MAGIC
        params.default_port = 18333
        params.dns_seeds = [
            "testnet-seed1.berzcoin.org",
            "testnet-seed2.berzcoin.org",
        ]
        params.checkpoint_data = {0: TESTNET_GENESIS_HASH}
        return params
    
    @classmethod
    def regtest(cls) -> 'ConsensusParams':
        """Regression test consensus parameters."""
        params = cls.mainnet()
        # Regtest profile:
        # - 120s nominal target block time
        # - no retargeting (stable local mining for dev/demo workflows)
        params.pow_target_spacing = 120
        params.pow_target_timespan = params.pow_target_spacing * 20
        params.pow_limit = 0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        params.pow_no_retargeting = True
        params.subsidy_halving_interval = cls._four_year_halving_interval(params.pow_target_spacing)
        params.initial_subsidy = cls._initial_subsidy_for_cap(params.max_money, params.subsidy_halving_interval)
        params.genesis_block_hash = REGTEST_GENESIS_HASH
        params.genesis_time = REGTEST_GENESIS_TIME
        params.genesis_nonce = REGTEST_GENESIS_NONCE
        params.genesis_bits = REGTEST_GENESIS_BITS
        params.genesis_merkle_root = REGTEST_GENESIS_MERKLE_ROOT
        params.bip34_height = 100000000  # Never activates
        params.bip66_height = 100000000
        params.bip65_height = 100000000
        params.csv_height = 100000000
        params.segwit_height = 0  # Always active
        params.message_magic = REGTEST_MAGIC
        params.default_port = 18444
        params.dns_seeds = []
        params.checkpoint_data = {0: REGTEST_GENESIS_HASH}
        return params
    
    def get_network_name(self) -> str:
        """Get network name from magic bytes."""
        if self.message_magic == MAINNET_MAGIC:
            return "mainnet"
        elif self.message_magic == TESTNET_MAGIC:
            return "testnet"
        elif self.message_magic == REGTEST_MAGIC:
            return "regtest"
        return "unknown"
