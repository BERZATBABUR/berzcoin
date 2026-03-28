"""Wallet RPC handlers (private-key wallet model)."""

from typing import Any, Dict, List, Optional

from node.wallet.core.tx_builder import TransactionBuilder
from node.wallet.simple_wallet import SimpleWalletManager
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


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
            )
            setattr(self.node, "simple_wallet_manager", manager)
        return manager

    def _active_wallet(self):
        return self._manager().get_active_wallet()

    async def get_wallet_info(self) -> Dict[str, Any]:
        wallet = self._active_wallet()
        if not wallet:
            return {"active": False}

        balance_sats = int(self.node.chainstate.get_balance(wallet.address))
        return {
            "active": True,
            "walletname": "simple",
            "walletversion": 1,
            "private_key": wallet.private_key_hex,
            "public_key": wallet.public_key_hex,
            "address": wallet.address,
            "seed_phrase": wallet.mnemonic,
            "balance": balance_sats / 100000000,
            "satoshis": balance_sats,
            "private_keys_enabled": True,
            "scanning": False,
        }

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
        wallet = self._manager().create_wallet()
        self._manager().active_wallet = wallet
        self._manager().active_private_key = wallet.private_key_hex
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

        satoshis = int(amount * 100000000)
        if satoshis <= 0:
            raise ValueError("Amount must be positive")

        utxos = self.node.chainstate.get_utxos_for_address(wallet.address, 1000)
        if not utxos:
            raise ValueError("No UTXOs found")
        best_height = int(self.node.chainstate.get_best_height())
        maturity = int(getattr(self.node.chainstate.params, "coinbase_maturity", 100))
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
        # Legacy estimate retained for compatibility with existing tests/UX.
        target_fee = max(10 + 150 + 34, min_relay_fee)

        selected = []
        selected_amount = 0
        for utxo in spendable_utxos:
            selected.append(utxo)
            selected_amount += int(utxo.get("value", 0))
            # Ensure selected inputs can cover amount plus baseline fee estimate.
            if selected_amount >= satoshis + target_fee:
                break

        if selected_amount < satoshis + target_fee:
            extra = ""
            if immature_sats > 0:
                extra = f" ({immature_sats / 100000000:.8f} BERZ immature coinbase)"
            raise ValueError(f"Insufficient spendable funds{extra}")

        builder = TransactionBuilder(self.node.config.get("network", "mainnet"))
        inputs = [(u["txid"], int(u["index"]), int(u["value"])) for u in selected]
        outputs = [(address, satoshis)]
        tx = builder.create_transaction(inputs, outputs, wallet.address, fee=target_fee)

        private_key = PrivateKey(int(wallet.private_key_hex, 16))
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
        wallet_addrs = [wallet.address]
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

    async def get_address_info(self, address: str) -> Dict[str, Any]:
        wallet = self._active_wallet()
        is_mine = bool(wallet and wallet.address == address)
        return {
            "address": address,
            "ismine": is_mine,
            "iswatchonly": False,
            "isscript": False,
            "iswitness": address.startswith("bc1") or address.startswith("tb1") or address.startswith("bcrt1"),
            "label": "",
            "timestamp": int(wallet.created_at) if is_mine else 0,
        }
