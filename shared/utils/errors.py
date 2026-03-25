"""Base exception classes for BerzCoin."""

class BerzCoinError(Exception):
    """Base exception for all BerzCoin errors."""
    pass

class ValidationError(BerzCoinError):
    """Raised when validation fails."""
    pass

class ScriptError(ValidationError):
    """Raised when script execution fails."""
    pass

class SignatureError(ValidationError):
    """Raised when signature verification fails."""
    pass

class SerializationError(BerzCoinError):
    """Raised when serialization/deserialization fails."""
    pass

class ProtocolError(BerzCoinError):
    """Raised when P2P protocol errors occur."""
    pass

class StorageError(BerzCoinError):
    """Raised when storage operations fail."""
    pass

class WalletError(BerzCoinError):
    """Raised when wallet operations fail."""
    pass

class MiningError(BerzCoinError):
    """Raised when mining operations fail."""
    pass

class ConfigurationError(BerzCoinError):
    """Raised when configuration is invalid."""
    pass
