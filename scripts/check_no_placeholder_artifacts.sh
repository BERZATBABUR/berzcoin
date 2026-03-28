#!/usr/bin/env bash
# Fail when placeholder binary artifacts are committed to the repo.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

mapfile -t BAD < <(
  find "${ROOT_DIR}/packaging" -type f \
    \( -name "*.deb" -o -name "*.rpm" \) \
    -size 0 -print | sort
)

if [[ "${#BAD[@]}" -gt 0 ]]; then
  echo "Error: zero-byte package artifacts detected:"
  for f in "${BAD[@]}"; do
    echo "  - ${f}"
  done
  echo ""
  echo "Delete placeholder binaries and generate real artifacts into dist/packages/."
  exit 1
fi

echo "OK: no placeholder .deb/.rpm files found under packaging/."
