#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

STAGE="${FIELD_STAGE:-A}" # A|B|C
ARTIFACT_BASE="${FIELD_ARTIFACT_BASE:-${ROOT_DIR}/artifacts/field-validation}"
PROFILES_PATH="${FIELD_PROFILES_PATH:-${ROOT_DIR}/configs/field_validation_profiles.json}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      cat <<'USAGE'
Usage: scripts/run_field_validation_stage.sh [--stage A|B|C] [--artifact-base DIR] [--profiles FILE]
USAGE
      exit 0
      ;;
    --stage)
      STAGE="$2"
      shift 2
      ;;
    --artifact-base)
      ARTIFACT_BASE="$2"
      shift 2
      ;;
    --profiles)
      PROFILES_PATH="$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "${STAGE}" != "A" && "${STAGE}" != "B" && "${STAGE}" != "C" ]]; then
  echo "stage must be A, B, or C" >&2
  exit 2
fi

export FIELD_STAGE="${STAGE}"
export FIELD_PROFILES_PATH="${PROFILES_PATH}"

eval "$(python - <<'PY'
import json
import os
from pathlib import Path

stage = os.environ["FIELD_STAGE"]
profiles_path = Path(os.environ["FIELD_PROFILES_PATH"])
cfg = json.loads(profiles_path.read_text(encoding="utf-8"))
stages = cfg.get("stages", {})
if stage not in stages:
    raise SystemExit(f"missing stage profile {stage}")
p = stages[stage]
print(f'FIELD_NODES="{int(p.get("nodes", 5))}"')
print(f'FIELD_CHAOS_STEPS="{int(p.get("chaos_steps", 1200))}"')
print(f'FIELD_MEMPOOL_CHAOS_STEPS="{int(p.get("mempool_chaos_steps", 2200))}"')
print(f'FIELD_CHAOS_LONG_STEPS="{int(p.get("chaos_long_steps", 6000))}"')
print(f'FIELD_INTEGRATION_STEPS="{int(p.get("integration_steps", 900))}"')
print(f'FIELD_FAULT_SOAK_ITERS="{int(p.get("fault_soak_iters", 400))}"')
PY
)"

START_TS="$(date -u +"%Y%m%dT%H%M%SZ")"
RUN_TMP_DIR="${ARTIFACT_BASE}/${STAGE}/${START_TS}-to-running"
RUN_DIR="${RUN_TMP_DIR}"
mkdir -p "${RUN_DIR}/artifacts"
LOG_FILE="${RUN_DIR}/field.log"
SUMMARY_FILE="${RUN_DIR}/summary.txt"

{
  echo "Field validation stage: ${STAGE}"
  echo "Nodes target: ${FIELD_NODES}"
  echo "Start UTC: ${START_TS}"
} | tee -a "${SUMMARY_FILE}" >>"${LOG_FILE}"

set +e
BERZ_TEST_ARTIFACT_DIR="${RUN_DIR}/artifacts" \
BERZ_CHAOS_SEED=20260405 \
BERZ_CHAOS_STEPS="${FIELD_CHAOS_STEPS}" \
BERZ_CHAOS_PEER_COUNT="${FIELD_NODES}" \
BERZ_MEMPOOL_CHAOS_SEED=20260407 \
BERZ_MEMPOOL_CHAOS_STEPS="${FIELD_MEMPOOL_CHAOS_STEPS}" \
BERZ_CHAOS_LONG=1 \
BERZ_CHAOS_LONG_STEPS="${FIELD_CHAOS_LONG_STEPS}" \
BERZ_CHAOS_INTEG_SEED=20260406 \
BERZ_CHAOS_INTEG_STEPS="${FIELD_INTEGRATION_STEPS}" \
BERZ_SOAK=1 \
BERZ_SOAK_ITERS="${FIELD_FAULT_SOAK_ITERS}" \
BERZ_SOAK_SEED=1337 \
pytest -q tests/chaos tests/integration/test_chaos_regression.py tests/integration/test_fault_injection_soak.py \
  --junitxml "${RUN_DIR}/junit.xml" >>"${LOG_FILE}" 2>&1
RC="$?"
set -e

if [[ "${RC}" -ne 0 ]]; then
  echo "pytest failed rc=${RC}" | tee -a "${SUMMARY_FILE}" >>"${LOG_FILE}"
fi

python scripts/evaluate_field_validation_slo.py \
  --run-dir "${RUN_DIR}" \
  --stage "${STAGE}" \
  --profiles "${PROFILES_PATH}" >>"${LOG_FILE}" 2>&1 || RC=1

END_TS="$(date -u +"%Y%m%dT%H%M%SZ")"
FINAL_RUN_DIR="${ARTIFACT_BASE}/${STAGE}/${START_TS}-to-${END_TS}"
mkdir -p "${ARTIFACT_BASE}/${STAGE}"
mv "${RUN_TMP_DIR}" "${FINAL_RUN_DIR}"
RUN_DIR="${FINAL_RUN_DIR}"

ARCHIVE="${ARTIFACT_BASE}/${STAGE}/${START_TS}-to-${END_TS}.tar.gz"
tar -czf "${ARCHIVE}" -C "${ARTIFACT_BASE}/${STAGE}" "$(basename "${RUN_DIR}")"

{
  echo "End UTC: ${END_TS}"
  echo "Run dir: ${RUN_DIR}"
  echo "Archive: ${ARCHIVE}"
  echo "Exit code: ${RC}"
} | tee -a "${RUN_DIR}/summary.txt" >>"${RUN_DIR}/field.log"

exit "${RC}"
