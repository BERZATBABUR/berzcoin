"""Bech32 encoding/decoding for SegWit addresses."""

BECH32_ALPHABET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'

def bech32_polymod(values: list) -> int:
    """Bech32 polynomial modulus."""
    generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = (chk >> 25)
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= generator[i] if ((b >> i) & 1) else 0
    return chk

def bech32_hrp_expand(hrp: str) -> list:
    """Expand HRP for checksum calculation."""
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]

def bech32_create_checksum(hrp: str, data: list) -> list:
    """Create bech32 checksum."""
    values = bech32_hrp_expand(hrp) + data
    polymod = bech32_polymod(values + [0,0,0,0,0,0]) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]

def bech32_verify_checksum(hrp: str, data: list) -> bool:
    """Verify bech32 checksum."""
    return bech32_polymod(bech32_hrp_expand(hrp) + data) == 1

def convertbits(data: bytes, frombits: int, tobits: int, pad: bool = True) -> list:
    """Convert bits between different bit lengths."""
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret

def bech32_encode(hrp: str, witver: int, witprog: bytes) -> str:
    """Encode SegWit address.

    Args:
        hrp: Human-readable part (bc, tb, bcrt)
        witver: Witness version (0-16)
        witprog: Witness program (2-40 bytes)

    Returns:
        Bech32 encoded address
    """
    if witver > 16:
        raise ValueError("Invalid witness version")
    if len(witprog) < 2 or len(witprog) > 40:
        raise ValueError("Invalid witness program length")

    # Convert to 5-bit data
    data = [witver] + convertbits(witprog, 8, 5)
    checksum = bech32_create_checksum(hrp, data)

    # Encode
    return hrp + '1' + ''.join(BECH32_ALPHABET[d] for d in data + checksum)

def bech32_decode(bech: str) -> tuple:
    """Decode bech32 address.

    Args:
        bech: Bech32 encoded string

    Returns:
        Tuple of (hrp, witver, witprog) or (None, None, None) if invalid
    """
    # Check length
    if len(bech) < 8 or len(bech) > 90:
        return (None, None, None)

    # Find separator
    if '1' not in bech:
        return (None, None, None)

    hrp = bech[:bech.rfind('1')]
    data = bech[bech.rfind('1') + 1:]

    # Check HRP length
    if len(hrp) < 1 or len(hrp) > 83:
        return (None, None, None)

    # Decode data
    try:
        decoded = [BECH32_ALPHABET.index(c) for c in data]
    except ValueError:
        return (None, None, None)

    # Verify checksum
    if not bech32_verify_checksum(hrp, decoded):
        return (None, None, None)

    # Remove checksum
    decoded = decoded[:-6]

    # Get witness version
    witver = decoded[0]
    if witver > 16:
        return (None, None, None)

    # Convert from 5-bit to 8-bit
    witprog = convertbits(decoded[1:], 5, 8, False)
    if witprog is None:
        return (None, None, None)

    # Check witness program length
    if len(witprog) < 2 or len(witprog) > 40:
        return (None, None, None)

    return (hrp, witver, bytes(witprog))
