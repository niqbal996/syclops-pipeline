#!/usr/bin/env bash
# Build the syclops image ON THE CLUSTER: start an NVIDIA base image with pyxis, run
# container/install_syclops.sh inside it, and save the result as a .sqsh for later jobs.
# Run from the repo root on the login node:
#
#   bash container/build_cluster_image.sh
#   BASE_IMAGE=/netscratch/naeem/nvcr.io_nvidia_pytorch_25.09-py3-HF.sqsh bash container/build_cluster_image.sh
#
# The compute node needs internet access (apt, blender.org, PyPI) for this one job.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${CLUSTER_ENV:-$REPO/slurm/cluster.env}"

# Small CUDA base (~250 MB). Any Ubuntu-based NVIDIA image works, since the install does not
# depend on the image's Python. Pyxis syntax for registry images: nvcr.io#nvidia/cuda:<tag>
BASE_IMAGE="${BASE_IMAGE:-nvcr.io#nvidia/cuda:12.4.1-base-ubuntu22.04}"
OUT="${OUT:-$IMAGE}"   # from cluster.env, e.g. /netscratch/naeem/syclops-pipeline.sqsh

git -C "$REPO" submodule update --init
[[ -e "$OUT" ]] && { echo "$OUT exists; delete it or set OUT=... first" >&2; exit 1; }

echo "base image: $BASE_IMAGE"
echo "saving to:  $OUT"
srun -K --ntasks=1 --gpus-per-task=1 --cpus-per-gpu=4 --mem=16000 -p "$PARTITION" --time=01:00:00 \
  --job-name=syclops_image_build \
  --container-image="$BASE_IMAGE" \
  --container-remap-root \
  --container-save="$OUT" \
  --container-mounts="$MOUNTS" \
  --container-workdir="$REPO" \
  bash -c "bash container/install_syclops.sh /opt && \
           source /opt/syclops_env.sh && nvidia-smi -L && \
           blender -b --factory-startup --python-expr 'import bpy; p=bpy.context.preferences.addons[\"cycles\"].preferences; p.refresh_devices(); print(\"Cycles devices:\", [(d.name, d.type) for d in p.devices])'"

echo "image saved: $OUT"
echo "smoke test:  slurm/submit.sh jobs/smoke_test.yaml 1"
