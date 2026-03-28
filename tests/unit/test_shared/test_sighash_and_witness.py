"""Regression tests for sighash matrix and witness verification."""

import unittest

from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.secp256k1 import N as SECP256K1_ORDER
from shared.crypto.signatures import sign_message_hash, sign_schnorr_message_hash
from shared.crypto.secp256k1 import taproot_tweak_pubkey
from shared.script.sigchecks import (
    SIGHASH_ALL,
    SIGHASH_ANYONECANPAY,
    SIGHASH_SINGLE,
    calculate_legacy_sighash,
    calculate_segwit_v0_sighash,
    calculate_tapleaf_hash,
    calculate_taproot_keypath_sighash,
    calculate_taproot_scriptpath_sighash,
)
from shared.script.opcodes import Opcode
from shared.script.verify import verify_input_script


class TestSighashAndWitness(unittest.TestCase):
    @staticmethod
    def _der_to_rs(signature_der: bytes) -> tuple[int, int]:
        r_len = signature_der[3]
        r = int.from_bytes(signature_der[4:4 + r_len], "big")
        s_len_pos = 4 + r_len + 1
        s_len = signature_der[s_len_pos]
        s_start = s_len_pos + 1
        s = int.from_bytes(signature_der[s_start:s_start + s_len], "big")
        return r, s

    @staticmethod
    def _encode_der(r: int, s: int) -> bytes:
        def _enc(num: int) -> bytes:
            b = num.to_bytes((num.bit_length() + 7) // 8 or 1, "big")
            if b[0] & 0x80:
                b = b"\x00" + b
            return b

        rb = _enc(r)
        sb = _enc(s)
        return b"\x30" + bytes([len(rb) + len(sb) + 4]) + b"\x02" + bytes([len(rb)]) + rb + b"\x02" + bytes([len(sb)]) + sb

    def test_legacy_anyonecanpay_changes_digest(self) -> None:
        tx = Transaction(version=2)
        tx.vin = [
            TxIn(prev_tx_hash=bytes.fromhex("11" * 32), prev_tx_index=0, sequence=1),
            TxIn(prev_tx_hash=bytes.fromhex("22" * 32), prev_tx_index=1, sequence=2),
        ]
        tx.vout = [
            TxOut(1000, b"\x51"),
            TxOut(2000, b"\x51"),
        ]

        script_code = b"\x76\xa9\x14" + (b"\x01" * 20) + b"\x88\xac"
        normal = calculate_legacy_sighash(tx, 0, SIGHASH_ALL, script_code)
        anyone = calculate_legacy_sighash(tx, 0, SIGHASH_ALL | SIGHASH_ANYONECANPAY, script_code)
        self.assertNotEqual(normal, anyone)

    def test_legacy_single_out_of_range_magic(self) -> None:
        tx = Transaction(version=1)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("33" * 32), prev_tx_index=0)]
        tx.vout = []
        digest = calculate_legacy_sighash(tx, 0, SIGHASH_SINGLE, b"\x51")
        self.assertEqual(digest, b"\x01" + (b"\x00" * 31))

    def test_native_p2wpkh_verification(self) -> None:
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        pkh = hash160(pub)

        tx = Transaction(version=2)
        tx.vin = [
            TxIn(
                prev_tx_hash=bytes.fromhex("44" * 32),
                prev_tx_index=0,
                script_sig=b"",
                sequence=0xFFFFFFFD,
            )
        ]
        tx.vout = [TxOut(45_000, b"\x51")]

        amount = 50_000
        script_pubkey = b"\x00\x14" + pkh
        script_code = b"\x76\xa9\x14" + pkh + b"\x88\xac"
        sighash = calculate_segwit_v0_sighash(tx, 0, amount, script_code, SIGHASH_ALL)
        sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])

        tx.vin[0].witness.push(sig)
        tx.vin[0].witness.push(pub)

        self.assertTrue(verify_input_script(tx, 0, b"", script_pubkey, amount))

    def test_native_p2wpkh_rejects_uncompressed_pubkey(self) -> None:
        key = PrivateKey()
        compressed_pub = key.public_key().to_bytes()
        uncompressed_pub = key.public_key().to_bytes(compressed=False)
        pkh = hash160(compressed_pub)

        tx = Transaction(version=2)
        tx.vin = [
            TxIn(
                prev_tx_hash=bytes.fromhex("54" * 32),
                prev_tx_index=0,
                script_sig=b"",
                sequence=0xFFFFFFFD,
            )
        ]
        tx.vout = [TxOut(45_000, b"\x51")]

        amount = 50_000
        script_pubkey = b"\x00\x14" + pkh
        script_code = b"\x76\xa9\x14" + pkh + b"\x88\xac"
        sighash = calculate_segwit_v0_sighash(tx, 0, amount, script_code, SIGHASH_ALL)
        sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])

        tx.vin[0].witness.push(sig)
        tx.vin[0].witness.push(uncompressed_pub)

        self.assertFalse(verify_input_script(tx, 0, b"", script_pubkey, amount))

    def test_non_der_signature_rejected(self) -> None:
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        pkh = hash160(pub)
        script_pubkey = b"\x76\xa9\x14" + pkh + b"\x88\xac"

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("77" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(10_000, b"\x51")]
        bad_sig = b"\x01\x02\x03\x01"  # not DER, but has a sighash byte
        tx.vin[0].script_sig = bytes([len(bad_sig)]) + bad_sig + bytes([len(pub)]) + pub
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, script_pubkey, 10_000))

    def test_high_s_signature_rejected(self) -> None:
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        pkh = hash160(pub)
        script_pubkey = b"\x76\xa9\x14" + pkh + b"\x88\xac"

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("88" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(9_000, b"\x51")]
        sighash = calculate_legacy_sighash(tx, 0, SIGHASH_ALL, script_pubkey)
        der = sign_message_hash(key, sighash)
        r, s = self._der_to_rs(der)
        high_s = SECP256K1_ORDER - s
        bad_der = self._encode_der(r, high_s)
        bad_sig = bad_der + bytes([SIGHASH_ALL])
        tx.vin[0].script_sig = bytes([len(bad_sig)]) + bad_sig + bytes([len(pub)]) + pub
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, script_pubkey, 10_000))

    def test_taproot_keypath_verification(self) -> None:
        key = PrivateKey()
        xonly = key.public_key().to_bytes()[1:33]
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("99" * 32), prev_tx_index=0, script_sig=b"")]
        tx.vout = [TxOut(8_000, b"\x51")]
        script_pubkey = b"\x51\x20" + xonly  # v1 witness program
        sighash = calculate_taproot_keypath_sighash(tx, 0, 8_000, script_pubkey, 0x00)
        sig = sign_schnorr_message_hash(key, sighash)
        tx.vin[0].witness.push(sig)
        self.assertTrue(verify_input_script(tx, 0, b"", script_pubkey, 8_000))

    def test_taproot_keypath_wrong_key_rejected(self) -> None:
        key = PrivateKey()
        wrong = PrivateKey()
        xonly = wrong.public_key().to_bytes()[1:33]
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("aa" * 32), prev_tx_index=0, script_sig=b"")]
        tx.vout = [TxOut(7_500, b"\x51")]
        script_pubkey = b"\x51\x20" + xonly
        sighash = calculate_taproot_keypath_sighash(tx, 0, 7_500, script_pubkey, 0x00)
        sig = sign_schnorr_message_hash(key, sighash)
        tx.vin[0].witness.push(sig)
        self.assertFalse(verify_input_script(tx, 0, b"", script_pubkey, 7_500))

    def test_taproot_keypath_with_annex(self) -> None:
        key = PrivateKey()
        xonly = key.public_key().to_bytes()[1:33]
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("af" * 32), prev_tx_index=0, script_sig=b"")]
        tx.vout = [TxOut(9_000, b"\x51")]
        script_pubkey = b"\x51\x20" + xonly
        annex = b"\x50\x01\x02\x03"
        sighash = calculate_taproot_keypath_sighash(tx, 0, 9_000, script_pubkey, 0x00, annex=annex)
        sig = sign_schnorr_message_hash(key, sighash)
        tx.vin[0].witness.push(sig)
        tx.vin[0].witness.push(annex)
        self.assertTrue(verify_input_script(tx, 0, b"", script_pubkey, 9_000))

    def test_taproot_scriptpath_single_sig_verification(self) -> None:
        internal_key = PrivateKey()
        script_key = PrivateKey()
        script_pub = script_key.public_key().to_bytes()[1:33]
        script = bytes([32]) + script_pub + b"\xac"  # <xonly pubkey> OP_CHECKSIG
        tapleaf = calculate_tapleaf_hash(script, leaf_version=0xC0)

        tweak = taproot_tweak_pubkey(internal_key.public_key().to_bytes()[1:33], tapleaf)
        self.assertIsNotNone(tweak)
        output_xonly, parity = tweak  # type: ignore[misc]
        script_pubkey = b"\x51\x20" + output_xonly
        control_block = bytes([(0xC0 | parity)]) + internal_key.public_key().to_bytes()[1:33]

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("ab" * 32), prev_tx_index=0, script_sig=b"")]
        tx.vout = [TxOut(12_000, b"\x51")]
        sighash = calculate_taproot_scriptpath_sighash(tx, 0, 12_000, script_pubkey, tapleaf, 0x00)
        sig = sign_schnorr_message_hash(script_key, sighash)
        tx.vin[0].witness.push(sig)
        tx.vin[0].witness.push(script)
        tx.vin[0].witness.push(control_block)
        self.assertTrue(verify_input_script(tx, 0, b"", script_pubkey, 12_000))

    def test_taproot_scriptpath_bad_control_block_rejected(self) -> None:
        internal_key = PrivateKey()
        script_key = PrivateKey()
        script_pub = script_key.public_key().to_bytes()[1:33]
        script = bytes([32]) + script_pub + b"\xac"
        tapleaf = calculate_tapleaf_hash(script, leaf_version=0xC0)
        tweak = taproot_tweak_pubkey(internal_key.public_key().to_bytes()[1:33], tapleaf)
        self.assertIsNotNone(tweak)
        output_xonly, parity = tweak  # type: ignore[misc]
        script_pubkey = b"\x51\x20" + output_xonly
        control_block = bytes([(0xC0 | parity)]) + (b"\x00" * 32)  # wrong internal key

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("cd" * 32), prev_tx_index=0, script_sig=b"")]
        tx.vout = [TxOut(11_000, b"\x51")]
        sighash = calculate_taproot_scriptpath_sighash(tx, 0, 11_000, script_pubkey, tapleaf, 0x00)
        sig = sign_schnorr_message_hash(script_key, sighash)
        tx.vin[0].witness.push(sig)
        tx.vin[0].witness.push(script)
        tx.vin[0].witness.push(control_block)
        self.assertFalse(verify_input_script(tx, 0, b"", script_pubkey, 11_000))

    def test_taproot_scriptpath_checksigverify_if(self) -> None:
        internal_key = PrivateKey()
        script_key = PrivateKey()
        script_pub = script_key.public_key().to_bytes()[1:33]
        script = bytes([Opcode.OP_IF, 32]) + script_pub + bytes(
            [Opcode.OP_CHECKSIGVERIFY, Opcode.OP_1, Opcode.OP_ELSE, Opcode.OP_0, Opcode.OP_ENDIF]
        )
        tapleaf = calculate_tapleaf_hash(script, leaf_version=0xC0)
        tweak = taproot_tweak_pubkey(internal_key.public_key().to_bytes()[1:33], tapleaf)
        self.assertIsNotNone(tweak)
        output_xonly, parity = tweak  # type: ignore[misc]
        script_pubkey = b"\x51\x20" + output_xonly
        control_block = bytes([(0xC0 | parity)]) + internal_key.public_key().to_bytes()[1:33]

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("bc" * 32), prev_tx_index=0, script_sig=b"")]
        tx.vout = [TxOut(13_000, b"\x51")]
        sighash = calculate_taproot_scriptpath_sighash(tx, 0, 13_000, script_pubkey, tapleaf, 0x00)
        sig = sign_schnorr_message_hash(script_key, sighash)
        tx.vin[0].witness.push(sig)
        tx.vin[0].witness.push(b"\x01")
        tx.vin[0].witness.push(script)
        tx.vin[0].witness.push(control_block)
        self.assertTrue(verify_input_script(tx, 0, b"", script_pubkey, 13_000))

    def test_taproot_scriptpath_minimalif_rejects_non_minimal_true(self) -> None:
        internal_key = PrivateKey()
        script = bytes([Opcode.OP_IF, Opcode.OP_1, Opcode.OP_ENDIF])
        tapleaf = calculate_tapleaf_hash(script, leaf_version=0xC0)
        tweak = taproot_tweak_pubkey(internal_key.public_key().to_bytes()[1:33], tapleaf)
        self.assertIsNotNone(tweak)
        output_xonly, parity = tweak  # type: ignore[misc]
        script_pubkey = b"\x51\x20" + output_xonly
        control_block = bytes([(0xC0 | parity)]) + internal_key.public_key().to_bytes()[1:33]

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("de" * 32), prev_tx_index=0, script_sig=b"")]
        tx.vout = [TxOut(6_000, b"\x51")]
        tx.vin[0].witness.push(b"\x02")
        tx.vin[0].witness.push(script)
        tx.vin[0].witness.push(control_block)
        self.assertFalse(verify_input_script(tx, 0, b"", script_pubkey, 6_000))

    def test_taproot_sighash_modes_change_digest(self) -> None:
        key = PrivateKey()
        xonly = key.public_key().to_bytes()[1:33]
        tx = Transaction(version=2)
        tx.vin = [
            TxIn(prev_tx_hash=bytes.fromhex("01" * 32), prev_tx_index=0, sequence=1),
            TxIn(prev_tx_hash=bytes.fromhex("02" * 32), prev_tx_index=1, sequence=2),
        ]
        tx.vout = [TxOut(3000, b"\x51"), TxOut(4000, b"\x51")]
        spk = b"\x51\x20" + xonly
        h_all = calculate_taproot_keypath_sighash(tx, 0, 50_000, spk, 0x00)
        h_none = calculate_taproot_keypath_sighash(tx, 0, 50_000, spk, 0x02)
        h_single = calculate_taproot_keypath_sighash(tx, 0, 50_000, spk, 0x03)
        h_acp = calculate_taproot_keypath_sighash(tx, 0, 50_000, spk, 0x81)
        self.assertNotEqual(h_all, h_none)
        self.assertNotEqual(h_all, h_single)
        self.assertNotEqual(h_all, h_acp)

    def test_taproot_invalid_sighash_type_rejected(self) -> None:
        key = PrivateKey()
        xonly = key.public_key().to_bytes()[1:33]
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("ef" * 32), prev_tx_index=0, script_sig=b"")]
        tx.vout = [TxOut(5_000, b"\x51")]
        script_pubkey = b"\x51\x20" + xonly
        sighash = calculate_taproot_keypath_sighash(tx, 0, 5_000, script_pubkey, 0x00)
        sig = sign_schnorr_message_hash(key, sighash) + b"\x7f"  # unsupported hash type
        tx.vin[0].witness.push(sig)
        self.assertFalse(verify_input_script(tx, 0, b"", script_pubkey, 5_000))


if __name__ == "__main__":
    unittest.main()
