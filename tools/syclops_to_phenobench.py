#!/usr/bin/env python3
"""Export a Syclops output folder (single run or merged shards) in the PhenoBench layout.

  python tools/syclops_to_phenobench.py DATASET_DIR OUT_ROOT [--split train] [--prefix syn]
         [--camera main_camera] [--crop-classes 1] [--weed-classes 2] [--partial-threshold 0.5]

Writes OUT_ROOT/<split>/{images,semantics,plant_instances,leaf_instances,plant_visibility,
leaf_visibility}/<prefix>_NNNN.png like PhenoBench (https://www.phenobench.org/dataset.html):

* images: RGB PNG (the rendered rect image)
* semantics (uint16): 0 background/soil, 1 crop, 2 weed, 3 partial crop, 4 partial weed
  (partial: plant visibility < threshold, i.e. mostly outside the image)
* plant_instances (uint16): crop and weed plants 1..N, 0 elsewhere
* leaf_instances (uint16): leaves of crop plants 1..N, 0 elsewhere
* plant_visibility / leaf_visibility (uint16, 0-255): visibility of the plant/leaf of
  each pixel (needs pixel_annotation overscan; without it these folders are skipped and
  no partial classes are written)

Needs numpy, PyYAML and OpenCV (16-bit PNG). Class ids are Syclops class ids
(class_id in the job); everything that is neither crop nor weed becomes 0.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

FRAME_RE = re.compile(r"^(\d+)\.(npz|png)$")


def frames(folder: Path) -> dict[int, Path]:
    found = {}
    if folder.is_dir():
        for f in folder.iterdir():
            m = FRAME_RE.match(f.name)
            if m:
                found[int(m.group(1))] = f
    return found


def renumber(ids: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """ids where keep, renumbered 1..N in order of first appearance (row-major), else 0."""
    out = np.zeros(ids.shape, np.uint16)
    values = ids[keep]
    if values.size:
        uniq, first = np.unique(values, return_index=True)
        order = uniq[np.argsort(first)]
        lut = {int(v): i + 1 for i, v in enumerate(order)}
        out[keep] = np.vectorize(lut.get, otypes=[np.uint16])(values)
    return out


def write16(path: Path, array: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), array.astype(np.uint16)):
        raise OSError(f"could not write {path}")


def export_frame(sem, inst, leaf, inst_vis, leaf_vis, crop_classes, weed_classes, threshold):
    """Return dict of PhenoBench arrays for one frame."""
    crop_cls = np.isin(sem, crop_classes)
    weed_cls = np.isin(sem, weed_classes)
    # class of every instance = majority semantic class of its pixels
    plant_kind = np.zeros(sem.shape, np.uint8)  # 1 crop, 2 weed
    partial = np.zeros(sem.shape, bool)
    ids, inverse = np.unique(inst, return_inverse=True)
    inverse = inverse.reshape(inst.shape)
    for k, inst_id in enumerate(ids.tolist()):
        if inst_id == 0:
            continue
        mask = inverse == k
        n_crop, n_weed = int(crop_cls[mask].sum()), int(weed_cls[mask].sum())
        if max(n_crop, n_weed) * 2 < mask.sum():
            continue  # mostly soil/debris: no plant
        plant_kind[mask] = 1 if n_crop >= n_weed else 2
        if inst_vis is not None:
            partial[mask] = np.median(inst_vis[mask]) / 255.0 < threshold
    plants = plant_kind > 0
    semantics = np.zeros(sem.shape, np.uint16)
    semantics[plant_kind == 1] = 1
    semantics[plant_kind == 2] = 2
    semantics[(plant_kind == 1) & partial] = 3
    semantics[(plant_kind == 2) & partial] = 4
    out = {"semantics": semantics, "plant_instances": renumber(inst, plants)}
    if leaf is not None:
        out["leaf_instances"] = renumber(leaf, (leaf > 0) & (plant_kind == 1))
    if inst_vis is not None:
        out["plant_visibility"] = np.where(plants, inst_vis, 0).astype(np.uint16)
    if leaf is not None and leaf_vis is not None:
        out["leaf_visibility"] = np.where(out["leaf_instances"] > 0, leaf_vis, 0).astype(np.uint16)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("out_root", type=Path)
    ap.add_argument("--split", default="train")
    ap.add_argument("--prefix", default="syn")
    ap.add_argument("--camera", default="main_camera")
    ap.add_argument("--crop-classes", type=int, nargs="+", default=[1])
    ap.add_argument("--weed-classes", type=int, nargs="+", default=[2])
    ap.add_argument("--partial-threshold", type=float, default=0.5)
    a = ap.parse_args()

    ann = a.dataset / f"{a.camera}_annotations"
    sem = frames(ann / "semantic_segmentation")
    inst = frames(ann / "instance_segmentation")
    leaf = frames(ann / "leaf_instance_segmentation")
    inst_vis = frames(ann / "instance_visibility")
    leaf_vis = frames(ann / "leaf_instance_visibility")
    images = frames(a.dataset / a.camera / "rect")
    todo = sorted(set(sem) & set(inst) & set(images))
    if not todo:
        print(f"no frames with image + semantic + instance masks in {a.dataset}", file=sys.stderr)
        return 1
    root = a.out_root / a.split
    load = lambda d, f: np.load(d[f])["array"] if f in d else None  # noqa: E731
    for f in todo:
        name = f"{a.prefix}_{f:04d}.png"
        (root / "images").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(images[f], root / "images" / name)
        arrays = export_frame(load(sem, f), load(inst, f), load(leaf, f), load(inst_vis, f), load(leaf_vis, f),
                              a.crop_classes, a.weed_classes, a.partial_threshold)
        for folder, array in arrays.items():
            write16(root / folder / name, array)
    print(f"wrote {len(todo)} frames to {root} ({', '.join(sorted(arrays))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
