# Container

The environment is defined by one script, [install_syclops.sh](install_syclops.sh). It installs
Blender 3.6.1 into `/opt/blender-3.6.1` and Syclops (from `external/syclops`) into a venv at
`/opt/syclops-venv`, then writes `/opt/syclops_env.sh`, which `scripts/render_dataset.sh` sources.

- **Cluster**: [build_cluster_image.sh](build_cluster_image.sh) starts an NVIDIA base image with
  pyxis, runs the install script inside it and saves the result with `--container-save`.
- **Local**: [Dockerfile](Dockerfile) runs the same script on the same kind of base image.

The venv is created from **Blender's bundled Python 3.10**, not the image's Python. Syclops
appends the venv's site-packages to Blender's `sys.path`, so compiled wheels must match
Blender's ABI. Because of this, any Ubuntu-based NVIDIA image works as the base, including the
`pytorch:25.09` image (Python 3.12). A small `cuda:*-base` image is the default since nothing else
is needed.

## Cluster: build once, reuse

```bash
# on the login node, in the repo (paths come from slurm/cluster.env)
bash container/build_cluster_image.sh
# or with a base image that's already on netscratch:
BASE_IMAGE=/netscratch/naeem/nvcr.io_nvidia_pytorch_25.09-py3-HF.sqsh bash container/build_cluster_image.sh
```

- The job needs internet on the compute node (apt, download.blender.org, PyPI) and uses
  `--container-remap-root` for apt.
- At the end it prints the Cycles devices it sees. Check that `OPTIX` is listed; otherwise
  set `render_hardware: CUDA` in the jobs.
- Re-run it (after deleting the old `.sqsh`) whenever `external/syclops` or the install script changes.

## Local (WSL + Docker)

```bash
git submodule update --init
docker build -f container/Dockerfile -t syclops-pipeline:latest .
```

Known issue: **OptiX crashes Blender 3.6 inside Docker on WSL** (segfault at the first render;
CUDA works). `scripts/docker_run.sh` therefore sets `GPU_BACKEND=CUDA`, which overrides
`render_hardware` and switches the denoiser to OpenImageDenoise. Native Linux cluster nodes are
not affected.

## Gotchas handled by the install script
- Blender 3.6 ships setuptools 63. Its `pkg_resources` cannot resolve wheels with normalized
  dist-info names (`ruamel_yaml_clib-0.2.15`), so Syclops' plugin loading fails inside Blender.
  The fix is to pin `ruamel.yaml.clib==0.2.8`; the script verifies the full dependency tree with
  Blender's Python.
- Syclops is installed non-editable (Blender doesn't process `.pth` hooks).
- Pyxis doesn't keep image `ENV`s, so the environment lives in `/opt/syclops_env.sh`.
