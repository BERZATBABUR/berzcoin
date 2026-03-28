# Mining Behavior (Current v0.1)

Mining implementation:
- `node/mining/miner.py`
- `node/mining/block_assembler.py`
- `node/mining/difficulty.py`
- Consensus subsidy/PoW in `shared/consensus/`.

## Target Block Timing

- Consensus target spacing is 120 seconds (2 minutes).
- Difficulty retargeting adjusts via configured timespan/interval rules.
- Miners do not sleep for 2 minutes; they mine continuously and retarget keeps long-run average near target.

## Reward Model

For each mined block:
- Coinbase reward = `subsidy(height) + sum(included_tx_fees)`.
- Coinbase output is created for the configured mining address.

## Miner Address Rules

- Mining requires `miningaddress` to be set.
- Current node guard can require mining address to match active wallet identity.
- If guard fails, mining start is denied or mining auto-stops.

## Transaction Selection

- Candidate transactions are selected from mempool by fee rate/weight constraints.
- Block weight cap is enforced by consensus params.

## RPC Controls

- `setgenerate true|false --threads N`
- `getminingstatus`
- `setminingaddress <address>`
- `generate <n>`

## Operational Note

Public-network mining economics/security are not yet Bitcoin Core parity. Use regtest for deterministic local validation and integration workflows.
