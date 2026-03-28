# Wallet API (Private-Key Model, v0.1)

## Model

BerzCoin v0.1 uses a private-key activation model:
- No username/password lock-unlock flow is required for active wallet identity.
- Wallet identity is derived from the provided private key.

Primary implementation:
- `node/wallet/simple_wallet.py`
- RPC handlers in `node/rpc/handlers/wallet.py` and `wallet_control.py`.

## Canonical Activation

- `activatewallet <private_key_hex>`

Activation loads wallet identity and enables operations that require an active wallet, including guarded mining address workflows.

## Key RPC Methods

- `createwallet`
- `listwallets`
- `loadwallet <private_key_hex>`
- `activatewallet <private_key_hex>`
- `get_wallet_info`
- `get_balance`
- `get_new_address`
- `list_unspent`
- `send_to_address <address> <amount>`

## Wallet Data and Responsibility

- Node-side wallet state is application-managed in datadir.
- Users are responsible for backing up private keys/seed material.
- Loss of key material means loss of spend authority.

## Current Limits vs Bitcoin Core Parity

Not yet fully parity-level:
- Descriptor-first wallet format compatibility.
- Full script-type coverage and signing matrix parity.
- Production-grade backup/recovery UX conventions.
