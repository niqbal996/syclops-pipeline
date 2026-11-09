# syclops-pipeline: notes for Claude

Tooling around [Syclops](https://github.com/DFKI-NI/syclops) (Blender-based synthetic data
generator) to manage asset libraries, define jobs and render datasets locally (WSL +
Docker) and on the DFKI Slurm cluster (pyxis/enroot). Owner: Naeem Iqbal.

**Read this file instead of re-exploring `external/syclops`.** Everything below was
verified against Syclops commit `a64c5f7` (v1.4.4), pinned as submodule `external/syclops`.

## Layout
- `container/install_syclops.sh`: **single source of truth for the environment**. Installs apt libs,
  Blender 3.6.1 to `/opt/blender-3.6.1`, and a venv `/opt/syclops-venv` **created from Blender's bundled
  Python 3.10** (works on any Ubuntu base image), with syclops non-editable. Writes `/opt/syclops_env.sh`.
- `container/build_cluster_image.sh`: on the cluster, srun on an NVIDIA base image with
  `--container-remap-root --container-save=$IMAGE` running the install script. The user does NOT want to
  transfer images; the cluster builds its own `.sqsh`.
- `container/Dockerfile`: local image (`nvidia/cuda:12.4.1-base-ubuntu22.04` + same install script).
- `tools/` (stdlib + PyYAML, except `inspect_blend.py`, which runs inside Blender):
  - `syclops_assets.py`: shared manifest parsing/validation (mirrors upstream crawler rules)
  - `check_library.py`: manifest vs disk report; `--prune` (keeps .bak); `--list` asset refs
  - `make_manifest.py`: scaffold/extend `assets.yaml` (models, HDRIs, PBR folders; picks `_gl` normals)
  - `build_catalog.py`: replaces `syclops -c`; drops assets with missing files, never writes into asset dirs,
    builds `schema.yaml` via `syclops.cli._build_schema`
  - `inspect_blend.py`: saved-with version, top-level meshes, polys, dims, origin, stray cameras/lights,
    missing/absolute textures; `--json` report
  - `shard_job.py`: N shards; seeds `numpy/cycles/textures + 1000*i`; `--set a.b.0.c=v` overrides; writes
    `shard_info.json` (frame_offset)
  - `merge_shards.py`: `RUN/shard_*/<ts>/...` into one dataset; renumbers every `NNNN.ext`, merges
    `metadata.yaml` steps, `index.csv`; newest complete run per shard wins; hardlinks by default
- `scripts/render_dataset.sh JOB RUN_DIR [SHARDS] [INDEX] [--set k=v]`: one shard inside the container.
  Sources `/opt/syclops_env.sh`, skips complete shards (`FORCE=1`), builds catalog from `ASSET_ROOTS`
  (colon-sep) into a per-task install folder (`$TMPDIR`), `GPU_BACKEND` override, **watchdog**: kills
  syclops if Blender is gone and the postprocessor is still waiting after `POSTPROC_GRACE` (300 s),
  then verifies all frames exist (exit 1 otherwise).
- `scripts/docker_run.sh CMD...`: local docker; mounts repo, `ASSET_ROOTS` (ro), `OUT_ROOT` at identical
  paths; defaults `GPU_BACKEND=CUDA`.
- `slurm/submit.sh JOB SHARDS [sbatch args]` + `slurm/cluster.env`: sbatch array (`%MAX_PARALLEL`), one GPU
  per task, `srun --container-image/--container-mounts/--container-workdir`; `RUN_DIR=... --array=3,7` to resubmit.
- `jobs/smoke_test.yaml` (packaged Example Assets only), `jobs/reference/sugarbeet_syn_v6.yaml` (+ class map).

## Verified locally (2026-11-09)
- Image builds; `jobs/smoke_test.yaml` 2 shards x 2 frames at 512px renders with CUDA in ~20-30 s per shard;
  outputs: rect png, semantic/instance/depth npz, YOLO bbox txt, intrinsics/extrinsics yaml; merge OK.
- **OptiX (render and denoise) segfaults Blender 3.6 in Docker on WSL** (empty backtrace at the first
  render); CUDA is fine. Upstream syclops then **hangs forever** (postprocessor waits), hence the watchdog
  (tested: kills and exits 1).
- Blender 3.6's setuptools 63 `pkg_resources` cannot resolve normalized dist-info names
  (`ruamel_yaml_clib-0.2.15`), so plugin loading fails inside Blender. Pinned `ruamel.yaml.clib==0.2.8`;
  the install script checks `pkg_resources.require('syclops')` with Blender's Python.
- Rich output from syclops ends without newline; status lines are prefixed with a blank line.
- `jobs/sugarbeet_v6_local.yaml` (v6 settings, substituted assets, see header) rendered 1 frame at 1024px/256spp on the
  2080 (CUDA + OIDN): ~3 min preprocessing (72 perlin density maps at 1024px, per shard!), ~21 s scene load,
  ~53 s RGB, ~25 s annotations; 300 s total. Outputs verified (sem classes 0/1/2, instance ids, depth 6.3-7.1 m,
  YOLO boxes match masks). **Blender-4.2-saved sugarbeet/LalWeco .blend files load and render fine in 3.6.**

## NOT yet verified on the cluster
- `build_cluster_image.sh` end to end (needs internet on the compute node and apt as remapped root).
- OptiX availability there (the build job prints Cycles devices). H100/sm_90: Blender 3.6 should
  JIT from PTX for CUDA; OptiX compiles kernels at first use (cache in ~/.cache, not persisted, so expect
  a startup delay per job).
- Whether pyxis containers are writable (`$TMPDIR`/`/tmp` used for the per-task install folder).

## How Syclops works (verified)
- **Install folder** (`syclops -if DIR`; persisted via appdirs in `$XDG_DATA_HOME/syclops/config.yaml`; without
  `-if` it *prompts interactively*). Holds `blender-3.6.1/` (auto-downloaded if missing; we symlink),
  `asset_paths.yaml`, `asset_catalog.yaml`, `schema.yaml`.
- **Crawl** (`syclops -c`): a library root = folder with `assets.yaml` containing top-level `name:`; all
  `assets.yaml` below it are merged (only `assets:` mergeable); roots must not nest. Keys containing
  `filepath` (not `md5`) are relative to the root. Upstream **rewrites every manifest** (md5), **raises on the
  first missing file**, stores **absolute `root_path`**. Appends the packaged `Example Assets` library.
- Asset types: `model` (.blend/.fbx/.obj), `texture`, `pbr_texture` (diffuse/normal/roughness/displacement
  `_filepath`, `texture_size` m, `texture_displacement_scale`), `environment_texture` (hdr/exr).
- **Job run** (`syclops -j job.yaml -o OUT`): creates `OUT/<YYYY_MM_DD_HH_MM_SS>/`; jsonschema validation
  (**only prints when invalid**); generates procedural `textures:` into `tmp/` as library `Preprocessed Assets`;
  runs Blender `-b -P syclops/blender/main.py` plus a parallel postprocessor (YOLO) polling metadata.yaml until
  `expected_steps`.
- Blender side: `sys.path.append(venv site-packages)` in Blender's Python 3.10, plugins via
  `pkg_resources` entry points. Plugins `syclops_plugin_ground/environment/crop/scatter/simulated_scatter/object`;
  sensor `syclops_sensor_camera`; outputs `syclops_output_rgb/pixel_annotation/keypoints/object_positions/
  structured_light`; postprocessing `syclops_postprocessing_bounding_boxes`. `for step in range(steps)`,
  `frame_set(step)`, `np.random.seed(seeds.numpy)` once. **No start-frame option**, hence sharding by seed.
- `render_hardware: CUDA|OPTIX`, `render_device: GPU|CPU`, `denoising_algorithm: OPTIX|OPENIMAGEDENOISE`.
- Asset refs: `"Library/Asset"`; not in the catalog means it's treated as a **file path** (v6 used absolute
  `C:/Users/aelmiger/...` Graswald paths). Samplers: `uniform`, `normal`, `linear`, `step`,
  `random_selection: [...]`, `selection_folder`, `selection_asset: {library, type}`, `wildcard: {library, pattern}`.
- Every object in an asset .blend is imported; scatter/crop use each object as a variant (so stray
  cameras/lights/planes get scattered). Unpacked missing textures render pink. Scatter `max_texture_size`,
  `decimate_mesh_factor` reduce memory.
- Output per run: `<cam>/rect/NNNN.png`, `<cam>/{intrinsics,extrinsics}/NNNN.yaml`,
  `<cam>_annotations/{semantic_segmentation,instance_segmentation,depth}/NNNN.npz` (key `array`),
  `<cam>_annotations/bounding_box/NNNN.txt` (YOLO: class cx cy w h), `metadata.yaml` per output folder,
  `class_id_mapping.yaml` (`class_id: [plugin names]`), `logs.log`, `blender.log`.

## Assets: state as of 2026-11-09
- Local root `/mnt/d/blender_assets` (user is still copying the full set + new assets from a network drive):
  `LalWeco` (7 models + 1 PBR soil), `NL_Assets` (lib name "NV Labs Assets", 4 debris models),
  `Polyhaven HDRI` (manifest lists 413, download in progress), `sugarbeets/` (3 .blend; manifest now in `asset_manifests/sugarbeets/assets.yaml`, copied there, lib "DFKI Dataset"),
  `Polyhaven Textures` (86 listed, partly copied; 6 of the 11 v6 ground textures usable on 2026-11-09).
- inspect_blend: **LalWeco and sugarbeets .blend files were saved with Blender 4.2** (render with 3.6 is
  untested: re-save from 3.6 or test). NL_Assets saved with 3.2 (fine; pine_needles has sub-mm pieces).
- **The sugarbeet .blend files differ from those used for v6** (md5 mismatch vs the v6 catalog) and are much
  heavier: large = 3.7M polys vs 137k vertices in v6. Consider decimating.
- v6 needed libraries missing locally: `Polyhaven Textures` (ground), `DFKI Dataset` manifest, Graswald.

## Previous dataset sugarbeet_syn_v6
- Data `/mnt/e/datasets/sugarbeet_syn_v6`; logs/config/catalog in `.../syclops_logs/`.
- Rendered 2024-06-10..14 on Windows, 5000 frames 1024x1024, 256 spp, OptiX, ~59 s/frame (~82 h).
  Classes: 0 ground+misc debris, 1 sugarbeet, 2 weeds+grass. Camera 5-12 m height, 65 mm focal length.

## Environments
- Local: WSL2, RTX 2080 Super Max-Q 8 GB, 12 cores, 27 GB RAM, Docker 28 + nvidia runtime. Windows Blender 4.4
  (authoring only). Old conda env `syclops_venv` unused.
- Cluster: Slurm + pyxis/enroot; images `.sqsh` in `/netscratch/naeem/`; assets `/netscratch/naeem/blender_assets`;
  repo `/home/iqbal/syclops-pipeline`. GPUs: RTXA6000, RTX3090, A100, H100, ...

## Syclops patches (branch `fix/plant-collisions-instance-ids`, fork git@github.com:niqbal996/syclops.git)
- **Crop `collision_avoidance`** (`utility/collision_utils.py`, `plugins/crop.py`): reads the crop geo-node instance
  poses back from the depsgraph, accepts plants largest first, retries colliding ones (yaw, shift <=3 cm, scale down to
  0.7), drops the rest; collision test = BVH overlap of decimated (2000 faces) proxies thickened by `clearance`.
  Plants become linked duplicates **with modifiers cleared** (with the per-object Volume_Attribute modifier every copy
  is a full mesh: 36 GB OOM). Resolver RNG is seeded from job seed+frame+name (global numpy stream untouched, so other
  plugins sample identically). Proxy build ~80 s per shard for the 3 sugarbeet files; resolve ~27 s for ~1100 plants.
  Verified: dense patch 25 intersecting plant pairs -> 0 (full-res check); v6 field: 365/1089 candidates collided.
- Intra-plant leaf crossings exist in the assets (large plants, outside the stem); they don't affect labels (one object).
  Runtime leaf layering was tried and made things worse (pushes leaves into third leaves): fix the assets instead.
- **Instance ids**: AOV = (OBJECT `syclops_object_id`, INSTANCER `syclops_instance_index` (Store Named Attribute added to
  every geo-node tree), INSTANCER `syclops_object_id`), exact in float32; remapped per image to 0 (none), 1..N,
  smallest uint dtype. Semantic -> uint8. `instance_segmentation.id_mode: location_hash` keeps the old int64 hashes
  (object_positions/keypoints still use them).
- **Datumaro**: postprocessor `syclops_postprocessing_datumaro` -> `<run>/<cam>_datumaro/` (annotations/default.json,
  images/default, items/NNNN.json). Core `postprocessing/datumaro_format.py` (numpy only, RLE == pycocotools);
  `tools/syclops_to_datumaro.py` converts any run/merged dir (also old int64 datasets); merge_shards rebuilds it.
  Validated with datumaro 1.13: import, masks == npz, full panoptic coverage, export to coco_instances/coco_panoptic.
- `utility.decimate_mesh` skips meshes with <=3 faces: upstream logged an ERROR (NV Labs pine needles) and the CLI
  then reported "Syclops Blender failed" and **terminated the postprocessor** (affected v6-style jobs).
- `container/Dockerfile.patch`: reinstall only syclops on top of `syclops-pipeline:latest` (seconds).
  `syclops-pipeline:upstream-a64c5f7` = unpatched image. Test job `jobs/test_plant_fixes.yaml`.
- On /mnt/e (drvfs) from the container hardlinks and chmod fail -> copies use shutil.copyfile.

## Next steps
1. When the asset copy finishes: `check_library.py`, `inspect_blend.py` on everything; add manifests
   (`make_manifest.py`) for libraries without one (sugarbeets/DFKI Dataset, new assets).
2. (done) 4.2-saved assets render in 3.6. LalWeco weeds are 1-5 cm, which is tiny at 5-12 m camera height.
3. Write `jobs/sugarbeet_v7.yaml` from the v6 reference with available libraries.
4. On the cluster: build the image, run the smoke test, then a real job.

## Conventions
- Never write into asset folders on shared storage; tools that modify manifests keep `.bak` copies.
- Patches to Syclops go into `external/syclops` (submodule = fork `niqbal996/syclops`, remote `origin`; DFKI-NI is
  remote `upstream`), then rebuild the images (`container/Dockerfile.patch` locally).
