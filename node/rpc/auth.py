"""RPC authentication and authorization."""

import os
import secrets
import hashlib
import time
from typing import Optional, Dict, Set, Any
from dataclasses import dataclass, field
from shared.utils.logging import get_logger

logger = get_logger()


@dataclass
class User:
    """RPC user."""
    username: str
    password_hash: str
    permissions: Set[str] = field(default_factory=set)
    created_at: int = 0


class AuthManager:
    """RPC authentication manager."""

    def __init__(self, rpc_dir: str):
        """Initialize auth manager.

        Generates a random RPC cookie on disk; there is no built-in default password user.
        Optional users may be added later (e.g. from node configuration).

        Args:
            rpc_dir: Directory for RPC files (expanded with ``~``).
        """
        self.rpc_dir = os.path.expanduser(rpc_dir)
        self.users: Dict[str, User] = {}
        self.cookie_file = os.path.join(self.rpc_dir, ".cookie")
        self.cookie: Optional[str] = None

        self._generate_cookie()

    def _generate_cookie(self) -> None:
        """Create or load ``berzcoin:<token>`` with strict permissions (exclusive create when new)."""
        self.cookie = secrets.token_urlsafe(48)

        try:
            os.makedirs(self.rpc_dir, exist_ok=True)

            with open(self.cookie_file, "x", encoding="utf-8") as f:
                f.write(f"berzcoin:{self.cookie}")
            os.chmod(self.cookie_file, 0o600)
            logger.info("Secure RPC cookie created: %s", self.cookie_file)

        except FileExistsError:
            try:
                with open(self.cookie_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if ":" in content:
                    self.cookie = content.split(":", 1)[1]
                else:
                    logger.error("Invalid RPC cookie file format: %s", self.cookie_file)
                    raise RuntimeError("Invalid .cookie file") from None
                logger.info("Loaded existing RPC cookie from %s", self.cookie_file)
            except Exception as e:
                logger.error("Failed to read existing RPC cookie: %s", e)
                raise

        except Exception as e:
            logger.error("Failed to create secure RPC cookie: %s", e)
            raise

    def authenticate(self, username: str, password: str) -> bool:
        """Authenticate via cookie user ``berzcoin`` or an explicit user entry."""
        if username == "berzcoin" and password == self.cookie:
            return True

        user = self.users.get(username)
        if not user:
            return False

        password_hash = self._hash_password(password)
        return password_hash == user.password_hash

    def add_user(self, username: str, password: str, permissions: Optional[Set[str]] = None) -> bool:
        """Add an RPC user (password is hashed; intended for config-driven setup, not a default user)."""
        if username in self.users:
            return False

        perms = permissions if permissions is not None else {"*"}
        self.users[username] = User(
            username=username,
            password_hash=self._hash_password(password),
            permissions=perms,
            created_at=int(time.time()),
        )

        logger.info(f"Added RPC user: {username}")
        return True

    def remove_user(self, username: str) -> bool:
        """Remove RPC user."""
        if username not in self.users:
            return False

        del self.users[username]
        logger.info(f"Removed RPC user: {username}")
        return True

    def check_permission(self, username: str, method: str) -> bool:
        """Check if user has permission for method."""
        if username == "berzcoin":
            return True

        user = self.users.get(username)
        if not user:
            return False

        if '*' in user.permissions:
            return True

        if method in user.permissions:
            return True

        category = method.split('_')[0] if '_' in method else method
        if f"{category}.*" in user.permissions:
            return True

        return False

    def _hash_password(self, password: str) -> str:
        """Hash password (SHA256 hex)."""
        return hashlib.sha256(password.encode()).hexdigest()

    def get_cookie(self) -> Optional[str]:
        """Get RPC cookie value."""
        return self.cookie

    def get_users(self) -> list:
        """List usernames."""
        return list(self.users.keys())

    def rotate_cookie(self) -> None:
        """Rotate RPC cookie (replace on-disk file)."""
        try:
            if os.path.isfile(self.cookie_file):
                os.unlink(self.cookie_file)
        except OSError as e:
            logger.error("Failed to remove RPC cookie for rotation: %s", e)
            raise
        self._generate_cookie()
        logger.info("RPC cookie rotated")

    def get_stats(self) -> Dict[str, Any]:
        """Auth statistics."""
        return {
            'cookie_exists': os.path.exists(self.cookie_file),
            'total_users': len(self.users),
            'users': list(self.users.keys())
        }
