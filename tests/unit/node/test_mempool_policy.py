"""Unit tests for mempool standardness policy."""

import unittest

from node.mempool.policy import MempoolPolicy
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.script.opcodes import Opcode
from shared.script.witness import Witness


class TestMempoolPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = MempoolPolicy()

    def test_push_only_rejects_reserved_opcode(self) -> None:
        self.assertFalse(self.policy._is_push_only(bytes([Opcode.OP_RESERVED])))

    def test_minimal_push_rejects_nonminimal_small_int_encoding(self) -> None:
        # Pushing 0x01 via data push must use OP_1.
        script = bytes([1, 1])
        self.assertFalse(self.policy._is_minimal_push(script))

    def test_minimal_push_accepts_small_integer_opcode(self) -> None:
        script = bytes([Opcode.OP_1])
        self.assertTrue(self.policy._is_push_only(script))
        self.assertTrue(self.policy._is_minimal_push(script))

    def test_minimal_push_accepts_op_0(self) -> None:
        script = bytes([Opcode.OP_0])
        self.assertTrue(self.policy._is_push_only(script))
        self.assertTrue(self.policy._is_minimal_push(script))

    def test_witness_with_scriptsig_is_nonstandard(self) -> None:
        tx = Transaction(version=2)
        tx.vin = [TxIn(script_sig=b"\x01\x01", witness=Witness([b"\x01" * 64]))]
        tx.vout = [TxOut(1000, b"\x51")]
        self.assertFalse(self.policy.is_standard(tx))

    def test_taproot_keypath_witness_length_check(self) -> None:
        tx = Transaction(version=2)
        txin = TxIn(script_sig=b"", witness=Witness([b"\x01" * 63]))
        tx.vin = [txin]
        tx.vout = [TxOut(1000, b"\x51")]
        self.assertFalse(self.policy._is_standard_input(txin.script_sig, txin))

        txin_ok = TxIn(script_sig=b"", witness=Witness([b"\x01" * 64]))
        tx.vin = [txin_ok]
        self.assertTrue(self.policy._is_standard_input(txin_ok.script_sig, txin_ok))

    def test_taproot_output_is_standard(self) -> None:
        taproot_spk = bytes([Opcode.OP_1, 0x20]) + (b"\x11" * 32)
        self.assertTrue(self.policy._is_standard_output(taproot_spk))

    def test_taproot_scriptpath_witness_is_standard(self) -> None:
        tx = Transaction(version=2)
        script = b"\x51" * 200  # tapscript item can exceed 80-byte stack item limit
        control = b"\xc0" + (b"\x22" * 32)
        txin = TxIn(script_sig=b"", witness=Witness([b"\x01" * 64, script, control]))
        tx.vin = [txin]
        tx.vout = [TxOut(1000, b"\x51")]
        self.assertTrue(self.policy._is_standard_input(txin.script_sig, txin))

    def test_dust_output_is_nonstandard(self) -> None:
        tx = Transaction(version=2)
        tx.vin = [TxIn(script_sig=b"")]
        # Standard P2PKH script with dust amount.
        spk = b"\x76\xa9\x14" + (b"\x11" * 20) + b"\x88\xac"
        tx.vout = [TxOut(545, spk)]
        self.assertFalse(self.policy.is_standard(tx))


if __name__ == "__main__":
    unittest.main()
