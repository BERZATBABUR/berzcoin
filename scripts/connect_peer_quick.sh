#!/usr/bin/env bash
# Quick one-command peer connect helper for two-machine setups.
# - Writes a clean berzcoin.conf for the target datadir
# - Stops existing node for that datadir
# - Clears temporary peer ban/score cache
# - Starts node (foreground by default, --background optional)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

NETWORK="mainnet"
DATADIR=""
PEER=""
RPC_PORT="8332"
P2P_PORT="8333"
WEB_PORT="8080"
ENABLE_WEB=1
BACKGROUND=0
BOOTSTRAP_MODE="isolated" # isolated|dns

usage() {
  cat <<EOF
Usage: $(basename "$0") --datadir PATH --peer HOST:PORT [options]

Required:
  --datadir PATH        Node datadir (example: ~/.berzcoin_v2_a)
  --peer HOST:PORT      Peer to connect/add (example: 10.241.220.94:8333)

Options:
  --network NAME        mainnet|testnet|regtest (default: ${NETWORK})
  --rpc-port PORT       RPC port (default: ${RPC_PORT})
  --p2p-port PORT       P2P port (default: ${P2P_PORT})
  --web-port PORT       Dashboard port (default: ${WEB_PORT})
  --no-web              Disable web dashboard
  --bootstrap MODE      isolated|dns (default: ${BOOTSTRAP_MODE})
                        isolated: addnode peer only, dnsseed false
                        dns: addnode peer + dnsseed true with built-in defaults
  --background          Start in background
  -h, --help            Show help

Examples:
  ./scripts/connect_peer_quick.sh --datadir ~/.berzcoin_v2_a --peer 10.241.220.94:8333
  ./scripts/connect_peer_quick.sh --datadir ~/.berzcoin_v2_b --peer 10.241.220.97:8333 --background
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --datadir)
      DATADIR="${2:-}"; shift 2 ;;
    --peer)
      PEER="${2:-}"; shift 2 ;;
    --network)
      NETWORK="${2:-}"; shift 2 ;;
    --rpc-port)
      RPC_PORT="${2:-}"; shift 2 ;;
    --p2p-port)
      P2P_PORT="${2:-}"; shift 2 ;;
    --web-port)
      WEB_PORT="${2:-}"; shift 2 ;;
    --no-web)
      ENABLE_WEB=0; shift ;;
    --bootstrap)
      BOOTSTRAP_MODE="${2:-}"; shift 2 ;;
    --background)
      BACKGROUND=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

if [[ -z "${DATADIR}" || -z "${PEER}" ]]; then
  echo "error: --datadir and --peer are required" >&2
  usage >&2
  exit 2
fi

if [[ "${BOOTSTRAP_MODE}" != "isolated" && "${BOOTSTRAP_MODE}" != "dns" ]]; then
  echo "error: --bootstrap must be isolated or dns" >&2
  exit 2
fi

mkdir -p "${DATADIR}"

if command -v berzcoind >/dev/null 2>&1 && command -v berzcoin-cli >/dev/null 2>&1; then
  BERZCOIND=(berzcoind)
  BERZCOINCLI=(berzcoin-cli)
else
  export PYTHONPATH="${REPO_ROOT}"
  BERZCOIND=(python3 -m node.app.main)
  BERZCOINCLI=(python3 -m cli.main)
fi

CONF="${DATADIR}/berzcoin.conf"
DNSSEED="false"
ALLOW_MISSING="true"
if [[ "${BOOTSTRAP_MODE}" == "dns" ]]; then
  DNSSEED="true"
  ALLOW_MISSING="false"
fi

WEB_ENABLED="true"
WEB_HOST="0.0.0.0"
if [[ "${ENABLE_WEB}" == "0" ]]; then
  WEB_ENABLED="false"
  WEB_HOST="127.0.0.1"
fi

cat > "${CONF}" <<EOF
[main]
network = ${NETWORK}
datadir = ${DATADIR}

bind = 0.0.0.0
port = ${P2P_PORT}

addnode = ${PEER}
connect =

dnsseed = ${DNSSEED}
bootstrap_enabled = false
allow_missing_bootstrap = ${ALLOW_MISSING}

rpcbind = 127.0.0.1
rpcport = ${RPC_PORT}
rpcallowip = 127.0.0.1
rpc_require_auth = true

activation_height_berz_softfork_bip34_strict = 180000
activation_height_berz_hardfork_tx_v2 = 180100
node_consensus_version = 2
enforce_hardfork_guardrails = true

webdashboard = ${WEB_ENABLED}
webhost = ${WEB_HOST}
webport = ${WEB_PORT}

mining = false
autominer = false
EOF

echo "[*] Wrote ${CONF}"

"${BERZCOINCLI[@]}" -datadir "${DATADIR}" -rpcport "${RPC_PORT}" stop >/dev/null 2>&1 || true

# Stop any existing process that is already using this datadir.
while read -r pid; do
  [[ -n "${pid}" ]] || continue
  kill "${pid}" >/dev/null 2>&1 || true
done < <(ps -eo pid=,args= | awk -v dd="${DATADIR}" '
  {
    pid=$1
    $1=""
    args=substr($0,2)
    if ((args ~ /berzcoind/ || args ~ /node\.app\.main/) &&
        (index(args, "-datadir " dd) > 0 || index(args, "-datadir=" dd) > 0 || index(args, "-conf " dd "/berzcoin.conf") > 0 || index(args, "-conf=" dd "/berzcoin.conf") > 0)) {
      print pid
    }
  }
')

rm -f "${DATADIR}/banlist.json" "${DATADIR}/peer_scores.json"
echo "[*] Cleared peer ban/score cache"

if [[ "${BACKGROUND}" == "1" ]]; then
  LOG="${DATADIR}/node.log"
  nohup "${BERZCOIND[@]}" -conf "${CONF}" > "${LOG}" 2>&1 &
  echo "[*] Node started in background"
  echo "    datadir: ${DATADIR}"
  echo "    rpc:     127.0.0.1:${RPC_PORT}"
  echo "    p2p:     0.0.0.0:${P2P_PORT}"
  echo "    web:     http://127.0.0.1:${WEB_PORT}/"
  echo "    log:     ${LOG}"
else
  echo "[*] Starting node in foreground..."
  exec "${BERZCOIND[@]}" -conf "${CONF}"
fi

