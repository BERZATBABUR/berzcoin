#!/usr/bin/env bash
# Quick node launcher for seed-registry based two-node demos.
#
# This wraps:
#   python3 -m cli.launcher node start --auto-discover ...
#
# It is intentionally foreground-first: keep the terminal open to keep the node
# running and use Ctrl+C to stop it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

NETWORK="mainnet"
P2P_PORT="38333"
RPC_PORT=""
DATADIR=""
REGISTRY_URL="http://127.0.0.1:8787"
SELF_IP=""
USE_SEEDS=0
PASSPHRASE="${BERZCOIN_WALLET_PASSPHRASE:-local-mainnet-demo-passphrase-2026}"
ENABLE_WEB=0
WEB_PORT="38080"
WEB_HOST="127.0.0.1"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Start one BerzCoin node and register it with a seed registry.

Options:
  --network NAME          mainnet|testnet|regtest (default: ${NETWORK})
  --p2p-port PORT         Node P2P port (default: ${P2P_PORT})
  --rpc-port PORT         Node RPC port (default: p2p + 1000)
  --datadir PATH          Node datadir (default: /tmp/berz-node-<p2p-port>)
  --registry URL          Seed registry URL (default: ${REGISTRY_URL})
  --self-ip IP            Reachable IP to register (default: auto-detect)
  --passphrase TEXT       Mainnet wallet encryption passphrase
  --web                   Enable dashboard interface
  --web-port PORT         Dashboard port when --web is used (default: ${WEB_PORT})
  --web-host HOST         Dashboard bind host (default: ${WEB_HOST})
  --use-seeds             Also use built-in seed fallback
  -h, --help              Show help

Linux example:
  scripts/start_discovery_node_quick.sh \\
    --p2p-port 38333 \\
    --rpc-port 39333 \\
    --web --web-port 38080 \\
    --registry http://10.159.189.97:8787 \\
    --self-ip 10.159.189.97

Windows Git Bash example:
  scripts/start_discovery_node_quick.sh \\
    --p2p-port 38334 \\
    --rpc-port 39334 \\
    --web --web-port 38081 \\
    --registry http://10.159.189.97:8787 \\
    --self-ip 10.159.189.7
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --network)
      NETWORK="${2:-}"; shift 2 ;;
    --p2p-port|--port)
      P2P_PORT="${2:-}"; shift 2 ;;
    --rpc-port)
      RPC_PORT="${2:-}"; shift 2 ;;
    --datadir|--data-dir)
      DATADIR="${2:-}"; shift 2 ;;
    --registry|--seed-registry)
      REGISTRY_URL="${2:-}"; shift 2 ;;
    --self-ip)
      SELF_IP="${2:-}"; shift 2 ;;
    --passphrase)
      PASSPHRASE="${2:-}"; shift 2 ;;
    --web)
      ENABLE_WEB=1; shift ;;
    --web-port)
      WEB_PORT="${2:-}"; shift 2 ;;
    --web-host)
      WEB_HOST="${2:-}"; shift 2 ;;
    --use-seeds)
      USE_SEEDS=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

if [[ "${NETWORK}" != "mainnet" && "${NETWORK}" != "testnet" && "${NETWORK}" != "regtest" ]]; then
  echo "error: --network must be mainnet, testnet, or regtest" >&2
  exit 2
fi
if [[ -z "${P2P_PORT}" || -z "${REGISTRY_URL}" ]]; then
  echo "error: --p2p-port and --registry must not be empty" >&2
  exit 2
fi
if [[ -z "${RPC_PORT}" ]]; then
  RPC_PORT="$((P2P_PORT + 1000))"
fi
if [[ -z "${DATADIR}" ]]; then
  DATADIR="/tmp/berz-node-${P2P_PORT}"
fi

auto_self_ip() {
  python3 - "$REGISTRY_URL" <<'PY'
import socket
import sys
from urllib.parse import urlparse

url = urlparse(sys.argv[1])
host = url.hostname or "127.0.0.1"
port = int(url.port or (443 if url.scheme == "https" else 80))

if host not in {"127.0.0.1", "localhost", "::1"}:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, port))
        print(sock.getsockname()[0])
        raise SystemExit(0)
    finally:
        sock.close()

try:
    infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    for item in infos:
        ip = item[4][0]
        if not ip.startswith("127."):
            print(ip)
            raise SystemExit(0)
except OSError:
    pass

print("127.0.0.1")
PY
}

if [[ -z "${SELF_IP}" ]]; then
  SELF_IP="$(auto_self_ip)"
fi

check_registry() {
  python3 - "$REGISTRY_URL" <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = sys.argv[1].rstrip("/") + "/health"
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except Exception as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
if not isinstance(data, dict) or not data.get("ok"):
    print("registry health response is not ok", file=sys.stderr)
    raise SystemExit(1)
PY
}

CMD=(
  python3 -m cli.launcher node start
  --network "${NETWORK}"
  --port "${P2P_PORT}"
  --rpc-port "${RPC_PORT}"
  --data-dir "${DATADIR}"
  --auto-discover
  --self-ip "${SELF_IP}"
  --seed-registry "${REGISTRY_URL}"
)
if [[ "${USE_SEEDS}" != "1" ]]; then
  CMD+=(--no-seeds)
fi

echo "[*] BerzCoin discovery node"
echo "    network:  ${NETWORK}"
echo "    datadir:  ${DATADIR}"
echo "    p2p:      0.0.0.0:${P2P_PORT}"
echo "    rpc:      127.0.0.1:${RPC_PORT}"
echo "    registry: ${REGISTRY_URL}"
echo "    self-ip:  ${SELF_IP}"
if [[ "${ENABLE_WEB}" == "1" ]]; then
  echo "    web:      http://${WEB_HOST}:${WEB_PORT}/"
fi
echo

cd "${REPO_ROOT}"

if ! REGISTRY_ERR="$(check_registry 2>&1)"; then
  echo "error: seed registry is not reachable: ${REGISTRY_URL}" >&2
  echo "detail: ${REGISTRY_ERR}" >&2
  echo >&2
  echo "Start it first on the registry machine:" >&2
  echo "  scripts/start_seed_registry_quick.sh --host 0.0.0.0 --port 8787" >&2
  echo >&2
  echo "Then test:" >&2
  echo "  curl ${REGISTRY_URL%/}/peers" >&2
  exit 1
fi

if [[ "${NETWORK}" == "mainnet" ]]; then
  export BERZCOIN_MAINNET_ALLOW_UNSAFE_BIND=true
  export BERZCOIN_WALLET_PASSPHRASE="${PASSPHRASE}"
fi
if [[ "${ENABLE_WEB}" == "1" ]]; then
  export BERZCOIN_WEBDASHBOARD=true
  export BERZCOIN_WEBHOST="${WEB_HOST}"
  export BERZCOIN_WEBPORT="${WEB_PORT}"
fi

exec "${CMD[@]}"
