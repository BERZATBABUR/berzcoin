"""Mempool policy rules."""

from typing import List, Set
from shared.core.transaction import Transaction
from shared.script.opcodes import Opcode
from shared.utils.logging import get_logger

logger = get_logger()

class MempoolPolicy:
    MAX_STANDARD_TX_SIZE = 100000
    MAX_STANDARD_TX_WEIGHT = 400000
    MAX_STANDARD_SIGOPS = 4000
    MIN_RELAY_FEE = 1000
    DUST_THRESHOLD = 546
    STANDARD_SCRIPTS = {'p2pk', 'p2pkh', 'p2sh', 'p2wpkh', 'p2wsh', 'p2tr'}
    MAX_STANDARD_WITNESS_ITEMS = 100
    MAX_STANDARD_WITNESS_ITEM_SIZE = 80
    MAX_STANDARD_TAPSCRIPT_SIZE = 10000
    MAX_STANDARD_TAPROOT_CONTROL_BLOCK = 33 + (32 * 128)
    MAX_STANDARD_SCRIPTSIG_SIZE = 1650
    MAX_OP_RETURN_OUTPUTS = 1

    def __init__(self):
        # Use a low default relay fee for regtest/dev environments
        self.min_relay_fee = 1
        self.max_tx_size = self.MAX_STANDARD_TX_SIZE
        self.max_tx_weight = self.MAX_STANDARD_TX_WEIGHT
        self.dust_threshold = self.DUST_THRESHOLD

    def is_standard(self, tx: Transaction) -> bool:
        if int(getattr(tx, "version", 1)) < 1 or int(getattr(tx, "version", 1)) > 2:
            logger.debug("Non-standard tx version")
            return False
        if len(tx.serialize()) > self.max_tx_size:
            logger.debug(f"Transaction too large: {len(tx.serialize())} > {self.max_tx_size}")
            return False
        if tx.weight() > self.max_tx_weight:
            logger.debug(f"Transaction weight too high: {tx.weight()} > {self.max_tx_weight}")
            return False
        op_return_count = 0
        for txout in tx.vout:
            if not self._is_standard_output(txout.script_pubkey):
                logger.debug("Non-standard output script")
                return False
            if txout.script_pubkey and txout.script_pubkey[0] == Opcode.OP_RETURN:
                op_return_count += 1
                if txout.value != 0:
                    logger.debug("OP_RETURN output must carry zero value")
                    return False
            elif self.is_dust(int(txout.value), txout.script_pubkey):
                logger.debug("Dust output")
                return False
        if op_return_count > self.MAX_OP_RETURN_OUTPUTS:
            logger.debug("Too many OP_RETURN outputs")
            return False
        if not tx.is_coinbase():
            for txin in tx.vin:
                if not self._is_standard_input(txin.script_sig, txin):
                    logger.debug("Non-standard input script")
                    return False
        return True

    def _is_standard_output(self, script_pubkey: bytes) -> bool:
        if not script_pubkey:
            return False
        # P2PK (compressed)
        if len(script_pubkey) == 35 and script_pubkey[0] == 0x21 and script_pubkey[-1] == Opcode.OP_CHECKSIG:
            return True
        # P2PKH
        if (
            len(script_pubkey) == 25
            and script_pubkey[0] == Opcode.OP_DUP
            and script_pubkey[1] == Opcode.OP_HASH160
            and script_pubkey[2] == 0x14
            and script_pubkey[23] == Opcode.OP_EQUALVERIFY
            and script_pubkey[24] == Opcode.OP_CHECKSIG
        ):
            return True
        # P2SH
        if (
            len(script_pubkey) == 23
            and script_pubkey[0] == Opcode.OP_HASH160
            and script_pubkey[1] == 0x14
            and script_pubkey[22] == Opcode.OP_EQUAL
        ):
            return True
        # P2WPKH
        if len(script_pubkey) == 22 and script_pubkey[0] == 0x00 and script_pubkey[1] == 0x14:
            return True
        # P2WSH
        if len(script_pubkey) == 34 and script_pubkey[0] == 0x00 and script_pubkey[1] == 0x20:
            return True
        # P2TR
        if len(script_pubkey) == 34 and script_pubkey[0] == Opcode.OP_1 and script_pubkey[1] == 0x20:
            return True
        if script_pubkey[0] == Opcode.OP_RETURN:
            if len(script_pubkey) > 83:
                return False
            return True
        return False

    def _is_standard_input(self, script_sig: bytes, txin: object = None) -> bool:
        if len(script_sig or b"") > self.MAX_STANDARD_SCRIPTSIG_SIZE:
            return False
        if not self._is_standard_witness(txin, script_sig):
            return False
        if not script_sig:
            return True
        if not self._is_push_only(script_sig):
            return False
        if not self._is_minimal_push(script_sig):
            return False
        return True

    def _is_standard_witness(self, txin: object, script_sig: bytes) -> bool:
        if txin is None:
            return True
        witness = getattr(txin, "witness", None)
        items = list(getattr(witness, "items", []) or [])
        if not items:
            return True
        # Native witness spends should not use scriptSig.
        if script_sig:
            return False
        if len(items) > self.MAX_STANDARD_WITNESS_ITEMS:
            return False
        if len(items) == 1:
            # Key-path Taproot witness should be a Schnorr signature (64/65 bytes).
            if len(items[0]) not in (64, 65):
                return False
            return True
        if len(items) >= 2:
            # Script-path witness: [... stack items ..., script, control_block]
            stack_items = items[:-2]
            script_item = items[-2]
            control_block = items[-1]
            for item in stack_items:
                if len(item) > self.MAX_STANDARD_WITNESS_ITEM_SIZE:
                    return False
            if len(script_item) == 0 or len(script_item) > self.MAX_STANDARD_TAPSCRIPT_SIZE:
                return False
            if len(control_block) < 33 or len(control_block) > self.MAX_STANDARD_TAPROOT_CONTROL_BLOCK:
                return False
            if ((len(control_block) - 33) % 32) != 0:
                return False
            # Disallow annex marker in stack for now.
            if stack_items and len(stack_items[-1]) > 0 and stack_items[-1][0] == 0x50:
                return False
            return True
        for item in items:
            if len(item) > self.MAX_STANDARD_WITNESS_ITEM_SIZE:
                return False
        return True

    def _is_push_only(self, script: bytes) -> bool:
        i = 0
        while i < len(script):
            opcode = script[i]
            i += 1
            if opcode <= 0x4b:
                i += opcode
            elif opcode == Opcode.OP_PUSHDATA1:
                if i >= len(script):
                    return False
                l = script[i]
                i += 1 + l
            elif opcode == Opcode.OP_PUSHDATA2:
                if i + 1 >= len(script):
                    return False
                l = int.from_bytes(script[i:i + 2], 'little')
                i += 2 + l
            elif opcode == Opcode.OP_PUSHDATA4:
                if i + 3 >= len(script):
                    return False
                l = int.from_bytes(script[i:i + 4], 'little')
                i += 4 + l
            elif opcode in (Opcode.OP_0, Opcode.OP_1NEGATE) or Opcode.OP_1 <= opcode <= Opcode.OP_16:
                continue
            else:
                return False
            if i > len(script):
                return False
        return True

    def _is_minimal_push(self, script: bytes) -> bool:
        i = 0
        while i < len(script):
            opcode = script[i]
            i += 1
            if opcode <= 0x4b:
                if i + opcode > len(script):
                    return False
                if opcode == 0:
                    # Empty vector must use OP_0 and this opcode is OP_0.
                    continue
                if opcode == 1:
                    value = script[i]
                    if value in range(0, 17) or value == 0x81:
                        return False
                i += opcode
            elif opcode == Opcode.OP_PUSHDATA1:
                if i >= len(script):
                    return False
                length = script[i]
                if length < Opcode.OP_PUSHDATA1:
                    return False
                i += 1 + length
            elif opcode == Opcode.OP_PUSHDATA2:
                if i + 1 >= len(script):
                    return False
                length = int.from_bytes(script[i:i + 2], 'little')
                if length < 0x100:
                    return False
                i += 2 + length
            elif opcode == Opcode.OP_PUSHDATA4:
                if i + 3 >= len(script):
                    return False
                length = int.from_bytes(script[i:i + 4], 'little')
                if length < 0x10000:
                    return False
                i += 4 + length
            elif opcode in (Opcode.OP_0, Opcode.OP_1NEGATE) or Opcode.OP_1 <= opcode <= Opcode.OP_16:
                # Small integer opcodes are already minimal.
                continue
            else:
                # Non-push opcode should already be filtered by _is_push_only
                return False
            if i > len(script):
                return False
        return True

    def is_dust(self, value: int, script_pubkey: bytes) -> bool:
        return value < self.dust_threshold

    def get_min_fee(self, size: int) -> int:
        return int(size) * self.min_relay_fee

    def get_min_fee_for_vsize(self, vsize: int) -> int:
        """Minimum relay fee by virtual size (sat/vB model)."""
        return int(vsize) * self.min_relay_fee

    def set_min_relay_fee(self, fee_rate: int) -> None:
        self.min_relay_fee = fee_rate

    def get_policy_summary(self) -> dict:
        return {
            'min_relay_fee': self.min_relay_fee,
            'max_tx_size': self.max_tx_size,
            'max_tx_weight': self.max_tx_weight,
            'dust_threshold': self.dust_threshold,
        }
