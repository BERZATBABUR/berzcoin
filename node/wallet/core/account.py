"""Account management for wallet."""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from shared.utils.logging import get_logger

logger = get_logger()

@dataclass
class Account:
    """Wallet account."""
    name: str
    index: int
    balance: int = 0
    created_at: int = 0
    transactions: List[str] = field(default_factory=list)  # txid list
    
    def __post_init__(self):
        if self.created_at == 0:
            self.created_at = int(time.time())

class AccountManager:
    """Account management for wallet."""
    
    def __init__(self):
        """Initialize account manager."""
        self.accounts: Dict[str, Account] = {}  # name -> Account
        self.default_account: Optional[str] = None
    
    def create_account(self, name: str) -> bool:
        """Create new account.
        
        Args:
            name: Account name
        
        Returns:
            True if created
        """
        if name in self.accounts:
            logger.warning(f"Account {name} already exists")
            return False
        
        index = len(self.accounts)
        self.accounts[name] = Account(
            name=name,
            index=index
        )
        
        if not self.default_account:
            self.default_account = name
        
        logger.info(f"Created account: {name}")
        return True
    
    def get_account(self, name: str) -> Optional[Account]:
        """Get account by name.
        
        Args:
            name: Account name
        
        Returns:
            Account or None
        """
        return self.accounts.get(name)
    
    def get_default_account(self) -> Optional[Account]:
        """Get default account.
        
        Returns:
            Default account or None
        """
        if self.default_account:
            return self.accounts.get(self.default_account)
        return None
    
    def set_default_account(self, name: str) -> bool:
        """Set default account.
        
        Args:
            name: Account name
        
        Returns:
            True if set
        """
        if name not in self.accounts:
            logger.warning(f"Account {name} not found")
            return False
        
        self.default_account = name
        logger.info(f"Default account: {name}")
        return True
    
    def update_balance(self, account_name: str, delta: int) -> None:
        """Update account balance.
        
        Args:
            account_name: Account name
            delta: Balance change
        """
        account = self.accounts.get(account_name)
        if account:
            account.balance += delta
    
    def add_transaction(self, account_name: str, txid: str) -> None:
        """Add transaction to account.
        
        Args:
            account_name: Account name
            txid: Transaction ID
        """
        account = self.accounts.get(account_name)
        if account:
            account.transactions.append(txid)
    
    def get_transactions(self, account_name: str, limit: int = 100) -> List[str]:
        """Get transactions for account.
        
        Args:
            account_name: Account name
            limit: Maximum number of transactions
        
        Returns:
            List of transaction IDs
        """
        account = self.accounts.get(account_name)
        if not account:
            return []
        
        return account.transactions[-limit:]
    
    def get_all_accounts(self) -> List[Account]:
        """Get all accounts.
        
        Returns:
            List of accounts
        """
        return list(self.accounts.values())
    
    def get_total_balance(self) -> int:
        """Get total balance across all accounts.
        
        Returns:
            Total balance in satoshis
        """
        return sum(acc.balance for acc in self.accounts.values())
    
    def get_account_summary(self) -> Dict[str, Any]:
        """Get account summary.
        
        Returns:
            Summary dictionary
        """
        return {
            'total_accounts': len(self.accounts),
            'default_account': self.default_account,
            'total_balance': self.get_total_balance(),
            'accounts': [
                {
                    'name': acc.name,
                    'balance': acc.balance,
                    'created_at': acc.created_at,
                    'transactions': len(acc.transactions)
                }
                for acc in self.accounts.values()
            ]
        }
    
    def delete_account(self, name: str) -> bool:
        """Delete account.
        
        Args:
            name: Account name
        
        Returns:
            True if deleted
        """
        if name not in self.accounts:
            return False
        
        if self.default_account == name:
            self.default_account = None
        
        del self.accounts[name]
        
        # Set new default if needed
        if not self.default_account and self.accounts:
            self.default_account = list(self.accounts.keys())[0]
        
        logger.info(f"Deleted account: {name}")
        return True
    
    def rename_account(self, old_name: str, new_name: str) -> bool:
        """Rename account.
        
        Args:
            old_name: Old account name
            new_name: New account name
        
        Returns:
            True if renamed
        """
        if old_name not in self.accounts:
            return False
        
        if new_name in self.accounts:
            logger.warning(f"Account {new_name} already exists")
            return False
        
        account = self.accounts.pop(old_name)
        account.name = new_name
        self.accounts[new_name] = account
        
        if self.default_account == old_name:
            self.default_account = new_name
        
        logger.info(f"Renamed account: {old_name} -> {new_name}")
        return True
