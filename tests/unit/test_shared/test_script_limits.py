"""Unit tests for script and tapscript consensus limits."""

import unittest

from shared.core.transaction import Transaction, TxIn, TxOut
from shared.script.engine import ScriptEngine
from shared.script.opcodes import Opcode
from shared.script.script_flags import ScriptFlags
from shared.script.tapscript import execute_tapscript
from shared.script.sigchecks import SignatureChecker
from shared.utils.errors import ScriptError


class TestScriptLimits(unittest.TestCase):
    def test_engine_rejects_oversized_script(self) -> None:
        eng = ScriptEngine(flags=ScriptFlags.STANDARD_VERIFY_FLAGS)
        tx = Transaction(version=1, inputs=[TxIn()], outputs=[TxOut(1, b"\x51")])
        script = b"\x51" * (ScriptEngine.MAX_SCRIPT_SIZE + 1)
        with self.assertRaises(ScriptError):
            eng.execute(script, tx, 0, 0)

    def test_engine_rejects_too_many_opcodes(self) -> None:
        eng = ScriptEngine(flags=ScriptFlags.STANDARD_VERIFY_FLAGS)
        tx = Transaction(version=1, inputs=[TxIn()], outputs=[TxOut(1, b"\x51")])
        script = b"\x61" * (ScriptEngine.MAX_OPS_PER_SCRIPT + 1)  # OP_NOP repeated
        with self.assertRaises(ScriptError):
            eng.execute(script, tx, 0, 0)

    def test_engine_enforces_cleanstack(self) -> None:
        eng = ScriptEngine(flags=ScriptFlags.STANDARD_VERIFY_FLAGS)
        tx = Transaction(version=1, inputs=[TxIn()], outputs=[TxOut(1, b"\x51")])
        # Leaves two truthy items on stack; CLEANSTACK must reject.
        script = bytes([Opcode.OP_1, Opcode.OP_1])
        with self.assertRaises(ScriptError):
            eng.execute(script, tx, 0, 0)

    def test_tapscript_stack_limit(self) -> None:
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("11" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(1, b"\x51")]
        checker = SignatureChecker(tx, 0, 1, ScriptFlags.STANDARD_VERIFY_FLAGS)
        stack = [b"\x01"] * 1001
        self.assertFalse(execute_tapscript(b"\x51", stack, checker))

    def test_negative_zero_is_false(self) -> None:
        self.assertFalse(ScriptEngine._cast_to_bool(b"\x80"))


if __name__ == "__main__":
    unittest.main()
