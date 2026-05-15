#!/usr/bin/env bash
# Build Linux release artifacts (.deb/.rpm) into dist/packages using fpm.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
PKG_DIR="${DIST_DIR}/packages"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required."
  exit 1
fi

if ! command -v fpm >/dev/null 2>&1; then
  echo "Error: fpm is required to build .deb/.rpm artifacts."
  echo "Install example (Debian/Ubuntu): sudo apt-get install -y ruby ruby-dev build-essential && sudo gem install --no-document fpm"
  exit 1
fi

if ! command -v rpmbuild >/dev/null 2>&1; then
  echo "Error: rpmbuild is required for RPM generation."
  echo "Install example (Debian/Ubuntu): sudo apt-get install -y rpm"
  exit 1
fi

mkdir -p "${PKG_DIR}"

echo "[1/4] Building Python wheel/sdist..."
(cd "${ROOT_DIR}" && python3 -m build)

WHEEL_PATH="$(ls -1 "${DIST_DIR}"/berzcoin-*.whl | head -n 1)"
if [[ -z "${WHEEL_PATH}" ]]; then
  echo "Error: wheel not found in dist/ after build."
  exit 1
fi

VERSION="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path
p = Path("pyproject.toml")
data = tomllib.loads(p.read_text(encoding="utf-8"))
print(data["project"]["version"])
PY
)"

TMP_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_ROOT}"
}
trap cleanup EXIT

make_root() {
  local name="$1"
  local root="${TMP_ROOT}/${name}"
  mkdir -p "${root}/usr/lib/berzcoin" "${root}/usr/bin" "${root}/usr/share/doc/${name}"
  python3 -m pip install --no-deps --target "${root}/usr/lib/berzcoin" "${WHEEL_PATH}" >/dev/null
  cat > "${root}/usr/share/doc/${name}/README" <<EOF
${name} package for BerzCoin ${VERSION}
Built from wheel: $(basename "${WHEEL_PATH}")
EOF
  echo "${root}"
}

write_wrapper() {
  local path="$1"
  local module="$2"
  cat > "${path}" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="/usr/lib/berzcoin\${PYTHONPATH:+:\${PYTHONPATH}}"
exec python3 -m ${module} "\$@"
EOF
  chmod 755 "${path}"
}

echo "[2/4] Staging berzcoin-core package..."
CORE_ROOT="$(make_root "berzcoin-core")"
write_wrapper "${CORE_ROOT}/usr/bin/berzcoind" "node.app.main"

echo "[3/4] Staging berzcoin-cli package..."
CLI_ROOT="$(make_root "berzcoin-cli")"
write_wrapper "${CLI_ROOT}/usr/bin/berzcoin-cli" "cli.main"
write_wrapper "${CLI_ROOT}/usr/bin/berzcoin-wallet" "cli.wallet_standalone"

echo "[4/4] Building .deb/.rpm artifacts..."
fpm -s dir -t deb -n berzcoin-core -v "${VERSION}" -C "${CORE_ROOT}" \
  --description "BerzCoin full node" \
  --license "MIT" \
  --url "https://example.com/berzcoin" \
  -p "${PKG_DIR}/berzcoin-core_${VERSION}_ARCH.deb" \
  usr/lib/berzcoin usr/bin/berzcoind usr/share/doc/berzcoin-core

fpm -s dir -t deb -n berzcoin-cli -v "${VERSION}" -C "${CLI_ROOT}" \
  --description "BerzCoin CLI and wallet client" \
  --license "MIT" \
  --url "https://example.com/berzcoin" \
  -p "${PKG_DIR}/berzcoin-cli_${VERSION}_ARCH.deb" \
  usr/lib/berzcoin usr/bin/berzcoin-cli usr/bin/berzcoin-wallet usr/share/doc/berzcoin-cli

fpm -s dir -t rpm -n berzcoin-core -v "${VERSION}" -C "${CORE_ROOT}" \
  --description "BerzCoin full node" \
  --license "MIT" \
  --url "https://example.com/berzcoin" \
  -p "${PKG_DIR}/berzcoin-core-${VERSION}.ARCH.rpm" \
  usr/lib/berzcoin usr/bin/berzcoind usr/share/doc/berzcoin-core

fpm -s dir -t rpm -n berzcoin-cli -v "${VERSION}" -C "${CLI_ROOT}" \
  --description "BerzCoin CLI and wallet client" \
  --license "MIT" \
  --url "https://example.com/berzcoin" \
  -p "${PKG_DIR}/berzcoin-cli-${VERSION}.ARCH.rpm" \
  usr/lib/berzcoin usr/bin/berzcoin-cli usr/bin/berzcoin-wallet usr/share/doc/berzcoin-cli

echo "Build complete. Artifacts:"
ls -1 "${PKG_DIR}"
