"""Inspect .blend files for problems that break Syclops renders. Runs INSIDE Blender.

  scripts/blender.sh -b --factory-startup -P tools/inspect_blend.py -- [--json out.json] FILE_OR_DIR...

Why each check matters for Syclops:
  * saved_with: Syclops renders with Blender 3.6. Files saved by Blender >= 4.0 can
    lose geometry/material data when opened in 3.6 -> re-save them from 3.6.
  * every object in the file is imported; for scatter/crop each object becomes a
    variant. Cameras, lights, empties, ground planes in an asset file end up in the
    field -> they are flagged.
  * images that are neither packed nor found on disk render pink on the cluster
    (paths like C:\\Users\\... only exist on the authoring machine) -> pack them
    (File > External Data > Pack Resources) or keep them relative inside the library.
  * the origin should be at the base of the plant (scatter places origins on the
    ground), size should be in metres.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def world_bbox(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def inspect(path: Path) -> dict:
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    report = {
        "file": str(path),
        "size_mb": round(path.stat().st_size / 1e6, 1),
        "saved_with": ".".join(map(str, bpy.data.version)),
        "objects": [],
        "images": [],
        "warnings": [],
    }
    w = report["warnings"]
    if tuple(bpy.data.version[:2]) > (3, 6):
        w.append(f"saved with Blender {report['saved_with']} > 3.6 (re-save from Blender 3.6)")

    total_polys = 0
    for obj in bpy.data.objects:
        info = {"name": obj.name, "type": obj.type, "parent": obj.parent.name if obj.parent else None}
        if obj.type == "MESH":
            polys = len(obj.data.polygons)
            total_polys += polys
            lo, hi = world_bbox(obj)
            dims = hi - lo
            info.update(polys=polys, dims_m=[round(d, 3) for d in dims], origin_z=round(obj.location.z, 3),
                        bottom_z=round(lo.z, 3), materials=[m.name for m in obj.data.materials if m])
            if not obj.parent:
                if max(dims) > 5:
                    w.append(f"{obj.name}: {max(dims):.2f} m large (wrong units?)")
                if 0 < max(dims) < 0.005:
                    w.append(f"{obj.name}: only {max(dims) * 1000:.2f} mm large (wrong units?)")
                if max(dims) > 0 and abs(lo.z - obj.matrix_world.translation.z) > 0.1 * max(dims[2], 1e-6) + 0.01:
                    w.append(f"{obj.name}: origin is not at the base (bottom {lo.z:.3f} m vs origin "
                             f"{obj.matrix_world.translation.z:.3f} m) -> will float/sink when scattered")
                if dims.z < 0.002 and dims.x > 1 and dims.y > 1:
                    w.append(f"{obj.name}: looks like a ground plane; it would be scattered as an instance")
            if not obj.data.materials:
                w.append(f"{obj.name}: no material")
        elif obj.type in {"CAMERA", "LIGHT", "EMPTY", "CURVE", "ARMATURE"}:
            w.append(f"{obj.name}: {obj.type} object will be imported as a scatter/crop instance")
        report["objects"].append(info)
    report["total_polys"] = total_polys

    for img in bpy.data.images:
        if img.source in {"GENERATED", "VIEWER"} or img.type in {"RENDER_RESULT", "COMPOSITING"}:
            continue
        packed = img.packed_file is not None
        abspath = bpy.path.abspath(img.filepath) if img.filepath else ""
        exists = bool(abspath) and os.path.exists(abspath)
        report["images"].append({"name": img.name, "filepath": img.filepath, "packed": packed, "found": exists,
                                 "size": list(img.size) if packed or exists else None})
        if not packed and not exists:
            w.append(f"image '{img.name}' missing ({img.filepath}) -> renders pink")
        elif not packed and img.filepath and not img.filepath.startswith("//"):
            w.append(f"image '{img.name}' uses absolute path {img.filepath} (pack it or make it relative)")
    return report


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="inspect_blend.py")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    files = []
    for p in args.paths:
        files += sorted(p.rglob("*.blend")) if p.is_dir() else [p]
    reports = []
    for f in files:
        try:
            r = inspect(f.resolve())
        except Exception as e:  # keep going on corrupt files
            r = {"file": str(f), "error": repr(e), "warnings": [f"could not open: {e!r}"]}
        reports.append(r)
        n_mesh = sum(1 for o in r.get("objects", []) if o["type"] == "MESH" and not o["parent"])
        print(f"\n### {f}  [{r.get('size_mb', '?')} MB, saved with {r.get('saved_with', '?')}, "
              f"{n_mesh} top-level meshes, {r.get('total_polys', 0):,} polys, {len(r.get('images', []))} images]")
        for o in r.get("objects", []):
            if o["type"] == "MESH" and not o["parent"]:
                print(f"    {o['name']:40s} {o['polys']:>9,} polys  dims {o['dims_m']} m")
        for msg in r["warnings"]:
            print(f"  ! {msg}")
    if args.json:
        args.json.write_text(json.dumps(reports, indent=2))
        print(f"\nreport: {args.json}")
    bad = sum(1 for r in reports if r["warnings"])
    print(f"\n{len(reports)} files inspected, {bad} with warnings")


main()
