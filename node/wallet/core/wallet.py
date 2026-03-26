"""Main wallet logic."""

import time
from typing import Any, Dict, List, Optional

from shared.core.transaction import Transaction
from shared.crypto.signatures import sign_message_hash
from shared.utils.logging import get_logger
from node.wallet.storage.wallet_file import WalletFile
from .account import Account, AccountManager
from .keystore import KeyStore
from .utxo_tracker import UTXOTracker
from .tx_builder import TransactionBuilder
from .coin_selection import CoinSelector

logger = get_logger()


class Wallet:
    """Main wallet class."""

    def __init__(self, wallet_path: str, network: str = "mainnet", chainstate: Any = None, mempool: Any = None):
        """Initialize wallet.

        Args:
            wallet_path: Path to wallet file
            network: Network (mainnet, testnet, regtest)
            chainstate: Chain state for UTXO queries (optional, can be set later)
            mempool: Mempool for transaction broadcasting (optional, can be set later)
        """
        self.wallet_path = wallet_path
        self.network = network
        self.chainstate = chainstate
        self.mempool = mempool

        self.storage = WalletFile(wallet_path)
        self.keystore = KeyStore(wallet_path, network=network)
        self.account_manager = AccountManager()
        self.utxo_tracker = UTXOTracker()
        self.tx_builder = TransactionBuilder(network)
        self.coin_selector = CoinSelector()

        self.is_loaded = False
        self.locked = True
        self._encryption_password: Optional[str] = None

    def create(self, password: Optional[str] = None) -> str:
        """Create new wallet; data is encrypted on disk when saved."""
        if not password:
            raise ValueError("Password required for wallet creation")

        mnemonic = self.keystore.create_master_key(password)

        self.account_manager.create_account("default")
        self.keystore.get_unused_address()

        self._encryption_password = password
        self.is_loaded = True
        self.locked = True

        self._save()

        logger.info("Created encrypted wallet: %s", self.wallet_path)
        return mnemonic

    def load(self, password: Optional[str] = None) -> bool:
        """Load wallet from disk using the encryption password."""
        if not password:
            raise ValueError("Password required to load wallet")

        data = self.storage.load(password)
        if not data:
            logger.error("Failed to load wallet")
            return False

        if not self._apply_persisted_data(data, password):
            logger.error("Failed to restore wallet state from file")
            return False

        self._encryption_password = password
        self.is_loaded = True
        self.locked = True
        logger.info("Loaded encrypted wallet: %s", self.wallet_path)
        return True

    def _serialize_state(self) -> Dict[str, Any]:
        accounts: list[Dict[str, Any]] = []
        for name, acc in self.account_manager.accounts.items():
            accounts.append(
                {
                    "name": acc.name,
                    "index": acc.index,
                    "balance": acc.balance,
                    "created_at": acc.created_at,
                    "transactions": list(acc.transactions),
                }
            )
        return {
            "network": self.network,
            "mnemonic": self.keystore.mnemonic,
            "accounts": accounts,
            "default_account": self.account_manager.default_account,
        }

    def _apply_persisted_data(self, data: Dict[str, Any], password: str) -> bool:
        mnemonic = data.get("mnemonic")
        if not mnemonic:
            return False

        self.network = data.get("network", self.network)
        if not self.keystore.import_mnemonic(mnemonic, password):
            return False

        self.account_manager = AccountManager()
        for acc in data.get("accounts", []):
            self.account_manager.accounts[acc["name"]] = Account(
                name=acc["name"],
                index=acc["index"],
                balance=acc.get("balance", 0),
                created_at=acc.get("created_at", int(time.time())),
                transactions=list(acc.get("transactions", [])),
            )
        self.account_manager.default_account = data.get("default_account")
        if not self.account_manager.accounts:
            self.account_manager.create_account("default")
        return True

    def _save(self) -> None:
        """Persist wallet to disk (encrypted)."""
        if not self._encryption_password:
            logger.warning("Cannot save wallet without encryption password")
            return
        payload = self._serialize_state()
        self.storage.save(payload, self._encryption_password)

    def unlock(self, password: str) -> bool:
        """Unlock wallet for signing and balance queries."""
        if self._encryption_password is None:
            logger.warning("Wallet has no encryption context")
            return False
        if password != self._encryption_password:
            logger.warning("Invalid wallet password")
            return False
        self.locked = False
        logger.info("Wallet unlocked")
        return True

    def lock(self) -> None:
        """Lock wallet."""
        self.locked = True
        logger.info("Wallet locked")

    def get_balance(self, account: Optional[str] = None) -> int:
        """Get wallet balance from tracked UTXOs (satoshis).

        Note: `UTXOTracker` must be populated (scanner / chain events).
        """
        if self.locked:
            logger.warning("Wallet is locked")
            return 0

        addresses = list(self.keystore.keys.keys())
        if not addresses:
            return 0

        total = 0
        for address in addresses:
            for utxo in self.utxo_tracker.get_utxos_for_address(
                address, include_spent=False
            ):
                if not utxo.spent:
                    total += int(utxo.amount)

        # Keep account balance in sync (best-effort).
        acc_name = account
        if not acc_name:
            default_acc = self.account_manager.get_default_account()
            acc_name = default_acc.name if default_acc else "default"
        if not self.account_manager.get_account(acc_name):
            self.account_manager.create_account(acc_name)
        acc_obj = self.account_manager.get_account(acc_name)
        if acc_obj:
            delta = total - int(acc_obj.balance)
            if delta:
                self.account_manager.update_balance(acc_name, delta)

        return total

    def get_new_address(self, account: Optional[str] = None, label: str = "") -> Optional[str]:
        """Get new receiving address."""
        _ = label
        if self.locked:
            logger.warning("Wallet is locked")
            return None

        address = self.keystore.get_unused_address()
        return address

    async def send_to_address(self, address: str, amount: int, fee_rate: Optional[int] = None,
                              account: str = None) -> Optional[str]:
        """Async send: gather UTXOs, build, sign, and broadcast transaction.

        This implementation prefers using the chainstate UTXO set and the mempool
        to filter spent outputs, performs simple fee estimation when needed,
        and returns the hex txid on success.
        """
        if self.locked:
            logger.warning("Wallet is locked")
            return None

        # Gather spendable UTXOs from chainstate (includes maturity checks)
        utxos = self._get_spendable_utxos(account)
        if not utxos:
            logger.error("No spendable UTXOs found")
            return None

        # Filter out outputs already tracked as spent in the mempool
        filtered = []
        for u in utxos:
            txid = u['txid']
            vout = u['vout']
            if self.mempool:
                spent_map = getattr(self.mempool, 'spent_utxos', {})
                if txid in spent_map and vout in spent_map[txid]:
                    continue
            filtered.append(u)

        if not filtered:
            logger.error("All available UTXOs are already spent (mempool)")
            return None

        # Simple coin selection: smallest-first until amount covered
        selected = []
        total = 0
        for u in sorted(filtered, key=lambda x: x['amount']):
            selected.append(u)
            total += u['amount']
            if total >= amount:
                break

        if total < amount:
            logger.error("Insufficient funds after filtering mempool-spent outputs")
            return None

        # Prepare builder inputs/outputs
        inputs = [(s['txid'], s['vout'], s['amount']) for s in selected]
        outputs = [(address, amount)]
        change_addr = self.get_new_address()

        # Fee handling: derive fee from fee_rate or mempool estimator
        fee: Optional[int] = None
        if fee_rate is not None:
            # fee_rate provided in sat/vbyte; approximate size then multiply
            est_size = 10 + len(inputs) * 150 + (len(outputs) + 1) * 34
            fee = int(fee_rate * est_size)
        elif self.mempool and getattr(self.mempool, 'fee_calculator', None):
            # Use mempool fee estimate (sats per vbyte)
            est_rate = self.mempool.fee_calculator.get_fee_estimate(6)
            est_size = 10 + len(inputs) * 150 + (len(outputs) + 1) * 34
            fee = int(est_rate * est_size)

        try:
            tx = self.tx_builder.create_transaction(inputs=inputs, outputs=outputs, change_address=change_addr, fee=fee)
        except Exception as e:
            logger.error(f"Failed to create transaction: {e}")
            return None

        # Debug: log transaction construction details for troubleshooting
        try:
            logger.debug(f"TX build: inputs={inputs}, outputs={outputs}, change={change_addr}, fee={fee}, total_in={total}")
            logger.debug(f"TX raw (hex): {tx.serialize(include_witness=True).hex()}")
        except Exception:
            logger.debug("Failed to serialize tx for debug output")

        # Sign transaction
        ok = await self._sign_transaction_v2(tx)
        if not ok:
            logger.error("Failed to sign transaction")
            return None

        # Broadcast to mempool
        if self.mempool:
            added = await self.mempool.add_transaction(tx)
            if not added:
                logger.error("Mempool rejected transaction")
                return None

        txid = tx.txid().hex()
        logger.info(f"Sent {amount} satoshis to {address[:16]}... (txid: {txid[:16]})")

        # Mark UTXOs spent in tracker
        for s in selected:
            self.utxo_tracker.spend_utxo(s['txid'], s['vout'], txid)

        return txid

    def _sign_transaction(self, tx: Transaction) -> bool:
        """Sign transaction inputs."""
        for i, txin in enumerate(tx.vin):
            prev_hex = txin.prev_tx_hash.hex() if isinstance(txin.prev_tx_hash, (bytes, bytearray)) else str(txin.prev_tx_hash)
            utxo = self.utxo_tracker.get_utxo(prev_hex, txin.prev_tx_index)
            if not utxo:
                continue

            address = utxo.address
            private_key = self.keystore.get_private_key(address)
            if not private_key:
                continue

            sighash = self._create_sighash(tx, i, utxo.script_pubkey)
            der_sig = sign_message_hash(private_key, sighash)

            txin.script_sig = der_sig + bytes([0x01])

        return True

    def _create_sighash(self, tx: Transaction, input_index: int, script_pubkey: bytes) -> bytes:
        """Create signature hash."""
        _ = input_index, script_pubkey
        from shared.core.hashes import hash256
        return hash256(tx.serialize())

    async def _sign_transaction_v2(self, tx: Transaction) -> bool:
        """Async signing helper used by the async send flow.

        Signs each input using the keystore. This is a simplified signing
        approach suitable for basic P2PKH/P2WPKH outputs stored in the keystore.
        """
        for i, txin in enumerate(tx.vin):
            prev_txid = txin.prev_tx_hash.hex() if isinstance(txin.prev_tx_hash, (bytes, bytearray)) else str(txin.prev_tx_hash)
            prev_index = txin.prev_tx_index
            utxo = self.utxo_tracker.get_utxo(prev_txid, prev_index)
            if not utxo:
                logger.warning(f"UTXO not found for signing: {prev_txid}:{prev_index}")
                return False

            addr = utxo.address
            priv = self.keystore.get_private_key(addr)
            if not priv:
                logger.warning(f"Private key not available for address {addr}")
                return False

            sighash = self._create_sighash(tx, i, utxo.script_pubkey)
            der_sig = sign_message_hash(priv, sighash)
            txin.script_sig = der_sig + bytes([0x01])

        return True
    
    def _get_spendable_utxos(self, account: str = None) -> List[Dict]:
        """Get spendable UTXOs - FIXED to return actual UTXOs."""
        addresses = list(self.keystore.keys.keys())
        spendable = []
        
        for address in addresses:
            utxos = self.chainstate.get_utxos_for_address(address, 1000)
            for utxo in utxos:
                # Check if UTXO is mature (coinbase needs 100 confirmations)
                height = utxo.get('height', 0)
                confirmations = self.chainstate.get_best_height() - height + 1
                is_coinbase = utxo.get('is_coinbase', False)
                
                if is_coinbase and confirmations < 100:
                    continue  # Coinbase not mature yet
                
                spendable.append({
                    'txid': utxo['txid'],
                    'vout': utxo['index'],  # Database column is "index", not "vout"
                    'amount': utxo['value'],
                    'address': address,
                    'script_pubkey': utxo['script_pubkey']
                })
        
        return spendable

    def get_info(self) -> Dict[str, Any]:
        """Get wallet information."""
        return {
            'loaded': self.is_loaded,
            'locked': self.locked,
            'network': self.network,
            'path': self.wallet_path,
            'balance': self.get_balance(),
            'account_summary': self.account_manager.get_account_summary(),
            'keystore_stats': self.keystore.get_stats()
        }
