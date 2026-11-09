#!/usr/bin/env python3
"""Create (or extend) an assets.yaml for a folder of new assets.

  python tools/make_manifest.py /path/to/MyLibrary --name "My Library" \
      --description "Weeds scanned 2026" --tags weed --license CC-BY-4.0

Detection rules (recursive, paths stored relative to the library root):
  * .blend .fbx .obj                       -> type: model         (one asset per file)
  * .hdr .exr                               -> type: environment_texture
  * a folder with diffuse/albedo/color + normal (+ roughness, displacement/height)
    maps                                    -> type: pbr_texture   (one asset per folder)
  * other images are ignored, or added as type: texture with --plain-textures

Asset names default to the file stem ("weed_small_01.blend" -> "weed_small_01").
Existing entries are kept untouched; only new files are added (use --dry-run to
preview). Edit the result by hand afterwards: names, tags, class grouping and
pbr texture_size (metres covered by one texture tile) are worth a look.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

MODEL_EXT = {".blend", ".fbx", ".obj"}
HDRI_EXT = {".hdr", ".exr"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr"}
SKIP_DIRS = {"thumbnails", "__pycache__", ".git", "textures_unused"}

PBR_PATTERNS = {
    "diffuse_filepath": r"(diff|diffuse|albedo|basecolor|base_color|color|col)(?!.*(nor|rough))",
    "normal_filepath": r"(nor_gl|normal_gl|nor|normal|nrm)",
    "roughness_filepath": r"(rough|roughness|rgh)",
    "displacement_filepath": r"(disp|displacement|height|bump)",
}


def classify_pbr(files: list[Path]) -> dict | None:
    maps: dict = {}
    for key, pat in PBR_PATTERNS.items():
        hits = [f for f in files if re.search(pat, f.stem.lower())]
        if key == "normal_filepath":  # prefer OpenGL normals (Blender convention)
            hits.sort(key=lambda f: ("_gl" not in f.stem.lower() and "opengl" not in f.stem.lower(), f.name))
        if hits:
            maps[key] = hits[0]
    if "diffuse_filepath" in maps and "normal_filepath" in maps:
        return maps
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--name", help="library name (required for a new manifest)")
    ap.add_argument("--description", default="")
    ap.add_argument("--tags", nargs="*", default=[])
    ap.add_argument("--license", default="unknown")
    ap.add_argument("--plain-textures", action="store_true", help="add loose images as type: texture")
    ap.add_argument("--pbr-size", type=float, default=1.0, help="texture_size (m) for new pbr_textures")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = args.root.resolve()
    manifest_path = root / "assets.yaml"
    data = {}
    if manifest_path.exists():
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if "name" not in data:
        if not args.name:
            ap.error("new library: --name is required")
        data = {"name": args.name, "description": args.description, **data}
    assets: dict = data.setdefault("assets", {}) or {}
    data["assets"] = assets
    known = {str(v).replace("\\", "/") for a in assets.values() for k, v in a.items()
             if "filepath" in k and "md5" not in k and isinstance(v, str)}

    added = []

    def add(name: str, entry: dict):
        base, i = name, 1
        while name in assets:
            i += 1
            name = f"{base}_{i}"
        assets[name] = entry
        added.append((name, entry["type"]))

    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    tags = list(args.tags)
    for d in sorted([root, *[p for p in root.rglob("*") if p.is_dir()]]):
        if any(part in SKIP_DIRS for part in d.relative_to(root).parts):
            continue
        files = sorted(f for f in d.iterdir() if f.is_file())
        images = [f for f in files if f.suffix.lower() in IMG_EXT]
        pbr = classify_pbr(images) if len(images) >= 2 else None
        if pbr and not any(rel(f) in known for f in pbr.values()):
            entry = {"type": "pbr_texture", "tags": tags or ["texture"], "license": args.license,
                     "texture_size": args.pbr_size, "texture_displacement_scale": 0.05}
            entry.update({k: rel(v) for k, v in pbr.items()})
            add(d.name if d != root else root.name, entry)
            used = set(pbr.values())
        else:
            used = set()
        for f in files:
            if f in used or rel(f) in known:
                continue
            ext = f.suffix.lower()
            if ext in MODEL_EXT:
                add(f.stem, {"type": "model", "tags": tags or ["model"], "license": args.license, "filepath": rel(f)})
            elif ext in HDRI_EXT and not pbr:
                add(f.stem, {"type": "environment_texture", "tags": tags or ["hdri"], "license": args.license,
                             "filepath": rel(f)})
            elif args.plain_textures and ext in IMG_EXT and not pbr:
                add(f.stem, {"type": "texture", "tags": tags or ["texture"], "license": args.license,
                             "filepath": rel(f)})

    for name, t in added:
        print(f"  + {t:20s} {data['name']}/{name}")
    print(f"{len(added)} new assets, {len(assets)} total")
    if args.dry_run or not added:
        return 0
    if manifest_path.exists():
        manifest_path.with_suffix(".yaml.bak").write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
    with open(manifest_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    print(f"written {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
