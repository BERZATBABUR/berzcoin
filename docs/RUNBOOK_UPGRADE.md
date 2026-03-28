# BerzCoin Upgrade Runbook

## Goal
Upgrade node binary/code with rollback safety.

## Pre-upgrade
1. Confirm current health/readiness are green.
2. Snapshot datadir and wallet backups.
3. Capture current version + tip:
   - `get_info`
   - `get_best_block_hash`

## Upgrade steps
1. Stop node cleanly: RPC `stop` (or service stop).
2. Deploy new code from pinned release SHA.
3. Reinstall dependencies if lockfile or requirements changed.
4. Start node.
5. Validate:
   - `GET /health` returns `status=ok`
   - `GET /ready` is true
   - `get_best_block_hash` continues from previous tip lineage

## Rollback
1. Stop upgraded node.
2. Restore previous release artifacts.
3. Restore pre-upgrade datadir snapshot if chainstate migration failed.
4. Start prior release and verify health/readiness again.
