"""Key storage and management."""

import hashlib
import secrets
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from shared.crypto.address import public_key_to_address
from shared.crypto.keys import PrivateKey, PublicKey
from shared.utils.logging import get_logger

logger = get_logger()


@dataclass
class KeyInfo:
    """Key information."""

    private_key: PrivateKey
    public_key: PublicKey
    address: str
    path: str
    created_at: int
    used: bool = False
    label: str = ""
    script_type: str = "p2wpkh"
    is_internal: bool = False
    account: int = 0
    index: int = 0


class KeyStore:
    """Key storage and management."""

    def __init__(self, wallet_path: str, network: str = "mainnet"):
        self.wallet_path = wallet_path
        self.network = network
        self.keys: Dict[str, KeyInfo] = {}
        self.keychain: Dict[str, List[str]] = {}
        self.master_key: Optional[PrivateKey] = None
        self.mnemonic: Optional[str] = None
        self.encrypted = False
        self.script_policy: Dict[str, str] = {
            "external": "p2wpkh",
            "internal": "p2wpkh",
        }

    def create_master_key(self, password: Optional[str] = None) -> str:
        entropy = secrets.token_bytes(32)
        self.mnemonic = self._entropy_to_mnemonic(entropy)
        seed = self._mnemonic_to_seed(self.mnemonic, password or "")
        self.master_key = PrivateKey(int.from_bytes(seed[:32], "big"))
        self._derive_account(0)
        logger.info("Created new master key")
        return self.mnemonic

    def import_mnemonic(self, mnemonic: str, password: Optional[str] = None) -> bool:
        if not self._validate_mnemonic(mnemonic):
            logger.error("Invalid mnemonic")
            return False

        seed = self._mnemonic_to_seed(mnemonic, password or "")
        self.master_key = PrivateKey(int.from_bytes(seed[:32], "big"))
        self.mnemonic = mnemonic

        self.keys = {}
        self.keychain = {}
        self._derive_account(0)

        logger.info("Imported wallet from mnemonic")
        return True

    def import_private_key(self, private_key_hex: str, label: str = "") -> Optional[str]:
        try:
            key_text = private_key_hex.strip()
            try:
                private_key = PrivateKey.from_wif(key_text)
            except Exception:
                key_int = int(key_text, 16)
                private_key = PrivateKey(key_int)
            public_key = private_key.public_key()
            address = public_key_to_address(public_key, network=self.network)

            if address in self.keys:
                logger.warning("Address %s already exists", address)
                return None

            self.keys[address] = KeyInfo(
                private_key=private_key,
                public_key=public_key,
                address=address,
                path="imported",
                created_at=int(time.time()),
                label=label,
                script_type="p2pkh",
            )

            logger.info("Imported private key: %s...", address[:16])
            return address
        except Exception as e:
            logger.error("Failed to import private key: %s", e)
            return None

    def get_key(self, address: str) -> Optional[KeyInfo]:
        return self.keys.get(address)

    def get_private_key(self, address: str) -> Optional[PrivateKey]:
        key_info = self.keys.get(address)
        return key_info.private_key if key_info else None

    def get_public_key(self, address: str) -> Optional[PublicKey]:
        key_info = self.keys.get(address)
        return key_info.public_key if key_info else None

    def get_addresses(
        self,
        account: int = 0,
        include_used: bool = True,
        internal: Optional[bool] = None,
    ) -> List[str]:
        addresses: List[str] = []
        chain_keys = [f"{account}_external", f"{account}_internal"]
        if internal is True:
            chain_keys = [f"{account}_internal"]
        elif internal is False:
            chain_keys = [f"{account}_external"]

        for chain_key in chain_keys:
            for addr in self.keychain.get(chain_key, []):
                key = self.keys.get(addr)
                if not key:
                    continue
                if include_used or not key.used:
                    addresses.append(addr)
        return addresses

    def get_unused_address(
        self,
        account: int = 0,
        internal: bool = False,
        script_type: Optional[str] = None,
    ) -> Optional[str]:
        addresses = self.get_addresses(account, include_used=False, internal=internal)
        if addresses:
            return addresses[0]
        return self._generate_new_address(account, internal=internal, script_type=script_type)

    def get_change_address(self, account: int = 0, script_type: Optional[str] = None) -> Optional[str]:
        return self.get_unused_address(account=account, internal=True, script_type=script_type)

    def _generate_new_address(
        self,
        account: int,
        internal: bool = False,
        script_type: Optional[str] = None,
    ) -> Optional[str]:
        private_key = PrivateKey()
        public_key = private_key.public_key()
        normalized_script = self._normalize_script_type(
            script_type or self.script_policy["internal" if internal else "external"]
        )
        address = self._public_key_to_script_address(public_key, normalized_script)
        if address in self.keys:
            return self._generate_new_address(account, internal=internal, script_type=normalized_script)

        chain_name = "internal" if internal else "external"
        chain_key = f"{account}_{chain_name}"
        if chain_key not in self.keychain:
            self.keychain[chain_key] = []
        child_index = len(self.keychain[chain_key])

        self.keys[address] = KeyInfo(
            private_key=private_key,
            public_key=public_key,
            address=address,
            path=f"m/44'/0'/{account}'/{1 if internal else 0}/{child_index}",
            created_at=int(time.time()),
            script_type=normalized_script,
            is_internal=internal,
            account=account,
            index=child_index,
        )
        self.keychain[chain_key].append(address)
        return address

    def mark_address_used(self, address: str) -> None:
        if address in self.keys:
            self.keys[address].used = True

    def _derive_account(self, account: int) -> None:
        if not self.master_key:
            raise ValueError("Master key not initialized")

        account_key = self._derive_path(f"m/44'/0'/{account}'")
        external_key = self._derive_path("0", account_key)
        self._generate_chain_addresses(external_key, account, "external")
        internal_key = self._derive_path("1", account_key)
        self._generate_chain_addresses(internal_key, account, "internal")

    def _generate_chain_addresses(self, parent_key: PrivateKey, account: int, chain: str) -> None:
        account_key = f"{account}_{chain}"
        if account_key not in self.keychain:
            self.keychain[account_key] = []

        script_type = self._normalize_script_type(self.script_policy.get(chain, "p2wpkh"))
        is_internal = chain == "internal"

        for i in range(20):
            child_key = self._derive_path(str(i), parent_key)
            public_key = child_key.public_key()
            address = self._public_key_to_script_address(public_key, script_type)

            if address in self.keys:
                continue

            self.keys[address] = KeyInfo(
                private_key=child_key,
                public_key=public_key,
                address=address,
                path=f"m/44'/0'/{account}'/{1 if is_internal else 0}/{i}",
                created_at=int(time.time()),
                script_type=script_type,
                is_internal=is_internal,
                account=account,
                index=i,
            )
            self.keychain[account_key].append(address)

    def _derive_path(self, path: str, base_key: Optional[PrivateKey] = None) -> PrivateKey:
        key = base_key or self.master_key
        if not key:
            raise ValueError("No master key")

        seed_material = (
            key.to_hex().encode("ascii")
            + b"|"
            + path.encode("utf-8")
            + b"|"
            + str(self.network).encode("ascii")
        )
        child_int = int.from_bytes(hashlib.sha256(seed_material).digest(), "big")
        curve_n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        child_int = (child_int % (curve_n - 1)) + 1
        return PrivateKey(child_int)

    def _normalize_script_type(self, script_type: str) -> str:
        st = (script_type or "").strip().lower()
        if st in ("p2pkh", "legacy"):
            return "p2pkh"
        if st in ("p2wpkh", "segwit", "bech32"):
            return "p2wpkh"
        return "p2wpkh"

    def _public_key_to_script_address(self, public_key: PublicKey, script_type: str) -> str:
        st = self._normalize_script_type(script_type)
        if st == "p2pkh":
            return public_key_to_address(public_key, network=self.network, segwit=False)
        return public_key_to_address(public_key, network=self.network, segwit=True)

    def export_descriptors(self, account: int = 0) -> List[Dict[str, Any]]:
        coin_type = 0 if self.network == "mainnet" else 1
        descriptors: List[Dict[str, Any]] = []
        for chain, branch in (("external", 0), ("internal", 1)):
            script_type = self._normalize_script_type(self.script_policy.get(chain, "p2wpkh"))
            fn = "wpkh" if script_type == "p2wpkh" else "pkh"
            descriptors.append(
                {
                    "descriptor": f"{fn}(m/44'/{coin_type}'/{account}'/{branch}/*)",
                    "internal": chain == "internal",
                    "script_type": script_type,
                    "range_start": 0,
                    "next_index": len(self.keychain.get(f"{account}_{chain}", [])),
                    "network": self.network,
                }
            )
        return descriptors

    def apply_key_metadata(self, metadata: List[Dict[str, Any]]) -> None:
        for row in metadata:
            addr = row.get("address")
            if not addr:
                continue
            key = self.keys.get(addr)
            if not key:
                continue
            key.used = bool(row.get("used", key.used))
            key.label = str(row.get("label", key.label))
            key.script_type = self._normalize_script_type(str(row.get("script_type", key.script_type)))

    def _entropy_to_mnemonic(self, entropy: bytes) -> str:
        words = [
            "abandon",
            "ability",
            "able",
            "about",
            "above",
            "absent",
            "absorb",
            "abstract",
            "absurd",
            "abuse",
            "access",
            "accident",
        ]

        mnemonic_words = []
        for i in range(12):
            idx = entropy[i] % len(words)
            mnemonic_words.append(words[idx])

        return " ".join(mnemonic_words)

    def _mnemonic_to_seed(self, mnemonic: str, password: str) -> bytes:
        salt = f"mnemonic{password}".encode()
        seed = hashlib.pbkdf2_hmac("sha512", mnemonic.encode(), salt, 2048)
        return seed

    def _validate_mnemonic(self, mnemonic: str) -> bool:
        words = mnemonic.split()
        return len(words) in [12, 15, 18, 21, 24]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_keys": len(self.keys),
            "used_keys": sum(1 for k in self.keys.values() if k.used),
            "unused_keys": sum(1 for k in self.keys.values() if not k.used),
            "has_master": self.master_key is not None,
            "has_mnemonic": self.mnemonic is not None,
            "encrypted": self.encrypted,
            "script_policy": dict(self.script_policy),
        }
