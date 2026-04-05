"""Wallet RPC handlers (private-key wallet model)."""

import base64
import hashlib
import json
import secrets
from typing import Any, Dict, List, Optional

from node.wallet.core.tx_builder import TransactionBuilder
from node.wallet.simple_wallet import SimpleWalletManager
from shared.core.transaction import Transaction
from shared.crypto.base58 import base58_check_decode
from shared.crypto.address import hash160, script_to_address
from shared.crypto.bech32 import bech32_decode
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.opcodes import Opcode
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash
from shared.utils.logging import get_logger


logger = get_logger()


class WalletHandlers:
    """RPC handlers for wallet operations."""

    def __init__(self, node: Any):
        self.node = node

    def _manager(self) -> SimpleWalletManager:
        manager = getattr(self.node, "simple_wallet_manager", None)
        if manager is None:
            manager = SimpleWalletManager(
                self.node.config.get_datadir(),
                network=self.node.config.get("network", "mainnet"),
                wallet_passphrase=self.node.config.get("wallet_encryption_passphrase", ""),
                default_unlock_timeout_secs=int(
                    self.node.config.get("wallet_default_unlock_timeout", 300)
                ),
            )
            setattr(self.node, "simple_wallet_manager", manager)
        return manager

    def _active_wallet(self):
        return self._manager().get_active_wallet()

    def _allow_wallet_debug_secrets(self) -> bool:
        cfg = self.node.config
        if not bool(cfg.get("wallet_debug_secrets", False)):
            return False
        network = str(cfg.get("network", "mainnet") or "mainnet").strip().lower()
        is_dev_mode = bool(cfg.get("debug", False))
        return network == "regtest" or is_dev_mode

    @staticmethod
    def _tracked_addresses(wallet: Any) -> List[str]:
        addrs = list(getattr(wallet, "tracked_addresses", []) or [])
        addr = str(getattr(wallet, "address", "") or "")
        if addr and addr not in addrs:
            addrs.append(addr)
        return addrs

    @staticmethod
    def _op_small_int(n: int) -> int:
        if n < 0 or n > 16:
            raise ValueError("small int opcode out of range")
        if n == 0:
            return int(Opcode.OP_0)
        return int(Opcode.OP_1) + (n - 1)

    def _make_multisig_redeem_script(self, required: int, pubkeys: List[bytes]) -> bytes:
        if required <= 0 or required > len(pubkeys) or len(pubkeys) > 16:
            raise ValueError("Invalid multisig threshold/pubkey count")
        out = bytes([self._op_small_int(required)])
        for pk in pubkeys:
            if len(pk) not in (33, 65):
                raise ValueError("Invalid pubkey length")
            out += bytes([len(pk)]) + pk
        out += bytes([self._op_small_int(len(pubkeys)), int(Opcode.OP_CHECKMULTISIG)])
        return out

    def _validate_destination_address(self, address: str, network: str) -> None:
        """Validate destination address format and network before building tx."""
        addr = str(address or "").strip()
        if not addr:
            raise ValueError("Recipient address required")

        net = str(network or "mainnet").strip().lower()
        base58_versions = {
            "mainnet": {0x00, 0x05},
            "testnet": {0x6F, 0xC4},
            "regtest": {0x6F, 0xC4},
        }
        bech32_hrp = {
            "mainnet": "bc",
            "testnet": "tb",
            "regtest": "bcrt",
        }

        try:
            if addr.startswith(("bc1", "tb1", "bcrt1")):
                hrp, witver, witprog = bech32_decode(addr)
                if hrp is None or witver is None or witprog is None:
                    raise ValueError("Invalid bech32 address")
                expected_hrp = bech32_hrp.get(net)
                if expected_hrp and hrp != expected_hrp:
                    raise ValueError(f"Address network mismatch (expected {expected_hrp})")
                if int(witver) < 0 or int(witver) > 16:
                    raise ValueError("Unsupported witness version")
                if len(witprog) < 2 or len(witprog) > 40:
                    raise ValueError("Invalid witness program length")
                return

            payload = base58_check_decode(addr)
            if len(payload) != 21:
                raise ValueError("Invalid base58 address payload length")
            version = int(payload[0])
            allowed = base58_versions.get(net, set())
            if version not in allowed:
                raise ValueError("Address network mismatch")
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            raise ValueError("Invalid recipient address") from e

    @staticmethod
    def _estimate_fee_sats(num_inputs: int, num_outputs: int, sat_per_vbyte: int) -> int:
        estimated_size = 10 + max(1, int(num_inputs)) * 150 + max(1, int(num_outputs)) * 34
        return max(1, int(estimated_size) * max(1, int(sat_per_vbyte)))

    def _select_spendable_inputs(
        self,
        utxos: List[Dict[str, Any]],
        amount_sats: int,
        baseline_fee: int,
    ) -> List[Dict[str, Any]]:
        """Select inputs with reduced predictability and exact-fit preference."""
        if not utxos:
            return []
        target = int(amount_sats) + int(baseline_fee)
        # Randomize candidate order to avoid deterministic fingerprinting.
        candidates = list(utxos)
        secrets.SystemRandom().shuffle(candidates)

        # Prefer single-input exact/near-exact cover to minimize change leakage.
        singles = [u for u in candidates if int(u.get("value", 0)) >= target]
        if singles:
            singles.sort(key=lambda u: int(u.get("value", 0)) - target)
            return [singles[0]]

        # Fallback: largest-first (after randomized tie-break) to minimize input count.
        candidates.sort(key=lambda u: int(u.get("value", 0)), reverse=True)
        selected: List[Dict[str, Any]] = []
        total = 0
        for utxo in candidates:
            selected.append(utxo)
            total += int(utxo.get("value", 0))
            if total >= target:
                break
        return selected

    async def get_wallet_info(self) -> Dict[str, Any]:
        wallet = self._active_wallet()
        if not wallet:
            return {"active": False}

        balance_sats = int(self.node.chainstate.get_balance(wallet.address))
        info = {
            "active": True,
            "walletname": "simple",
            "walletversion": 1,
            "public_key": wallet.public_key_hex,
            "address": wallet.address,
            "balance": balance_sats / 100000000,
            "satoshis": balance_sats,
            "private_keys_enabled": not bool(getattr(wallet, "watch_only", False)),
            "unlocked_until": int(getattr(self._manager(), "_unlocked_until", 0)),
            "unlocked": bool(self._manager().is_wallet_unlocked()),
            "watch_only": bool(getattr(wallet, "watch_only", False)),
            "scanning": False,
        }
        if self._allow_wallet_debug_secrets():
            info["private_key"] = wallet.private_key_hex
            info["seed_phrase"] = wallet.mnemonic
        elif bool(self.node.config.get("wallet_debug_secrets", False)):
            logger.warning(
                "wallet_debug_secrets requested but blocked outside regtest/dev mode"
            )
        return info

    async def get_balance(self, account: Optional[str] = None, min_conf: int = 1) -> float:
        _ = account, min_conf
        wallet = self._active_wallet()
        if not wallet:
            return 0.0
        balance_sats = int(self.node.chainstate.get_balance(wallet.address))
        return balance_sats / 100000000

    async def get_new_address(
        self,
        account: Optional[str] = None,
        label: str = "",
        address_type: Optional[str] = None,
    ) -> str:
        _ = account, label, address_type
        manager = self._manager()
        wallet = manager.get_active_wallet()
        if wallet is None:
            wallet = manager.create_wallet()
            manager.active_wallet = wallet
        else:
            derived = manager.derive_new_address()
            if derived is not None:
                wallet = derived
        return wallet.address

    async def send_to_address(
        self,
        address: str,
        amount: float,
        fee_rate: Optional[int] = None,
        comment: str = "",
        comment_to: str = "",
    ) -> str:
        _ = fee_rate, comment, comment_to
        if not self.node.mempool:
            raise ValueError("Mempool unavailable")

        wallet = self._active_wallet()
        if not wallet:
            raise ValueError("No active wallet")
        if bool(getattr(wallet, "watch_only", False)):
            raise ValueError("Active wallet is watch-only and cannot sign transactions")
        network = str(self.node.config.get("network", "mainnet") or "mainnet")
        self._validate_destination_address(address, network)

        satoshis = int(amount * 100000000)
        if satoshis <= 0:
            raise ValueError("Amount must be positive")

        utxos = self.node.chainstate.get_utxos_for_address(wallet.address, 1000)
        if not utxos:
            raise ValueError("No UTXOs found")
        get_best_height = getattr(self.node.chainstate, "get_best_height", None)
        if callable(get_best_height):
            best_height = int(get_best_height())
        else:
            best_height = int(getattr(self.node.chainstate, "best_height", 0))
        chain_params = getattr(self.node.chainstate, "params", None)
        maturity = int(getattr(chain_params, "coinbase_maturity", 100))
        spendable_utxos = []
        immature_sats = 0
        for utxo in utxos:
            if bool(utxo.get("is_coinbase", False)):
                utxo_height = int(utxo.get("height", 0) or 0)
                confirmations = best_height - utxo_height + 1 if utxo_height > 0 else 0
                if confirmations < maturity:
                    immature_sats += int(utxo.get("value", 0))
                    continue
            spendable_utxos.append(utxo)
        if not spendable_utxos:
            raise ValueError(
                f"No spendable UTXOs yet (coinbase maturity: {maturity}, "
                f"immature={immature_sats / 100000000:.8f} BERZ)"
            )

        mempool_policy = getattr(self.node.mempool, "policy", None)
        min_relay_fee = int(getattr(mempool_policy, "min_relay_fee", 1))
        dust_threshold = int(getattr(mempool_policy, "dust_threshold", 546))
        requested_fee_rate = int(fee_rate) if fee_rate is not None else 1
        target_fee = max(
            min_relay_fee,
            self._estimate_fee_sats(num_inputs=1, num_outputs=1, sat_per_vbyte=requested_fee_rate),
        )

        selected = self._select_spendable_inputs(spendable_utxos, satoshis, target_fee)
        selected_amount = sum(int(u.get("value", 0)) for u in selected)

        if selected_amount < satoshis + target_fee:
            extra = ""
            if immature_sats > 0:
                extra = f" ({immature_sats / 100000000:.8f} BERZ immature coinbase)"
            raise ValueError(f"Insufficient spendable funds{extra}")

        # Avoid dust-change leaks by folding tiny change into fee before tx construction.
        pre_change = selected_amount - satoshis - target_fee
        if 0 < pre_change < dust_threshold:
            target_fee += pre_change

        builder = TransactionBuilder(network)
        inputs = [(u["txid"], int(u["index"]), int(u["value"])) for u in selected]
        outputs = [(address, satoshis)]
        tx = builder.create_transaction(inputs, outputs, wallet.address, fee=target_fee)

        private_key_hex = self._manager().get_active_private_key()
        if not private_key_hex:
            raise ValueError("Wallet is locked. Use walletpassphrase <passphrase> <timeout>")
        private_key = PrivateKey(int(private_key_hex, 16))
        pubkey = bytes.fromhex(wallet.public_key_hex)
        selected_map = {
            (str(u["txid"]), int(u["index"])): u for u in selected
        }

        def _sign_transaction(candidate_tx) -> None:
            for idx, txin in enumerate(candidate_tx.vin):
                outpoint = (txin.prev_tx_hash.hex(), int(txin.prev_tx_index))
                utxo = selected_map.get(outpoint)
                if not utxo or "script_pubkey" not in utxo:
                    utxo = self.node.chainstate.get_utxo(*outpoint)
                if not utxo:
                    raise ValueError(f"Missing UTXO for input {idx}")
                script_pubkey = utxo.get("script_pubkey", b"")
                if not isinstance(script_pubkey, (bytes, bytearray)):
                    script_pubkey = bytes(script_pubkey)
                sighash = calculate_legacy_sighash(
                    candidate_tx,
                    idx,
                    SIGHASH_ALL,
                    bytes(script_pubkey),
                )
                signature = sign_message_hash(private_key, sighash) + bytes([SIGHASH_ALL])
                txin.script_sig = (
                    bytes([len(signature)]) + signature + bytes([len(pubkey)]) + pubkey
                )

        _sign_transaction(tx)

        # Enforce relay-fee floor against the signed tx size when policy is available.
        if mempool_policy is not None:
            required_fee = max(min_relay_fee * tx.size(), min_relay_fee)
            current_fee = selected_amount - sum(out.value for out in tx.vout)
            if required_fee > current_fee:
                if selected_amount < satoshis + required_fee:
                    raise ValueError("Insufficient funds")
                tx = builder.create_transaction(inputs, outputs, wallet.address, fee=required_fee)
                _sign_transaction(tx)

        if hasattr(self.node, "on_transaction"):
            accepted, txid, reason = await self.node.on_transaction(tx, relay=True)
            if not accepted:
                raise ValueError(f"Transaction rejected: {reason}")
            return txid

        if not await self.node.mempool.add_transaction(tx):
            raise ValueError("Transaction rejected")
        return tx.txid().hex()

    async def list_unspent(
        self,
        min_conf: int = 1,
        max_conf: int = 9999999,
        addresses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        wallet = self._active_wallet()
        if not wallet:
            return []

        best_height = int(self.node.chainstate.get_best_height())
        wallet_addrs = self._tracked_addresses(wallet)
        if addresses:
            wallet_addrs = [a for a in wallet_addrs if a in addresses]

        result: List[Dict[str, Any]] = []
        for addr in wallet_addrs:
            for utxo in self.node.chainstate.get_utxos_for_address(addr, 1000):
                conf = 0
                height = int(utxo.get("height", 0) or 0)
                if height > 0:
                    conf = best_height - height + 1
                if conf < min_conf or conf > max_conf:
                    continue
                result.append(
                    {
                        "txid": utxo.get("txid"),
                        "vout": int(utxo.get("index", 0)),
                        "address": addr,
                        "amount": int(utxo.get("value", 0)) / 100000000,
                        "confirmations": conf,
                        "spendable": True,
                        "solvable": True,
                        "safe": True,
                    }
                )
        return result

    async def list_transactions(
        self,
        account: Optional[str] = None,
        count: int = 10,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        _ = account, count, skip
        return []

    async def create_wallet(
        self,
        wallet_name: str = "default",
    ) -> Dict[str, Any]:
        _ = wallet_name
        wallet = self._manager().create_wallet()
        self._manager().active_wallet = wallet
        self._manager().active_private_key = wallet.private_key_hex
        return {
            "name": "simple",
            "private_key": wallet.private_key_hex,
            "public_key": wallet.public_key_hex,
            "address": wallet.address,
            "mnemonic": wallet.mnemonic,
            "warning": "Store your private key safely.",
        }

    async def load_wallet(self, private_key: str) -> Dict[str, Any]:
        private_key = (private_key or "").strip()
        if not private_key:
            raise ValueError("Private key required")
        try:
            wallet = self._manager().activate_wallet(private_key)
        except Exception as e:
            raise ValueError("Invalid private key") from e
        return {
            "name": "simple",
            "address": wallet.address,
            "public_key": wallet.public_key_hex,
        }

    async def import_xpub_watchonly(self, xpub: str, label: str = "") -> Dict[str, Any]:
        wallet = self._manager().import_xpub_watch_only(xpub, label=label)
        return {
            "name": "watch_only_xpub",
            "address": wallet.address,
            "public_key": wallet.public_key_hex,
            "watch_only": True,
            "wallet_id": wallet.wallet_id,
            "xpub_fingerprint": hashlib.sha256(wallet.xpub.encode("utf-8")).hexdigest()[:16],
        }

    async def wallet_create_funded_psbt(
        self,
        address: str,
        amount: float,
        fee_rate: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a minimal unsigned PSBT-like payload for offline/hardware signing."""
        wallet = self._active_wallet()
        if not wallet:
            raise ValueError("No active wallet")
        network = str(self.node.config.get("network", "mainnet") or "mainnet")
        self._validate_destination_address(address, network)

        satoshis = int(amount * 100000000)
        if satoshis <= 0:
            raise ValueError("Amount must be positive")
        utxos = self.node.chainstate.get_utxos_for_address(wallet.address, 1000)
        if not utxos:
            raise ValueError("No UTXOs found")
        mempool_policy = getattr(self.node.mempool, "policy", None)
        min_relay_fee = int(getattr(mempool_policy, "min_relay_fee", 1))
        requested_fee_rate = int(fee_rate) if fee_rate is not None else 1
        target_fee = max(
            min_relay_fee,
            self._estimate_fee_sats(num_inputs=1, num_outputs=1, sat_per_vbyte=requested_fee_rate),
        )
        selected = self._select_spendable_inputs(utxos, satoshis, target_fee)
        selected_amount = sum(int(u.get("value", 0)) for u in selected)
        if selected_amount < satoshis + target_fee:
            raise ValueError("Insufficient funds")

        builder = TransactionBuilder(network)
        inputs = [(u["txid"], int(u["index"]), int(u["value"])) for u in selected]
        tx = builder.create_transaction(inputs, [(address, satoshis)], wallet.address, fee=target_fee)
        psbt_obj: Dict[str, Any] = {
            "format": "berzcoin.psbt.v1",
            "network": network,
            "unsigned_tx_hex": tx.serialize(include_witness=True).hex(),
            "inputs": [
                {
                    "txid": str(u["txid"]),
                    "index": int(u["index"]),
                    "value": int(u["value"]),
                    "script_pubkey_hex": (
                        (u.get("script_pubkey") or b"").hex()
                        if isinstance(u.get("script_pubkey"), (bytes, bytearray))
                        else ""
                    ),
                }
                for u in selected
            ],
            "outputs": [{"address": address, "value": satoshis}],
            "change_address": wallet.address,
            "fee": int(selected_amount - sum(o.value for o in tx.vout)),
            "complete": False,
        }
        psbt_b64 = base64.b64encode(json.dumps(psbt_obj, sort_keys=True).encode("utf-8")).decode("ascii")
        return {"psbt": psbt_b64, "fee": int(psbt_obj["fee"])}

    async def wallet_process_psbt(self, psbt: str, sign: bool = True) -> Dict[str, Any]:
        """Process PSBT: optionally sign with active wallet key."""
        try:
            decoded = json.loads(base64.b64decode(str(psbt).encode("ascii")).decode("utf-8"))
        except Exception as e:
            raise ValueError("Invalid PSBT payload") from e
        if str(decoded.get("format", "")) != "berzcoin.psbt.v1":
            raise ValueError("Unsupported PSBT format")
        if not bool(sign):
            out = base64.b64encode(json.dumps(decoded, sort_keys=True).encode("utf-8")).decode("ascii")
            return {"psbt": out, "complete": bool(decoded.get("complete", False))}

        wallet = self._active_wallet()
        if not wallet:
            raise ValueError("No active wallet")
        if bool(getattr(wallet, "watch_only", False)):
            return {"psbt": psbt, "complete": False}
        private_key_hex = self._manager().get_active_private_key()
        if not private_key_hex:
            raise ValueError("Wallet is locked. Use walletpassphrase <passphrase> <timeout>")
        private_key = PrivateKey(int(private_key_hex, 16))
        pubkey = bytes.fromhex(wallet.public_key_hex)

        tx_hex = str(decoded.get("unsigned_tx_hex", "") or "")
        if not tx_hex:
            raise ValueError("PSBT missing unsigned transaction")
        tx, _ = Transaction.deserialize(bytes.fromhex(tx_hex))
        inputs_meta = list(decoded.get("inputs", []) or [])
        if len(inputs_meta) != len(tx.vin):
            raise ValueError("PSBT input metadata mismatch")
        for idx, txin in enumerate(tx.vin):
            meta = inputs_meta[idx]
            spk_hex = str(meta.get("script_pubkey_hex", "") or "")
            if not spk_hex:
                utxo = self.node.chainstate.get_utxo(txin.prev_tx_hash.hex(), int(txin.prev_tx_index))
                spk = utxo.get("script_pubkey", b"") if utxo else b""
            else:
                spk = bytes.fromhex(spk_hex)
            sighash = calculate_legacy_sighash(tx, idx, SIGHASH_ALL, bytes(spk))
            signature = sign_message_hash(private_key, sighash) + bytes([SIGHASH_ALL])
            txin.script_sig = bytes([len(signature)]) + signature + bytes([len(pubkey)]) + pubkey
        decoded["signed_tx_hex"] = tx.serialize(include_witness=True).hex()
        decoded["complete"] = True
        out = base64.b64encode(json.dumps(decoded, sort_keys=True).encode("utf-8")).decode("ascii")
        return {"psbt": out, "complete": True}

    async def finalize_psbt(self, psbt: str, extract: bool = True) -> Dict[str, Any]:
        """Finalize PSBT into transaction hex when complete."""
        _ = extract
        try:
            decoded = json.loads(base64.b64decode(str(psbt).encode("ascii")).decode("utf-8"))
        except Exception as e:
            raise ValueError("Invalid PSBT payload") from e
        complete = bool(decoded.get("complete", False))
        tx_hex = str(decoded.get("signed_tx_hex", "") or decoded.get("unsigned_tx_hex", "") or "")
        return {"complete": complete, "hex": tx_hex}

    async def create_multisig_policy(
        self,
        required: int,
        pubkeys: List[str],
        label: str = "",
    ) -> Dict[str, Any]:
        """Create and persist a simple multisig policy (P2SH watch policy)."""
        if not pubkeys:
            raise ValueError("pubkeys required")
        key_bytes: List[bytes] = []
        for k in pubkeys:
            kb = bytes.fromhex(str(k or "").strip())
            if len(kb) not in (33, 65):
                raise ValueError("invalid pubkey length")
            key_bytes.append(kb)
        redeem = self._make_multisig_redeem_script(int(required), key_bytes)
        network = str(self.node.config.get("network", "mainnet") or "mainnet")
        p2sh = script_to_address(hash160(redeem), network=network)
        policy = {
            "policy_id": hashlib.sha256(redeem).hexdigest()[:24],
            "type": "multisig_p2sh",
            "required": int(required),
            "pubkeys": [k.hex() for k in key_bytes],
            "redeem_script": redeem.hex(),
            "address": p2sh,
            "label": str(label or ""),
            "network": network,
        }
        manager = self._manager()
        policies_file = manager.wallets_dir / "multisig_policies.json"
        current: List[Dict[str, Any]] = []
        if policies_file.exists():
            try:
                current = json.loads(policies_file.read_text(encoding="utf-8"))
            except Exception:
                current = []
        current = [p for p in current if p.get("policy_id") != policy["policy_id"]]
        current.append(policy)
        policies_file.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        return policy

    async def get_address_info(self, address: str) -> Dict[str, Any]:
        wallet = self._active_wallet()
        is_mine = bool(wallet and address in self._tracked_addresses(wallet))
        is_watch = bool(wallet and wallet.watch_only and is_mine)
        return {
            "address": address,
            "ismine": is_mine,
            "iswatchonly": is_watch,
            "isscript": False,
            "iswitness": address.startswith("bc1") or address.startswith("tb1") or address.startswith("bcrt1"),
            "label": "",
            "timestamp": int(wallet.created_at) if is_mine else 0,
        }
