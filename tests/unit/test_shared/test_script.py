"""Unit tests for script components."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from shared.script.engine import ScriptEngine
from shared.script.opcodes import Opcode
from shared.script.script_flags import ScriptFlags
from shared.script.stack import Stack
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash
from shared.script.verify import verify_input_script
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash


class TestScript(unittest.TestCase):
    """Test script functionality."""

    def test_opcodes(self) -> None:
        self.assertEqual(Opcode.OP_0, 0x00)
        self.assertEqual(Opcode.OP_DUP, 0x76)
        self.assertEqual(Opcode.OP_HASH160, 0xA9)
        self.assertEqual(Opcode.OP_CHECKSIG, 0xAC)
        self.assertTrue(Opcode.OP_1.is_push())
        self.assertTrue(Opcode.OP_PUSHDATA1.is_push())
        self.assertFalse(Opcode.OP_DUP.is_push())

    def test_stack_operations(self) -> None:
        stack = Stack()
        stack.push(b"\x01")
        stack.push(b"\x02")
        stack.push(b"\x04")
        stack.swap()
        self.assertEqual(stack.peek(-1), b"\x02")
        self.assertEqual(stack.peek(-2), b"\x04")

    def test_script_engine_instantiation(self) -> None:
        eng = ScriptEngine(flags=ScriptFlags.VERIFY_NONE)
        self.assertIsNotNone(eng.stack)

    def test_p2pkh_script_validates_true(self) -> None:
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        pkh = hash160(pub)
        script_pubkey = b"\x76\xa9\x14" + pkh + b"\x88\xac"

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("11" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(10_000, b"\x51")]

        sighash = calculate_legacy_sighash(tx, 0, SIGHASH_ALL, script_pubkey)
        sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
        tx.vin[0].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub

        self.assertTrue(verify_input_script(tx, 0, tx.vin[0].script_sig, script_pubkey, 10_000))

    def test_p2pkh_script_rejects_wrong_pubkey(self) -> None:
        key = PrivateKey()
        wrong = PrivateKey()
        pub = key.public_key().to_bytes()
        wrong_pub = wrong.public_key().to_bytes()
        pkh = hash160(pub)
        script_pubkey = b"\x76\xa9\x14" + pkh + b"\x88\xac"

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("22" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(10_000, b"\x51")]

        sighash = calculate_legacy_sighash(tx, 0, SIGHASH_ALL, script_pubkey)
        sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
        tx.vin[0].script_sig = bytes([len(sig)]) + sig + bytes([len(wrong_pub)]) + wrong_pub

        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, script_pubkey, 10_000))

    def test_p2sh_redeem_script_true(self) -> None:
        redeem_script = bytes([Opcode.OP_1])
        script_pubkey = b"\xA9\x14" + hash160(redeem_script) + b"\x87"

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("33" * 32), prev_tx_index=1)]
        tx.vout = [TxOut(5_000, b"\x51")]

        tx.vin[0].script_sig = bytes([len(redeem_script)]) + redeem_script
        self.assertTrue(verify_input_script(tx, 0, tx.vin[0].script_sig, script_pubkey, 5_000))

    def test_p2sh_rejects_non_push_scriptsig(self) -> None:
        redeem_script = bytes([Opcode.OP_1])
        script_pubkey = b"\xA9\x14" + hash160(redeem_script) + b"\x87"

        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("44" * 32), prev_tx_index=2)]
        tx.vout = [TxOut(5_000, b"\x51")]

        tx.vin[0].script_sig = bytes([Opcode.OP_DUP])
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, script_pubkey, 5_000))


if __name__ == "__main__":
    unittest.main()
