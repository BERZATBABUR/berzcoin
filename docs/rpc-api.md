# BerzCoin RPC API (Current v0.1)

RPC server implementation:
- `node/rpc/server.py`
- Handler wiring in `node/app/main.py`.

## Endpoint and Auth

- Transport: HTTP JSON-RPC 2.0 on configured `rpcbind:rpcport`.
- Health/readiness/metrics HTTP endpoints are also exposed by RPC server.
- Access controls:
  - `rpcallowip` IP filtering.
  - HTTP Basic auth using cookie/auth manager.

## Core Methods

Control:
- `get_info`
- `stop`
- `help`
- `get_network_info`
- `ping`
- `uptime`
- `get_health`
- `get_readiness`
- `get_metrics`

Blockchain:
- `get_blockchain_info`
- `get_block`
- `get_block_count`
- `get_best_block_hash`

Mempool:
- `get_mempool_info`
- `get_mempool_diagnostics`
- `get_raw_mempool`
- `send_raw_transaction`
- `submit_package`

Wallet (private-key model):
- `get_wallet_info`
- `get_balance`
- `get_new_address`
- `send_to_address`
- `createwallet`
- `loadwallet`
- `listwallets`
- `activatewallet`

Mining:
- `get_mining_info`
- `get_block_template`
- `submit_block`
- `generate`
- `setgenerate`
- `getminingstatus`
- `setminingaddress`

## Notes

- Method names are currently mixed (snake_case and legacy bitcoin-style names).
- `activatewallet` is the canonical wallet-activation path for v0.1 private-key model.
- Setgenerate-style background mining is intended mainly for regtest workflows.
