#!/usr/bin/env bash
# Render ONE shard of a Syclops job. Runs inside the syclops-pipeline container
# (Slurm/pyxis on the cluster, scripts/docker_run.sh locally).
#
#   scripts/render_dataset.sh JOB.yaml RUN_DIR [SHARDS] [INDEX] [--set key.path=value ...]
#
#   JOB      job description (jobs/*.yaml)
#   RUN_DIR  output root of this run; shard i goes to RUN_DIR/shard_<iii>/
#   SHARDS   number of shards the job is split into (default 1)
#   INDEX    shard index (default $SLURM_ARRAY_TASK_ID, else 0)
#   --set    overrides passed to tools/shard_job.py (e.g. --set steps=4)
#
# Env:
#   ASSET_ROOTS  colon-separated folders containing asset libraries
#                (default /netscratch/naeem/blender_assets)
#   SYCLOPS_HOME per-task syclops install folder (default: node-local tmp)
#   FORCE=1      re-render even if the shard is already complete
#   GPU_BACKEND  override render_hardware: OPTIX | CUDA (CUDA also switches the denoiser to
#                OPENIMAGEDENOISE). docker_run.sh sets CUDA: OptiX crashes Blender 3.6 on WSL.
#   POSTPROC_GRACE  seconds the postprocessor may run after Blender exited (default 300)
set -euo pipefail
# Environment written by container/install_syclops.sh (PATH to syclops venv, Blender dir).
SYCLOPS_ENV="${SYCLOPS_ENV:-/opt/syclops_env.sh}"
if [[ -f "$SYCLOPS_ENV" ]]; then source "$SYCLOPS_ENV"; fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOB="$(realpath "$1")"; RUN_DIR="$(realpath -m "$2")"; SHARDS="${3:-1}"; INDEX="${4:-${SLURM_ARRAY_TASK_ID:-0}}"
shift $(( $# < 4 ? $# : 4 ))
OVERRIDES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --set) OVERRIDES+=(--set "$2"); shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done

ASSET_ROOTS="${ASSET_ROOTS:-/netscratch/naeem/blender_assets}"
BLENDER_DIR="${SYCLOPS_BLENDER_DIR:-/opt/blender-3.6.1}"
BLENDER_VERSION="${SYCLOPS_BLENDER_VERSION:-3.6.1}"
SHARD_DIR="$RUN_DIR/shard_$(printf %03d "$INDEX")"
SYCLOPS_HOME="${SYCLOPS_HOME:-${TMPDIR:-/tmp}/syclops_home_${SLURM_JOB_ID:-local}_${INDEX}}"

echo "== shard $INDEX/$SHARDS  job=$JOB  out=$SHARD_DIR  host=$(hostname)"
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || echo "no GPU visible"

mkdir -p "$SHARD_DIR"
cd "$REPO"
# Skip shards that are already complete (makes resubmitting a whole array cheap).
if [[ "${FORCE:-0}" != 1 && -f "$SHARD_DIR/shard_info.json" ]]; then
  if python3 - "$SHARD_DIR" <<'EOF'
import json, sys
from pathlib import Path
sys.path.insert(0, "tools")
from merge_shards import pick_run
d = Path(sys.argv[1]); info = json.loads((d / "shard_info.json").read_text())
sys.exit(0 if pick_run(d, info["steps"], False) else 1)
EOF
  then echo "shard already complete, skipping (FORCE=1 to re-render)"; exit 0; fi
fi

# Per-task syclops install folder: blender symlink + catalog + schema.
mkdir -p "$SYCLOPS_HOME"
ln -sfn "$BLENDER_DIR" "$SYCLOPS_HOME/blender-$BLENDER_VERSION"
export XDG_DATA_HOME="$SYCLOPS_HOME/xdg"   # syclops stores its config via appdirs

IFS=: read -r -a ROOTS <<< "$ASSET_ROOTS"
python3 tools/build_catalog.py --install-folder "$SYCLOPS_HOME" -q "${ROOTS[@]}"
if [[ -n "${GPU_BACKEND:-}" ]]; then
  OVERRIDES+=(--set "render_hardware=$GPU_BACKEND")
  [[ "$GPU_BACKEND" != OPTIX ]] && OVERRIDES+=(--set "denoising_algorithm=OPENIMAGEDENOISE")
fi
python3 tools/shard_job.py "$JOB" --shards "$SHARDS" --index "$INDEX" -o "$SHARD_DIR/job.yaml" "${OVERRIDES[@]}"
cp "$SYCLOPS_HOME/asset_catalog.yaml" "$SHARD_DIR/asset_catalog.input.yaml"

# Watchdog: if Blender crashes, syclops' postprocessor waits forever for missing frames.
# Kill the run when Blender is gone and syclops has not finished within POSTPROC_GRACE s.
blender_running() {
  local p
  for p in /proc/[0-9]*; do
    [[ "$(readlink "$p/exe" 2>/dev/null)" == "$BLENDER_DIR/blender" ]] && return 0
  done
  return 1
}
start=$(date +%s)
setsid syclops -if "$SYCLOPS_HOME" -j "$SHARD_DIR/job.yaml" -o "$SHARD_DIR" &
SYC_PID=$!
seen_blender=0; gone_since=0; killed=0
while kill -0 "$SYC_PID" 2>/dev/null; do
  sleep 10
  if blender_running; then seen_blender=1; gone_since=0; continue; fi
  (( seen_blender )) || continue
  (( gone_since )) || gone_since=$(date +%s)
  if (( $(date +%s) - gone_since > ${POSTPROC_GRACE:-300} )); then
    echo >&2; echo "!! Blender exited but syclops is still running after ${POSTPROC_GRACE:-300}s: killing (crash?)" >&2
    grep -h -A3 -i "crash\|error\|Traceback" "$SHARD_DIR"/*/blender.log 2>/dev/null | tail -20 >&2 || true
    kill -- -"$SYC_PID" 2>/dev/null || true; killed=1; break
  fi
done
rc=0; wait "$SYC_PID" || rc=$?
(( killed )) && rc=1

# Verify the shard really contains all frames (syclops exits 0 on some failures).
if python3 - "$SHARD_DIR" <<'EOF2'
import json, sys
from pathlib import Path
sys.path.insert(0, "tools")
from merge_shards import pick_run
d = Path(sys.argv[1]); info = json.loads((d / "shard_info.json").read_text())
sys.exit(0 if pick_run(d, info["steps"], False) else 1)
EOF2
then
  echo; echo "== shard $INDEX complete in $(( $(date +%s) - start )) s"
else
  echo >&2; echo "!! shard $INDEX INCOMPLETE (syclops exit code $rc), see $SHARD_DIR/*/blender.log" >&2
  exit 1
fi
