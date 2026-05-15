# Testing Policy

## Determinism Requirements
- Tests must be deterministic by default.
- Use fixed seeds (`BERZ_TEST_SEED`, fuzz/chaos seed envs).
- Use fixed test keys/fixtures; avoid ad-hoc random keys unless seed-controlled.
- Prefer fixed block timestamps in fixtures when consensus logic allows it.
- Avoid uncontrolled `time.sleep(...)`. If real waiting is required, mark test with `@pytest.mark.allow_sleep`.

## Regression-Test Rule
- Every fixed consensus/storage/security bug must add a named regression test.
- Test name should clearly state the prevented bug (example: `test_regression_invalid_pow_does_not_mutate_utxo_state`).
- Add `@pytest.mark.regression` to dedicated regression tests when possible.

## Test Tiers
- `unit`: fast deterministic local logic tests
- `integration`: component interaction tests
- `e2e`: full flow tests
- `fuzz`: adversarial randomized parser/state tests (seeded)
- `chaos`: long-run fault and instability simulations (seeded)
- `slow`: timing/process/network-heavy tests

## Coverage Gates
- Global minimum coverage must pass (`--cov-fail-under`).
- Critical-module minimums are enforced in CI (consensus, validation, UTXO/storage, script, chain).

## Flaky-Test Protection
- CI runs `scripts/ci/check_flaky_patterns.py` to catch common nondeterministic patterns.
- Real-time/process tests must be marked (`slow`, `allow_sleep`) and isolated in appropriate jobs.
