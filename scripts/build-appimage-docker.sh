#!/usr/bin/env bash
# Build a portable AppImage inside Ubuntu 22.04 (FHS). Use this on NixOS —
# host `make appimage` links Flutter against /nix/store and will not run on
# other distros.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="${TBFG_APPIMAGE_IMAGE:-tb-fitgirl-appimage-builder:22.04}"
DOCKERFILE="${ROOT}/packaging/appimage/Dockerfile"

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: missing $1" >&2; exit 1; }; }
need docker

# Rootless Docker (common on NixOS): socket under $XDG_RUNTIME_DIR.
if [[ -z "${DOCKER_HOST:-}" ]]; then
  _rsock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock"
  if [[ -S "$_rsock" && ! -S /var/run/docker.sock ]]; then
    export DOCKER_HOST="unix://${_rsock}"
  fi
fi

if ! docker info >/dev/null 2>&1; then
  echo "error: docker daemon not running (start it, then retry)" >&2
  echo "hint: rootless -> export DOCKER_HOST=unix://\$XDG_RUNTIME_DIR/docker.sock" >&2
  exit 1
fi

echo "==> Building builder image ${IMAGE}"
docker build -t "${IMAGE}" -f "${DOCKERFILE}" "${ROOT}/packaging/appimage"

echo "==> Running AppImage build in container"
mkdir -p "${ROOT}/dist" "${ROOT}/build"
# Rootless Docker: container root maps to the host user, so bind-mount
# writes stay owned by you. Named user (uid 1000) would hit a different
# subuid and fail to touch host-owned build trees.
docker run --rm \
  --user root \
  -e HOME=/root \
  -e PUB_CACHE=/tmp/pub-cache \
  -e TAR_OPTIONS=--no-same-owner \
  -e PATH=/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  -v "${ROOT}:/src:rw" \
  -w /src \
  "${IMAGE}" \
  bash -c 'git config --global --add safe.directory /opt/flutter && rm -rf build/appimage gui/build && bash scripts/build-appimage.sh'

echo "==> Artifact(s):"
ls -lh "${ROOT}/dist"/tb-fitgirl-*-*.AppImage
