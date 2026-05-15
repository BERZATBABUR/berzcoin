#!/bin/bash
# Reproducible build script for BerzCoin

set -e

echo "🔧 Building BerzCoin reproducibly..."

# Use fixed Python version
PYTHON_VERSION="3.10.12"
DOCKER_IMAGE="python:${PYTHON_VERSION}-slim"

# Build in Docker for consistency
docker run --rm -v $(pwd):/build -w /build $DOCKER_IMAGE bash -c "
    # Install build dependencies
    pip install build wheel
    
    # Build with deterministic settings
    export PYTHONHASHSEED=0
    export SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)
    
    # Build packages
    python -m build --no-isolation
"

echo "✅ Build complete! Packages in dist/"
ls -la dist/

# Generate checksums
sha256sum dist/* > dist/SHA256SUMS
gpg --detach-sign --armor dist/SHA256SUMS

echo "✅ Checksums generated"
echo "✅ GPG signature created"
