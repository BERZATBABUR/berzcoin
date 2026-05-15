#!/usr/bin/env bash
# Checklist for operators who want to publish a DNS seed or refresh bootstrap_nodes.json.
# This does not register anything automatically; DNS and project lists are maintained by you.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "BerzCoin public seed checklist"
echo "=============================="
echo ""
echo "1. Run a reachable full node (public IP or stable DNS) on your P2P port (default 8333)."
echo "2. Document hostnames in configs/mainnet_seeds.toml (reference layout)."
echo "3. For berzcoind (INI config), use comma-separated hostnames, for example:"
echo "     dnsseed = true"
echo "     dnsseeds = seed1.example.org,seed2.example.org"
echo "4. Publish DNS A (and optionally AAAA) records for those names to your peers."
echo ""
echo "Refresh configs/bootstrap_nodes.json from one or more seed hostnames:"
echo "  python3 \"${REPO_ROOT}/scripts/update_dns_seeds.py\""
echo "  python3 \"${REPO_ROOT}/scripts/update_dns_seeds.py\" your.seed.hostname --port 8333"
echo ""
echo "Edit configs/mainnet_seeds.toml and your deployment config under version control; there is no central registry."
