"""Wallet file storage."""

import json
import os
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from shared.utils.logging import get_logger

logger = get_logger()

class WalletFile:
    """Wallet file manager."""
    
    VERSION = 1
    
    def __init__(self, path: str):
        """Initialize wallet file.
        
        Args:
            path: Path to wallet file
        """
        self.path = Path(path)
        self.temp_path = self.path.with_suffix('.tmp')
    
    def save(self, data: Dict[str, Any], password: Optional[str] = None) -> bool:
        """Save wallet data.
        
        Args:
            data: Wallet data
            password: Optional encryption password
        
        Returns:
            True if saved
        """
        try:
            # Prepare wallet file
            wallet_data = {
                'version': self.VERSION,
                'data': data
            }
            
            # Convert to JSON
            json_data = json.dumps(wallet_data, indent=2)
            
            # Encrypt if password provided
            if password:
                encrypted = self._encrypt(json_data.encode(), password)
                data_to_write = encrypted
            else:
                data_to_write = json_data.encode()
            
            # Write to temp file
            with open(self.temp_path, 'wb') as f:
                f.write(data_to_write)
            
            # Atomic rename
            self.temp_path.rename(self.path)
            
            logger.info(f"Saved wallet to {self.path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save wallet: {e}")
            return False
    
    def load(self, password: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Load wallet data (legacy plaintext JSON or AES-encrypted blob)."""
        if not self.path.exists():
            logger.warning(f"Wallet file not found: {self.path}")
            return None

        try:
            with open(self.path, "rb") as f:
                raw_data = f.read()
        except OSError as e:
            logger.error("Failed to read wallet: %s", e)
            return None

        try:
            if raw_data.lstrip().startswith(b"{"):
                json_data = raw_data.decode()
                wallet_data = json.loads(json_data)
                if wallet_data.get("version") != self.VERSION:
                    logger.warning("Wallet version mismatch: %s", wallet_data.get("version"))
                inner = wallet_data.get("data", {})
                if inner.get("mnemonic") and not password:
                    logger.error("Password required to open wallet file")
                    return None
                logger.info("Loaded wallet from %s (legacy plaintext)", self.path)
                return inner

            if not password:
                logger.error("Encrypted wallet requires a password")
                return None

            decrypted = self._decrypt(raw_data, password)
            json_data = decrypted.decode()
            wallet_data = json.loads(json_data)
            if wallet_data.get("version") != self.VERSION:
                logger.warning("Wallet version mismatch: %s", wallet_data.get("version"))
            logger.info("Loaded wallet from %s", self.path)
            return wallet_data.get("data", {})

        except Exception as e:
            logger.error("Failed to load wallet: %s", e)
            return None
    
    def _encrypt(self, data: bytes, password: str) -> bytes:
        """Encrypt data with password.
        
        Args:
            data: Data to encrypt
            password: Encryption password
        
        Returns:
            Encrypted data
        """
        # Generate salt
        salt = os.urandom(32)
        
        # Derive key from password (PBKDF2)
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000, dklen=32)
        
        # Generate IV
        iv = os.urandom(16)
        
        # Encrypt
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        
        # Pad data
        pad_len = 16 - (len(data) % 16)
        padded_data = data + bytes([pad_len] * pad_len)
        
        encrypted = encryptor.update(padded_data) + encryptor.finalize()
        
        # Return salt + iv + encrypted
        return salt + iv + encrypted
    
    def _decrypt(self, data: bytes, password: str) -> bytes:
        """Decrypt data with password.
        
        Args:
            data: Data to decrypt
            password: Decryption password
        
        Returns:
            Decrypted data
        """
        # Extract salt, iv, and encrypted data
        salt = data[:32]
        iv = data[32:48]
        encrypted = data[48:]
        
        # Derive key from password
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000, dklen=32)
        
        # Decrypt
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        
        decrypted = decryptor.update(encrypted) + decryptor.finalize()
        
        # Remove padding
        pad_len = decrypted[-1]
        return decrypted[:-pad_len]
    
    def backup(self) -> bool:
        """Create backup of wallet.
        
        Returns:
            True if backed up
        """
        if not self.path.exists():
            return False
        
        try:
            backup_path = self.path.with_suffix('.bak')
            import shutil
            shutil.copy(self.path, backup_path)
            logger.info(f"Created wallet backup: {backup_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to backup wallet: {e}")
            return False
    
    def exists(self) -> bool:
        """Check if wallet exists."""
        return self.path.exists()
    
    def get_size(self) -> int:
        """Get wallet file size."""
        if self.path.exists():
            return self.path.stat().st_size
        return 0
    
    def delete(self) -> bool:
        """Delete wallet file.
        
        Returns:
            True if deleted
        """
        try:
            if self.path.exists():
                self.path.unlink()
            if self.temp_path.exists():
                self.temp_path.unlink()
            logger.info(f"Deleted wallet: {self.path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete wallet: {e}")
            return False
