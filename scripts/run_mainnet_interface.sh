#!/usr/bin/env bash
# One-command mainnet + dashboard launcher.
# Wraps scripts/run_v1_interface.sh with mainnet-safe defaults.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BASE_LAUNCHER="${SCRIPT_DIR}/run_v1_interface.sh"

DATADIR="${BERZCOIN_MAINNET_DATADIR:-${HOME}/.berzcoin_v1_mainnet}"
BOOTSTRAP_FILE="${BERZCOIN_MAINNET_BOOTSTRAP_FILE:-}"
COPY_DEFAULT_BOOTSTRAP=1

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Start BerzCoin mainnet node + web interface in one command.

Defaults:
  datadir: ${DATADIR}
  mode: keep existing chain state (no reset)

Options:
  --datadir PATH             Mainnet datadir (default: ${DATADIR})
  --bootstrap-file PATH      JSON bootstrap file to copy into datadir/bootstrap_nodes.json
  --no-bootstrap-copy        Do not auto-copy bootstrap file
  --rpc-port PORT            Forwarded to run_v1_interface.sh
  --p2p-port PORT            Forwarded to run_v1_interface.sh
  --p2p-bind HOST            Forwarded to run_v1_interface.sh
  --addnode HOST:PORT        Forwarded to run_v1_interface.sh (repeatable)
  --lan-mode                 Forwarded to run_v1_interface.sh
  --web-port PORT            Forwarded to run_v1_interface.sh
  -h, --help                 Show this help

Notes:
  - This script forces: --network mainnet --no-reset-datadir
  - If bootstrap copy is enabled and no file is provided, it copies:
      ${REPO_ROOT}/configs/bootstrap_nodes.json
EOF
}

FORWARD_ARGS=()

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --datadir)
        [[ $# -ge 2 ]] || { echo "error: --datadir requires a value" >&2; exit 2; }
        DATADIR="$2"
        shift 2
        ;;
      --bootstrap-file)
        [[ $# -ge 2 ]] || { echo "error: --bootstrap-file requires a value" >&2; exit 2; }
        BOOTSTRAP_FILE="$2"
        shift 2
        ;;
      --no-bootstrap-copy)
        COPY_DEFAULT_BOOTSTRAP=0
        shift
        ;;
      --rpc-port|--p2p-port|--p2p-bind|--addnode|--web-port)
        [[ $# -ge 2 ]] || { echo "error: $1 requires a value" >&2; exit 2; }
        FORWARD_ARGS+=("$1" "$2")
        shift 2
        ;;
      --lan-mode)
        FORWARD_ARGS+=("$1")
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "error: unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done
}

prepare_bootstrap() {
  local target="${DATADIR}/bootstrap_nodes.json"
  local source=""

  mkdir -p "${DATADIR}"

  if [[ -n "${BOOTSTRAP_FILE}" ]]; then
    source="${BOOTSTRAP_FILE}"
  elif [[ "${COPY_DEFAULT_BOOTSTRAP}" == "1" && ! -f "${target}" ]]; then
    source="${REPO_ROOT}/configs/bootstrap_nodes.json"
  fi

  if [[ -n "${source}" ]]; then
    if [[ ! -f "${source}" ]]; then
      echo "error: bootstrap file not found: ${source}" >&2
      exit 1
    fi
    cp "${source}" "${target}"
    chmod 600 "${target}" || true
    echo "[*] bootstrap file prepared: ${target}"
  fi
}

main() {
  if [[ ! -x "${BASE_LAUNCHER}" ]]; then
    echo "error: missing launcher: ${BASE_LAUNCHER}" >&2
    exit 1
  fi

  parse_args "$@"
  prepare_bootstrap

  exec "${BASE_LAUNCHER}" \
    --network mainnet \
    --datadir "${DATADIR}" \
    --no-reset-datadir \
    "${FORWARD_ARGS[@]}"
}

main "$@"
