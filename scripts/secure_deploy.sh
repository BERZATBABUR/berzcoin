#!/usr/bin/env bash
# Secure deployment helper for BerzCoin (datadir layout, cookie, config, optional firewall).

set -euo pipefail

echo "[*] Secure BerzCoin deployment"

DATADIR="${BERZCOIN_DATADIR:-${HOME}/.berzcoin}"
mkdir -p "${DATADIR}"
chmod 700 "${DATADIR}"

COOKIE="$(openssl rand -hex 32)"
printf 'berzcoin:%s\n' "${COOKIE}" > "${DATADIR}/.cookie"
chmod 600 "${DATADIR}/.cookie"

cat > "${DATADIR}/berzcoin.conf" << EOF
# Auto-generated secure configuration (do not commit; edit as needed)
network=mainnet
bind=0.0.0.0
port=8333
rpcbind=127.0.0.1
rpcport=8332
rpcallowip=127.0.0.1
disablewallet=false
mining=false
rpc_require_auth=true
EOF

chmod 600 "${DATADIR}/berzcoin.conf"

if command -v ufw >/dev/null 2>&1; then
  echo "[*] Configuring firewall (ufw): allow P2P 8333/tcp; RPC 8332 stays localhost-only."
  if command -v sudo >/dev/null 2>&1; then
    sudo ufw allow 8333/tcp comment 'BerzCoin P2P' || true
  else
    ufw allow 8333/tcp comment 'BerzCoin P2P' || true
  fi
else
  echo "[*] ufw not found; configure your firewall manually (allow 8333/tcp for P2P if needed)."
fi

echo "[OK] Secure deployment complete."
echo ""
echo "Start the node:"
echo "  berzcoind -conf ${DATADIR}/berzcoin.conf"
echo ""
echo "Notes:"
echo "  - RPC is bound to localhost; use ${DATADIR}/.cookie for berzcoin-cli (or cookie auth)."
echo "  - Wallet activation is private-key based (activatewallet)."
