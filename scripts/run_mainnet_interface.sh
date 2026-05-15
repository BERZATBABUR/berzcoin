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
STEP_TOTAL=7
STEP_NO=0

step_begin() {
  STEP_NO=$((STEP_NO + 1))
  echo "STEP ${STEP_NO}/${STEP_TOTAL} - $1"
}

step_ok() {
  echo "STEP ${STEP_NO}/${STEP_TOTAL} - OK"
}

step_fail() {
  local reason="$1"
  local recovery="${2:-}"
  echo "STEP ${STEP_NO}/${STEP_TOTAL} - FAIL: ${reason}" >&2
  if [[ -n "${recovery}" ]]; then
    echo "Recovery command:" >&2
    echo "  ${recovery}" >&2
  fi
  exit 1
}

find_node_pids_for_datadir() {
  local target="$1"
  ps -eo pid=,args= | awk -v dd="${target}" '
    {
      pid=$1
      $1=""
      args=substr($0,2)
      is_node=(args ~ /berzcoind/ || args ~ /node\.app\.main/)
      has_dd=(index(args, "-datadir " dd) > 0 || index(args, "-datadir=" dd) > 0)
      if (is_node && has_dd) {
        print pid
      }
    }
  '
}

extract_mainnet_passphrase() {
  local conf="${DATADIR}/berzcoin.conf"
  if [[ ! -f "${conf}" ]]; then
    echo ""
    return 0
  fi
  awk -F= '
    BEGIN { in_main=0 }
    /^\[main\]/ { in_main=1; next }
    /^\[/ { in_main=0 }
    in_main && $1 ~ /^[[:space:]]*wallet_encryption_passphrase[[:space:]]*$/ {
      v=$2
      sub(/^[[:space:]]+/, "", v)
      sub(/[[:space:]]+$/, "", v)
      print v
      exit
    }
  ' "${conf}"
}

passphrase_is_weak_or_placeholder() {
  local p="$1"
  local lower
  lower="$(echo "${p}" | tr '[:upper:]' '[:lower:]')"
  [[ -z "${lower}" ]] && return 0
  [[ "${#p}" -lt 12 ]] && return 0
  case "${lower}" in
    *replace*|*your_real*|*strong_pass*|*changeme*|*example*|*password*|*passphrase*)
      return 0
      ;;
  esac
  return 1
}

repair_stale_pid_file() {
  local pid_file="${DATADIR}/node.pid"
  [[ -f "${pid_file}" ]] || return 0
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    rm -f "${pid_file}" || true
    return 0
  fi
  if ! kill -0 "${pid}" 2>/dev/null; then
    echo "[!] Removing stale PID file: ${pid_file}"
    rm -f "${pid_file}" || true
  fi
}

ensure_no_parallel_node_for_datadir() {
  local pids=""
  while read -r pid; do
    [[ -n "${pid}" ]] || continue
    pids="${pids} ${pid}"
  done < <(find_node_pids_for_datadir "${DATADIR}")
  if [[ -n "${pids// }" ]]; then
    step_fail \
      "parallel node process already running for datadir ${DATADIR}:${pids}" \
      "pkill -f 'berzcoind|node.app.main' && rm -f ${DATADIR}/node.pid"
  fi
}

validate_mainnet_passphrase() {
  local passphrase="${BERZCOIN_WALLET_PASSPHRASE:-}"
  if [[ -z "${passphrase}" ]]; then
    passphrase="$(extract_mainnet_passphrase)"
  fi
  if passphrase_is_weak_or_placeholder "${passphrase}"; then
    step_fail \
      "mainnet wallet_encryption_passphrase is missing/weak/placeholder" \
      "echo 'wallet_encryption_passphrase = <REAL_LONG_SECRET>' >> ${DATADIR}/berzcoin.conf"
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Start BerzCoin mainnet node + web interface in one command.

Defaults:
  datadir: ${DATADIR}
  mode: keep existing chain state (no reset)

Options:
  --datadir PATH             Mainnet datadir (default: ${DATADIR})
  --starter                  Starter mode shortcut (LAN bind + canonical mainnet path)
  --join IP:PORT             Join a starter peer (adds --addnode IP:PORT; repeatable)
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
STARTER_MODE=0
JOIN_PEERS=()

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --datadir)
        [[ $# -ge 2 ]] || step_fail "--datadir requires a value" "scripts/run_mainnet_interface.sh --datadir ~/.berzcoin_mainnet --starter"
        DATADIR="$2"
        shift 2
        ;;
      --bootstrap-file)
        [[ $# -ge 2 ]] || step_fail "--bootstrap-file requires a value" "scripts/run_mainnet_interface.sh --bootstrap-file /path/to/bootstrap_nodes.json"
        BOOTSTRAP_FILE="$2"
        shift 2
        ;;
      --starter)
        STARTER_MODE=1
        shift
        ;;
      --join)
        [[ $# -ge 2 ]] || step_fail "--join requires a value" "scripts/run_mainnet_interface.sh --join 10.119.110.97:8333"
        JOIN_PEERS+=("$2")
        shift 2
        ;;
      --no-bootstrap-copy)
        COPY_DEFAULT_BOOTSTRAP=0
        shift
        ;;
      --rpc-port|--p2p-port|--p2p-bind|--addnode|--web-port)
        [[ $# -ge 2 ]] || step_fail "$1 requires a value" "scripts/run_mainnet_interface.sh --datadir ~/.berzcoin_mainnet --starter"
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
        usage >&2
        step_fail "unknown option: $1" "scripts/run_mainnet_interface.sh --help"
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
      step_fail "bootstrap file not found: ${source}" "scripts/run_mainnet_interface.sh --no-bootstrap-copy --datadir ${DATADIR}"
    fi
    cp "${source}" "${target}"
    chmod 600 "${target}" || true
    echo "[*] bootstrap file prepared: ${target}"
  fi
}

main() {
  step_begin "Validate Launcher Path"
  if [[ ! -x "${BASE_LAUNCHER}" ]]; then
    step_fail "missing launcher: ${BASE_LAUNCHER}" "git checkout -- scripts/run_v1_interface.sh && chmod +x scripts/run_v1_interface.sh"
  fi
  step_ok

  step_begin "Parse Flags"
  parse_args "$@"
  step_ok

  step_begin "Self-Repair Stale PID"
  repair_stale_pid_file
  step_ok

  step_begin "Guard Parallel Node"
  ensure_no_parallel_node_for_datadir
  step_ok

  step_begin "Validate Mainnet Passphrase"
  validate_mainnet_passphrase
  step_ok

  step_begin "Prepare Bootstrap"
  prepare_bootstrap
  step_ok

  step_begin "Start Mainnet Interface"

  if [[ "${STARTER_MODE}" == "1" ]]; then
    # Starter should be reachable by other peers on LAN/public interfaces.
    FORWARD_ARGS+=("--p2p-bind" "0.0.0.0")
  fi

  if [[ "${#JOIN_PEERS[@]}" -gt 0 ]]; then
    local peer
    for peer in "${JOIN_PEERS[@]}"; do
      if [[ "${peer}" != *:* ]]; then
        step_fail "--join expects IP:PORT (got: ${peer})" "scripts/run_mainnet_interface.sh --join 10.119.110.97:8333"
      fi
      FORWARD_ARGS+=("--addnode" "${peer}")
    done
  fi

  echo "Command:"
  echo "  ${BASE_LAUNCHER} --network mainnet --datadir ${DATADIR} --no-reset-datadir ${FORWARD_ARGS[*]}"
  step_ok

  exec "${BASE_LAUNCHER}" \
    --network mainnet \
    --datadir "${DATADIR}" \
    --no-reset-datadir \
    --block-time-secs 120 \
    "${FORWARD_ARGS[@]}"
}

main "$@"
