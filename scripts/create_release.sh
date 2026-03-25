#!/usr/bin/env bash
# Create a source tarball (no venv; requires tar).

set -euo pipefail

VERSION="${1:-0.1.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="${ROOT}/berzcoin-${VERSION}"
TARBALL="${ROOT}/berzcoin-${VERSION}.tar.gz"

echo "Creating release ${VERSION}..."

rm -rf "${RELEASE_DIR}"
mkdir -p "${RELEASE_DIR}"

cp -r "${ROOT}/node" "${ROOT}/shared" "${ROOT}/cli" "${RELEASE_DIR}/"
cp -r "${ROOT}/scripts" "${RELEASE_DIR}/"
[[ -f "${ROOT}/README.md" ]] && cp "${ROOT}/README.md" "${RELEASE_DIR}/"
[[ -f "${ROOT}/LICENSE" ]] && cp "${ROOT}/LICENSE" "${RELEASE_DIR}/"
[[ -f "${ROOT}/pyproject.toml" ]] && cp "${ROOT}/pyproject.toml" "${RELEASE_DIR}/"
[[ -f "${ROOT}/requirements.txt" ]] && cp "${ROOT}/requirements.txt" "${RELEASE_DIR}/"

echo "${VERSION}" > "${RELEASE_DIR}/VERSION"

tar -czf "${TARBALL}" -C "${ROOT}" "berzcoin-${VERSION}"
rm -rf "${RELEASE_DIR}"

echo "Created ${TARBALL} ($(du -h "${TARBALL}" | cut -f1))"
