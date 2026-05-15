#!/usr/bin/env bash
set -euo pipefail

tier="${1:-unit}"

case "${tier}" in
  unit)
    pytest -m "unit and not slow" tests/unit
    ;;
  integration)
    pytest -m "integration" tests/integration
    ;;
  e2e)
    pytest -m "e2e" tests/e2e
    ;;
  fuzz)
    pytest -m "fuzz" tests/fuzz
    ;;
  chaos)
    pytest -m "chaos" tests/chaos
    ;;
  security)
    pytest tests/integration/test_security_regressions.py tests/fuzz
    ;;
  slow)
    pytest -m "slow" tests
    ;;
  all)
    pytest tests
    ;;
  *)
    echo "usage: $0 {unit|integration|e2e|fuzz|chaos|security|slow|all}" >&2
    exit 2
    ;;
esac
