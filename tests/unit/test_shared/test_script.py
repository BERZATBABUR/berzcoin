"""Unit tests for script components."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from shared.script.engine import ScriptEngine
from shared.script.opcodes import Opcode
from shared.script.script_flags import ScriptFlags
from shared.script.stack import Stack


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

    def test_p2pk_script(self) -> None:
        """Placeholder for future P2PK execution tests."""
        self.skipTest("requires full signing fixture")

    def test_p2pkh_script(self) -> None:
        """Placeholder for future P2PKH execution tests."""
        self.skipTest("requires full signing fixture")


if __name__ == "__main__":
    unittest.main()
