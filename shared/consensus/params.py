"""Consensus parameters for BerzCoin."""

from dataclasses import dataclass
from typing import Dict, Any

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

    def retarget_interval_blocks(self) -> int:
        """Blocks between difficulty adjustments (e.g. 2016 when span 2 weeks / 10 min spacing)."""
        spacing = max(1, self.pow_target_spacing)
        return max(1, self.pow_target_timespan // spacing)

    @classmethod
    def mainnet(cls) -> 'ConsensusParams':
        """Mainnet consensus parameters."""
        return cls(
            max_block_size=1000000,
            max_block_weight=4000000,
            max_block_sigops=20000,
            pow_target_spacing=120,  # 2 minutes
            pow_target_timespan=1209600,  # 2 weeks
            pow_limit=0x00000000ffffffffffffffffffffffffffffffffffffffffffffffffffffffff,
            pow_no_retargeting=False,
            initial_subsidy=2 * 100000000,  # 2 BerzCoin in satoshis
            # Halving every 4 years -> blocks = (4*365*24*3600) / spacing
            subsidy_halving_interval=1051200,
            bip34_height=227931,
            bip66_height=363725,
            bip65_height=388381,
            csv_height=419328,
            segwit_height=481824,
            genesis_block_hash="000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f",
            genesis_time=1231006505,
            genesis_nonce=2083236893,
            genesis_bits=0x1d00ffff,
            genesis_version=1,
            genesis_merkle_root="4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b",
            message_magic=b'\xf9\xbe\xb4\xd9',
            default_port=8333,
            dns_seeds=[
                "seed.berzcoin.sipa.be",
                "dnsseed.bluematt.me",
                "seed.bitcoinstats.com",
                "seed.bitcoin.sipa.be"
            ],
            checkpoint_data={
                0: "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f",
                11111: "0000000069e244f73d78e8fd29ba2fd2ed618bd6fa2ee92559f542fdb26e7c1d",
                100000: "000000000003ba27aa200b1cecaad478d2b00432346c3f1f3986da1afd33e506",
            }
        )
    
    @classmethod
    def testnet(cls) -> 'ConsensusParams':
        """Testnet consensus parameters."""
        params = cls.mainnet()
        params.pow_limit = 0x00000000ffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        # Enable retargeting and use mainnet subsidy (2 BerzCoin)
        params.pow_no_retargeting = False
        params.initial_subsidy = 2 * 100000000
        params.bip34_height = 21111
        params.bip66_height = 330776
        params.bip65_height = 330776
        params.csv_height = 330776
        params.segwit_height = 0  # Always active on testnet
        params.message_magic = b'\x0b\x11\x09\x07'
        params.default_port = 18333
        params.dns_seeds = [
            "testnet-seed.berzcoin.jonasschnelli.ch",
            "seed.tbtc.petertodd.org",
            "testnet-seed.bluematt.me"
        ]
        return params
    
    @classmethod
    def regtest(cls) -> 'ConsensusParams':
        """Regression test consensus parameters."""
        params = cls.mainnet()
        # Use fast 2-minute blocks and enable difficulty retargeting for regtest
        params.pow_target_spacing = 120
        params.pow_target_timespan = 1209600
        params.pow_limit = 0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        params.pow_no_retargeting = False
        params.bip34_height = 100000000  # Never activates
        params.bip66_height = 100000000
        params.bip65_height = 100000000
        params.csv_height = 100000000
        params.segwit_height = 0  # Always active
        params.message_magic = b'\xfa\xbf\xb5\xda'
        params.default_port = 18444
        params.dns_seeds = []
        return params
    
    def get_network_name(self) -> str:
        """Get network name from magic bytes."""
        if self.message_magic == b'\xf9\xbe\xb4\xd9':
            return "mainnet"
        elif self.message_magic == b'\x0b\x11\x09\x07':
            return "testnet"
        elif self.message_magic == b'\xfa\xbf\xb5\xda':
            return "regtest"
        return "unknown"
