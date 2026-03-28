#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() {
  printf '%s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

print_install_hints() {
  local os_id=""
  local os_like=""
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    os_id="${ID:-}"
    os_like="${ID_LIKE:-}"
  fi

  say ""
  say "Install hints for this environment:"

  if have apt-get || [[ "$os_id" == "ubuntu" || "$os_id" == "debian" || "$os_like" == *"debian"* ]]; then
    say "  sudo apt-get update"
    say "  sudo apt-get install -y bitcoind bitcoin-cli"
    return
  fi

  if have dnf || [[ "$os_like" == *"rhel"* || "$os_like" == *"fedora"* ]]; then
    say "  sudo dnf install -y bitcoin"
    say "  # if package name differs on your repo: search with 'dnf search bitcoin'"
    return
  fi

  if have yum; then
    say "  sudo yum install -y bitcoin"
    say "  # if package name differs on your repo: search with 'yum search bitcoin'"
    return
  fi

  if have pacman || [[ "$os_id" == "arch" ]]; then
    say "  sudo pacman -S --needed bitcoin"
    return
  fi

  if have brew || [[ "$os_id" == "macos" ]]; then
    say "  brew install bitcoin"
    return
  fi

  say "  Could not detect package manager automatically."
  say "  Install Bitcoin Core so both 'bitcoind' and 'bitcoin-cli' are in PATH."
}

main() {
  say "=== BerzCoin Core Differential Preflight ==="
  say "Repository: ${ROOT_DIR}"

  local missing=0

  if have bitcoind; then
    say "[OK] bitcoind found: $(command -v bitcoind)"
    say "     version: $(bitcoind --version 2>/dev/null | head -n 1 || true)"
  else
    say "[MISS] bitcoind not found in PATH"
    missing=1
  fi

  if have bitcoin-cli; then
    say "[OK] bitcoin-cli found: $(command -v bitcoin-cli)"
    say "     version: $(bitcoin-cli --version 2>/dev/null | head -n 1 || true)"
  else
    say "[MISS] bitcoin-cli not found in PATH"
    missing=1
  fi

  if [[ "$missing" -ne 0 ]]; then
    print_install_hints
    say ""
    say "After installation, re-run:"
    say "  ${ROOT_DIR}/scripts/core_diff_preflight.sh"
    say ""
    say "Then run differential tests:"
    say "  BERZ_ENABLE_CORE_DIFF=1 pytest -q ${ROOT_DIR}/tests/integration/test_bitcoin_core_differential.py -rs"
    say ""
    say "To enforce dependency presence in CI/release gates:"
    say "  BERZ_ENABLE_CORE_DIFF=1 BERZ_REQUIRE_CORE_DIFF=1 pytest -q ${ROOT_DIR}/tests/integration/test_bitcoin_core_differential.py -rs"
    exit 1
  fi

  say ""
  say "[READY] Core differential dependencies are available."
  say "Run:"
  say "  BERZ_ENABLE_CORE_DIFF=1 pytest -q ${ROOT_DIR}/tests/integration/test_bitcoin_core_differential.py -rs"
  say "Strict gate mode:"
  say "  BERZ_ENABLE_CORE_DIFF=1 BERZ_REQUIRE_CORE_DIFF=1 pytest -q ${ROOT_DIR}/tests/integration/test_bitcoin_core_differential.py -rs"
}

main "$@"
