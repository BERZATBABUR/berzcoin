"""Signature verification for Bitcoin script."""

from typing import List
from ..crypto.keys import PublicKey
from ..crypto.signatures import verify_signature
from ..core.hashes import hash256

class SignatureChecker:
    """Signature verification for script execution."""

    def __init__(self, tx: any, input_index: int, amount: int, flags: int):
        self.tx = tx
        self.input_index = input_index
        self.amount = amount
        self.flags = flags

    def check_signature(self, signature: bytes, pubkey: bytes) -> bool:
        if len(signature) == 0 or len(pubkey) == 0:
            return False
        try:
            pubkey_obj = PublicKey.from_bytes(pubkey)
            sighash = self._get_sighash(signature)
            return verify_signature(pubkey_obj, sighash, signature[:-1])
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

    def _get_sighash(self, signature: bytes) -> bytes:
        if len(signature) == 0:
            return b'\x00' * 32
        sighash_type = signature[-1]
        if sighash_type == 0x01:
            return self._hash_all()
        if sighash_type == 0x02:
            return self._hash_none()
        if sighash_type == 0x03:
            return self._hash_single()
        return self._hash_all()

    def _hash_all(self) -> bytes:
        tx_copy = self.tx.copy()
        for txin in tx_copy.vin:
            txin.script_sig = b''
        tx_copy.vin[self.input_index].script_sig = b'\x00' * 32
        return hash256(tx_copy.serialize())

    def _hash_none(self) -> bytes:
        return hash256(b'\x00' * 32)

    def _hash_single(self) -> bytes:
        return hash256(b'\x00' * 32)
