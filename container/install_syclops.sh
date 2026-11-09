#!/usr/bin/env bash
# Install Blender 3.6.1 + Syclops into a running container (any Ubuntu/Debian based
# image, e.g. nvcr.io/nvidia/cuda:*-ubuntu22.04 or nvcr.io/nvidia/pytorch:*).
# Used by container/Dockerfile (local) and container/build_cluster_image.sh (pyxis
# --container-save on the cluster), so both environments are identical.
#
#   bash container/install_syclops.sh [PREFIX=/opt]
#
# The Syclops venv is created from Blender's bundled Python 3.10: Syclops appends the
# venv's site-packages to Blender's sys.path, so the ABI must match, and this makes the
# install independent of the base image's own Python version.
# Needs internet (apt, blender.org, PyPI) and root for apt (pyxis: --container-remap-root).
set -euo pipefail

PREFIX="${1:-/opt}"
BLENDER_VERSION="${BLENDER_VERSION:-3.6.1}"
BLENDER_MAJOR="${BLENDER_VERSION%.*}"
BLENDER_MIRROR="${BLENDER_MIRROR:-https://download.blender.org/release}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYCLOPS_SRC="${SYCLOPS_SRC:-$REPO/external/syclops}"
BLENDER_DIR="$PREFIX/blender-$BLENDER_VERSION"
VENV="$PREFIX/syclops-venv"

[[ -f "$SYCLOPS_SRC/pyproject.toml" ]] || { echo "Syclops source not found at $SYCLOPS_SRC (git submodule update --init?)" >&2; exit 1; }

echo "== system libraries for Blender"
if command -v apt-get >/dev/null && [[ $(id -u) == 0 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates wget xz-utils \
    libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxext6 libxkbcommon0 \
    libsm6 libice6 libgl1 libegl1 libglu1-mesa libgomp1 libglib2.0-0
  rm -rf /var/lib/apt/lists/*
else
  echo "WARNING: not root or no apt-get, skipping system packages (pyxis: use --container-remap-root)"
fi

echo "== Blender $BLENDER_VERSION -> $BLENDER_DIR"
if [[ ! -x "$BLENDER_DIR/blender" ]]; then
  tmp="$(mktemp -d)"
  url="$BLENDER_MIRROR/Blender$BLENDER_MAJOR/blender-$BLENDER_VERSION-linux-x64.tar.xz"
  if command -v wget >/dev/null; then wget -q "$url" -O "$tmp/b.tar.xz"; else curl -fsSL "$url" -o "$tmp/b.tar.xz"; fi
  mkdir -p "$PREFIX"
  tar -xJf "$tmp/b.tar.xz" -C "$tmp"
  mv "$tmp/blender-$BLENDER_VERSION-linux-x64" "$BLENDER_DIR"
  rm -rf "$tmp"
fi
"$BLENDER_DIR/blender" --version | head -1

echo "== Syclops venv (Blender's Python 3.10) -> $VENV"
BPY="$BLENDER_DIR/$BLENDER_MAJOR/python/bin/python3.10"
"$BPY" -m venv "$VENV"
"$VENV/bin/pip" install --no-cache-dir --upgrade "pip<25" "setuptools<70" wheel
# Install from a copy: pip would otherwise write build/ and *.egg-info into the repo.
build="$(mktemp -d)"; cp -r "$SYCLOPS_SRC" "$build/syclops"; rm -rf "$build/syclops/.git"
"$VENV/bin/pip" install --no-cache-dir "$build/syclops"
rm -rf "$build"
# Blender 3.6 ships setuptools 63; its pkg_resources cannot resolve wheels with normalized
# dist-info names (ruamel_yaml_clib-0.2.15.dist-info) -> pin a version with the old naming.
"$VENV/bin/pip" install --no-cache-dir "ruamel.yaml.clib==0.2.8"
# Same check Blender does at runtime when loading syclops plugins.
"$BPY" -c "import sys; sys.path.append('$VENV/lib/python3.10/site-packages'); import pkg_resources; pkg_resources.require('syclops'); print('Blender python resolves syclops dependencies: OK')"

cat > "$PREFIX/syclops_env.sh" <<EOF
# Sourced by scripts/render_dataset.sh (container env vars are not saved by --container-save).
export PATH="$VENV/bin:\$PATH"
export SYCLOPS_BLENDER_DIR="$BLENDER_DIR"
export SYCLOPS_BLENDER_VERSION="$BLENDER_VERSION"
export PYTHONUNBUFFERED=1
EOF
ln -sf "$BLENDER_DIR/blender" /usr/local/bin/blender 2>/dev/null || true
echo "== done. Environment file: $PREFIX/syclops_env.sh"
