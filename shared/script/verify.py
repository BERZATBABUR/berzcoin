"""Helpers for validating unlocking/locking scripts."""

from __future__ import annotations

from typing import Any

from ..core.hashes import hash160
from ..crypto.signatures import verify_schnorr_signature
from .sigchecks import calculate_taproot_keypath_sighash
from .sigchecks import SignatureChecker
from .sigchecks import calculate_tapleaf_hash
from .engine import ScriptEngine
from .script_flags import ScriptFlags
from .tapscript import execute_tapscript, verify_taproot_scriptpath_commitment


def _parse_push_only_items(script: bytes) -> list[bytes]:
    """Parse a push-only script into stack items."""
    items: list[bytes] = []
    i = 0
    while i < len(script):
        opcode = script[i]
        i += 1
        if opcode == 0:
            items.append(b"")
            continue
        if 1 <= opcode <= 75:
            if i + opcode > len(script):
                raise ValueError("truncated push in scriptSig")
            items.append(script[i:i + opcode])
            i += opcode
            continue
        if opcode == 0x4C:  # OP_PUSHDATA1
            if i >= len(script):
                raise ValueError("missing OP_PUSHDATA1 length")
            size = script[i]
            i += 1
            if i + size > len(script):
                raise ValueError("truncated OP_PUSHDATA1")
            items.append(script[i:i + size])
            i += size
            continue
        if opcode == 0x4D:  # OP_PUSHDATA2
            if i + 2 > len(script):
                raise ValueError("missing OP_PUSHDATA2 length")
            size = int.from_bytes(script[i:i + 2], "little")
            i += 2
            if i + size > len(script):
                raise ValueError("truncated OP_PUSHDATA2")
            items.append(script[i:i + size])
            i += size
            continue
        if opcode == 0x4E:  # OP_PUSHDATA4
            if i + 4 > len(script):
                raise ValueError("missing OP_PUSHDATA4 length")
            size = int.from_bytes(script[i:i + 4], "little")
            i += 4
            if i + size > len(script):
                raise ValueError("truncated OP_PUSHDATA4")
            items.append(script[i:i + size])
            i += size
            continue
        if opcode == 0x4F:  # OP_1NEGATE
            items.append(b"\x81")
            continue
        if 0x51 <= opcode <= 0x60:  # OP_1..OP_16
            items.append(bytes([opcode - 0x50]))
            continue
        raise ValueError("non-push opcode in scriptSig")
    return items


def _split_taproot_annex(witness_items: list[bytes]) -> tuple[list[bytes], bytes]:
    if witness_items and len(witness_items[-1]) > 0 and witness_items[-1][0] == 0x50:
        return witness_items[:-1], witness_items[-1]
    return witness_items, b""


def verify_input_script(
    tx: Any,
    input_index: int,
    script_sig: bytes,
    script_pubkey: bytes,
    amount: int = 0,
    flags: int = ScriptFlags.STANDARD_VERIFY_FLAGS,
) -> bool:
    """Validate a transaction input script pair.

    This codebase uses a simplified script/signature model.
    """
    try:
        verify_flags = flags.flags if isinstance(flags, ScriptFlags) else int(flags)

        if (
            len(script_pubkey) == 22
            and script_pubkey[0] == 0x00
            and script_pubkey[1] == 0x14
        ):
            # Native P2WPKH spends must have empty scriptSig and 2 witness items.
            if script_sig:
                return False
            txin = tx.vin[input_index]
            witness_items = list(getattr(getattr(txin, "witness", None), "items", []) or [])
            if len(witness_items) != 2:
                return False
            signature, pubkey = witness_items
            if hash160(pubkey) != script_pubkey[2:22]:
                return False
            # BIP143 scriptCode for P2WPKH is the equivalent P2PKH script.
            script_code = b"\x76\xa9\x14" + script_pubkey[2:22] + b"\x88\xac"
            checker = SignatureChecker(
                tx,
                input_index,
                amount,
                verify_flags,
                script_code=script_code,
                use_segwit_v0=True,
            )
            return checker.check_signature(signature, pubkey, script_code=script_code)

        if len(script_pubkey) == 34 and script_pubkey[0] == 0x51 and script_pubkey[1] == 0x20:
            # Taproot key-path (v1 witness program) support.
            if script_sig:
                return False
            txin = tx.vin[input_index]
            witness_items = list(getattr(getattr(txin, "witness", None), "items", []) or [])
            witness_items, annex = _split_taproot_annex(witness_items)
            if len(witness_items) == 1:
                sig_with_type = witness_items[0]
                if len(sig_with_type) not in (64, 65):
                    return False
                sighash_type = sig_with_type[64] if len(sig_with_type) == 65 else 0x00
                if sighash_type not in (0x00, 0x01, 0x02, 0x03, 0x81, 0x82, 0x83):
                    return False
                sig = sig_with_type[:64]
                key_xonly = script_pubkey[2:34]
                sighash = calculate_taproot_keypath_sighash(
                    tx,
                    input_index,
                    amount,
                    script_pubkey,
                    sighash_type=sighash_type,
                    annex=annex,
                )
                return verify_schnorr_signature(key_xonly, sighash, sig)
            if len(witness_items) >= 2:
                script = witness_items[-2]
                control_block = witness_items[-1]
                stack_items = witness_items[:-2]
                if not verify_taproot_scriptpath_commitment(script_pubkey, script, control_block):
                    return False
                leaf_ver = control_block[0] & 0xFE
                if leaf_ver != 0xC0:
                    return False
                tapleaf_hash = calculate_tapleaf_hash(script, leaf_version=leaf_ver)
                checker = SignatureChecker(
                    tx,
                    input_index,
                    amount,
                    verify_flags,
                    script_code=script_pubkey,
                    use_taproot_scriptpath=True,
                    tapleaf_hash=tapleaf_hash,
                    annex=annex,
                )
                return execute_tapscript(script, stack_items, checker, verify_flags)
            return False

        # Standard P2SH: OP_HASH160 <20-byte hash> OP_EQUAL
        if (
            len(script_pubkey) == 23
            and script_pubkey[0] == 0xA9
            and script_pubkey[1] == 0x14
            and script_pubkey[22] == 0x87
        ):
            if not (verify_flags & ScriptFlags.VERIFY_P2SH):
                return False
            items = _parse_push_only_items(script_sig or b"")
            if len(items) < 1:
                return False
            redeem_script = items[-1]
            if hash160(redeem_script) != script_pubkey[2:22]:
                return False

            # Nested witness redeem programs are validated under witness rules.
            if len(redeem_script) in (22, 34) and redeem_script[0] in (0x00, 0x51):
                return verify_input_script(
                    tx,
                    input_index,
                    b"",
                    redeem_script,
                    amount,
                    verify_flags,
                )

            engine = ScriptEngine(flags=verify_flags)
            return bool(
                engine.execute(
                    redeem_script,
                    tx,
                    input_index,
                    amount,
                    initial_stack=items[:-1],
                )
            )

        # Standard P2PKH:
        # OP_DUP OP_HASH160 <20-byte hash> OP_EQUALVERIFY OP_CHECKSIG
        if (
            len(script_pubkey) == 25
            and script_pubkey[0] == 0x76
            and script_pubkey[1] == 0xA9
            and script_pubkey[2] == 0x14
            and script_pubkey[23] == 0x88
            and script_pubkey[24] == 0xAC
        ):
            items = _parse_push_only_items(script_sig or b"")
            if len(items) != 2:
                return False
            signature, pubkey = items
            if hash160(pubkey) != script_pubkey[3:23]:
                return False
            checker = SignatureChecker(
                tx,
                input_index,
                amount,
                verify_flags,
                script_code=script_pubkey,
            )
            return checker.check_signature(signature, pubkey, script_code=script_pubkey)

        # Generic fallback for non-P2PKH scripts.
        engine = ScriptEngine(flags=verify_flags)
        combined = (script_sig or b"") + (script_pubkey or b"")
        return bool(engine.execute(combined, tx, input_index, amount))
    except Exception:
        return False
