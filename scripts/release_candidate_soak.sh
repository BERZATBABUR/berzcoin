#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOURS="${SOAK_HOURS:-24}"
ITERATIONS="${SOAK_ITERATIONS:-0}"
ARTIFACT_BASE="${SOAK_ARTIFACT_BASE:-$ROOT_DIR/artifacts/rc-soak}"
RC_TAG="${SOAK_RC_TAG:-v2.0.0-rc0}"
SEED_MODE="${SOAK_SEED_MODE:-rotating}" # rotating | fixed
FIXED_SEED_BASE="${SOAK_FIXED_SEED_BASE:-20260405}"
FUZZ_SAMPLES="${SOAK_FUZZ_SAMPLES:-4000}"
MEMPOOL_FUZZ_SAMPLES="${SOAK_MEMPOOL_FUZZ_SAMPLES:-2200}"
CHAOS_STEPS="${SOAK_CHAOS_STEPS:-1200}"
MEMPOOL_CHAOS_STEPS="${SOAK_MEMPOOL_CHAOS_STEPS:-2200}"
CHAOS_LONG_STEPS="${SOAK_CHAOS_LONG_STEPS:-6000}"
INTEG_STEPS="${SOAK_INTEG_STEPS:-900}"
MEMPOOL_MAX_VSIZE_BOUND="${SOAK_MEMPOOL_MAX_VSIZE_BOUND:-300000}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hours)
      HOURS="$2"
      shift 2
      ;;
    --rc-tag)
      RC_TAG="$2"
      shift 2
      ;;
    --artifact-base)
      ARTIFACT_BASE="$2"
      shift 2
      ;;
    --iterations)
      ITERATIONS="$2"
      shift 2
      ;;
    --seed-mode)
      SEED_MODE="$2"
      shift 2
      ;;
    --fixed-seed-base)
      FIXED_SEED_BASE="$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

START_TS="$(date -u +"%Y%m%dT%H%M%SZ")"
RUN_TMP_DIR="${ARTIFACT_BASE}/${RC_TAG}/${START_TS}-to-running"
RUN_DIR="${RUN_TMP_DIR}"
mkdir -p "$RUN_DIR"
LOG_FILE="${RUN_DIR}/soak.log"
SUMMARY_FILE="${RUN_DIR}/summary.txt"
PROCESS_EVENTS_FILE="${RUN_DIR}/process_events.jsonl"

END_EPOCH="$(( $(date +%s) + HOURS * 3600 ))"
ITER=0
FAILURES=0

{
  echo "RC soak start (UTC): ${START_TS}"
  echo "Root: ${ROOT_DIR}"
  echo "RC tag: ${RC_TAG}"
  echo "Hours: ${HOURS}"
  echo "Iterations mode: ${ITERATIONS}"
  echo "Seed mode: ${SEED_MODE}"
  echo "End epoch: ${END_EPOCH}"
  echo
} | tee -a "$SUMMARY_FILE" >>"$LOG_FILE"

while :; do
  if [[ "${ITERATIONS}" -gt 0 && "${ITER}" -ge "${ITERATIONS}" ]]; then
    break
  fi
  if [[ "${ITERATIONS}" -le 0 && "$(date +%s)" -ge "${END_EPOCH}" ]]; then
    break
  fi
  ITER="$((ITER + 1))"
  ITER_DIR="${RUN_DIR}/iter-${ITER}"
  mkdir -p "${ITER_DIR}"
  NOW="$(date -u +"%Y%m%dT%H%M%SZ")"
  if [[ "${SEED_MODE}" == "fixed" ]]; then
    SEED_BASE="$(( FIXED_SEED_BASE + ITER ))"
  else
    SEED_BASE="$(( $(date +%s) + ITER ))"
  fi

  {
    echo "---- ITER ${ITER} @ ${NOW} ----"
    echo "seed_base=${SEED_BASE}"
  } | tee -a "$SUMMARY_FILE" >>"$LOG_FILE"

  set +e
  BERZ_TEST_ARTIFACT_DIR="${ITER_DIR}/artifacts" \
  BERZ_FUZZ_SAMPLES="${FUZZ_SAMPLES}" \
  BERZ_FUZZ_SEED="${SEED_BASE}" \
  BERZ_MEMPOOL_FUZZ_SAMPLES="${MEMPOOL_FUZZ_SAMPLES}" \
  BERZ_MEMPOOL_FUZZ_SEED="$((SEED_BASE + 1))" \
  BERZ_CHAOS_SEED="$((SEED_BASE + 2))" \
  BERZ_CHAOS_STEPS="${CHAOS_STEPS}" \
  BERZ_MEMPOOL_CHAOS_SEED="$((SEED_BASE + 3))" \
  BERZ_MEMPOOL_CHAOS_STEPS="${MEMPOOL_CHAOS_STEPS}" \
  BERZ_CHAOS_LONG=1 \
  BERZ_CHAOS_LONG_STEPS="${CHAOS_LONG_STEPS}" \
  BERZ_CHAOS_INTEG_SEED="$((SEED_BASE + 4))" \
  BERZ_CHAOS_INTEG_STEPS="${INTEG_STEPS}" \
  pytest -q tests/fuzz tests/chaos tests/integration/test_chaos_regression.py \
    --junitxml "${ITER_DIR}/junit.xml" >>"$LOG_FILE" 2>&1
  RC="$?"
  set -e

  printf '{"iter":%s,"timestamp":"%s","pytest_exit_code":%s,"runner_restarts_detected":0}\n' \
    "${ITER}" "${NOW}" "${RC}" >> "${PROCESS_EVENTS_FILE}"

  echo "iter=${ITER} rc=${RC}" | tee -a "$SUMMARY_FILE" >>"$LOG_FILE"
  if [[ "${RC}" -ne 0 ]]; then
    FAILURES="$((FAILURES + 1))"
    echo "Stopping soak due to failure in iter ${ITER}" | tee -a "$SUMMARY_FILE" >>"$LOG_FILE"
    break
  fi
done

END_TS="$(date -u +"%Y%m%dT%H%M%SZ")"
FINAL_RUN_DIR="${ARTIFACT_BASE}/${RC_TAG}/${START_TS}-to-${END_TS}"
mkdir -p "${ARTIFACT_BASE}/${RC_TAG}"
mv "${RUN_TMP_DIR}" "${FINAL_RUN_DIR}"
RUN_DIR="${FINAL_RUN_DIR}"
LOG_FILE="${RUN_DIR}/soak.log"
SUMMARY_FILE="${RUN_DIR}/summary.txt"

# Validate mandatory artifact set and policy bounds.
python scripts/validate_soak_artifacts.py \
  --run-dir "${RUN_DIR}" \
  --max-mempool-vsize "${MEMPOOL_MAX_VSIZE_BOUND}" >>"${LOG_FILE}" 2>&1 || FAILURES="$((FAILURES + 1))"

ARCHIVE="${ARTIFACT_BASE}/${RC_TAG}/${START_TS}-to-${END_TS}.tar.gz"
tar -czf "${ARCHIVE}" -C "${ARTIFACT_BASE}/${RC_TAG}" "$(basename "${RUN_DIR}")"

{
  echo
  echo "RC soak end (UTC): ${END_TS}"
  echo "Iterations: ${ITER}"
  echo "Failures: ${FAILURES}"
  echo "Run directory: ${RUN_DIR}"
  echo "Archive: ${ARCHIVE}"
} | tee -a "$SUMMARY_FILE" >>"$LOG_FILE"

if [[ "${FAILURES}" -gt 0 ]]; then
  exit 1
fi
