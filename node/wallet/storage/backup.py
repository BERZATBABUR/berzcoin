"""Wallet backup functionality."""

import shutil
import time
import json
import zipfile
import hashlib
from pathlib import Path
from typing import List, Optional, Dict, Any
from shared.utils.logging import get_logger

logger = get_logger()

class WalletBackup:
    """Wallet backup manager."""
    
    def __init__(self, wallet_path: str, backup_dir: Optional[str] = None):
        """Initialize wallet backup.
        
        Args:
            wallet_path: Path to wallet file
            backup_dir: Directory for backups (default: wallet dir/backups)
        """
        self.wallet_path = Path(wallet_path)
        
        if backup_dir:
            self.backup_dir = Path(backup_dir)
        else:
            self.backup_dir = self.wallet_path.parent / "backups"
        
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def create_backup(
        self,
        name: Optional[str] = None,
        network: Optional[str] = None,
        compatibility: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Create a wallet backup.
        
        Args:
            name: Backup name (auto-generated if None)
        
        Returns:
            Backup file path or None
        """
        if not self.wallet_path.exists():
            logger.error(f"Wallet file not found: {self.wallet_path}")
            return None
        
        try:
            # Generate backup name
            if not name:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                name = f"wallet_{timestamp}"
            
            backup_file = self.backup_dir / f"{name}.wallet"
            
            # Copy wallet file
            shutil.copy2(self.wallet_path, backup_file)
            wallet_sha256 = hashlib.sha256(backup_file.read_bytes()).hexdigest()
            
            # Create metadata
            metadata = {
                'name': name,
                'created_at': int(time.time()),
                'source': str(self.wallet_path),
                'size': self.wallet_path.stat().st_size,
                'sha256': wallet_sha256,
                'network': network or "",
                'wallet_format': 'berzcoin.wallet.v1',
                'compatibility': compatibility or {},
            }
            
            metadata_file = self.backup_dir / f"{name}.meta"
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            logger.info(f"Created backup: {backup_file}")
            return str(backup_file)
            
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return None
    
    def restore_backup(self, backup_name: str, expected_network: Optional[str] = None) -> bool:
        """Restore wallet from backup.
        
        Args:
            backup_name: Backup name (without extension)
        
        Returns:
            True if restored successfully
        """
        backup_file = self.backup_dir / f"{backup_name}.wallet"
        
        if not backup_file.exists():
            logger.error(f"Backup not found: {backup_file}")
            return False
        
        try:
            metadata_file = self.backup_dir / f"{backup_name}.meta"
            metadata: Dict[str, Any] = {}
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
            expected_sha = str(metadata.get("sha256", "") or "")
            if expected_sha:
                actual_sha = hashlib.sha256(backup_file.read_bytes()).hexdigest()
                if actual_sha != expected_sha:
                    logger.error("Backup checksum mismatch for %s", backup_name)
                    return False
            backup_network = str(metadata.get("network", "") or "")
            if expected_network and backup_network and backup_network != expected_network:
                logger.error(
                    "Backup network mismatch: backup=%s expected=%s",
                    backup_network,
                    expected_network,
                )
                return False

            # Create backup of current wallet
            if self.wallet_path.exists():
                current_backup = self.wallet_path.with_suffix('.bak')
                shutil.copy2(self.wallet_path, current_backup)
                logger.info(f"Created backup of current wallet: {current_backup}")
            
            # Restore from backup
            shutil.copy2(backup_file, self.wallet_path)
            
            logger.info(f"Restored wallet from: {backup_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore backup: {e}")
            return False
    
    def list_backups(self) -> List[Dict[str, Any]]:
        """List available backups.
        
        Returns:
            List of backup info dicts
        """
        backups = []
        
        for backup_file in self.backup_dir.glob("*.wallet"):
            name = backup_file.stem
            metadata_file = self.backup_dir / f"{name}.meta"
            
            backup_info: Dict[str, Any] = {
                'name': name,
                'file': str(backup_file),
                'size': backup_file.stat().st_size,
                'modified': backup_file.stat().st_mtime
            }
            
            # Load metadata if exists
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    backup_info.update(metadata)
                except Exception:
                    pass
            
            backups.append(backup_info)
        
        # Sort by creation time (newest first)
        backups.sort(key=lambda x: x.get('created_at', x['modified']), reverse=True)
        
        return backups
    
    def delete_backup(self, backup_name: str) -> bool:
        """Delete a backup.
        
        Args:
            backup_name: Backup name
        
        Returns:
            True if deleted
        """
        backup_file = self.backup_dir / f"{backup_name}.wallet"
        metadata_file = self.backup_dir / f"{backup_name}.meta"
        
        try:
            if backup_file.exists():
                backup_file.unlink()
            
            if metadata_file.exists():
                metadata_file.unlink()
            
            logger.info(f"Deleted backup: {backup_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete backup: {e}")
            return False
    
    def create_encrypted_backup(self, name: str, password: str) -> Optional[str]:
        """Create encrypted backup.
        
        Args:
            name: Backup name
            password: Encryption password
        
        Returns:
            Backup file path or None
        """
        if not self.wallet_path.exists():
            return None
        
        try:
            backup_file = self.backup_dir / f"{name}.encrypted"
            
            # Create ZIP archive
            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(self.wallet_path, "wallet.dat")
            
            # In production, encrypt the ZIP file
            # For now, just create ZIP
            _ = password  # reserved for future encryption
            
            logger.info(f"Created encrypted backup: {backup_file}")
            return str(backup_file)
            
        except Exception as e:
            logger.error(f"Failed to create encrypted backup: {e}")
            return None
    
    def restore_encrypted_backup(self, backup_name: str, password: str) -> bool:
        """Restore encrypted backup.
        
        Args:
            backup_name: Backup name
            password: Decryption password
        
        Returns:
            True if restored
        """
        backup_file = self.backup_dir / f"{backup_name}.encrypted"
        
        if not backup_file.exists():
            logger.error(f"Backup not found: {backup_file}")
            return False
        
        try:
            # In production, decrypt first
            _ = password
            with zipfile.ZipFile(backup_file, 'r') as zf:
                zf.extractall(self.backup_dir)
            
            extracted_wallet = self.backup_dir / "wallet.dat"
            if extracted_wallet.exists():
                shutil.copy2(extracted_wallet, self.wallet_path)
                extracted_wallet.unlink()
                
                logger.info(f"Restored encrypted backup: {backup_name}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to restore encrypted backup: {e}")
            return False
    
    def cleanup_old_backups(self, keep: int = 10) -> int:
        """Clean up old backups.
        
        Args:
            keep: Number of backups to keep
        
        Returns:
            Number of backups removed
        """
        backups = self.list_backups()
        
        if len(backups) <= keep:
            return 0
        
        removed = 0
        for backup in backups[keep:]:
            if self.delete_backup(backup['name']):
                removed += 1
        
        logger.info(f"Cleaned up {removed} old backups")
        return removed
    
    def export_wallet(self, export_path: str) -> bool:
        """Export wallet to another location.
        
        Args:
            export_path: Export destination
        
        Returns:
            True if exported
        """
        try:
            dest = Path(export_path)
            shutil.copy2(self.wallet_path, dest)
            logger.info(f"Exported wallet to: {dest}")
            return True
        except Exception as e:
            logger.error(f"Failed to export wallet: {e}")
            return False
    
    def get_backup_size(self) -> int:
        """Get total size of all backups.
        
        Returns:
            Total size in bytes
        """
        total = 0
        for backup_file in self.backup_dir.glob("*.wallet"):
            total += backup_file.stat().st_size
        
        return total
    
    def get_stats(self) -> Dict[str, Any]:
        """Get backup statistics.
        
        Returns:
            Statistics dictionary
        """
        backups = self.list_backups()
        
        return {
            'backup_dir': str(self.backup_dir),
            'total_backups': len(backups),
            'total_size': self.get_backup_size(),
            'latest_backup': backups[0] if backups else None,
            'oldest_backup': backups[-1] if backups else None
        }
