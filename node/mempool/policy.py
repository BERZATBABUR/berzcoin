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

    def __init__(self):
        # Use a low default relay fee for regtest/dev environments
        self.min_relay_fee = 1
        self.max_tx_size = self.MAX_STANDARD_TX_SIZE
        self.max_tx_weight = self.MAX_STANDARD_TX_WEIGHT
        self.dust_threshold = self.DUST_THRESHOLD

    def is_standard(self, tx: Transaction) -> bool:
        if len(tx.serialize()) > self.max_tx_size:
            logger.debug(f"Transaction too large: {len(tx.serialize())} > {self.max_tx_size}")
            return False
        if tx.weight() > self.max_tx_weight:
            logger.debug(f"Transaction weight too high: {tx.weight()} > {self.max_tx_weight}")
            return False
        for txout in tx.vout:
            if not self._is_standard_output(txout.script_pubkey):
                logger.debug("Non-standard output script")
                return False
        if not tx.is_coinbase():
            for txin in tx.vin:
                if not self._is_standard_input(txin.script_sig):
                    logger.debug("Non-standard input script")
                    return False
        return True

    def _is_standard_output(self, script_pubkey: bytes) -> bool:
        if not script_pubkey:
            return False
        if len(script_pubkey) == 35 and script_pubkey[0] == 0x21:
            return True
        if len(script_pubkey) == 25 and script_pubkey[0] == 0x76:
            return True
        if len(script_pubkey) == 23 and script_pubkey[0] == 0xa9:
            return True
        if len(script_pubkey) == 22 and script_pubkey[0] == 0x00 and script_pubkey[1] == 0x14:
            return True
        if len(script_pubkey) == 34 and script_pubkey[0] == 0x00 and script_pubkey[1] == 0x20:
            return True
        if script_pubkey[0] == Opcode.OP_RETURN:
            if len(script_pubkey) > 83:
                return False
            return True
        return False

    def _is_standard_input(self, script_sig: bytes) -> bool:
        if not script_sig:
            return True
        if not self._is_push_only(script_sig):
            return False
        if not self._is_minimal_push(script_sig):
            return False
        return True

    def _is_push_only(self, script: bytes) -> bool:
        i = 0
        while i < len(script):
            opcode = script[i]
            if opcode <= Opcode.OP_16:
                if opcode == Opcode.OP_PUSHDATA1:
                    i += 2
                elif opcode == Opcode.OP_PUSHDATA2:
                    i += 3
                elif opcode == Opcode.OP_PUSHDATA4:
                    i += 5
                else:
                    i += 1
            else:
                return False
            i += 1
        return True

    def _is_minimal_push(self, script: bytes) -> bool:
        i = 0
        while i < len(script):
            opcode = script[i]
            if opcode == Opcode.OP_PUSHDATA1:
                length = script[i + 1]
                if length < Opcode.OP_PUSHDATA1:
                    return False
            elif opcode == Opcode.OP_PUSHDATA2:
                length = int.from_bytes(script[i + 1:i + 3], 'little')
                if length < 0x100:
                    return False
            elif opcode == Opcode.OP_PUSHDATA4:
                length = int.from_bytes(script[i + 1:i + 5], 'little')
                if length < 0x10000:
                    return False
            elif opcode >= 0x01 and opcode <= 0x4b:
                if opcode == 0x01 and i + 1 < len(script):
                    value = script[i + 1]
                    if value == 0x80:
                        pass
                    elif value >= 1 and value <= 16:
                        pass
                    else:
                        return False
            i += 1
        return True

    def is_dust(self, value: int, script_pubkey: bytes) -> bool:
        return value < self.dust_threshold

    def get_min_fee(self, size: int) -> int:
        return size * self.min_relay_fee

    def set_min_relay_fee(self, fee_rate: int) -> None:
        self.min_relay_fee = fee_rate

    def get_policy_summary(self) -> dict:
        return {
            'min_relay_fee': self.min_relay_fee,
            'max_tx_size': self.max_tx_size,
            'max_tx_weight': self.max_tx_weight,
            'dust_threshold': self.dust_threshold,
        }
