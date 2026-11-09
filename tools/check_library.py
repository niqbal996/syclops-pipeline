#!/usr/bin/env python3
"""Check asset libraries (assets.yaml) against the files on disk.

  python tools/check_library.py /mnt/d/blender_assets            # report only
  python tools/check_library.py "/mnt/d/blender_assets/Polyhaven HDRI" --prune

--prune rewrites each library's manifests without the entries whose files are
missing (original kept as assets.yaml.bak). Only use it on your own copy of a
library, never on the shared network drive.
--list prints every asset reference ("Library/Asset") for use in job files.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from syclops_assets import discover, load_yaml  # noqa: E402


def prune(lib) -> int:
    removed = 0
    for mf in lib.manifests:
        data = load_yaml(mf)
        assets = data.get("assets") or {}
        keep = {k: v for k, v in assets.items() if k not in lib.bad_assets}
        if len(keep) == len(assets):
            continue
        shutil.copy2(mf, mf.with_suffix(".yaml.bak"))
        data["assets"] = keep
        with open(mf, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        removed += len(assets) - len(keep)
        print(f"  pruned {len(assets) - len(keep)} entries from {mf} (backup: {mf.name}.bak)")
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--prune", action="store_true", help="remove entries with missing files from the manifests")
    ap.add_argument("--list", action="store_true", help="list all asset references")
    ap.add_argument("--max-issues", type=int, default=20, help="issues shown per library (0 = all)")
    args = ap.parse_args()

    libs = discover(args.roots)
    if not libs:
        print("no libraries found (an assets.yaml with a top-level 'name:' marks a library root)")
        return 1
    total_err = 0
    for lib in libs:
        errs = [i for i in lib.issues if i.level == "error"]
        warns = [i for i in lib.issues if i.level == "warning"]
        total_err += len(errs)
        types: dict = {}
        for a in lib.assets.values():
            types[a.get("type")] = types.get(a.get("type"), 0) + 1
        status = "OK" if not errs else f"{len(lib.bad_assets)} unusable assets"
        print(f"\n== {lib.name}  [{lib.root}]")
        print(f"   {len(lib.assets)} assets {types}, {len(lib.manifests)} manifest(s): {status}, {len(warns)} warnings")
        shown = lib.issues if args.max_issues == 0 else lib.issues[: args.max_issues]
        for i in shown:
            print("   ", i)
        if len(shown) < len(lib.issues):
            print(f"    ... {len(lib.issues) - len(shown)} more")
        if args.list:
            for name, a in lib.assets.items():
                mark = "  (MISSING)" if name in lib.bad_assets else ""
                print(f"     {a.get('type', '?'):20s} {lib.name}/{name}{mark}")
        if args.prune and lib.bad_assets:
            prune(lib)
    print(f"\n{len(libs)} libraries, {total_err} errors")
    return 1 if total_err and not args.prune else 0


if __name__ == "__main__":
    sys.exit(main())
