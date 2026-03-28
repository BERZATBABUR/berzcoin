"""Script execution engine for BerzCoin."""

from typing import List, Optional, Tuple
from .opcodes import Opcode
from .stack import Stack
from .sigchecks import SignatureChecker
from .script_flags import ScriptFlags
from ..utils.errors import ScriptError

class ScriptEngine:
    """Bitcoin script execution engine."""
    MAX_SCRIPT_SIZE = 10_000
    MAX_OPS_PER_SCRIPT = 201
    MAX_STACK_ITEMS = 1_000
    MAX_SCRIPT_ELEMENT_SIZE = 520
    
    def __init__(self, flags: ScriptFlags = ScriptFlags.STANDARD_VERIFY_FLAGS):
        """Initialize script engine.
        
        Args:
            flags: Script verification flags
        """
        self.flags = flags if isinstance(flags, ScriptFlags) else ScriptFlags(int(flags))
        self.stack = Stack()
        self.altstack = Stack()
        self.if_stack: List[bool] = []
        self.script: bytes = b''
        self.pc: int = 0
        self.sigchecker: Optional[SignatureChecker] = None
    
    def execute(
        self,
        script: bytes,
        tx: any,
        input_index: int,
        amount: int = 0,
        initial_stack: Optional[List[bytes]] = None,
    ) -> bool:
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
        if len(self.script) > self.MAX_SCRIPT_SIZE:
            raise ScriptError("Script size limit exceeded")
        self.pc = 0
        self.stack.clear()
        if initial_stack:
            for item in initial_stack:
                if len(item) > self.MAX_SCRIPT_ELEMENT_SIZE:
                    raise ScriptError("Initial stack element too large")
                self.stack.push(item)
        self.stack.clear_altstack()
        self.if_stack.clear()
        op_count = 0
        
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
            if not opcode.is_push():
                op_count += 1
                if op_count > self.MAX_OPS_PER_SCRIPT:
                    raise ScriptError("Too many opcodes")

            # Execute opcode
            if not self._execute_opcode(opcode):
                return False
            self._enforce_limits()
        
        if self.if_stack:
            raise ScriptError("Unbalanced conditional")

        if self.flags.is_enabled(ScriptFlags.VERIFY_CLEANSTACK) and self.stack.size() != 1:
            raise ScriptError("CLEANSTACK violation")

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
            if self.pc >= len(self.script):
                raise ScriptError("PUSHDATA1 missing length")
            length = self.script[self.pc]
            self.pc += 1
            if self.pc + length > len(self.script):
                raise ScriptError("PUSHDATA1 truncated")
            data = self.script[self.pc:self.pc + length]
            self.pc += length
            if len(data) > self.MAX_SCRIPT_ELEMENT_SIZE:
                raise ScriptError("Push element too large")
            self.stack.push(data)
        
        elif opcode == Opcode.OP_PUSHDATA2:
            if self.pc + 2 > len(self.script):
                raise ScriptError("PUSHDATA2 missing length")
            length = int.from_bytes(self.script[self.pc:self.pc+2], 'little')
            self.pc += 2
            if self.pc + length > len(self.script):
                raise ScriptError("PUSHDATA2 truncated")
            data = self.script[self.pc:self.pc + length]
            self.pc += length
            if len(data) > self.MAX_SCRIPT_ELEMENT_SIZE:
                raise ScriptError("Push element too large")
            self.stack.push(data)
        
        elif opcode == Opcode.OP_PUSHDATA4:
            if self.pc + 4 > len(self.script):
                raise ScriptError("PUSHDATA4 missing length")
            length = int.from_bytes(self.script[self.pc:self.pc+4], 'little')
            self.pc += 4
            if self.pc + length > len(self.script):
                raise ScriptError("PUSHDATA4 truncated")
            data = self.script[self.pc:self.pc + length]
            self.pc += length
            if len(data) > self.MAX_SCRIPT_ELEMENT_SIZE:
                raise ScriptError("Push element too large")
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

    def _enforce_limits(self) -> None:
        if self.stack.size() + self.stack.altstack_size() > self.MAX_STACK_ITEMS:
            raise ScriptError("Stack item limit exceeded")
        for item in self.stack.get_items():
            if len(item) > self.MAX_SCRIPT_ELEMENT_SIZE:
                raise ScriptError("Stack element too large")
    
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
            top = self.stack.pop()
            if top is None:
                raise ScriptError("OP_IF: empty stack")
            if (
                self.flags.is_enabled(ScriptFlags.VERIFY_MINIMALIF)
                and top not in (b"", b"\x01")
            ):
                raise ScriptError("MINIMALIF violation")
            condition = self._cast_to_bool(top)
            self.if_stack.append(condition)
            if not condition:
                self._skip_to_endif()
        
        elif opcode == Opcode.OP_NOTIF:
            top = self.stack.pop()
            if top is None:
                raise ScriptError("OP_NOTIF: empty stack")
            if (
                self.flags.is_enabled(ScriptFlags.VERIFY_MINIMALIF)
                and top not in (b"", b"\x01")
            ):
                raise ScriptError("MINIMALIF violation")
            condition = not self._cast_to_bool(top)
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
            top = self.stack.pop()
            if top is None:
                raise ScriptError("OP_VERIFY: empty stack")
            if not self._cast_to_bool(top):
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
            self.stack.push(self._encode_script_num(depth))
        
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
            top = self.stack.pop()
            if top is None:
                raise ScriptError("OP_PICK: empty stack")
            n = self._cast_to_int(top)
            if n < 0 or n >= self.stack.size():
                raise ScriptError("OP_PICK: invalid depth")
            if not self.stack.pick(n):
                raise ScriptError("OP_PICK failed")
        
        elif opcode == Opcode.OP_ROLL:
            top = self.stack.pop()
            if top is None:
                raise ScriptError("OP_ROLL: empty stack")
            n = self._cast_to_int(top)
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
            self.stack.push(self._encode_script_num(size))
        
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
            if self.stack.size() < 2:
                raise ScriptError("OP_EQUAL: insufficient stack")
            a = self.stack.pop()
            b = self.stack.pop()
            result = a == b
            self.stack.push(b'\x01' if result else b'')
        
        elif opcode == Opcode.OP_EQUALVERIFY:
            if self.stack.size() < 2:
                raise ScriptError("OP_EQUALVERIFY: insufficient stack")
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

        elif opcode == Opcode.OP_HASH256:
            from ..core.hashes import hash256
            data = self.stack.pop()
            if data is None:
                raise ScriptError("OP_HASH256: empty stack")
            self.stack.push(hash256(data))

        elif opcode == Opcode.OP_SHA1:
            import hashlib
            data = self.stack.pop()
            if data is None:
                raise ScriptError("OP_SHA1: empty stack")
            self.stack.push(hashlib.sha1(data).digest())

        elif opcode == Opcode.OP_RIPEMD160:
            import hashlib
            data = self.stack.pop()
            if data is None:
                raise ScriptError("OP_RIPEMD160: empty stack")
            self.stack.push(hashlib.new("ripemd160", data).digest())

        elif opcode == Opcode.OP_CHECKLOCKTIMEVERIFY:
            if not self.flags.is_enabled(ScriptFlags.VERIFY_CHECKLOCKTIMEVERIFY):
                return True
            top = self.stack.top()
            if top is None:
                raise ScriptError("OP_CHECKLOCKTIMEVERIFY: empty stack")
            required = self._cast_to_int(top)
            tx_locktime = int(getattr(self.sigchecker.tx, "locktime", 0)) if self.sigchecker else 0
            if required < 0 or tx_locktime < required:
                raise ScriptError("OP_CHECKLOCKTIMEVERIFY failed")

        elif opcode == Opcode.OP_CHECKSEQUENCEVERIFY:
            if not self.flags.is_enabled(ScriptFlags.VERIFY_CHECKSEQUENCEVERIFY):
                return True
            top = self.stack.top()
            if top is None:
                raise ScriptError("OP_CHECKSEQUENCEVERIFY: empty stack")
            required = self._cast_to_int(top)
            if required < 0:
                raise ScriptError("OP_CHECKSEQUENCEVERIFY failed")
            if not self.sigchecker:
                raise ScriptError("Signature checker not initialized")
            txin = self.sigchecker.tx.vin[self.sigchecker.input_index]
            if int(getattr(txin, "sequence", 0)) < required:
                raise ScriptError("OP_CHECKSEQUENCEVERIFY failed")

        elif opcode in (
            Opcode.OP_NOP1,
            Opcode.OP_NOP4,
            Opcode.OP_NOP5,
            Opcode.OP_NOP6,
            Opcode.OP_NOP7,
            Opcode.OP_NOP8,
            Opcode.OP_NOP9,
            Opcode.OP_NOP10,
        ):
            if self.flags.is_enabled(ScriptFlags.VERIFY_DISCOURAGE_UPGRADABLE_NOPS):
                raise ScriptError(f"Discouraged NOP opcode: {opcode.name}")
            return True

        elif opcode in (
            Opcode.OP_RESERVED,
            Opcode.OP_VER,
            Opcode.OP_VERIF,
            Opcode.OP_VERNOTIF,
            Opcode.OP_RESERVED1,
            Opcode.OP_RESERVED2,
        ):
            raise ScriptError(f"Reserved opcode executed: {opcode.name}")
        
        else:
            raise ScriptError(f"Unknown/unsupported opcode: {opcode}")
        
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

        valid = self.sigchecker.check_signature(sig, pubkey)
        if (not valid) and self.flags.is_enabled(ScriptFlags.VERIFY_NULLFAIL) and sig not in (b"", None):
            raise ScriptError("NULLFAIL violation")

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
        dummy = self.stack.pop()
        if self.flags.is_enabled(ScriptFlags.VERIFY_NULLDUMMY):
            if dummy not in (b"", None):
                raise ScriptError("OP_CHECKMULTISIG dummy argument must be empty")
        
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

        # Negative zero (0x80 with all other bytes zero) is false in Script.
        for idx, byte in enumerate(data):
            if byte != 0:
                if idx == len(data) - 1 and byte == 0x80 and all(b == 0 for b in data[:-1]):
                    return False
                return True
        return False
    
    @staticmethod
    def _cast_to_int(data: Optional[bytes]) -> int:
        """Cast bytes to integer."""
        if data is None or len(data) == 0:
            return 0

        result = int.from_bytes(data, "little", signed=False)
        if data[-1] & 0x80:
            result &= ~(0x80 << (8 * (len(data) - 1)))
            return -result
        return result

    @staticmethod
    def _encode_script_num(value: int) -> bytes:
        """Encode integer using Bitcoin ScriptNum little-endian sign-bit format."""
        if value == 0:
            return b""
        neg = value < 0
        abs_value = -value if neg else value
        out = bytearray()
        while abs_value:
            out.append(abs_value & 0xFF)
            abs_value >>= 8
        if out[-1] & 0x80:
            out.append(0x80 if neg else 0x00)
        elif neg:
            out[-1] |= 0x80
        return bytes(out)
