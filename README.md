# syclops-pipeline

A pipeline built around the [Syclops](https://github.com/DFKI-NI/syclops) rendering tool to
generate complex synthetic datasets in an automated fashion: asset library management,
versioned job configs, one container for local and Slurm rendering, sharding and merging.

```bash
git clone --recurse-submodules git@github.com:niqbal996/syclops-pipeline.git
```

## 1. Asset libraries

A library is a folder with an `assets.yaml` that has a top-level `name:`. Assets are
referenced in jobs as `Library Name/Asset Name`.

```bash
# check manifests against files (missing files, bad types, Windows paths)
python3 tools/check_library.py /mnt/d/blender_assets
python3 tools/check_library.py /mnt/d/blender_assets/LalWeco --list     # asset references for jobs

# new library: scaffold assets.yaml from the files, then edit names/tags by hand
python3 tools/make_manifest.py /mnt/d/blender_assets/MyWeeds --name "My Weeds" --tags weed --license CC0 --dry-run
python3 tools/make_manifest.py /mnt/d/blender_assets/MyWeeds --name "My Weeds" --tags weed --license CC0

# inspect .blend files (Blender version, stray cameras/lights, origin, units, missing textures)
scripts/docker_run.sh blender -b --factory-startup -P tools/inspect_blend.py -- /mnt/d/blender_assets/MyWeeds
```

Checklist for new `.blend` assets:
- Saved with Blender **3.6** (Syclops renders with 3.6.1; 4.x files may lose data).
- Only the asset objects in the file: each object becomes a scatter/crop variant.
- Origin at the base of the plant, real-world size in metres, transforms applied.
- Textures packed (File > External Data > Pack Resources) or relative paths inside the library.

## 2. Local rendering (WSL + Docker)

Local Docker renders with CUDA, because OptiX crashes Blender 3.6 on WSL (`GPU_BACKEND` in `docker_run.sh`).

```bash
docker build -f container/Dockerfile -t syclops-pipeline:latest .
ASSET_ROOTS=/mnt/d/blender_assets OUT_ROOT=$PWD/output \
  scripts/docker_run.sh scripts/render_dataset.sh jobs/smoke_test.yaml output/smoke 2 0
ASSET_ROOTS=/mnt/d/blender_assets OUT_ROOT=$PWD/output \
  scripts/docker_run.sh scripts/render_dataset.sh jobs/smoke_test.yaml output/smoke 2 1
python3 tools/merge_shards.py output/smoke output/smoke/merged
```

`render_dataset.sh JOB RUN_DIR [SHARDS] [INDEX] [--set key.path=value ...]`. `--set` overrides any job
value for quick tests, e.g. `--set steps=2 --set sensor.syclops_sensor_camera.0.resolution=[256,256]`.

## 3. Cluster (Slurm + enroot)

1. Image: `bash container/build_cluster_image.sh` runs the install script in an NVIDIA base image and saves
   `/netscratch/naeem/syclops-pipeline.sqsh` (see [container/README.md](container/README.md)).
2. Assets: `rsync -aP /mnt/d/blender_assets/ <cluster>:/netscratch/naeem/blender_assets/`
3. Repo: clone to `/home/iqbal/syclops-pipeline` (with submodules), adjust [slurm/cluster.env](slurm/cluster.env).
4. Submit: `slurm/submit.sh jobs/<job>.yaml <SHARDS>`. This is an array with one shard per GPU.
   Failed shards can be resubmitted with `RUN_DIR=<run> slurm/submit.sh jobs/<job>.yaml <SHARDS> --array=3,7`.
5. Merge: `python3 tools/merge_shards.py <run> <run>/merged` (inside the container if the login node lacks PyYAML).

Shard *i* renders `steps/SHARDS` frames with seeds `seed + 1000*i`; merging renumbers frames
globally and writes `index.csv` (global frame → shard, local frame, seed).
