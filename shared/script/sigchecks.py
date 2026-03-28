"""Signature verification and sighash helpers."""

from typing import List, Optional, Tuple

from ..core.hashes import hash256
from ..core.hashes import sha256
from ..core.hashes import tagged_hash
from ..core.serialization import Serializer
from ..core.transaction import Transaction, TxOut
from ..crypto.keys import PublicKey
from ..crypto.secp256k1 import N as SECP256K1_ORDER
from ..crypto.signatures import verify_signature
from .script_flags import ScriptFlags


SIGHASH_ALL = 0x01
SIGHASH_NONE = 0x02
SIGHASH_SINGLE = 0x03
SIGHASH_ANYONECANPAY = 0x80


def calculate_legacy_sighash(
    tx: Transaction,
    input_index: int,
    sighash_type: int = SIGHASH_ALL,
    script_code: bytes = b"",
) -> bytes:
    """Compute a Bitcoin-style legacy sighash."""
    if input_index < 0 or input_index >= len(tx.vin):
        return b"\x00" * 32

    tx_copy, _ = Transaction.deserialize(tx.serialize(include_witness=False))

    for txin in tx_copy.vin:
        txin.script_sig = b""

    anyone_can_pay = (sighash_type & SIGHASH_ANYONECANPAY) != 0
    if anyone_can_pay:
        target = tx_copy.vin[input_index]
        target.script_sig = script_code or b""
        tx_copy.vin = [target]
        input_index = 0
    else:
        tx_copy.vin[input_index].script_sig = script_code or b""

    base_type = sighash_type & 0x1F
    if base_type == SIGHASH_NONE:
        tx_copy.vout = []
        for i, txin in enumerate(tx_copy.vin):
            if i != input_index:
                txin.sequence = 0
    elif base_type == SIGHASH_SINGLE:
        if input_index >= len(tx_copy.vout):
            return b"\x01" + (b"\x00" * 31)
        keep = tx_copy.vout[input_index]
        tx_copy.vout = [TxOut(value=0xFFFFFFFFFFFFFFFF, script_pubkey=b"")] * input_index + [keep]
        for i, txin in enumerate(tx_copy.vin):
            if i != input_index:
                txin.sequence = 0

    preimage = tx_copy.serialize(include_witness=False) + int(sighash_type).to_bytes(4, "little")
    return hash256(preimage)


def calculate_segwit_v0_sighash(
    tx: Transaction,
    input_index: int,
    amount: int,
    script_code: bytes,
    sighash_type: int = SIGHASH_ALL,
) -> bytes:
    """Compute BIP143-style SegWit v0 sighash."""
    if input_index < 0 or input_index >= len(tx.vin):
        return b"\x00" * 32

    base_type = sighash_type & 0x1F
    anyone_can_pay = (sighash_type & SIGHASH_ANYONECANPAY) != 0

    hash_prevouts = b"\x00" * 32
    hash_sequence = b"\x00" * 32
    hash_outputs = b"\x00" * 32

    if not anyone_can_pay:
        prevouts_data = b"".join(
            txin.prev_tx_hash + int(txin.prev_tx_index).to_bytes(4, "little")
            for txin in tx.vin
        )
        hash_prevouts = hash256(prevouts_data)

    if not anyone_can_pay and base_type not in (SIGHASH_NONE, SIGHASH_SINGLE):
        sequence_data = b"".join(int(txin.sequence).to_bytes(4, "little") for txin in tx.vin)
        hash_sequence = hash256(sequence_data)

    if base_type == SIGHASH_ALL:
        outputs_data = b"".join(txout.serialize() for txout in tx.vout)
        hash_outputs = hash256(outputs_data)
    elif base_type == SIGHASH_SINGLE:
        if input_index >= len(tx.vout):
            return b"\x01" + (b"\x00" * 31)
        hash_outputs = hash256(tx.vout[input_index].serialize())

    txin = tx.vin[input_index]
    preimage = b"".join(
        [
            int(tx.version).to_bytes(4, "little"),
            hash_prevouts,
            hash_sequence,
            txin.prev_tx_hash,
            int(txin.prev_tx_index).to_bytes(4, "little"),
            Serializer.write_bytes(script_code or b""),
            int(amount).to_bytes(8, "little"),
            int(txin.sequence).to_bytes(4, "little"),
            hash_outputs,
            int(tx.locktime).to_bytes(4, "little"),
            int(sighash_type).to_bytes(4, "little"),
        ]
    )
    return hash256(preimage)


def calculate_taproot_keypath_sighash(
    tx: Transaction,
    input_index: int,
    amount: int,
    script_pubkey: bytes,
    sighash_type: int = 0x00,
    annex: bytes = b"",
) -> bytes:
    """Compute BIP341-style key-path sighash with hash-type mode handling."""
    if input_index < 0 or input_index >= len(tx.vin):
        return b"\x00" * 32

    hash_type = sighash_type & 0xFF
    base_type = (hash_type & 0x03) or SIGHASH_ALL
    anyone_can_pay = bool(hash_type & SIGHASH_ANYONECANPAY)

    if hash_type not in (0x00, 0x01, 0x02, 0x03, 0x81, 0x82, 0x83):
        return b"\x00" * 32
    if base_type == SIGHASH_SINGLE and input_index >= len(tx.vout):
        return b"\x00" * 32

    prevouts = b"".join(txin.prev_tx_hash + int(txin.prev_tx_index).to_bytes(4, "little") for txin in tx.vin)
    sequences = b"".join(int(txin.sequence).to_bytes(4, "little") for txin in tx.vin)
    outputs_all = b"".join(txout.serialize() for txout in tx.vout)
    amounts = b"".join((int(amount) if idx == input_index else 0).to_bytes(8, "little") for idx in range(len(tx.vin)))
    scriptpubkeys = b"".join(
        Serializer.write_bytes(script_pubkey if idx == input_index else b"")
        for idx in range(len(tx.vin))
    )
    txin = tx.vin[input_index]

    hash_prevouts = sha256(prevouts) if not anyone_can_pay else (b"\x00" * 32)
    hash_amounts = sha256(amounts) if not anyone_can_pay else (b"\x00" * 32)
    hash_scriptpubkeys = sha256(scriptpubkeys) if not anyone_can_pay else (b"\x00" * 32)
    hash_sequences = sha256(sequences) if (not anyone_can_pay and base_type == SIGHASH_ALL) else (b"\x00" * 32)
    if base_type == SIGHASH_ALL:
        hash_outputs = sha256(outputs_all)
    elif base_type == SIGHASH_SINGLE:
        hash_outputs = sha256(tx.vout[input_index].serialize())
    else:
        hash_outputs = b"\x00" * 32

    spend_type = 1 if annex else 0
    preimage = b"".join(
        [
            b"\x00",  # epoch
            int(hash_type).to_bytes(1, "little"),
            int(tx.version).to_bytes(4, "little"),
            int(tx.locktime).to_bytes(4, "little"),
            hash_prevouts,
            hash_amounts,
            hash_scriptpubkeys,
            hash_sequences,
            hash_outputs,
            int(spend_type).to_bytes(1, "little"),
        ]
    )
    if anyone_can_pay:
        preimage += txin.prev_tx_hash
        preimage += int(txin.prev_tx_index).to_bytes(4, "little")
        preimage += int(amount).to_bytes(8, "little")
        preimage += Serializer.write_bytes(script_pubkey)
        preimage += int(txin.sequence).to_bytes(4, "little")
    else:
        preimage += int(input_index).to_bytes(4, "little")
    if annex:
        preimage += sha256(Serializer.write_bytes(annex))
    return tagged_hash("TapSighash", preimage)


def calculate_tapleaf_hash(script: bytes, leaf_version: int = 0xC0) -> bytes:
    """Compute TapLeaf hash for a tapscript leaf."""
    return tagged_hash(
        "TapLeaf",
        bytes([leaf_version & 0xFE]) + Serializer.write_varint(len(script)) + script,
    )


def calculate_taproot_scriptpath_sighash(
    tx: Transaction,
    input_index: int,
    amount: int,
    script_pubkey: bytes,
    tapleaf_hash: bytes,
    sighash_type: int = 0x00,
    codesep_pos: int = 0xFFFFFFFF,
    annex: bytes = b"",
) -> bytes:
    """Compute BIP342-style script-path sighash."""
    if len(tapleaf_hash) != 32:
        return b"\x00" * 32
    base = calculate_taproot_keypath_sighash(
        tx,
        input_index,
        amount,
        script_pubkey,
        sighash_type=sighash_type,
        annex=annex,
    )
    if base == b"\x00" * 32:
        return base
    ext = b"".join(
        [
            tapleaf_hash,
            bytes([0x00]),  # key_version
            int(codesep_pos).to_bytes(4, "little", signed=False),
        ]
    )
    return tagged_hash("TapSighash", base + ext)


def _parse_der_signature(signature_der: bytes) -> Optional[Tuple[int, int]]:
    if len(signature_der) < 8 or len(signature_der) > 72:
        return None
    if signature_der[0] != 0x30:
        return None
    if signature_der[1] != len(signature_der) - 2:
        return None
    if signature_der[2] != 0x02:
        return None

    r_len = signature_der[3]
    r_start = 4
    r_end = r_start + r_len
    if r_len == 0 or r_end >= len(signature_der):
        return None
    if signature_der[r_start] & 0x80:
        return None
    if r_len > 1 and signature_der[r_start] == 0x00 and not (signature_der[r_start + 1] & 0x80):
        return None

    if signature_der[r_end] != 0x02:
        return None
    s_len = signature_der[r_end + 1]
    s_start = r_end + 2
    s_end = s_start + s_len
    if s_len == 0 or s_end != len(signature_der):
        return None
    if signature_der[s_start] & 0x80:
        return None
    if s_len > 1 and signature_der[s_start] == 0x00 and not (signature_der[s_start + 1] & 0x80):
        return None

    r = int.from_bytes(signature_der[r_start:r_end], "big")
    s = int.from_bytes(signature_der[s_start:s_end], "big")
    return r, s


def _is_low_s(signature_der: bytes) -> bool:
    parsed = _parse_der_signature(signature_der)
    if not parsed:
        return False
    _, s = parsed
    return 1 <= s <= (SECP256K1_ORDER // 2)


class SignatureChecker:
    """Signature verification for script execution."""

    def __init__(
        self,
        tx: Transaction,
        input_index: int,
        amount: int,
        flags: int,
        script_code: bytes = b"",
        use_segwit_v0: bool = False,
        use_taproot_scriptpath: bool = False,
        tapleaf_hash: bytes = b"",
        annex: bytes = b"",
    ):
        self.tx = tx
        self.input_index = input_index
        self.amount = amount
        self.flags = flags
        self.script_code = script_code
        self.use_segwit_v0 = use_segwit_v0
        self.use_taproot_scriptpath = use_taproot_scriptpath
        self.tapleaf_hash = tapleaf_hash
        self.annex = annex

    def check_signature(
        self,
        signature: bytes,
        pubkey: bytes,
        script_code: Optional[bytes] = None,
    ) -> bool:
        if len(signature) == 0 or len(pubkey) == 0:
            return False
        try:
            sig_der = signature[:-1]
            if self.flags & ScriptFlags.VERIFY_DERSIG:
                if _parse_der_signature(sig_der) is None:
                    return False
            if self.flags & ScriptFlags.VERIFY_LOW_S:
                if not _is_low_s(sig_der):
                    return False
            if self.flags & ScriptFlags.VERIFY_STRICTENC and len(pubkey) not in (33, 65):
                return False
            if (
                self.use_segwit_v0
                and (self.flags & ScriptFlags.VERIFY_WITNESS_PUBKEYTYPE)
                and (len(pubkey) != 33 or pubkey[0] not in (0x02, 0x03))
            ):
                return False

            pubkey_obj = PublicKey.from_bytes(pubkey)
            sighash = self._get_sighash(signature, script_code=script_code)
            verified = verify_signature(pubkey_obj, sighash, sig_der)
            if not verified and (self.flags & ScriptFlags.VERIFY_NULLFAIL) and len(sig_der) > 0:
                return False
            return verified
        except Exception:
            return False

    def check_multisig(self, signatures: List[bytes], pubkeys: List[bytes]) -> bool:
        sig_index = 0
        for pubkey in pubkeys:
            if sig_index >= len(signatures):
                break
            if self.check_signature(signatures[sig_index], pubkey):
                sig_index += 1
        return sig_index == len(signatures)

    def _get_sighash(
        self,
        signature: bytes,
        script_code: Optional[bytes] = None,
    ) -> bytes:
        if len(signature) == 0:
            return b"\x00" * 32
        sighash_type = signature[-1]
        final_script = script_code if script_code is not None else self.script_code
        if self.use_segwit_v0:
            return calculate_segwit_v0_sighash(
                self.tx,
                self.input_index,
                self.amount,
                final_script,
                sighash_type=sighash_type,
            )
        if self.use_taproot_scriptpath:
            return calculate_taproot_scriptpath_sighash(
                self.tx,
                self.input_index,
                self.amount,
                final_script,
                self.tapleaf_hash,
                sighash_type=sighash_type,
                annex=self.annex,
            )
        return calculate_legacy_sighash(
            self.tx,
            self.input_index,
            sighash_type=sighash_type,
            script_code=final_script,
        )

    def check_schnorr_signature(
        self,
        signature: bytes,
        pubkey_xonly: bytes,
        script_code: Optional[bytes] = None,
    ) -> bool:
        if len(pubkey_xonly) != 32:
            return False
        if len(signature) not in (64, 65):
            return False
        sighash_type = signature[64] if len(signature) == 65 else 0x00
        if sighash_type not in (0x00, 0x01, 0x02, 0x03, 0x81, 0x82, 0x83):
            return False
        sig = signature[:64]
        sighash = self._get_sighash(signature if len(signature) == 65 else signature + b"\x00", script_code=script_code)
        try:
            from ..crypto.signatures import verify_schnorr_signature
            return verify_schnorr_signature(pubkey_xonly, sighash, sig)
        except Exception:
            return False
