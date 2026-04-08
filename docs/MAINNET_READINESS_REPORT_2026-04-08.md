# Mainnet Readiness Report (2026-04-08)

Timestamp: **2026-04-08 09:48:50 UTC**

## Scope
- Reorg-safe index rollback for `tx_index` / `address_index`
- Integration and e2e coverage for sync/reorg/mempool/restart paths
- Soak/chaos execution
- Security regression pass (RPC, wallet key handling, ban/DoS protections)
- Release checklist evidence update

## Code changes
- Reorg index reconciliation added in:
  - `node/app/main.py`
    - `_deindex_disconnected_block`
    - `_reconcile_indexes_after_reorg`
    - invoked after successful reorg in `on_block`
- Address index deterministic rebuild added in:
  - `node/indexer/addressindex.py`
    - `rebuild_from_tx_index()`
- New regression tests:
  - `tests/unit/node/test_indexer_reorg_rollback.py`

## Test evidence
- Unit full suite:
  - `pytest -q tests/unit -q` -> **PASS**
- Integration full suite:
  - `pytest -q tests/integration -q` -> **PASS** (some opt-in tests skipped by design)
- e2e suite:
  - `pytest -q tests/e2e -q` -> **PASS**
- Focused reorg/index/security gates:
  - `pytest -q tests/unit/node/test_indexer_reorg_rollback.py tests/unit/node/test_indexer_wiring.py tests/unit/node/test_reorg_manager.py tests/integration/test_reorg_activation_boundary.py -q` -> **PASS**
  - `pytest -q tests/unit/node/test_peer_scoring_hardening.py tests/unit/node/test_relay_hardening.py tests/unit/node/test_connman_hardening.py tests/unit/rpc/test_wallet_advanced_security.py tests/unit/rpc/test_wallet_private_key_activation.py tests/unit/rpc/test_activatewallet_rpc.py -q` -> **PASS**
  - `pytest -q tests/unit/node/test_mempool_persistence_restart.py -q` -> **PASS**

## Soak/chaos evidence
- Fault injection soak (accelerated run):
  - `BERZ_SOAK=1 BERZ_SOAK_ITERS=200 pytest -q tests/integration/test_fault_injection_soak.py -q` -> **PASS**
- Long chaos run (accelerated):
  - `BERZ_CHAOS_LONG=1 BERZ_CHAOS_LONG_STEPS=1200 pytest -q tests/chaos/test_network_chaos_suite.py::TestNetworkChaosSuite::test_chaos_long_run -q` -> **PASS**

## Security pass outcome
- RPC exposure checks validated through existing regression tests and config defaults (`rpcbind`, `rpcallowip`, auth cookie/user checks).
- Wallet key handling checks validated through wallet advanced/private-key activation tests.
- Ban/DoS and relay hardening validated through peer scoring, relay, and connman hardening tests.
- No new critical/high security defects were found in this pass.

## Remaining launch requirement (cannot be fully closed in local sandbox)
- Public-node real-network soak for **24–72h** with live peer churn and DNS/bootstrap failures still requires running on internet-connected infrastructure.
- This report includes accelerated deterministic soak coverage, but not multi-day public-net runtime.

## Release decision
- **Staging/Pre-mainnet: READY**
- **Production mainnet launch: READY after successful 24–72h public-network soak and sign-off**
