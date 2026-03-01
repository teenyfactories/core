#!/usr/bin/env bash
set -euo pipefail

TAG="${1:-dev}"
GHCR_IMAGE="ghcr.io/teenyfactories/agent:${TAG}"
DOCKERHUB_IMAGE="teenyfactories/agent:${TAG}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Building and publishing pip package..."
cd "${SCRIPT_DIR}"
pip install build
python -m build
twine upload dist/*

echo ""
echo "Building teenyfactories/agent:${TAG}..."
docker build -t "${GHCR_IMAGE}" "${SCRIPT_DIR}"
# docker build -t "${DOCKERHUB_IMAGE}" "${SCRIPT_DIR}"

echo ""
echo "Pushing to GitHub Container Registry..."
docker push "${GHCR_IMAGE}"

# echo ""
# echo "Pushing to Docker Hub..."
# docker push "${DOCKERHUB_IMAGE}"

echo ""
echo "Done. Pushed:"
echo "  PyPI: teenyfactories"
echo "  ${GHCR_IMAGE}"
# echo "  ${DOCKERHUB_IMAGE}"
