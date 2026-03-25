"""Logging configuration for BerzCoin."""

import logging
import sys
from typing import Optional

# Global logger instance
_logger: Optional[logging.Logger] = None

def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    debug: bool = False
) -> logging.Logger:
    """Setup logging configuration.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file to write logs to
        debug: Enable debug mode with more detailed logging

    Returns:
        Configured logger instance
    """
    global _logger

    # Convert string level to logging constant
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Create logger
    logger = logging.getLogger("berzcoin")
    logger.setLevel(log_level)

    # Clear existing handlers
    logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _logger = logger
    return logger

def get_logger() -> logging.Logger:
    """Get the global logger instance.

    Returns:
        Logger instance

    Raises:
        RuntimeError: If logger hasn't been setup
    """
    if _logger is None:
        # Default setup if not configured
        setup_logging()
    return _logger
