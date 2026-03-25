"""Base58 encoding/decoding."""

import hashlib

# Base58 alphabet
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

def base58_encode(data: bytes) -> str:
    """Encode bytes to base58 string.

    Args:
        data: Bytes to encode

    Returns:
        Base58 encoded string
    """
    # Count leading zeros
    leading_zeros = 0
    for byte in data:
        if byte == 0:
            leading_zeros += 1
        else:
            break

    # Convert to integer
    num = int.from_bytes(data, 'big')

    # Encode
    result = []
    while num > 0:
        num, remainder = divmod(num, 58)
        result.append(ALPHABET[remainder])

    # Add leading zeros
    result.extend([ALPHABET[0]] * leading_zeros)

    # Reverse and return
    return ''.join(reversed(result))

def base58_decode(data: str) -> bytes:
    """Decode base58 string to bytes.

    Args:
        data: Base58 encoded string

    Returns:
        Decoded bytes
    """
    # Count leading zeros
    leading_zeros = 0
    for char in data:
        if char == ALPHABET[0]:
            leading_zeros += 1
        else:
            break

    # Convert from base58
    num = 0
    for char in data:
        num = num * 58 + ALPHABET.index(char)

    # Convert to bytes
    result = num.to_bytes((num.bit_length() + 7) // 8, 'big')

    # Add leading zero bytes
    return b"\x00" * leading_zeros + result

def base58_check_encode(data: bytes) -> str:
    """Encode with checksum (version + data + checksum).

    Args:
        data: Data to encode

    Returns:
        Base58Check encoded string
    """
    # Calculate checksum (first 4 bytes of double SHA256)
    checksum = hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]

    # Append checksum and encode
    return base58_encode(data + checksum)

def base58_check_decode(data: str) -> bytes:
    """Decode base58check string.

    Args:
        data: Base58Check encoded string

    Returns:
        Decoded data (without checksum)

    Raises:
        ValueError: If checksum is invalid
    """
    decoded = base58_decode(data)

    # Split data and checksum
    payload = decoded[:-4]
    checksum = decoded[-4:]

    # Verify checksum
    calculated = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != calculated:
        raise ValueError("Invalid checksum")

    return payload
