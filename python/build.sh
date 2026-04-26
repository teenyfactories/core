#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configurable via env vars (for CI) or defaults (for local)
TAG="${TAG:-dev}"
GHCR_IMAGE="${GHCR_IMAGE:-ghcr.io/teenyfactories/agent}"
PUSH="${PUSH:-false}"

IMAGE="${GHCR_IMAGE}:${TAG}"

echo "==> Building agent image: ${IMAGE}"
docker build -t "${IMAGE}" -f "${SCRIPT_DIR}/Dockerfile.build" "${SCRIPT_DIR}"

if [ "${PUSH}" = "true" ]; then
    echo "==> Pushing ${IMAGE}"
    docker push "${IMAGE}"
else
    echo "==> Skipping push (set PUSH=true to push)"
fi

echo ""
echo "Done: ${IMAGE}"
