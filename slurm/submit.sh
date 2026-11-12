#!/usr/bin/env bash
# Submit a Syclops job as a Slurm array (one shard per task, one GPU per task).
#
#   slurm/submit.sh JOB.yaml SHARDS [extra sbatch args...]
#       new run in $OUT_ROOT/<job name>_<timestamp>
#   RUN_DIR=/netscratch/naeem/syclops_runs/x_2026... slurm/submit.sh JOB.yaml SHARDS --array=3,7
#       resubmit selected shards of an existing run (complete shards are skipped anyway)
#   SET="steps=40 sensor.syclops_sensor_camera.0.outputs.syclops_output_rgb.0.samples=32" slurm/submit.sh ...
#       overrides for every shard (space-separated key=value)
#
# Settings come from slurm/cluster.env (override with CLUSTER_ENV=...).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CLUSTER_ENV:-$HERE/cluster.env}"

JOB="$1"; SHARDS="$2"; shift 2
JOB_REL="$(realpath --relative-to="$(dirname "$HERE")" "$JOB")"   # path inside the repo
NAME="$(basename "$JOB" .yaml)"; NAME="${NAME%.syclops}"
RUN_DIR="${RUN_DIR:-$OUT_ROOT/${NAME}_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$RUN_DIR/slurm_logs"
cp "$JOB" "$RUN_DIR/job.source.yaml"
git -C "$HERE/.." rev-parse HEAD > "$RUN_DIR/pipeline_commit.txt" 2>/dev/null || true

SET_ARGS=""
for kv in ${SET:-}; do SET_ARGS+=" --set $kv"; done

sbatch --array="0-$((SHARDS - 1))%${MAX_PARALLEL}" "$@" \
  --job-name="syc_${NAME}" --partition="$PARTITION" \
  --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-gpu="$CPUS_PER_GPU" --mem="$MEM" --time="$TIME" \
  --mail-type="$MAIL_TYPE" --mail-user="$MAIL_USER" \
  --output="$RUN_DIR/slurm_logs/%A_%a.out" \
  --export=ALL,ASSET_ROOTS="$ASSET_ROOTS" \
  --wrap="srun -K --container-image='$IMAGE' --container-mounts='$MOUNTS' --container-workdir='$REPO' \
    bash scripts/render_dataset.sh '$JOB_REL' '$RUN_DIR' '$SHARDS' \"\$SLURM_ARRAY_TASK_ID\" $SET_ARGS"

echo "run dir: $RUN_DIR"
echo "merge when done:  python3 tools/merge_shards.py '$RUN_DIR' '$RUN_DIR/merged'"
