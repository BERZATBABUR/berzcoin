#!/usr/bin/env bash
# One-command v1 launcher:
# - starts regtest node with dashboard
# - by default leaves wallet/mining neutral (user activates via private key)
# - optional demo bootstrap can create/activate wallet and start mining
# - verifies interface is reachable
#
# Starts in background and exits after readiness checks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

NETWORK="${BERZCOIN_V1_NETWORK:-regtest}"
DATADIR="${BERZCOIN_V1_DATADIR:-}"
CONF=""
RPC_PORT="${BERZCOIN_V1_RPC_PORT:-}"
P2P_PORT="${BERZCOIN_V1_P2P_PORT:-}"
P2P_BIND="${BERZCOIN_V1_P2P_BIND:-127.0.0.1}"
ADDNODE="${BERZCOIN_V1_ADDNODE:-}"
WEB_PORT="${BERZCOIN_V1_WEB_PORT:-8080}"
MINER_THREADS="${BERZCOIN_V1_MINER_THREADS:-1}"
MINING_TARGET_SECS="${BERZCOIN_V1_MINING_TARGET_SECS:-60}"
COINBASE_MATURITY="${BERZCOIN_V1_COINBASE_MATURITY:-1}"
MINING_REQUIRE_WALLET_MATCH="${BERZCOIN_V1_MINING_REQUIRE_WALLET_MATCH:-false}"
BOOTSTRAP_DEMO=0
# Auto behavior:
# - regtest => reset by default
# - mainnet/testnet => keep datadir by default
RESET_DATADIR="${BERZCOIN_V1_RESET_DATADIR:-}"

NODE_LOG=""
NODE_PID_FILE=""
RUN_INFO_FILE=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Run BerzCoin v1 in one command (background mode):
  node + dashboard

Options:
  --network NAME       Network: regtest|testnet|mainnet (default: ${NETWORK})
  --datadir PATH       Data directory (default: auto by network)
  --rpc-port PORT      RPC port (default: auto by network)
  --p2p-port PORT      P2P port (default: auto by network)
  --p2p-bind HOST      P2P bind host (default: ${P2P_BIND})
  --addnode HOST:PORT  Add static peer (can be repeated)
  --lan-mode           Shortcut for --p2p-bind 0.0.0.0
  --web-port PORT      Dashboard web port (default: ${WEB_PORT})
  --threads N          Miner thread count (default: ${MINER_THREADS})
  --block-time-secs N  Mining target seconds/block (default: ${MINING_TARGET_SECS})
  --coinbase-maturity N  Coinbase maturity confirmations (default: ${COINBASE_MATURITY})
  --bootstrap-demo     Auto create+activate wallet and start mining (regtest only)
  --reset-datadir      Delete datadir first (destructive; default on regtest)
  --no-reset-datadir   Keep existing datadir/chain state
  -h, --help           Show this help

Environment (optional):
  BERZCOIN_V1_NETWORK
  BERZCOIN_V1_DATADIR
  BERZCOIN_V1_RPC_PORT
  BERZCOIN_V1_P2P_PORT
  BERZCOIN_V1_P2P_BIND
  BERZCOIN_V1_ADDNODE
  BERZCOIN_V1_WEB_PORT
  BERZCOIN_V1_MINER_THREADS
  BERZCOIN_V1_MINING_TARGET_SECS
  BERZCOIN_V1_COINBASE_MATURITY
  BERZCOIN_V1_MINING_REQUIRE_WALLET_MATCH
  BERZCOIN_V1_RESET_DATADIR

Stop command:
  kill \$(cat <datadir>/node.pid)
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --network)
        [[ $# -ge 2 ]] || { echo "error: --network requires a value" >&2; exit 2; }
        NETWORK="$2"
        shift 2
        ;;
      --datadir)
        [[ $# -ge 2 ]] || { echo "error: --datadir requires a value" >&2; exit 2; }
        DATADIR="$2"
        shift 2
        ;;
      --rpc-port)
        [[ $# -ge 2 ]] || { echo "error: --rpc-port requires a value" >&2; exit 2; }
        RPC_PORT="$2"
        shift 2
        ;;
      --p2p-port)
        [[ $# -ge 2 ]] || { echo "error: --p2p-port requires a value" >&2; exit 2; }
        P2P_PORT="$2"
        shift 2
        ;;
      --p2p-bind)
        [[ $# -ge 2 ]] || { echo "error: --p2p-bind requires a value" >&2; exit 2; }
        P2P_BIND="$2"
        shift 2
        ;;
      --addnode)
        [[ $# -ge 2 ]] || { echo "error: --addnode requires a value" >&2; exit 2; }
        if [[ -n "${ADDNODE}" ]]; then
          ADDNODE="${ADDNODE},$2"
        else
          ADDNODE="$2"
        fi
        shift 2
        ;;
      --lan-mode)
        P2P_BIND="0.0.0.0"
        shift
        ;;
      --web-port)
        [[ $# -ge 2 ]] || { echo "error: --web-port requires a value" >&2; exit 2; }
        WEB_PORT="$2"
        shift 2
        ;;
      --threads)
        [[ $# -ge 2 ]] || { echo "error: --threads requires a value" >&2; exit 2; }
        MINER_THREADS="$2"
        shift 2
        ;;
      --block-time-secs)
        [[ $# -ge 2 ]] || { echo "error: --block-time-secs requires a value" >&2; exit 2; }
        MINING_TARGET_SECS="$2"
        shift 2
        ;;
      --coinbase-maturity)
        [[ $# -ge 2 ]] || { echo "error: --coinbase-maturity requires a value" >&2; exit 2; }
        COINBASE_MATURITY="$2"
        shift 2
        ;;
      --bootstrap-demo)
        BOOTSTRAP_DEMO=1
        shift
        ;;
      --reset-datadir)
        RESET_DATADIR=1
        shift
        ;;
      --no-reset-datadir)
        RESET_DATADIR=0
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

safe_reset_datadir() {
  local target="$1"

  if [[ -z "${target}" || "${target}" == "/" ]]; then
    echo "error: refusing to delete unsafe datadir path: '${target}'" >&2
    exit 1
  fi
  if [[ "${target}" == "${HOME}" || "${target}" == "${HOME}/" ]]; then
    echo "error: refusing to delete HOME directory: '${target}'" >&2
    exit 1
  fi
  if [[ "${target}" == "${HOME}/.berzcoin" ]]; then
    echo "error: refusing to delete main datadir '${target}' from launcher" >&2
    exit 1
  fi

  local attempt
  for attempt in $(seq 1 5); do
    if rm -rf -- "${target}" 2>/dev/null; then
      return 0
    fi
    # A racing writer can repopulate entries briefly; retry a few times.
    sleep 0.2
  done

  echo "error: failed to fully delete datadir '${target}' after retries" >&2
  if [[ -d "${target}" ]]; then
    find "${target}" -mindepth 1 -maxdepth 2 -print | sed -n '1,40p' >&2 || true
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

stop_nodes_for_datadir() {
  local target="$1"
  local pid_file="${target}/node.pid"
  local pid
  local pids=""
  local wait_i

  if [[ -f "${pid_file}" ]]; then
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && node_pid_is_expected "${pid}"; then
      pids="${pid}"
    fi
  fi

  while read -r pid; do
    [[ -n "${pid}" ]] || continue
    case " ${pids} " in
      *" ${pid} "*) ;;
      *) pids="${pids} ${pid}" ;;
    esac
  done < <(find_node_pids_for_datadir "${target}")

  if [[ -n "${pids// }" ]]; then
    echo "[*] Stopping existing node process(es) for datadir:${pids}"
    kill ${pids} 2>/dev/null || true
    for wait_i in $(seq 1 50); do
      local any_alive=0
      for pid in ${pids}; do
        if kill -0 "${pid}" 2>/dev/null; then
          any_alive=1
          break
        fi
      done
      if [[ "${any_alive}" == "0" ]]; then
        break
      fi
      sleep 0.1
    done

    for pid in ${pids}; do
      if kill -0 "${pid}" 2>/dev/null; then
        kill -9 "${pid}" 2>/dev/null || true
      fi
    done
  fi

  rm -f "${pid_file}" 2>/dev/null || true
}

select_commands() {
  if command -v berzcoind >/dev/null 2>&1 && command -v berzcoin-cli >/dev/null 2>&1; then
    BERZCOIND=(berzcoind)
    BERZCOINCLI=(berzcoin-cli)
  else
    export PYTHONPATH="${REPO_ROOT}"
    BERZCOIND=(python3 -m node.app.main)
    BERZCOINCLI=(python3 -m cli.main)
  fi
}

rpc_cli() {
  "${BERZCOINCLI[@]}" -datadir "${DATADIR}" -rpcport "${RPC_PORT}" "$@"
}

wait_for_rpc() {
  local i
  for i in $(seq 1 100); do
    if rpc_cli getblockcount >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

wait_for_web() {
  local url="http://127.0.0.1:${WEB_PORT}/"
  local i
  for i in $(seq 1 80); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

resolve_network_defaults() {
  if [[ "${NETWORK}" != "regtest" && "${NETWORK}" != "testnet" && "${NETWORK}" != "mainnet" ]]; then
    echo "error: --network must be one of: regtest, testnet, mainnet" >&2
    exit 2
  fi

  if [[ -z "${DATADIR}" ]]; then
    case "${NETWORK}" in
      regtest) DATADIR="${HOME}/.berzcoin_v1" ;;
      testnet) DATADIR="${HOME}/.berzcoin_v1_testnet" ;;
      mainnet) DATADIR="${HOME}/.berzcoin_v1_mainnet" ;;
    esac
  fi

  if [[ -z "${RPC_PORT}" ]]; then
    case "${NETWORK}" in
      regtest) RPC_PORT="18443" ;;
      testnet) RPC_PORT="18332" ;;
      mainnet) RPC_PORT="8332" ;;
    esac
  fi

  if [[ -z "${P2P_PORT}" ]]; then
    case "${NETWORK}" in
      regtest) P2P_PORT="18444" ;;
      testnet) P2P_PORT="18333" ;;
      mainnet) P2P_PORT="8333" ;;
    esac
  fi

  if [[ -z "${RESET_DATADIR}" ]]; then
    if [[ "${NETWORK}" == "regtest" ]]; then
      RESET_DATADIR="1"
    else
      RESET_DATADIR="0"
    fi
  fi
}

network_flag_args() {
  case "${NETWORK}" in
    regtest)
      echo "--regtest"
      ;;
    testnet)
      echo "--testnet"
      ;;
    *)
      ;;
  esac
}

port_free() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket
import sys

host = str(sys.argv[1])
port = int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PY
}

find_free_port() {
  local host="${1:-127.0.0.1}"
  python3 - "$host" <<'PY'
import socket
import sys

host = str(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.bind((host, 0))
print(sock.getsockname()[1])
sock.close()
PY
}

node_pid_is_expected() {
  local pid="$1"
  kill -0 "${pid}" 2>/dev/null || return 1
  local cmdline
  cmdline="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1
  if [[ "${cmdline}" == *"berzcoind"* || "${cmdline}" == *"node.app.main"* ]]; then
    return 0
  fi
  return 1
}

normalize_ports() {
  local requested_rpc="${RPC_PORT}"
  local requested_p2p="${P2P_PORT}"
  local requested_web="${WEB_PORT}"

  if ! port_free "127.0.0.1" "${RPC_PORT}"; then
    RPC_PORT="$(find_free_port)"
    echo "[!] Requested RPC port ${requested_rpc} is busy; using ${RPC_PORT} instead."
  fi

  if ! port_free "${P2P_BIND}" "${P2P_PORT}"; then
    P2P_PORT="$(find_free_port "${P2P_BIND}")"
    echo "[!] Requested P2P port ${requested_p2p} is busy; using ${P2P_PORT} instead."
  fi

  if ! port_free "127.0.0.1" "${WEB_PORT}"; then
    WEB_PORT="$(find_free_port)"
    echo "[!] Requested web port ${requested_web} is busy; using ${WEB_PORT} instead."
  fi
}

write_conf() {
  local addnode_line=""
  if [[ -n "${ADDNODE}" ]]; then
    addnode_line="addnode = ${ADDNODE}"
  fi
  cat > "${CONF}" <<EOF
[main]
network = ${NETWORK}
datadir = ${DATADIR}

disablewallet = false

webdashboard = true
webhost = 127.0.0.1
webport = ${WEB_PORT}

rpcbind = 127.0.0.1
rpcport = ${RPC_PORT}
rpcallowip = 127.0.0.1

bind = ${P2P_BIND}
port = ${P2P_PORT}
${addnode_line}

mining = false
autominer = false
miningaddress =
mining_threads = ${MINER_THREADS}
mining_target_time_secs = ${MINING_TARGET_SECS}
coinbase_maturity = ${COINBASE_MATURITY}
mining_require_wallet_match = ${MINING_REQUIRE_WALLET_MATCH}

dnsseed = false
debug = true
EOF
  chmod 600 "${CONF}"
}

start_node_if_needed() {
  local network_arg="${1:-}"
  if [[ -f "${NODE_PID_FILE}" ]]; then
    local existing_pid
    existing_pid="$(cat "${NODE_PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${existing_pid}" ]] && node_pid_is_expected "${existing_pid}"; then
      echo "[*] Existing node detected (pid ${existing_pid}); restarting..."
      kill "${existing_pid}" 2>/dev/null || true
      wait "${existing_pid}" 2>/dev/null || true
    fi
    echo "[!] Removing stale PID file: ${NODE_PID_FILE}"
    rm -f "${NODE_PID_FILE}"
  fi

  echo "[*] Starting berzcoind in background..."
  local launch_args=("${BERZCOIND[@]}")
  if [[ -n "${network_arg}" ]]; then
    launch_args+=("${network_arg}")
  fi
  launch_args+=(-conf "${CONF}" -datadir "${DATADIR}")
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "${launch_args[@]}" </dev/null >"${NODE_LOG}" 2>&1 &
  else
    nohup "${launch_args[@]}" </dev/null >"${NODE_LOG}" 2>&1 &
  fi
  local node_pid=$!
  disown "${node_pid}" 2>/dev/null || true
  echo "${node_pid}" > "${NODE_PID_FILE}"
}

write_run_info() {
  cat > "${RUN_INFO_FILE}" <<EOF
NETWORK=${NETWORK}
DATADIR=${DATADIR}
RPC_PORT=${RPC_PORT}
P2P_PORT=${P2P_PORT}
P2P_BIND=${P2P_BIND}
ADDNODE=${ADDNODE}
WEB_PORT=${WEB_PORT}
BOOTSTRAP_DEMO=${BOOTSTRAP_DEMO}
RESET_DATADIR=${RESET_DATADIR}
MINING_TARGET_SECS=${MINING_TARGET_SECS}
COINBASE_MATURITY=${COINBASE_MATURITY}
MINING_REQUIRE_WALLET_MATCH=${MINING_REQUIRE_WALLET_MATCH}
DASHBOARD_URL=http://127.0.0.1:${WEB_PORT}/
NODE_PID_FILE=${NODE_PID_FILE}
NODE_LOG=${NODE_LOG}
EOF
  chmod 600 "${RUN_INFO_FILE}" || true
}

ensure_wallet_active() {
  echo "[*] Creating private-key wallet (demo bootstrap)..."
  local wallet_json
  local wallet_key
  wallet_json="$(rpc_cli createwallet default | tr -d '\r')"
  wallet_key="$(echo "${wallet_json}" | sed -n 's/.*"private_key"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  if [[ -z "${wallet_key}" ]]; then
    echo "error: failed to derive private key from createwallet output" >&2
    echo "raw output: ${wallet_json}" >&2
    exit 1
  fi
  echo "[*] Activating wallet (demo bootstrap)..."
  rpc_cli activatewallet "${wallet_key}" >/dev/null
  echo "[*] Demo wallet private key: ${wallet_key}"
}

ensure_mining_address() {
  local addr
  addr="$(rpc_cli getnewaddress | tail -1 | tr -d '\r\n')"
  if [[ -z "${addr}" ]]; then
    echo "error: getnewaddress returned empty address" >&2
    exit 1
  fi

  echo "[*] Setting mining reward address: ${addr}"
  rpc_cli setminingaddress "${addr}" >/dev/null
}

prime_and_start_mining() {
  echo "[*] Priming wallet with 101 blocks..."
  rpc_cli generate 101 >/dev/null

  echo "[*] Enabling background mining (${MINER_THREADS} thread(s))..."
  rpc_cli setgenerate true --threads "${MINER_THREADS}" >/dev/null
}

main() {
  parse_args "$@"
  resolve_network_defaults

  if [[ "${RESET_DATADIR}" != "0" && "${RESET_DATADIR}" != "1" ]]; then
    echo "error: BERZCOIN_V1_RESET_DATADIR must be 0 or 1" >&2
    exit 2
  fi
  if [[ "${BOOTSTRAP_DEMO}" == "1" && "${NETWORK}" != "regtest" ]]; then
    echo "error: --bootstrap-demo is only supported on regtest" >&2
    exit 2
  fi

  NODE_LOG="${DATADIR}/node.log"
  NODE_PID_FILE="${DATADIR}/node.pid"
  RUN_INFO_FILE="${DATADIR}/run_info.env"
  CONF="${DATADIR}/berzcoin.conf"
  local network_arg=""

  normalize_ports

  echo "[*] BerzCoin v1 launcher"
  echo "    network: ${NETWORK}"
  echo "    datadir: ${DATADIR}"
  echo "    rpc:     127.0.0.1:${RPC_PORT}"
  echo "    p2p:     ${P2P_BIND}:${P2P_PORT}"
  echo "    web:     127.0.0.1:${WEB_PORT}"
  echo "    block:   ${MINING_TARGET_SECS}s target"
  if [[ -n "${ADDNODE}" ]]; then
    echo "    addnode: ${ADDNODE}"
  fi
  if [[ "${RESET_DATADIR}" == "1" ]]; then
    echo "    mode:    fresh chain (datadir reset)"
  else
    echo "    mode:    keep existing chain"
  fi

  if [[ "${RESET_DATADIR}" == "1" ]]; then
    echo "[*] Reset requested; deleting datadir..."
    stop_nodes_for_datadir "${DATADIR}"
    safe_reset_datadir "${DATADIR}"
  fi

  mkdir -p "${DATADIR}"
  chmod 700 "${DATADIR}" || true
  rm -f "${DATADIR}/wallet_private_key.txt" "${DATADIR}/mining_address.txt" || true

  write_conf
  select_commands
  network_arg="$(network_flag_args)"
  start_node_if_needed "${network_arg}"

  echo "[*] Waiting for RPC..."
  if ! wait_for_rpc; then
    echo "error: RPC did not become ready. Check ${NODE_LOG}" >&2
    exit 1
  fi

  if [[ "${BOOTSTRAP_DEMO}" == "1" ]]; then
    ensure_wallet_active
    ensure_mining_address
    prime_and_start_mining
  else
    echo "[*] Neutral startup mode: wallet/mining are not auto-configured."
    echo "[*] Use the Wallet page to activate with your private key."
    echo "[*] Then set mining address/start mining from the Mining page."
  fi

  echo "[*] Verifying dashboard interface..."
  if ! wait_for_web; then
    echo "error: dashboard interface is not reachable at http://127.0.0.1:${WEB_PORT}/" >&2
    echo "Check log: ${NODE_LOG}" >&2
    exit 1
  fi

  local bal
  bal="$(rpc_cli getbalance | tail -1 | tr -d '\r\n' || echo "0")"
  local height
  height="$(rpc_cli getblockcount | tail -1 | tr -d '\r\n')"
  local pid
  pid="$(cat "${NODE_PID_FILE}" 2>/dev/null || true)"
  write_run_info

  echo ""
  echo "[OK] BerzCoin v1 is running."
  echo "Dashboard: http://127.0.0.1:${WEB_PORT}/"
  echo "RPC port:  ${RPC_PORT}"
  echo "Height:    ${height}"
  echo "Balance:   ${bal} BERZ"
  echo "PID:       ${pid}"
  echo "Log:       ${NODE_LOG}"
  echo "Run info:  ${RUN_INFO_FILE}"
  echo ""
  echo "Open exactly:"
  echo "  http://127.0.0.1:${WEB_PORT}/"
  echo ""
  echo "Stop:"
  echo "  kill \$(cat \"${NODE_PID_FILE}\")"
}

main "$@"
