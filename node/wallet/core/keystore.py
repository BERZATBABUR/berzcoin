"""Key storage and management."""

import hashlib
import secrets
import time
from typing import List, Optional, Dict
from dataclasses import dataclass
from shared.crypto.keys import PrivateKey, PublicKey
from shared.crypto.address import public_key_to_address
from shared.utils.logging import get_logger

logger = get_logger()

@dataclass
class KeyInfo:
    """Key information."""
    private_key: PrivateKey
    public_key: PublicKey
    address: str
    path: str  # BIP32 derivation path
    created_at: int
    used: bool = False
    label: str = ""

class KeyStore:
    """Key storage and management."""
    
    def __init__(self, wallet_path: str, network: str = "mainnet"):
        """Initialize keystore.
        
        Args:
            wallet_path: Path to wallet file
            network: Network (mainnet, testnet, regtest)
        """
        self.wallet_path = wallet_path
        self.network = network
        self.keys: Dict[str, KeyInfo] = {}  # address -> KeyInfo
        self.keychain: Dict[str, List[str]] = {}  # account -> list of addresses
        self.master_key: Optional[PrivateKey] = None
        self.mnemonic: Optional[str] = None
        self.encrypted = False
    
    def create_master_key(self, password: Optional[str] = None) -> str:
        """Create new master key.
        
        Args:
            password: Optional password for encryption
        
        Returns:
            Mnemonic seed phrase
        """
        # Generate entropy
        entropy = secrets.token_bytes(32)
        
        # Generate mnemonic (BIP39)
        self.mnemonic = self._entropy_to_mnemonic(entropy)
        
        # Generate seed from mnemonic
        seed = self._mnemonic_to_seed(self.mnemonic, password or "")
        
        # Generate master private key
        self.master_key = PrivateKey(int.from_bytes(seed[:32], 'big'))
        
        # Generate first account
        self._derive_account(0)
        
        logger.info("Created new master key")
        return self.mnemonic
    
    def import_mnemonic(self, mnemonic: str, password: Optional[str] = None) -> bool:
        """Import wallet from mnemonic.
        
        Args:
            mnemonic: BIP39 mnemonic phrase
            password: Optional password
        
        Returns:
            True if successful
        """
        # Validate mnemonic
        if not self._validate_mnemonic(mnemonic):
            logger.error("Invalid mnemonic")
            return False
        
        # Generate seed
        seed = self._mnemonic_to_seed(mnemonic, password or "")
        
        # Generate master key
        self.master_key = PrivateKey(int.from_bytes(seed[:32], 'big'))
        self.mnemonic = mnemonic
        
        # Derive accounts
        self._derive_account(0)
        
        logger.info("Imported wallet from mnemonic")
        return True
    
    def import_private_key(self, private_key_hex: str, label: str = "") -> Optional[str]:
        """Import private key.
        
        Args:
            private_key_hex: Private key in hex
            label: Label for the key
        
        Returns:
            Address or None
        """
        try:
            key_int = int(private_key_hex, 16)
            private_key = PrivateKey(key_int)
            public_key = private_key.public_key()
            address = public_key_to_address(public_key, network=self.network)
            
            if address in self.keys:
                logger.warning(f"Address {address} already exists")
                return None
            
            self.keys[address] = KeyInfo(
                private_key=private_key,
                public_key=public_key,
                address=address,
                path="imported",
                created_at=int(time.time()),
                label=label
            )
            
            logger.info(f"Imported private key: {address[:16]}...")
            return address
            
        except Exception as e:
            logger.error(f"Failed to import private key: {e}")
            return None
    
    def get_key(self, address: str) -> Optional[KeyInfo]:
        """Get key by address.
        
        Args:
            address: Bitcoin address
        
        Returns:
            KeyInfo or None
        """
        return self.keys.get(address)
    
    def get_private_key(self, address: str) -> Optional[PrivateKey]:
        """Get private key by address.
        
        Args:
            address: Bitcoin address
        
        Returns:
            PrivateKey or None
        """
        key_info = self.keys.get(address)
        return key_info.private_key if key_info else None
    
    def get_public_key(self, address: str) -> Optional[PublicKey]:
        """Get public key by address.
        
        Args:
            address: Bitcoin address
        
        Returns:
            PublicKey or None
        """
        key_info = self.keys.get(address)
        return key_info.public_key if key_info else None
    
    def get_addresses(self, account: int = 0, include_used: bool = True) -> List[str]:
        """Get addresses for an account.
        
        Args:
            account: Account index
            include_used: Include used addresses
        
        Returns:
            List of addresses
        """
        addresses = []
        for chain_key in (f"{account}_external", f"{account}_internal"):
            for addr in self.keychain.get(chain_key, []):
                if include_used or not self.keys[addr].used:
                    addresses.append(addr)
        return addresses
    
    def get_unused_address(self, account: int = 0) -> Optional[str]:
        """Get an unused address.
        
        Args:
            account: Account index
        
        Returns:
            Address or None
        """
        addresses = self.get_addresses(account, include_used=False)
        if addresses:
            return addresses[0]
        
        # Generate new address
        return self._generate_new_address(account)

    def _generate_new_address(self, account: int) -> Optional[str]:
        """Add a new receiving key when the pre-derived pool is exhausted (BIP32 stub)."""
        private_key = PrivateKey()
        public_key = private_key.public_key()
        address = public_key_to_address(public_key, network=self.network)
        if address in self.keys:
            return self._generate_new_address(account)
        chain_key = f"{account}_external"
        if chain_key not in self.keychain:
            self.keychain[chain_key] = []
        self.keys[address] = KeyInfo(
            private_key=private_key,
            public_key=public_key,
            address=address,
            path=f"{chain_key}/generated",
            created_at=int(time.time()),
        )
        self.keychain[chain_key].append(address)
        return address
    
    def mark_address_used(self, address: str) -> None:
        """Mark address as used.
        
        Args:
            address: Bitcoin address
        """
        if address in self.keys:
            self.keys[address].used = True
    
    def _derive_account(self, account: int) -> None:
        """Derive keys for an account (BIP44).
        
        Args:
            account: Account index
        """
        if not self.master_key:
            raise ValueError("Master key not initialized")
        
        # Derive account key: m/44'/0'/account'
        account_key = self._derive_path(f"m/44'/0'/{account}'")
        
        # Generate external chain (receiving)
        external_key = self._derive_path("0", account_key)
        self._generate_chain_addresses(external_key, account, "external")
        
        # Generate internal chain (change)
        internal_key = self._derive_path("1", account_key)
        self._generate_chain_addresses(internal_key, account, "internal")
    
    def _generate_chain_addresses(self, parent_key: PrivateKey, account: int, chain: str) -> None:
        """Generate addresses for a chain.
        
        Args:
            parent_key: Parent key for the chain
            account: Account index
            chain: Chain type (external/internal)
        """
        account_key = f"{account}_{chain}"
        if account_key not in self.keychain:
            self.keychain[account_key] = []
        
        # Generate first 20 addresses
        for i in range(20):
            child_key = self._derive_path(str(i), parent_key)
            public_key = child_key.public_key()
            address = public_key_to_address(public_key, network=self.network)
            
            if address not in self.keys:
                self.keys[address] = KeyInfo(
                    private_key=child_key,
                    public_key=public_key,
                    address=address,
                    path=f"m/44'/0'/{account}'/{chain}/{i}",
                    created_at=int(time.time())
                )
                self.keychain[account_key].append(address)
    
    def _derive_path(self, path: str, base_key: Optional[PrivateKey] = None) -> PrivateKey:
        """Derive key from BIP32 path.
        
        Args:
            path: Derivation path
            base_key: Base key (uses master if None)
        
        Returns:
            Derived private key
        """
        # Simplified BIP32 derivation
        # In production, implement full BIP32
        key = base_key or self.master_key
        if not key:
            raise ValueError("No master key")
        
        # For now, just return the base key
        return key
    
    def _entropy_to_mnemonic(self, entropy: bytes) -> str:
        """Convert entropy to BIP39 mnemonic.
        
        Args:
            entropy: Entropy bytes
        
        Returns:
            BIP39 mnemonic phrase
        """
        # Simplified BIP39 - use 12 words from entropy
        # In production, implement full BIP39 with wordlist
        words = ["abandon", "ability", "able", "about", "above", "absent",
                 "absorb", "abstract", "absurd", "abuse", "access", "accident"]
        
        mnemonic_words = []
        for i in range(12):
            idx = entropy[i] % len(words)
            mnemonic_words.append(words[idx])
        
        return " ".join(mnemonic_words)
    
    def _mnemonic_to_seed(self, mnemonic: str, password: str) -> bytes:
        """Convert mnemonic to seed.
        
        Args:
            mnemonic: BIP39 mnemonic
            password: Optional password
        
        Returns:
            64-byte seed
        """
        # PBKDF2 with HMAC-SHA512
        salt = f"mnemonic{password}".encode()
        seed = hashlib.pbkdf2_hmac('sha512', mnemonic.encode(), salt, 2048)
        return seed
    
    def _validate_mnemonic(self, mnemonic: str) -> bool:
        """Validate BIP39 mnemonic.
        
        Args:
            mnemonic: Mnemonic phrase
        
        Returns:
            True if valid
        """
        words = mnemonic.split()
        # Check word count (12, 15, 18, 21, 24)
        if len(words) not in [12, 15, 18, 21, 24]:
            return False
        
        # In production, validate checksum
        return True
    
    def get_stats(self) -> Dict:
        """Get keystore statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            'total_keys': len(self.keys),
            'used_keys': sum(1 for k in self.keys.values() if k.used),
            'has_master': self.master_key is not None,
            'has_mnemonic': self.mnemonic is not None,
            'encrypted': self.encrypted
        }
