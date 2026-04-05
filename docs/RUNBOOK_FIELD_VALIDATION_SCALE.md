# Field Validation at Scale Runbook

Purpose: validate network behavior at scale with staged topology and SLO gates.

## Stages

- Stage A: 5 nodes
- Stage B: 20 nodes
- Stage C: 50+ nodes

Stage profiles and SLO thresholds are defined in:
- `configs/field_validation_profiles.json`

## Weekly Scenario Set

Each weekly run executes:
- peer churn storms
- reorg under load
- double-spend and conflict storms
- restart storms

Implemented by:
- `tests/chaos/test_network_chaos_suite.py`
- `tests/integration/test_chaos_regression.py`
- `tests/integration/test_fault_injection_soak.py`

## SLO Gates

Per stage, enforce:
- tip convergence time (`tip_convergence_max_steps`)
- max reorg depth observed (`max_reorg_depth`)
- mempool memory ceiling (`peak_mempool_vsize`)
- reject-rate ceiling (`reject_rate`)

Plus hard safety:
- zero crashes
- no consensus divergence/drift

## How to Run

Single stage locally:
```bash
scripts/run_field_validation_stage.sh --stage A
scripts/run_field_validation_stage.sh --stage B
scripts/run_field_validation_stage.sh --stage C
```

Evaluate rolling window (2-4 weeks):
```bash
python scripts/assert_field_validation_window.py \
  --artifact-base artifacts/field-validation \
  --stage A \
  --min-runs 2 \
  --max-runs 4
```

Repeat for `B` and `C`.

## Artifacts

Stored with immutable naming:
- `artifacts/field-validation/<stage>/<start>-to-<end>/...`
- `artifacts/field-validation/<stage>/<start>-to-<end>.tar.gz`

Each run includes:
- `junit.xml`
- `field.log`
- `summary.txt`
- `field_validation_slo.json`
- `artifacts/chaos/*` metrics files

## Done Criteria

Field-validation is considered complete when:
- SLO gates pass for all stages (`A`, `B`, `C`)
- for a rolling 2-4 week window
- with no crash and no consensus drift/divergence.
