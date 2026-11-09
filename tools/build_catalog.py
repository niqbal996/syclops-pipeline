#!/usr/bin/env python3
"""Build a Syclops asset_catalog.yaml (and schema.yaml) without touching asset folders.

Replaces `syclops -c` for pipeline use. Run it on the machine/container that will
render, because root_path entries are absolute paths as seen from there.

  python tools/build_catalog.py --install-folder /tmp/syclops_home /assets/root1 /assets/root2

Assets with missing files are dropped (with a warning) instead of crashing the
whole crawl; use --strict to fail instead. The packaged "Example Assets" library
is included when syclops is importable (same as upstream).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from syclops_assets import discover  # noqa: E402
from syclops_assets import catalog_entry


def syclops_package_root() -> Path | None:
    try:
        import syclops  # noqa: F401
    except ImportError:
        return None
    return Path(sys.modules["syclops"].__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asset_roots", nargs="+", type=Path, help="folders to search for asset libraries")
    ap.add_argument("--install-folder", type=Path, help="syclops install folder: writes asset_catalog.yaml + schema.yaml there")
    ap.add_argument("--out", type=Path, help="write the catalog to this file instead")
    ap.add_argument("--strict", action="store_true", help="fail on any missing file / invalid asset")
    ap.add_argument("--no-examples", action="store_true", help="do not add the packaged Example Assets library")
    ap.add_argument("-q", "--quiet", action="store_true", help="only print errors and the summary")
    args = ap.parse_args()

    if not args.install_folder and not args.out:
        ap.error("give --install-folder or --out")

    roots = [r.resolve() for r in args.asset_roots]
    for r in roots:
        if not r.is_dir():
            print(f"asset root does not exist: {r}", file=sys.stderr)
            return 2
    pkg = None if args.no_examples else syclops_package_root()
    if pkg:
        roots.append(pkg / "syclops" / "__example_assets__")

    libs = discover(roots)
    catalog: dict = {}
    n_err = 0
    for lib in libs:
        for issue in lib.issues:
            if issue.level == "error":
                n_err += 1
            if issue.level == "error" or not args.quiet:
                print(issue, file=sys.stderr)
        if lib.name in catalog:
            print(f"[ERROR  ] duplicate library name '{lib.name}' at {lib.root} "
                  f"(already from {catalog[lib.name]['root_path']})", file=sys.stderr)
            return 1
        catalog[lib.name] = catalog_entry(lib)

    if args.strict and n_err:
        print(f"{n_err} errors, aborting (--strict)", file=sys.stderr)
        return 1

    out = args.out or (args.install_folder / "asset_catalog.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(catalog, f, sort_keys=False, allow_unicode=True)

    for name, lib in catalog.items():
        types: dict = {}
        for a in lib["assets"].values():
            types[a["type"]] = types.get(a["type"], 0) + 1
        print(f"  {name:40s} {len(lib['assets']):5d} assets  {types}  <- {lib['root_path']}")
    print(f"catalog: {out}  ({len(catalog)} libraries, {n_err} dropped/invalid entries)")

    if args.install_folder:
        if pkg is None:
            print("syclops not importable: schema.yaml NOT built", file=sys.stderr)
            return 1
        from syclops.cli import _build_schema  # builds schema.yaml from catalog + plugin schemas
        _build_schema(args.install_folder.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
