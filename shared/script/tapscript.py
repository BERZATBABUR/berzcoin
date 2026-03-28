"""Taproot script-path helpers and tapscript executor."""

from __future__ import annotations

import hashlib
from typing import List

from ..core.hashes import hash160, hash256, sha256, tagged_hash
from ..crypto.secp256k1 import taproot_tweak_pubkey
from .opcodes import Opcode
from .script_flags import ScriptFlags
from .sigchecks import SignatureChecker, calculate_tapleaf_hash

MAX_TAPSCRIPT_SIZE = 10_000
MAX_TAPSCRIPT_OPS = 201
MAX_TAPSCRIPT_STACK_ITEMS = 1_000
MAX_TAPSCRIPT_ELEMENT_SIZE = 520


def _decode_script_num(data: bytes, max_size: int = 4) -> int:
    if len(data) > max_size:
        raise ValueError("script number overflow")
    if not data:
        return 0
    result = int.from_bytes(data, "little", signed=False)
    if data[-1] & 0x80:
        result &= ~(0x80 << (8 * (len(data) - 1)))
        return -result
    return result


def _encode_script_num(value: int) -> bytes:
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


def _cast_to_bool(data: bytes) -> bool:
    if not data:
        return False
    for idx, b in enumerate(data):
        if b != 0:
            if idx == len(data) - 1 and b == 0x80 and all(x == 0 for x in data[:-1]):
                return False
            return True
    return False


def _is_op_success(op: int) -> bool:
    # BIP342 OP_SUCCESSx ranges/opcodes.
    return op in (
        80,
        98,
        126,
        127,
        128,
        129,
        131,
        132,
        133,
        134,
        137,
        138,
        141,
        142,
        149,
        150,
        151,
        152,
        153,
    ) or (187 <= op <= 254)


def _parse_tapbranch_merkle_root(tapleaf_hash: bytes, control_block: bytes) -> bytes:
    merkle = tapleaf_hash
    if len(control_block) > 33:
        pos = 33
        while pos < len(control_block):
            h = control_block[pos:pos + 32]
            if merkle <= h:
                merkle = tagged_hash("TapBranch", merkle + h)
            else:
                merkle = tagged_hash("TapBranch", h + merkle)
            pos += 32
    return merkle


def verify_taproot_scriptpath_commitment(script_pubkey: bytes, script: bytes, control_block: bytes) -> bool:
    if len(script_pubkey) != 34 or script_pubkey[0] != 0x51 or script_pubkey[1] != 0x20:
        return False
    if len(control_block) < 33 or ((len(control_block) - 33) % 32) != 0:
        return False

    leaf_version = control_block[0] & 0xFE
    internal_key = control_block[1:33]
    output_parity = control_block[0] & 1

    tapleaf = calculate_tapleaf_hash(script, leaf_version=leaf_version)
    merkle_root = _parse_tapbranch_merkle_root(tapleaf, control_block)
    tweaked = taproot_tweak_pubkey(internal_key, merkle_root)
    if tweaked is None:
        return False
    q_xonly, q_parity = tweaked
    return q_xonly == script_pubkey[2:34] and q_parity == output_parity


def execute_tapscript(
    script: bytes,
    initial_stack: List[bytes],
    checker: SignatureChecker,
    flags: int = ScriptFlags.STANDARD_VERIFY_FLAGS,
) -> bool:
    if len(script) > MAX_TAPSCRIPT_SIZE:
        return False
    verify_flags = flags.flags if isinstance(flags, ScriptFlags) else int(flags)
    stack: List[bytes] = list(initial_stack)
    altstack: List[bytes] = []
    exec_stack: List[bool] = []
    if len(stack) > MAX_TAPSCRIPT_STACK_ITEMS:
        return False
    for item in stack:
        if len(item) > MAX_TAPSCRIPT_ELEMENT_SIZE:
            return False

    def _check_stack_limits() -> bool:
        if len(stack) + len(altstack) > MAX_TAPSCRIPT_STACK_ITEMS:
            return False
        return not any(len(x) > MAX_TAPSCRIPT_ELEMENT_SIZE for x in stack + altstack)

    def _is_executing() -> bool:
        return all(exec_stack)

    i = 0
    op_count = 0

    while i < len(script):
        if not _check_stack_limits():
            return False
        op = script[i]
        i += 1

        if op <= 75:
            if i + op > len(script):
                return False
            pushed = script[i:i + op]
            i += op
            if _is_executing():
                if len(pushed) > MAX_TAPSCRIPT_ELEMENT_SIZE:
                    return False
                stack.append(pushed)
            continue

        if op == Opcode.OP_PUSHDATA1:
            if i >= len(script):
                return False
            length = script[i]
            i += 1
            if i + length > len(script):
                return False
            pushed = script[i:i + length]
            i += length
            if _is_executing():
                if len(pushed) > MAX_TAPSCRIPT_ELEMENT_SIZE:
                    return False
                stack.append(pushed)
            continue

        if op == Opcode.OP_PUSHDATA2:
            if i + 2 > len(script):
                return False
            length = int.from_bytes(script[i:i + 2], "little")
            i += 2
            if i + length > len(script):
                return False
            pushed = script[i:i + length]
            i += length
            if _is_executing():
                if len(pushed) > MAX_TAPSCRIPT_ELEMENT_SIZE:
                    return False
                stack.append(pushed)
            continue

        if op == Opcode.OP_PUSHDATA4:
            if i + 4 > len(script):
                return False
            length = int.from_bytes(script[i:i + 4], "little")
            i += 4
            if i + length > len(script):
                return False
            pushed = script[i:i + length]
            i += length
            if _is_executing():
                if len(pushed) > MAX_TAPSCRIPT_ELEMENT_SIZE:
                    return False
                stack.append(pushed)
            continue

        if op == Opcode.OP_0:
            if _is_executing():
                stack.append(b"")
            continue

        if Opcode.OP_1 <= op <= Opcode.OP_16:
            if _is_executing():
                stack.append(bytes([op - Opcode.OP_1 + 1]))
            continue

        # Conditionals must be processed even in non-executed branches.
        if op in (Opcode.OP_IF, Opcode.OP_NOTIF):
            if _is_executing():
                if not stack:
                    return False
                cond_item = stack.pop()
                if (verify_flags & ScriptFlags.VERIFY_MINIMALIF) and cond_item not in (b"", b"\x01"):
                    return False
                cond = _cast_to_bool(cond_item)
                if op == Opcode.OP_NOTIF:
                    cond = not cond
                exec_stack.append(cond)
            else:
                exec_stack.append(False)
            continue

        if op == Opcode.OP_ELSE:
            if not exec_stack:
                return False
            parent_exec = all(exec_stack[:-1])
            exec_stack[-1] = parent_exec and (not exec_stack[-1])
            continue

        if op == Opcode.OP_ENDIF:
            if not exec_stack:
                return False
            exec_stack.pop()
            continue

        if not _is_executing():
            continue

        if _is_op_success(op):
            return True

        op_count += 1
        if op_count > MAX_TAPSCRIPT_OPS:
            return False

        if op in (
            Opcode.OP_VER,
            Opcode.OP_VERIF,
            Opcode.OP_VERNOTIF,
            Opcode.OP_RESERVED,
            Opcode.OP_RESERVED1,
            Opcode.OP_RESERVED2,
            Opcode.OP_CHECKMULTISIG,
            Opcode.OP_CHECKMULTISIGVERIFY,
        ):
            return False

        if op in (
            Opcode.OP_NOP,
            Opcode.OP_NOP1,
            Opcode.OP_NOP2,
            Opcode.OP_NOP3,
            Opcode.OP_NOP4,
            Opcode.OP_NOP5,
            Opcode.OP_NOP6,
            Opcode.OP_NOP7,
            Opcode.OP_NOP8,
            Opcode.OP_NOP9,
            Opcode.OP_NOP10,
            Opcode.OP_CODESEPARATOR,
        ):
            continue

        if op == Opcode.OP_RETURN:
            return False

        if op == Opcode.OP_TOALTSTACK:
            if not stack:
                return False
            altstack.append(stack.pop())
            continue

        if op == Opcode.OP_FROMALTSTACK:
            if not altstack:
                return False
            stack.append(altstack.pop())
            continue

        if op == Opcode.OP_DROP:
            if not stack:
                return False
            stack.pop()
            continue

        if op == Opcode.OP_DUP:
            if not stack:
                return False
            stack.append(stack[-1])
            continue

        if op == Opcode.OP_NIP:
            if len(stack) < 2:
                return False
            top = stack.pop()
            stack.pop()
            stack.append(top)
            continue

        if op == Opcode.OP_OVER:
            if len(stack) < 2:
                return False
            stack.append(stack[-2])
            continue

        if op == Opcode.OP_SWAP:
            if len(stack) < 2:
                return False
            stack[-1], stack[-2] = stack[-2], stack[-1]
            continue

        if op == Opcode.OP_TUCK:
            if len(stack) < 2:
                return False
            top = stack[-1]
            stack.insert(len(stack) - 2, top)
            continue

        if op == Opcode.OP_2DROP:
            if len(stack) < 2:
                return False
            stack.pop()
            stack.pop()
            continue

        if op == Opcode.OP_2DUP:
            if len(stack) < 2:
                return False
            stack.append(stack[-2])
            stack.append(stack[-2])
            continue

        if op == Opcode.OP_IFDUP:
            if not stack:
                return False
            if _cast_to_bool(stack[-1]):
                stack.append(stack[-1])
            continue

        if op == Opcode.OP_DEPTH:
            stack.append(_encode_script_num(len(stack)))
            continue

        if op == Opcode.OP_SIZE:
            if not stack:
                return False
            stack.append(_encode_script_num(len(stack[-1])))
            continue

        if op == Opcode.OP_EQUAL:
            if len(stack) < 2:
                return False
            a = stack.pop()
            b = stack.pop()
            stack.append(b"\x01" if a == b else b"")
            continue

        if op == Opcode.OP_EQUALVERIFY:
            if len(stack) < 2:
                return False
            a = stack.pop()
            b = stack.pop()
            if a != b:
                return False
            continue

        if op == Opcode.OP_VERIFY:
            if not stack:
                return False
            if not _cast_to_bool(stack.pop()):
                return False
            continue

        if op == Opcode.OP_HASH160:
            if not stack:
                return False
            stack.append(hash160(stack.pop()))
            continue

        if op == Opcode.OP_SHA256:
            if not stack:
                return False
            stack.append(sha256(stack.pop()))
            continue

        if op == Opcode.OP_HASH256:
            if not stack:
                return False
            stack.append(hash256(stack.pop()))
            continue

        if op == Opcode.OP_SHA1:
            if not stack:
                return False
            stack.append(hashlib.sha1(stack.pop()).digest())
            continue

        if op == Opcode.OP_CHECKSIG:
            if len(stack) < 2:
                return False
            pubkey = stack.pop()
            sig = stack.pop()
            ok = checker.check_schnorr_signature(sig, pubkey)
            stack.append(b"\x01" if ok else b"")
            continue

        if op == Opcode.OP_CHECKSIGVERIFY:
            if len(stack) < 2:
                return False
            pubkey = stack.pop()
            sig = stack.pop()
            if not checker.check_schnorr_signature(sig, pubkey):
                return False
            continue

        if op == Opcode.OP_CHECKSIGADD:
            if len(stack) < 3:
                return False
            pubkey = stack.pop()
            try:
                n = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            sig = stack.pop()
            ok = checker.check_schnorr_signature(sig, pubkey)
            stack.append(_encode_script_num(n + (1 if ok else 0)))
            continue

        if op == Opcode.OP_NOT:
            if not stack:
                return False
            try:
                n = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            stack.append(b"\x01" if n == 0 else b"")
            continue

        if op == Opcode.OP_0NOTEQUAL:
            if not stack:
                return False
            try:
                n = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            stack.append(b"\x01" if n != 0 else b"")
            continue

        if op == Opcode.OP_ADD:
            if len(stack) < 2:
                return False
            try:
                a = _decode_script_num(stack.pop(), max_size=4)
                b = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            stack.append(_encode_script_num(b + a))
            continue

        if op == Opcode.OP_SUB:
            if len(stack) < 2:
                return False
            try:
                a = _decode_script_num(stack.pop(), max_size=4)
                b = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            stack.append(_encode_script_num(b - a))
            continue

        if op == Opcode.OP_BOOLAND:
            if len(stack) < 2:
                return False
            try:
                a = _decode_script_num(stack.pop(), max_size=4)
                b = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            stack.append(b"\x01" if (a != 0 and b != 0) else b"")
            continue

        if op == Opcode.OP_BOOLOR:
            if len(stack) < 2:
                return False
            try:
                a = _decode_script_num(stack.pop(), max_size=4)
                b = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            stack.append(b"\x01" if (a != 0 or b != 0) else b"")
            continue

        if op == Opcode.OP_NUMEQUAL:
            if len(stack) < 2:
                return False
            try:
                a = _decode_script_num(stack.pop(), max_size=4)
                b = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            stack.append(b"\x01" if a == b else b"")
            continue

        if op == Opcode.OP_NUMEQUALVERIFY:
            if len(stack) < 2:
                return False
            try:
                a = _decode_script_num(stack.pop(), max_size=4)
                b = _decode_script_num(stack.pop(), max_size=4)
            except ValueError:
                return False
            if a != b:
                return False
            continue

        return False

    if exec_stack:
        return False
    if not stack:
        return False
    if not _check_stack_limits():
        return False
    return _cast_to_bool(stack.pop())
