"""Time utilities for BerzCoin."""

import time
from datetime import datetime
from typing import Optional

def current_time() -> int:
    """Get current Unix timestamp.

    Returns:
        Current time in seconds since epoch
    """
    return int(time.time())

def time_to_datetime(timestamp: int) -> datetime:
    """Convert Unix timestamp to datetime object.

    Args:
        timestamp: Unix timestamp in seconds

    Returns:
        Datetime object
    """
    return datetime.fromtimestamp(timestamp)

def datetime_to_time(dt: datetime) -> int:
    """Convert datetime to Unix timestamp.

    Args:
        dt: Datetime object

    Returns:
        Unix timestamp in seconds
    """
    return int(dt.timestamp())

def is_timestamp_valid(timestamp: int, max_future_seconds: int = 7200) -> bool:
    """Check if timestamp is valid (not too far in past or future).

    Args:
        timestamp: Timestamp to check
        max_future_seconds: Maximum allowed future seconds

    Returns:
        True if timestamp is valid
    """
    now = current_time()
    if timestamp > now + max_future_seconds:
        return False
    if timestamp < now - max_future_seconds:
        return False
    return True

def median_time_past(timestamps: list) -> int:
    """Calculate median time past from list of timestamps.

    Args:
        timestamps: List of timestamps

    Returns:
        Median timestamp
    """
    if not timestamps:
        return 0

    sorted_timestamps = sorted(timestamps)
    length = len(sorted_timestamps)

    if length % 2 == 0:
        return (sorted_timestamps[length // 2 - 1] + sorted_timestamps[length // 2]) // 2
    else:
        return sorted_timestamps[length // 2]
