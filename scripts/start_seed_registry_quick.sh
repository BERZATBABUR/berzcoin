#!/usr/bin/env bash
# Quick seed registry launcher for local/LAN two-node demos.
#
# By default this disables the reachability probe because cli.launcher registers
# a node before that node starts listening. This makes discovery smooth for demos:
# registered peers are immediately returned to other joining nodes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="0.0.0.0"
PORT="8787"
DB="${BERZCOIN_SEED_REGISTRY_DB:-${HOME}/.berzcoin/seed_registry_server.json}"
REQUIRE_REACHABLE=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Start the BerzCoin seed registry for easy two-node discovery.

Options:
  --host HOST             Bind host (default: ${HOST})
  --port PORT             Registry port (default: ${PORT})
  --db PATH               Registry JSON DB path (default: ${DB})
  --require-reachable     Probe peers before verifying them
  -h, --help              Show help

Examples:
  scripts/start_seed_registry_quick.sh
  scripts/start_seed_registry_quick.sh --host 0.0.0.0 --port 8787
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="${2:-}"; shift 2 ;;
    --port)
      PORT="${2:-}"; shift 2 ;;
    --db)
      DB="${2:-}"; shift 2 ;;
    --require-reachable)
      REQUIRE_REACHABLE=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

if [[ -z "${HOST}" || -z "${PORT}" || -z "${DB}" ]]; then
  echo "error: --host, --port, and --db must not be empty" >&2
  exit 2
fi

echo "[*] BerzCoin seed registry"
echo "    bind: http://${HOST}:${PORT}"
echo "    db:   ${DB}"
if [[ "${REQUIRE_REACHABLE}" == "1" ]]; then
  echo "    peer probe: on"
else
  echo "    peer probe: off (demo-friendly)"
fi
echo
echo "LAN URL hint:"
if command -v hostname >/dev/null 2>&1; then
  # shellcheck disable=SC2046
  for ip in $(hostname -I 2>/dev/null || true); do
    case "${ip}" in
      127.*|172.17.*) ;;
      *) echo "  http://${ip}:${PORT}" ;;
    esac
  done
fi
echo

cd "${REPO_ROOT}"

ARGS=(scripts/seed_registry_server.py --host "${HOST}" --port "${PORT}" --db "${DB}")
if [[ "${REQUIRE_REACHABLE}" != "1" ]]; then
  ARGS+=(--no-require-reachable)
fi

exec python3 "${ARGS[@]}"
