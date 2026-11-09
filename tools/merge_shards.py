#!/usr/bin/env python3
"""Merge shard outputs of one render run into a single dataset with global frame ids.

Expected layout (created by scripts/render_dataset.sh):
  RUN_DIR/shard_000/shard_info.json
  RUN_DIR/shard_000/<syclops timestamp>/main_camera/rect/0000.png ...
  RUN_DIR/shard_001/...

  python tools/merge_shards.py RUN_DIR MERGED_DIR [--move] [--allow-incomplete]

Every file whose name is a frame number (0000.png, 0012.npz, 0003.txt, ...) is
renumbered to frame_offset + local frame and placed under the same relative path.
metadata.yaml files get their `steps` merged. Logs and per-shard configs go to
MERGED_DIR/_shards/. index.csv maps global frame -> shard/local frame/seed.
Datumaro exports (<camera>_datumaro/) are rebuilt from the renumbered per-frame items.
If a shard was rendered several times (resubmitted), the newest complete run wins.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from pathlib import Path

import yaml

FRAME_RE = re.compile(r"^(\d+)(\.[A-Za-z0-9.]+)$")
SKIP_DIRS = {"tmp"}


def frames_in(run: Path) -> set[int]:
    found = set()
    for rect in run.glob("*/rect"):
        found |= {int(m.group(1)) for f in rect.iterdir() if (m := FRAME_RE.match(f.name))}
    return found


def pick_run(shard_dir: Path, steps: int, allow_incomplete: bool) -> Path | None:
    runs = sorted((p for p in shard_dir.iterdir() if p.is_dir() and (p / "config.yaml").exists()),
                  key=lambda p: p.name, reverse=True)
    for run in runs:
        n = len(frames_in(run))
        if n >= steps:
            return run
    if allow_incomplete and runs:
        return max(runs, key=lambda r: len(frames_in(r)))
    return None


def place(src: Path, dst: Path, move: bool):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(src, dst)
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def rename_frames_in(obj, local: int, new_name, width_old=4):
    """Rewrite file names like '0003.png' inside metadata entries."""
    if isinstance(obj, dict):
        return {k: rename_frames_in(v, local, new_name) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rename_frames_in(v, local, new_name) for v in obj]
    if isinstance(obj, str):
        p = Path(obj)
        m = FRAME_RE.match(p.name)
        if m and int(m.group(1)) == local:
            return str(p.with_name(new_name(local) + m.group(2)))
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--move", action="store_true", help="move instead of hardlink/copy")
    ap.add_argument("--allow-incomplete", action="store_true")
    args = ap.parse_args()

    shard_dirs = sorted(p for p in args.run_dir.glob("shard_*") if (p / "shard_info.json").exists())
    if not shard_dirs:
        print(f"no shard_*/shard_info.json in {args.run_dir}", file=sys.stderr)
        return 1
    infos = [json.loads((d / "shard_info.json").read_text()) for d in shard_dirs]
    total = infos[0]["total_steps"]
    n_shards = infos[0]["shards"]
    width = max(4, len(str(total - 1)))
    present = {i["shard"] for i in infos}
    missing = sorted(set(range(n_shards)) - present)

    runs = []
    for d, info in zip(shard_dirs, infos):
        run = pick_run(d, info["steps"], args.allow_incomplete)
        if run is None:
            missing.append(info["shard"])
        runs.append(run)
    if missing and not args.allow_incomplete:
        print(f"incomplete shards: {sorted(set(missing))} (resubmit them: sbatch --array=<ids> ..., "
              f"or use --allow-incomplete)", file=sys.stderr)
        return 1

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    index_rows = []
    metadata: dict[str, dict] = {}
    class_maps = set()
    for d, info, run in zip(shard_dirs, infos, runs):
        if run is None:
            continue
        off = info["frame_offset"]
        new_name = lambda local, off=off: str(off + local).zfill(width)  # noqa: E731
        shard_meta = out / "_shards" / d.name
        shard_meta.mkdir(parents=True, exist_ok=True)
        shutil.copy2(d / "shard_info.json", shard_meta / "shard_info.json")
        for root, dirs, files in os.walk(run):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            rel_root = Path(root).relative_to(run)
            for fn in files:
                src = Path(root) / fn
                if fn.endswith(".lock"):
                    continue
                m = FRAME_RE.match(fn)
                if m:
                    local = int(m.group(1))
                    place(src, out / rel_root / (new_name(local) + m.group(2)), args.move)
                    if rel_root.name == "rect":
                        index_rows.append({"frame": off + local, "shard": info["shard"], "local_frame": local,
                                           "seed_numpy": info["seeds"].get("numpy"), "run": str(run)})
                elif fn == "metadata.yaml":
                    md = yaml.safe_load(src.read_text()) or {}
                    key = str(rel_root)
                    merged = metadata.setdefault(key, {k: v for k, v in md.items() if k != "steps"})
                    merged["expected_steps"] = total
                    steps = merged.setdefault("steps", {})
                    for local, entry in (md.get("steps") or {}).items():
                        steps[off + int(local)] = rename_frames_in(entry, int(local), new_name)
                elif fn == "class_id_mapping.yaml" and rel_root == Path("."):
                    class_maps.add(src.read_text())
                    shutil.copy2(src, out / fn)
                else:
                    place(src, shard_meta / rel_root / fn, move=False)

    for key, md in metadata.items():
        md["steps"] = dict(sorted(md["steps"].items()))
        (out / key).mkdir(parents=True, exist_ok=True)
        with open(out / key / "metadata.yaml", "w") as f:
            yaml.safe_dump(md, f, sort_keys=False)
    index_rows.sort(key=lambda r: r["frame"])
    with open(out / "index.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["frame", "shard", "local_frame", "seed_numpy", "run"])
        w.writeheader()
        w.writerows(index_rows)
    if len(class_maps) > 1:
        print("WARNING: class_id_mapping.yaml differs between shards!", file=sys.stderr)
    for items in sorted(out.glob("*_datumaro/items")):
        # per-frame Datumaro fragments were renumbered above; rebuild the dataset json
        try:
            from syclops_to_datumaro import convert
            camera = items.parent.name[: -len("_datumaro")]
            print(f"rebuilt {convert(out, out=items.parent, camera=camera)}")
        except ImportError as e:  # numpy missing
            print(f"WARNING: could not rebuild {items.parent} ({e}); run tools/syclops_to_datumaro.py",
                  file=sys.stderr)
    print(f"merged {len(index_rows)}/{total} frames from {len(shard_dirs)} shards into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
