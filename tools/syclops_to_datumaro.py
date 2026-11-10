#!/usr/bin/env python3
"""Build a Datumaro dataset from a Syclops output folder (single run or merged shards).

  python tools/syclops_to_datumaro.py DATASET_DIR [--out DIR] [--camera main_camera]
         [--stuff-classes 0] [--ignore-classes ...] [--class-names 0=background,1=sugarbeet,2=weed]
         [--min-pixels 1] [--subset default] [--from-npz]

* If DATASET_DIR/<camera>_datumaro/items/ exists (written by the Syclops postprocessor
  syclops_postprocessing_datumaro, renumbered by tools/merge_shards.py), only
  annotations/<subset>.json is rebuilt from it (fast).
* Otherwise (or with --from-npz) annotations are computed from
  <camera>_annotations/{semantic_segmentation,instance_segmentation}/NNNN.npz (plus
  leaf_instance_segmentation/ and *_visibility/ if present) and
  <camera>/rect/NNNN.png are hardlinked. This also converts old datasets whose
  instance ids are Syclops' int64 location hashes (e.g. sugarbeet_syn_v6); note that
  the hash collisions/splits of those ids cannot be undone by a conversion.

Options not given on the command line are taken from the syclops_postprocessing_datumaro
entry of the job config (DATASET_DIR/config.yaml or _shards/*/config.yaml) if present.
Needs numpy + PyYAML. Uses external/syclops/syclops/postprocessing/datumaro_format.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
FORMAT_PY = REPO / "external/syclops/syclops/postprocessing/datumaro_format.py"
FRAME_RE = re.compile(r"^(\d+)\.(npz|png)$")


def load_format():
    spec = importlib.util.spec_from_file_location("datumaro_format", FORMAT_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def job_options(dataset: Path) -> dict:
    for cfg in [dataset / "config.yaml", *sorted(dataset.glob("_shards/*/config.yaml")),
                *sorted(dataset.glob("_shards/*/*/config.yaml"))]:
        if cfg.exists():
            job = yaml.safe_load(cfg.read_text()) or {}
            entries = (job.get("postprocessing") or {}).get("syclops_postprocessing_datumaro") or []
            if entries:
                return entries[0]
    return {}


def class_names(dataset: Path, given: str | None, options: dict) -> dict[int, str]:
    if given:
        return {int(k): v for k, v in (kv.split("=", 1) for kv in given.split(","))}
    if options.get("class_names"):
        return {int(k): str(v) for k, v in options["class_names"].items()}
    mapping = dataset / "class_id_mapping.yaml"
    if mapping.exists():
        return {int(k): "+".join(v) if isinstance(v, list) else str(v)
                for k, v in (yaml.safe_load(mapping.read_text()) or {}).items()}
    return {}


def frames(folder: Path, ext: str) -> dict[int, Path]:
    found = {}
    if folder.is_dir():
        for f in folder.iterdir():
            m = FRAME_RE.match(f.name)
            if m and m.group(2) == ext:
                found[int(m.group(1))] = f
    return found


def convert(dataset: Path, out: Path | None = None, camera: str = "main_camera", stuff=None, ignore=None,
            names: str | None = None, min_pixels=None, subset=None, from_npz=False) -> Path:
    import numpy as np

    fmt = load_format()
    options = job_options(dataset)
    out = out or dataset / f"{camera}_datumaro"
    subset = subset or options.get("subset", "default")
    labels = class_names(dataset, names, options)
    leaf_label = options.get("leaf_label", "leaf")
    if not from_npz and (out / "items").is_dir() and any((out / "items").glob("*.json")):
        return fmt.assemble(out, labels, subset=subset, leaf_label=leaf_label)

    stuff = stuff if stuff is not None else options.get("stuff_classes", [])
    ignore = ignore if ignore is not None else options.get("ignore_classes", [])
    min_pixels = min_pixels if min_pixels is not None else int(options.get("min_pixels", 1))
    ann = dataset / f"{camera}_annotations"
    sem_dir = next((d for d in (ann / "semantic_segmentation", ann / "semantics") if d.is_dir()), None)
    if sem_dir is None:
        raise SystemExit(f"no semantic segmentation in {ann}")
    semantic = frames(sem_dir, "npz")
    instance = frames(ann / "instance_segmentation", "npz")
    images = frames(dataset / camera / "rect", "png")
    leaves = frames(ann / "leaf_instance_segmentation", "npz")
    inst_vis = frames(ann / "instance_visibility", "npz")
    leaf_vis = frames(ann / "leaf_instance_visibility", "npz")
    leaf_id = max(labels) + 1 if labels else 0
    todo = sorted(set(semantic) & set(instance))
    if not todo:
        raise SystemExit(f"no frames with both semantic and instance masks in {ann}")
    for n, frame in enumerate(todo):
        sem = np.load(semantic[frame])["array"]
        inst = np.load(instance[frame])["array"]
        if inst.dtype.kind == "i" and inst.dtype.itemsize == 8:
            # legacy Syclops ids (int64 location hashes): remap to 1..N first
            inst = np.unique(inst, return_inverse=True)[1].reshape(inst.shape) + 1
        load = lambda d: np.load(d[frame])["array"] if frame in d else None  # noqa: E731
        leaf = load(leaves)
        anns = fmt.frame_annotations(sem, inst, stuff_classes=stuff, ignore_classes=ignore,
                                     min_pixels=min_pixels, leaf_instance=leaf,
                                     leaf_label=leaf_id if leaf is not None else None,
                                     instance_visibility=load(inst_vis), leaf_visibility=load(leaf_vis),
                                     partial_threshold=float(options.get("partial_threshold", 0.5)))
        fmt.write_item(out, frame, anns, sem.shape[:2], images.get(frame), subset=subset)
        if (n + 1) % 100 == 0:
            print(f"  {n + 1}/{len(todo)} frames", flush=True)
    return fmt.assemble(out, labels, subset=subset, leaf_label=leaf_label)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--camera", default="main_camera")
    ap.add_argument("--stuff-classes", type=int, nargs="*")
    ap.add_argument("--ignore-classes", type=int, nargs="*")
    ap.add_argument("--class-names", help="e.g. 0=background,1=sugarbeet,2=weed")
    ap.add_argument("--min-pixels", type=int)
    ap.add_argument("--subset")
    ap.add_argument("--from-npz", action="store_true", help="recompute even if items/ exist")
    a = ap.parse_args()
    path = convert(a.dataset, a.out, a.camera, a.stuff_classes, a.ignore_classes, a.class_names,
                   a.min_pixels, a.subset, a.from_npz)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
