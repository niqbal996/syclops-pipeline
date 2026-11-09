#!/usr/bin/env bash
# Run a command in the syclops-pipeline image locally (WSL/Linux, NVIDIA runtime).
# The repo and every ASSET_ROOTS / OUT_ROOT folder are mounted at the SAME path
# inside the container, so paths are identical inside and outside.
#
#   ASSET_ROOTS=/mnt/d/blender_assets OUT_ROOT=/mnt/e/datasets/syclops_runs \
#     scripts/docker_run.sh scripts/render_dataset.sh jobs/smoke_test.yaml /mnt/e/datasets/syclops_runs/smoke
#   scripts/docker_run.sh blender -b --factory-startup -P tools/inspect_blend.py -- /mnt/d/blender_assets/LalWeco
#   scripts/docker_run.sh            # interactive shell
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-syclops-pipeline:latest}"
ASSET_ROOTS="${ASSET_ROOTS:-/mnt/d/blender_assets}"
OUT_ROOT="${OUT_ROOT:-$REPO/output}"
mkdir -p "$OUT_ROOT"

MOUNTS=(-v "$REPO:$REPO" -v "$OUT_ROOT:$OUT_ROOT")
IFS=: read -r -a ROOTS <<< "$ASSET_ROOTS"
for r in "${ROOTS[@]}"; do MOUNTS+=(-v "$r:$r:ro"); done

TTY=(); [[ -t 0 && -t 1 ]] && TTY=(-it)
exec docker run --rm "${TTY[@]}" --gpus all --shm-size=8g \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e ASSET_ROOTS="$ASSET_ROOTS" -e FORCE="${FORCE:-0}" \
  -e GPU_BACKEND="${GPU_BACKEND:-CUDA}" -e POSTPROC_GRACE="${POSTPROC_GRACE:-300}" \
  "${MOUNTS[@]}" -w "$REPO" "$IMAGE" "${@:-bash}"
