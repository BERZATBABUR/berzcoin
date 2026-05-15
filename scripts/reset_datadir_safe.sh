#!/usr/bin/env bash
set -euo pipefail

NETWORK=""
DATADIR=""
CONFIRM="0"

usage() {
  cat <<'EOF'
Usage: scripts/reset_datadir_safe.sh --network <regtest|dev> --datadir <path> --confirm-reset

Safety:
- Only allows regtest/dev reset.
- Rejects mainnet/testnet by default.
- Requires --confirm-reset.
- Requires datadir marker file `.network` containing regtest/dev.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --network)
      NETWORK="${2:-}"; shift 2 ;;
    --datadir)
      DATADIR="${2:-}"; shift 2 ;;
    --confirm-reset)
      CONFIRM="1"; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage
      exit 2 ;;
  esac
done

if [[ -z "${NETWORK}" || -z "${DATADIR}" ]]; then
  echo "error: --network and --datadir are required" >&2
  usage
  exit 2
fi

case "${NETWORK}" in
  regtest|dev) ;;
  *)
    echo "error: reset is only allowed for regtest/dev (got: ${NETWORK})" >&2
    exit 3 ;;
esac

if [[ "${CONFIRM}" != "1" ]]; then
  echo "error: reset requires --confirm-reset" >&2
  exit 4
fi

TARGET="$(python3 - <<'PY' "${DATADIR}"
import os,sys
print(os.path.realpath(os.path.expanduser(sys.argv[1])))
PY
)"

if [[ ! -d "${TARGET}" ]]; then
  echo "error: datadir does not exist: ${TARGET}" >&2
  exit 5
fi

MARKER="${TARGET}/.network"
if [[ ! -f "${MARKER}" ]]; then
  echo "error: datadir marker missing: ${MARKER}" >&2
  exit 6
fi

MARKER_NET="$(tr -d '\r\n' < "${MARKER}" | tr '[:upper:]' '[:lower:]')"
if [[ "${MARKER_NET}" != "regtest" && "${MARKER_NET}" != "dev" ]]; then
  echo "error: datadir marker is not regtest/dev: ${MARKER_NET}" >&2
  exit 7
fi

# Guard against unsafe broad deletions.
if [[ "${TARGET}" == "/" || "${TARGET}" == "/home" || "${TARGET}" == "/root" ]]; then
  echo "error: refusing dangerous target path: ${TARGET}" >&2
  exit 8
fi

echo "Resetting datadir safely: ${TARGET}"
find "${TARGET}" -mindepth 1 -maxdepth 1 ! -name ".network" -exec rm -rf -- {} +
echo "OK: reset complete for ${TARGET}"

