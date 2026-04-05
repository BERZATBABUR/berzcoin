"""Simple private-key based wallet with encrypted-at-rest storage."""

import base64
import json
import os
import secrets
import time
import hashlib
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shared.crypto.keys import PrivateKey, PublicKey
from shared.crypto.address import public_key_to_address
from shared.crypto.hd import derive_bip44_private_key, generate_bip39_mnemonic, mnemonic_to_seed
from shared.crypto.xpub import derive_xpub_external_pubkey, parse_xpub
from shared.utils.logging import get_logger

logger = get_logger()

_WALLET_ENCRYPTED_FORMAT = "berzcoin.wallet.encrypted.v1"
_WALLET_AAD = b"berzcoin.wallet.v1"
_INSECURE_FALLBACK_PASSPHRASE = "berzcoin-dev-insecure-passphrase"


def redact_secret(secret: str, keep_start: int = 6, keep_end: int = 4) -> str:
    """Return a redacted secret for safe logs."""
    text = str(secret or "").strip()
    if not text:
        return "<empty>"
    if len(text) <= keep_start + keep_end:
        return "*" * len(text)
    return f"{text[:keep_start]}...{text[-keep_end:]}"


def normalize_private_key_hex(private_key: str) -> str:
    """Normalize and validate private key hex string."""
    key = str(private_key or "").strip().lower()
    if key.startswith("0x"):
        key = key[2:]
    if not key:
        raise ValueError("Private key required")
    if len(key) > 64:
        raise ValueError("Private key too long")
    if any(ch not in "0123456789abcdef" for ch in key):
        raise ValueError("Invalid private key hex")
    return key.rjust(64, "0")


def mnemonic_from_private_key(private_key_hex: str) -> str:
    """Derive a deterministic 12-word phrase from private key material."""
    words = [
        "abandon", "ability", "able", "about", "above", "absent",
        "absorb", "abstract", "absurd", "abuse", "access", "accident",
    ]
    key_bytes = bytes.fromhex(private_key_hex.rjust(64, "0"))
    digest = hashlib.sha256(key_bytes).digest()
    return " ".join(words[b % len(words)] for b in digest[:12])


@dataclass
class SimpleWallet:
    """Simple wallet that uses private key as identity."""

    private_key_hex: str
    public_key_hex: str
    address: str
    mnemonic: str
    wallet_type: str = "deterministic"
    wallet_id: str = ""
    derivation_path: str = ""
    coin_type: int = 0
    account: int = 0
    external_index: int = 0
    internal_index: int = 0
    watch_only: bool = False
    xpub: str = ""
    label: str = ""
    tracked_addresses: List[str] = field(default_factory=list)
    network: str = "mainnet"
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(cls, network: str = "mainnet") -> "SimpleWallet":
        """Create a new deterministic BIP39/BIP44 wallet."""
        mnemonic = generate_bip39_mnemonic(128)
        return cls.from_mnemonic(mnemonic, network=network, external_index=0)

    @classmethod
    def from_mnemonic(
        cls,
        mnemonic: str,
        network: str = "mainnet",
        external_index: int = 0,
        account: int = 0,
        internal_index: int = 0,
    ) -> "SimpleWallet":
        seed = mnemonic_to_seed(mnemonic, "")
        coin_type = 0 if str(network).lower() == "mainnet" else 1
        child_key_int, path = derive_bip44_private_key(
            seed=seed,
            coin_type=coin_type,
            account=account,
            change=0,
            address_index=external_index,
        )
        private_key = PrivateKey(child_key_int)
        public_key = private_key.public_key()
        address = public_key_to_address(public_key, network=network)
        return cls(
            private_key_hex=private_key.to_hex(),
            public_key_hex=public_key.to_bytes().hex(),
            address=address,
            mnemonic=mnemonic,
            wallet_type="deterministic",
            wallet_id=address,
            derivation_path=path,
            coin_type=coin_type,
            account=account,
            external_index=int(external_index),
            internal_index=int(internal_index),
            watch_only=False,
            xpub="",
            label="",
            tracked_addresses=[address],
            network=network,
        )

    @classmethod
    def from_private_key(cls, private_key_hex: str, network: str = "mainnet") -> "SimpleWallet":
        """Load wallet from private key."""
        normalized_key = normalize_private_key_hex(private_key_hex)
        private_key = PrivateKey(int(normalized_key, 16))
        public_key = private_key.public_key()
        address = public_key_to_address(public_key, network=network)

        return cls(
            private_key_hex=normalized_key,
            public_key_hex=public_key.to_bytes().hex(),
            address=address,
            mnemonic=mnemonic_from_private_key(normalized_key),
            wallet_type="imported",
            wallet_id=address,
            derivation_path="imported",
            coin_type=0 if str(network).lower() == "mainnet" else 1,
            account=0,
            external_index=0,
            internal_index=0,
            watch_only=False,
            xpub="",
            label="",
            tracked_addresses=[address],
            network=network,
        )

    @classmethod
    def from_xpub(
        cls,
        xpub: str,
        network: str = "mainnet",
        external_index: int = 0,
        label: str = "",
    ) -> "SimpleWallet":
        node = parse_xpub(xpub)
        node_net = node.network
        expected = str(network or "mainnet").strip().lower()
        if expected == "regtest":
            expected = "testnet"
        if node_net != expected:
            raise ValueError("xpub network mismatch")
        pubkey_bytes = derive_xpub_external_pubkey(xpub, int(external_index))
        pub = PublicKey.from_bytes(pubkey_bytes)
        address = public_key_to_address(pub, network=network, segwit=True)
        wallet_id = "xpub_" + hashlib.sha256(str(xpub).encode("utf-8")).hexdigest()[:24]
        return cls(
            private_key_hex="",
            public_key_hex=pubkey_bytes.hex(),
            address=address,
            mnemonic="",
            wallet_type="watch_only_xpub",
            wallet_id=wallet_id,
            derivation_path=f"xpub/0/{int(external_index)}",
            coin_type=0 if str(network).lower() == "mainnet" else 1,
            account=0,
            external_index=int(external_index),
            internal_index=0,
            watch_only=True,
            xpub=str(xpub),
            label=str(label or ""),
            tracked_addresses=[address],
            network=network,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "private_key": self.private_key_hex,
            "public_key": self.public_key_hex,
            "address": self.address,
            "mnemonic": self.mnemonic,
            "wallet_type": self.wallet_type,
            "wallet_id": self.wallet_id or self.address,
            "derivation_path": self.derivation_path,
            "coin_type": int(self.coin_type),
            "account": int(self.account),
            "external_index": int(self.external_index),
            "internal_index": int(self.internal_index),
            "watch_only": bool(self.watch_only),
            "xpub": self.xpub,
            "label": self.label,
            "tracked_addresses": list(self.tracked_addresses or []),
            "network": self.network,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SimpleWallet":
        """Create from dictionary."""
        return cls(
            private_key_hex=data["private_key"],
            public_key_hex=data["public_key"],
            address=data["address"],
            mnemonic=data.get("mnemonic", ""),
            wallet_type=str(data.get("wallet_type", "imported" if data.get("derivation_path") == "imported" else "deterministic")),
            wallet_id=str(data.get("wallet_id", data.get("address", ""))),
            derivation_path=str(data.get("derivation_path", "")),
            coin_type=int(data.get("coin_type", 0)),
            account=int(data.get("account", 0)),
            external_index=int(data.get("external_index", 0)),
            internal_index=int(data.get("internal_index", 0)),
            watch_only=bool(data.get("watch_only", False)),
            xpub=str(data.get("xpub", "")),
            label=str(data.get("label", "")),
            tracked_addresses=list(data.get("tracked_addresses", [])),
            network=data.get("network", "mainnet"),
            created_at=data.get("created_at", time.time()),
        )


class SimpleWalletManager:
    """Manage simple wallets by private key."""

    def __init__(
        self,
        data_dir: Path,
        network: str = "mainnet",
        wallet_passphrase: Optional[str] = None,
        default_unlock_timeout_secs: int = 300,
    ):
        """Initialize wallet manager."""
        self.data_dir = data_dir
        self.network = str(network or "mainnet")
        self.wallets_dir = data_dir / "wallets"
        self.wallets_dir.mkdir(parents=True, exist_ok=True)
        self.active_wallet: Optional[SimpleWallet] = None
        self.active_private_key: Optional[str] = None
        self.default_unlock_timeout_secs = max(1, int(default_unlock_timeout_secs or 300))
        self._unlocked_until: float = 0.0
        self._wallet_passphrase = self._resolve_wallet_passphrase(wallet_passphrase)

    def create_wallet(self) -> SimpleWallet:
        """Create a new wallet."""
        wallet = SimpleWallet.create(network=self.network)
        self._save_wallet(wallet)
        self._unlock_for(self.default_unlock_timeout_secs, wallet.private_key_hex)
        return wallet

    def activate_wallet(self, private_key: str) -> Optional[SimpleWallet]:
        """Activate wallet using private key."""
        normalized_key = normalize_private_key_hex(private_key)
        # Try to load existing
        wallet = self._load_wallet_by_private_key(normalized_key)
        if not wallet:
            # Create new from private key
            wallet = SimpleWallet.from_private_key(normalized_key, network=self.network)
            self._save_wallet(wallet)

        self.active_wallet = wallet
        self._unlock_for(self.default_unlock_timeout_secs, normalized_key)
        logger.info(
            "Wallet activated: %s... (key=%s)",
            wallet.address[:16],
            redact_secret(normalized_key),
        )
        return wallet

    def derive_new_address(self) -> Optional[SimpleWallet]:
        """Derive next external child address for active deterministic wallet."""
        wallet = self.active_wallet
        if wallet is None:
            return None
        wt = str(wallet.wallet_type).lower()
        if wt not in {"deterministic", "watch_only_xpub"}:
            # Imported-key wallet has no HD seed chain.
            return wallet
        if not wallet.mnemonic:
            if wt == "watch_only_xpub":
                next_index = int(wallet.external_index) + 1
                derived = SimpleWallet.from_xpub(
                    wallet.xpub,
                    network=wallet.network,
                    external_index=next_index,
                    label=wallet.label,
                )
            else:
                raise ValueError("Deterministic wallet missing mnemonic")
        else:
            next_index = int(wallet.external_index) + 1
            derived = SimpleWallet.from_mnemonic(
                wallet.mnemonic,
                network=wallet.network,
                external_index=next_index,
                account=int(wallet.account),
                internal_index=int(wallet.internal_index),
            )
        # Preserve stable wallet identity/file id and created timestamp.
        derived.wallet_id = wallet.wallet_id or wallet.address
        derived.created_at = wallet.created_at
        seen = list(wallet.tracked_addresses or [])
        if wallet.address and wallet.address not in seen:
            seen.append(wallet.address)
        if derived.address not in seen:
            seen.append(derived.address)
        derived.tracked_addresses = seen
        self.active_wallet = derived
        # Keep signing key unlocked window with new derived child key.
        if derived.private_key_hex:
            self._unlock_for(self.default_unlock_timeout_secs, derived.private_key_hex)
        else:
            self.lock_wallet()
        self._save_wallet(derived)
        return derived

    def import_xpub_watch_only(self, xpub: str, label: str = "") -> SimpleWallet:
        """Create and activate a watch-only wallet from account-level xpub."""
        wallet = SimpleWallet.from_xpub(xpub, network=self.network, external_index=0, label=label)
        self._save_wallet(wallet)
        self.active_wallet = wallet
        self.lock_wallet()
        logger.info("Watch-only xpub imported: %s...", wallet.address[:16])
        return wallet

    def get_active_wallet(self) -> Optional[SimpleWallet]:
        """Get active wallet."""
        return self.active_wallet

    def get_active_address(self) -> Optional[str]:
        """Get active wallet address."""
        return self.active_wallet.address if self.active_wallet else None

    def get_active_public_key(self) -> Optional[str]:
        """Get active wallet public key."""
        return self.active_wallet.public_key_hex if self.active_wallet else None

    def get_active_private_key(self) -> Optional[str]:
        """Get active wallet private key (USE WITH CARE)."""
        self._auto_lock_if_needed()
        return self.active_private_key

    def is_wallet_unlocked(self) -> bool:
        """Return True when wallet signing key is unlocked in memory."""
        self._auto_lock_if_needed()
        return bool(self.active_private_key)

    def lock_wallet(self) -> None:
        """Lock wallet in memory immediately."""
        self.active_private_key = None
        self._unlocked_until = 0.0

    def wallet_passphrase(self, passphrase: str, timeout_secs: int) -> bool:
        """Unlock currently active wallet with passphrase for timeout seconds."""
        if not self.active_wallet:
            return False
        key = str(passphrase or "")
        if not key:
            return False
        if int(timeout_secs) <= 0:
            return False

        wallet_id = str(self.active_wallet.wallet_id or self.active_wallet.address).strip()
        wallet_file = self.wallets_dir / f"{wallet_id}.json"
        if not wallet_file.exists():
            return False
        try:
            with open(wallet_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            decrypted = self._decrypt_wallet_record(raw, key)
            wallet = SimpleWallet.from_dict(decrypted)
        except Exception:
            return False

        if wallet.address != self.active_wallet.address:
            return False

        self.active_wallet = wallet
        self._wallet_passphrase = key
        if not wallet.private_key_hex:
            self.lock_wallet()
            return False
        self._unlock_for(int(timeout_secs), wallet.private_key_hex)
        return True

    def get_balance(self, chainstate) -> int:
        """Get balance for active wallet from chain."""
        if not self.active_wallet:
            return 0

        tracked = list(self.active_wallet.tracked_addresses or [])
        if self.active_wallet.address and self.active_wallet.address not in tracked:
            tracked.append(self.active_wallet.address)
        if not tracked:
            tracked = [self.active_wallet.address]
        total = 0
        for addr in tracked:
            utxos = chainstate.get_utxos_for_address(addr, 1000)
            total += sum(int(u.get("value", 0)) for u in utxos)
        return total

    def _save_wallet(self, wallet: SimpleWallet) -> None:
        """Save wallet to disk."""
        wallet_id = str(wallet.wallet_id or wallet.address or "").strip()
        wallet_file = self.wallets_dir / f"{wallet_id}.json"
        payload = self._encrypt_wallet_record(wallet.to_dict(), self._wallet_passphrase)
        with open(wallet_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.debug(f"Wallet saved: {wallet_file}")

    def _load_wallet_by_private_key(self, private_key_hex: str) -> Optional[SimpleWallet]:
        """Load wallet by private key by scanning stored wallet records."""
        target = normalize_private_key_hex(private_key_hex)
        for wallet_file in self.wallets_dir.glob("*.json"):
            try:
                with open(wallet_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                decoded = self._try_decode_wallet_record(raw, self._wallet_passphrase)
                if not decoded:
                    continue
                if normalize_private_key_hex(str(decoded.get("private_key", ""))) == target:
                    return SimpleWallet.from_dict(decoded)
            except Exception:
                continue
        return None

    def list_wallets(self) -> List[str]:
        """List all wallet addresses."""
        wallets: List[str] = []
        for f in self.wallets_dir.glob("*.json"):
            wallets.append(f.stem)
        return wallets

    def _unlock_for(self, timeout_secs: int, private_key_hex: str) -> None:
        self.active_private_key = normalize_private_key_hex(private_key_hex)
        self._unlocked_until = time.time() + max(1, int(timeout_secs))

    def _auto_lock_if_needed(self) -> None:
        if self.active_private_key and time.time() >= self._unlocked_until:
            logger.info("Wallet auto-locked after timeout")
            self.lock_wallet()

    def _resolve_wallet_passphrase(self, configured: Optional[str]) -> str:
        candidate = str(configured or "").strip()
        if candidate:
            return candidate
        env_value = str(os.getenv("BERZCOIN_WALLET_PASSPHRASE", "")).strip()
        if env_value:
            return env_value
        logger.warning(
            "Using insecure fallback wallet encryption passphrase; set wallet_encryption_passphrase"
        )
        return _INSECURE_FALLBACK_PASSPHRASE

    def _encrypt_wallet_record(self, record: Dict[str, Any], passphrase: str) -> Dict[str, Any]:
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        key = hashlib.scrypt(
            passphrase.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
        plaintext = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, _WALLET_AAD)
        return {
            "format": _WALLET_ENCRYPTED_FORMAT,
            "cipher": {"name": "aes-256-gcm", "nonce_b64": base64.b64encode(nonce).decode("ascii")},
            "kdf": {
                "name": "scrypt",
                "salt_b64": base64.b64encode(salt).decode("ascii"),
                "n": 2**14,
                "r": 8,
                "p": 1,
                "dklen": 32,
            },
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        }

    def _decrypt_wallet_record(self, raw: Dict[str, Any], passphrase: str) -> Dict[str, Any]:
        if str(raw.get("format", "")) != _WALLET_ENCRYPTED_FORMAT:
            raise ValueError("Unsupported wallet format")
        kdf = dict(raw.get("kdf", {}))
        cipher = dict(raw.get("cipher", {}))
        salt = base64.b64decode(str(kdf.get("salt_b64", "")))
        nonce = base64.b64decode(str(cipher.get("nonce_b64", "")))
        ciphertext = base64.b64decode(str(raw.get("ciphertext_b64", "")))
        key = hashlib.scrypt(
            passphrase.encode("utf-8"),
            salt=salt,
            n=int(kdf.get("n", 2**14)),
            r=int(kdf.get("r", 8)),
            p=int(kdf.get("p", 1)),
            dklen=int(kdf.get("dklen", 32)),
        )
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _WALLET_AAD)
        decoded = json.loads(plaintext.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Invalid decrypted wallet payload")
        return decoded

    def _try_decode_wallet_record(self, raw: Dict[str, Any], passphrase: str) -> Optional[Dict[str, Any]]:
        if "private_key" in raw:
            # Legacy plaintext wallet file.
            return raw
        try:
            return self._decrypt_wallet_record(raw, passphrase)
        except Exception:
            return None
