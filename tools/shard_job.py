#!/usr/bin/env python3
"""Split one Syclops job into N independent shards (one per Slurm array task).

Syclops renders frames 0..steps-1 sequentially from one numpy seed, so shard i gets
  * steps_i      = its share of the total steps
  * seeds.numpy  = seeds.numpy  + i * SEED_STRIDE   (different scenes per shard)
  * seeds.cycles = seeds.cycles + i * SEED_STRIDE   (different render noise)
  * seeds.textures (procedural density maps) offset the same way
and writes shard_info (frame offset) for merge_shards.py.

  # one shard (what render_dataset.sh does)
  python tools/shard_job.py jobs/x.yaml --shards 20 --index 3 -o /out/shard_003/job.yaml
  # quick overrides for tests, dotted paths, list indices allowed
  python tools/shard_job.py jobs/x.yaml --shards 1 --index 0 -o t.yaml \
      --set steps=2 --set sensor.syclops_sensor_camera.0.outputs.syclops_output_rgb.0.samples=8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

SEED_STRIDE = 1000


def set_path(cfg, dotted: str, value):
    keys = dotted.split(".")
    cur = cfg
    for k in keys[:-1]:
        cur = cur[int(k)] if isinstance(cur, list) else cur.setdefault(k, {})
    last = keys[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def shard_steps(total: int, shards: int, index: int) -> tuple[int, int]:
    base, rem = divmod(total, shards)
    steps = base + (1 if index < rem else 0)
    offset = index * base + min(index, rem)
    return steps, offset


def make_shard(cfg: dict, shards: int, index: int, overrides: list[str]) -> tuple[dict, dict]:
    for ov in overrides:
        key, _, val = ov.partition("=")
        set_path(cfg, key.strip(), yaml.safe_load(val))
    total = int(cfg["steps"])
    if not 0 <= index < shards:
        raise ValueError(f"index {index} out of range for {shards} shards")
    if shards > total:
        raise ValueError(f"more shards ({shards}) than steps ({total})")
    steps, offset = shard_steps(total, shards, index)
    seeds = cfg.setdefault("seeds", {})
    base_seeds = dict(seeds)
    for k in ("numpy", "cycles"):
        seeds[k] = int(seeds.get(k, 0)) + index * SEED_STRIDE
    if "textures" in cfg:
        seeds["textures"] = int(seeds.get("textures", 0)) + index * SEED_STRIDE
    cfg["steps"] = steps
    info = {"shard": index, "shards": shards, "frame_offset": offset, "steps": steps,
            "total_steps": total, "seeds": dict(seeds), "base_seeds": base_seeds}
    return cfg, info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", type=Path)
    ap.add_argument("--shards", type=int, required=True)
    ap.add_argument("--index", type=int, required=True)
    ap.add_argument("-o", "--out", type=Path, required=True, help="shard job yaml to write")
    ap.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY.PATH=VALUE")
    args = ap.parse_args()

    cfg = yaml.safe_load(args.job.read_text(encoding="utf-8"))
    cfg, info = make_shard(cfg, args.shards, args.index, args.overrides)
    info["source_job"] = str(args.job.resolve())
    info["overrides"] = args.overrides
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    (args.out.parent / "shard_info.json").write_text(json.dumps(info, indent=2))
    print(f"shard {args.index}/{args.shards}: {info['steps']} steps, frames "
          f"{info['frame_offset']}..{info['frame_offset'] + info['steps'] - 1}, seeds {info['seeds']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
