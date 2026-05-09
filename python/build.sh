#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configurable via env vars (for CI) or defaults (for local)
TAG="${TAG:-dev}"
GHCR_IMAGE="${GHCR_IMAGE:-ghcr.io/teenyfactories/agent}"
PUSH="${PUSH:-false}"

IMAGE="${GHCR_IMAGE}:${TAG}"

# Build provenance — bake git SHA + build date into the image. Falls back
# gracefully when invoked outside a git checkout (CI tarball, etc).
if BUILD_SHA="$(git -C "${SCRIPT_DIR}" rev-parse --short HEAD 2>/dev/null)"; then
    if ! { git -C "${SCRIPT_DIR}" diff --quiet 2>/dev/null \
        && git -C "${SCRIPT_DIR}" diff --cached --quiet 2>/dev/null; }; then
        BUILD_SHA="${BUILD_SHA}-dirty"
    fi
else
    BUILD_SHA="unknown"
fi
BUILD_DATE="$(date -u +%Y-%m-%d)"

echo "==> Building agent image: ${IMAGE}"
echo "    BUILD_SHA=${BUILD_SHA}"
echo "    BUILD_DATE=${BUILD_DATE}"
docker build \
    --build-arg "BUILD_SHA=${BUILD_SHA}" \
    --build-arg "BUILD_DATE=${BUILD_DATE}" \
    -t "${IMAGE}" \
    -f "${SCRIPT_DIR}/Dockerfile.build" \
    "${SCRIPT_DIR}"

if [ "${PUSH}" = "true" ]; then
    echo "==> Pushing ${IMAGE}"
    docker push "${IMAGE}"
else
    echo "==> Skipping push (set PUSH=true to push)"
fi

echo ""
echo "Done: ${IMAGE}"
