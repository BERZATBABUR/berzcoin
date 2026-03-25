"""Script execution engine for BerzCoin."""

from typing import List, Optional, Tuple
from .opcodes import Opcode
from .stack import Stack
from .sigchecks import SignatureChecker
from .script_flags import ScriptFlags
from ..utils.errors import ScriptError

class ScriptEngine:
    """Bitcoin script execution engine."""
    
    def __init__(self, flags: ScriptFlags = ScriptFlags.STANDARD_VERIFY_FLAGS):
        """Initialize script engine.
        
        Args:
            flags: Script verification flags
        """
        self.flags = flags
        self.stack = Stack()
        self.altstack = Stack()
        self.if_stack: List[bool] = []
        self.script: bytes = b''
        self.pc: int = 0
        self.sigchecker: Optional[SignatureChecker] = None
    
    def execute(self, script: bytes, tx: any, input_index: int, amount: int = 0) -> bool:
        """Execute script.
        
        Args:
            script: Script bytes to execute
            tx: Transaction containing this input
            input_index: Index of input being executed
            amount: Amount being spent (for SegWit)
        
        Returns:
            True if script executed successfully
        
        Raises:
            ScriptError: If script execution fails
        """
        self.script = script
        self.pc = 0
        self.stack.clear()
        self.if_stack.clear()
        
        # Initialize signature checker
        self.sigchecker = SignatureChecker(tx, input_index, amount, self.flags)
        
        while self.pc < len(self.script):
            opcode = self._read_opcode()
            
            # Handle push operations
            if opcode.is_push():
                self._handle_push(opcode)
                continue
            
            # Check disabled opcodes
            if opcode.is_disabled() and self.flags.is_enabled(ScriptFlags.VERIFY_DISCOURAGE_UPGRADABLE_NOPS):
                raise ScriptError(f"Disabled opcode: {opcode.name}")
            
            # Execute opcode
            if not self._execute_opcode(opcode):
                return False
        
        # Script must return true
        result = self._cast_to_bool(self.stack.pop() if self.stack.size() > 0 else b'')
        return result
    
    def _read_opcode(self) -> Opcode:
        """Read next opcode from script.
        
        Returns:
            Opcode value
        """
        opcode = self.script[self.pc]
        self.pc += 1
        return Opcode(opcode)
    
    def _handle_push(self, opcode: Opcode) -> None:
        """Handle push operation.
        
        Args:
            opcode: Push opcode
        """
        if opcode == Opcode.OP_PUSHDATA1:
            length = self.script[self.pc]
            self.pc += 1
            data = self.script[self.pc:self.pc + length]
            self.pc += length
            self.stack.push(data)
        
        elif opcode == Opcode.OP_PUSHDATA2:
            length = int.from_bytes(self.script[self.pc:self.pc+2], 'little')
            self.pc += 2
            data = self.script[self.pc:self.pc + length]
            self.pc += length
            self.stack.push(data)
        
        elif opcode == Opcode.OP_PUSHDATA4:
            length = int.from_bytes(self.script[self.pc:self.pc+4], 'little')
            self.pc += 4
            data = self.script[self.pc:self.pc + length]
            self.pc += length
            self.stack.push(data)
        
        else:
            # Small integer push (OP_1NEGATE or OP_1 to OP_16)
            if opcode == Opcode.OP_1NEGATE:
                data = bytes([0x81])  # -1 encoded
            elif opcode >= Opcode.OP_1 and opcode <= Opcode.OP_16:
                value = opcode - Opcode.OP_1 + 1
                data = bytes([value])
            else:
                # Direct push (OP_0)
                data = b''
            
            self.stack.push(data)
    
    def _execute_opcode(self, opcode: Opcode) -> bool:
        """Execute an opcode.
        
        Args:
            opcode: Opcode to execute
        
        Returns:
            True if execution should continue
        """
        # Control flow
        if opcode == Opcode.OP_NOP:
            return True
        
        elif opcode == Opcode.OP_IF:
            condition = self._cast_to_bool(self.stack.pop())
            self.if_stack.append(condition)
            if not condition:
                self._skip_to_endif()
        
        elif opcode == Opcode.OP_NOTIF:
            condition = not self._cast_to_bool(self.stack.pop())
            self.if_stack.append(condition)
            if not condition:
                self._skip_to_endif()
        
        elif opcode == Opcode.OP_ELSE:
            if not self.if_stack:
                raise ScriptError("OP_ELSE without OP_IF")
            self.if_stack[-1] = not self.if_stack[-1]
            if not self.if_stack[-1]:
                self._skip_to_endif()
        
        elif opcode == Opcode.OP_ENDIF:
            if not self.if_stack:
                raise ScriptError("OP_ENDIF without OP_IF")
            self.if_stack.pop()
        
        elif opcode == Opcode.OP_VERIFY:
            if not self._cast_to_bool(self.stack.pop()):
                raise ScriptError("OP_VERIFY failed")
        
        elif opcode == Opcode.OP_RETURN:
            raise ScriptError("OP_RETURN encountered")
        
        # Stack operations
        elif opcode == Opcode.OP_TOALTSTACK:
            if not self.stack.to_altstack():
                raise ScriptError("OP_TOALTSTACK failed")
        
        elif opcode == Opcode.OP_FROMALTSTACK:
            if not self.stack.from_altstack():
                raise ScriptError("OP_FROMALTSTACK failed")
        
        elif opcode == Opcode.OP_2DROP:
            if not self.stack.drop2():
                raise ScriptError("OP_2DROP failed")
        
        elif opcode == Opcode.OP_2DUP:
            if not self.stack.dup2():
                raise ScriptError("OP_2DUP failed")
        
        elif opcode == Opcode.OP_3DUP:
            if self.stack.size() < 3:
                raise ScriptError("OP_3DUP: insufficient stack")
            a = self.stack.peek(-3)
            b = self.stack.peek(-2)
            c = self.stack.peek(-1)
            if a is None or b is None or c is None:
                raise ScriptError("OP_3DUP failed")
            self.stack.push(a)
            self.stack.push(b)
            self.stack.push(c)
        
        elif opcode == Opcode.OP_2OVER:
            if self.stack.size() < 4:
                raise ScriptError("OP_2OVER: insufficient stack")
            a = self.stack.peek(-4)
            b = self.stack.peek(-3)
            if a is None or b is None:
                raise ScriptError("OP_2OVER failed")
            self.stack.push(a)
            self.stack.push(b)
        
        elif opcode == Opcode.OP_2ROT:
            if self.stack.size() < 6:
                raise ScriptError("OP_2ROT: insufficient stack")
            # Complex rotation - simplified
            items = []
            for _ in range(6):
                items.append(self.stack.pop())
            for item in items[:2]:
                self.stack.push(item)
            for item in items[2:]:
                self.stack.push(item)
        
        elif opcode == Opcode.OP_2SWAP:
            if self.stack.size() < 4:
                raise ScriptError("OP_2SWAP: insufficient stack")
            a = self.stack.pop()
            b = self.stack.pop()
            c = self.stack.pop()
            d = self.stack.pop()
            self.stack.push(a)
            self.stack.push(b)
            self.stack.push(c)
            self.stack.push(d)
        
        elif opcode == Opcode.OP_IFDUP:
            item = self.stack.top()
            if item and self._cast_to_bool(item):
                self.stack.dup()
        
        elif opcode == Opcode.OP_DEPTH:
            depth = self.stack.depth()
            self.stack.push(bytes([depth]))
        
        elif opcode == Opcode.OP_DROP:
            if not self.stack.drop():
                raise ScriptError("OP_DROP failed")
        
        elif opcode == Opcode.OP_DUP:
            if not self.stack.dup():
                raise ScriptError("OP_DUP failed")
        
        elif opcode == Opcode.OP_NIP:
            if not self.stack.nip():
                raise ScriptError("OP_NIP failed")
        
        elif opcode == Opcode.OP_OVER:
            if not self.stack.over():
                raise ScriptError("OP_OVER failed")
        
        elif opcode == Opcode.OP_PICK:
            n = self._cast_to_int(self.stack.pop())
            if n < 0 or n >= self.stack.size():
                raise ScriptError("OP_PICK: invalid depth")
            if not self.stack.pick(n):
                raise ScriptError("OP_PICK failed")
        
        elif opcode == Opcode.OP_ROLL:
            n = self._cast_to_int(self.stack.pop())
            if n < 0 or n >= self.stack.size():
                raise ScriptError("OP_ROLL: invalid depth")
            if not self.stack.roll(n):
                raise ScriptError("OP_ROLL failed")
        
        elif opcode == Opcode.OP_ROT:
            if not self.stack.rot():
                raise ScriptError("OP_ROT failed")
        
        elif opcode == Opcode.OP_SWAP:
            if not self.stack.swap():
                raise ScriptError("OP_SWAP failed")
        
        elif opcode == Opcode.OP_TUCK:
            if not self.stack.tuck():
                raise ScriptError("OP_TUCK failed")
        
        elif opcode == Opcode.OP_SIZE:
            item = self.stack.top()
            if item is None:
                raise ScriptError("OP_SIZE: empty stack")
            size = len(item)
            self.stack.push(bytes([size]))
        
        # Crypto operations
        elif opcode == Opcode.OP_CHECKSIG:
            return self._op_checksig()
        
        elif opcode == Opcode.OP_CHECKSIGVERIFY:
            if not self._op_checksig():
                raise ScriptError("OP_CHECKSIGVERIFY failed")
        
        elif opcode == Opcode.OP_CHECKMULTISIG:
            return self._op_checkmultisig()
        
        elif opcode == Opcode.OP_CHECKMULTISIGVERIFY:
            if not self._op_checkmultisig():
                raise ScriptError("OP_CHECKMULTISIGVERIFY failed")
        
        elif opcode == Opcode.OP_EQUAL:
            a = self.stack.pop()
            b = self.stack.pop()
            result = a == b
            self.stack.push(b'\x01' if result else b'')
        
        elif opcode == Opcode.OP_EQUALVERIFY:
            a = self.stack.pop()
            b = self.stack.pop()
            if a != b:
                raise ScriptError("OP_EQUALVERIFY failed")
        
        # Hash operations
        elif opcode == Opcode.OP_HASH160:
            from ..core.hashes import hash160
            data = self.stack.pop()
            if data is None:
                raise ScriptError("OP_HASH160: empty stack")
            self.stack.push(hash160(data))
        
        elif opcode == Opcode.OP_SHA256:
            from ..core.hashes import sha256
            data = self.stack.pop()
            if data is None:
                raise ScriptError("OP_SHA256: empty stack")
            self.stack.push(sha256(data))
        
        else:
            # Unknown opcode
            if self.flags.is_enabled(ScriptFlags.VERIFY_DISCOURAGE_UPGRADABLE_NOPS):
                raise ScriptError(f"Unknown opcode: {opcode}")
        
        return True
    
    def _op_checksig(self) -> bool:
        """Execute OP_CHECKSIG with signature verification.

        Stack (typical scriptSig): <sig> <pubkey>
        Note: We keep the sighash algorithm consistent with `SignatureChecker`
        (which is simplified in this repo).
        """
        pubkey = self.stack.pop()
        sig = self.stack.pop()

        if pubkey is None or sig is None:
            raise ScriptError("OP_CHECKSIG: insufficient stack")

        if not self.sigchecker:
            raise ScriptError("Signature checker not initialized")

        # Remove sighash flag from signature (default SIGHASH_ALL if missing).
        sighash_flag = sig[-1] if len(sig) > 0 else 0x01
        sig_wo_flag = sig[:-1] if len(sig) > 0 else sig

        # Calculate sighash for verification.
        sighash = self._calculate_sighash(sighash_flag, sig_with_flag=sig)

        try:
            from ..crypto.keys import PublicKey
            from ..crypto.signatures import verify_signature

            pubkey_obj = PublicKey.from_bytes(pubkey)
            valid = verify_signature(pubkey_obj, sighash, sig_wo_flag)
        except Exception:
            valid = False

        self.stack.push(b"\x01" if valid else b"")
        return True

    def _calculate_sighash(self, sighash_type: int, sig_with_flag: Optional[bytes] = None) -> bytes:
        """Calculate signature hash for the current tx/input.

        This repo's sighash is intentionally simplified; we reuse the existing
        `SignatureChecker` implementation for consistency.
        """
        if not self.sigchecker:
            raise ScriptError("Signature checker not initialized")

        # If caller provided the original signature bytes, let SignatureChecker parse
        # its sighash type (matches existing behavior).
        if sig_with_flag is not None:
            return self.sigchecker._get_sighash(sig_with_flag)  # type: ignore[attr-defined]

        # Otherwise, synthesize a minimal signature ending with the requested flag.
        return self.sigchecker._get_sighash(bytes([sighash_type]))  # type: ignore[attr-defined]
    
    def _op_checkmultisig(self) -> bool:
        """Execute OP_CHECKMULTISIG.
        
        Returns:
            True if signatures are valid
        """
        # Get number of pubkeys
        n_keys = self._cast_to_int(self.stack.pop())
        if n_keys < 0 or n_keys > 20:
            raise ScriptError("OP_CHECKMULTISIG: invalid pubkey count")
        
        # Get pubkeys
        pubkeys = []
        for _ in range(n_keys):
            pubkey = self.stack.pop()
            if pubkey is None:
                raise ScriptError("OP_CHECKMULTISIG: missing pubkey")
            pubkeys.append(pubkey)
        
        # Get number of signatures
        n_sigs = self._cast_to_int(self.stack.pop())
        if n_sigs < 0 or n_sigs > n_keys:
            raise ScriptError("OP_CHECKMULTISIG: invalid signature count")
        
        # Get signatures
        signatures = []
        for _ in range(n_sigs):
            sig = self.stack.pop()
            if sig is None:
                raise ScriptError("OP_CHECKMULTISIG: missing signature")
            signatures.append(sig)
        
        # Pop extra element (bug compatibility)
        self.stack.pop()
        
        if not self.sigchecker:
            raise ScriptError("Signature checker not initialized")
        
        # Verify signatures
        valid = self.sigchecker.check_multisig(signatures, pubkeys)
        self.stack.push(b'\x01' if valid else b'')
        return True
    
    def _skip_to_endif(self) -> None:
        """Skip script until matching OP_ENDIF."""
        depth = 1
        while self.pc < len(self.script):
            opcode = self.script[self.pc]
            self.pc += 1
            
            if opcode == Opcode.OP_IF or opcode == Opcode.OP_NOTIF:
                depth += 1
            elif opcode == Opcode.OP_ELSE:
                if depth == 1:
                    return
            elif opcode == Opcode.OP_ENDIF:
                depth -= 1
                if depth == 0:
                    return
    
    @staticmethod
    def _cast_to_bool(data: Optional[bytes]) -> bool:
        """Cast bytes to boolean."""
        if data is None or len(data) == 0:
            return False
        
        # Check if all bytes are zero
        for byte in data:
            if byte != 0:
                return True
        return False
    
    @staticmethod
    def _cast_to_int(data: Optional[bytes]) -> int:
        """Cast bytes to integer."""
        if data is None or len(data) == 0:
            return 0
        
        # Bitcoin uses little-endian signed integers
        return int.from_bytes(data, 'little', signed=True)
