"""Shared helpers for reading Syclops asset libraries (assets.yaml manifests).

Mirrors the rules of syclops/asset_manager/asset_crawler.py, but never writes
into asset folders (the upstream crawler rewrites every manifest, which fails on
read-only storage and changes files on the network drive).

Rules (from upstream):
  * A *library root* is a folder whose assets.yaml contains a top-level `name:`.
  * All assets.yaml files below a library root are merged into that library
    (only the `assets:` key may appear in more than one file).
  * Library roots must not be nested.
  * Any asset key containing "filepath" (but not "md5") is a path relative to the
    library root. Values may be a string or a list of strings.
  * Assets are referenced in job files as "<library name>/<asset name>".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MANIFEST = "assets.yaml"
ASSET_TYPES = {"model", "texture", "pbr_texture", "environment_texture"}


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def path_keys(asset: dict) -> list[str]:
    return [k for k in asset if "filepath" in k and "md5" not in k]


def as_list(value) -> list[str]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def normalize(rel: str) -> str:
    """Manifests written on Windows contain backslashes; Linux needs '/'."""
    return str(rel).replace("\\", "/")


@dataclass
class Issue:
    level: str  # "error" | "warning"
    library: str
    asset: str | None
    message: str

    def __str__(self) -> str:
        where = f"{self.library}/{self.asset}" if self.asset else self.library
        return f"[{self.level.upper():7}] {where}: {self.message}"


@dataclass
class Library:
    name: str
    root: Path
    manifests: list[Path]
    data: dict  # merged manifest (name, description, assets, ...)
    issues: list[Issue] = field(default_factory=list)
    bad_assets: set[str] = field(default_factory=set)

    @property
    def assets(self) -> dict:
        return self.data.get("assets") or {}


def find_library_roots(search_root: Path) -> list[Path]:
    roots = []
    for dirpath, dirnames, filenames in os.walk(search_root, followlinks=True):
        dirnames.sort()
        if MANIFEST in filenames:
            m = load_yaml(Path(dirpath) / MANIFEST)
            if isinstance(m, dict) and "name" in m:
                roots.append(Path(dirpath))
    for r in roots:
        for o in roots:
            if r != o and r in o.parents:
                raise ValueError(f"Library {o} is nested inside library {r} (not allowed by Syclops)")
    return roots


def load_library(root: Path) -> Library:
    manifests = sorted(Path(dp) / MANIFEST for dp, _, fns in os.walk(root, followlinks=True) if MANIFEST in fns)
    merged: dict = {}
    issues: list[Issue] = []
    for mf in manifests:
        data = load_yaml(mf)
        for key, value in data.items():
            if key == "assets":
                dup = set(merged.get("assets", {})) & set(value or {})
                for d in sorted(dup):
                    issues.append(Issue("warning", str(root), d, f"defined twice, {mf} wins"))
                merged.setdefault("assets", {}).update(value or {})
            elif key in merged:
                issues.append(Issue("error", str(root), None, f"key '{key}' defined in several manifests ({mf})"))
            else:
                merged[key] = value
    lib = Library(name=merged["name"], root=root, manifests=manifests, data=merged, issues=issues)
    validate_library(lib)
    return lib


def validate_library(lib: Library) -> None:
    if not lib.assets:
        lib.issues.append(Issue("error", lib.name, None, "no assets defined"))
    for name, asset in lib.assets.items():
        if "/" in str(name):
            lib.issues.append(Issue("error", lib.name, name, "asset names must not contain '/'"))
            lib.bad_assets.add(name)
        atype = asset.get("type")
        if atype not in ASSET_TYPES:
            lib.issues.append(Issue("error", lib.name, name, f"unknown type '{atype}'"))
            lib.bad_assets.add(name)
        keys = path_keys(asset)
        if not keys:
            lib.issues.append(Issue("error", lib.name, name, "no *filepath key"))
            lib.bad_assets.add(name)
        if atype == "pbr_texture":
            for k in ("diffuse_filepath", "normal_filepath", "roughness_filepath", "displacement_filepath"):
                if k not in asset:
                    lib.issues.append(Issue("warning", lib.name, name, f"pbr_texture without {k}"))
        for k in keys:
            for rel in as_list(asset[k]):
                if "\\" in str(rel):
                    lib.issues.append(Issue("warning", lib.name, name, f"backslash path '{rel}' (normalized in catalog)"))
                p = lib.root / normalize(rel)
                if not p.exists():
                    lib.issues.append(Issue("error", lib.name, name, f"missing file {k}: {rel}"))
                    lib.bad_assets.add(name)


def discover(search_roots: list[Path]) -> list[Library]:
    libs = []
    for sr in search_roots:
        for root in find_library_roots(sr):
            libs.append(load_library(root))
    return libs


def catalog_entry(lib: Library, drop_bad: bool = True) -> dict:
    """Build the catalog dict for one library (same layout as syclops' asset_catalog.yaml)."""
    entry = {k: v for k, v in lib.data.items() if k != "assets"}
    assets = {}
    for name, asset in lib.assets.items():
        if drop_bad and name in lib.bad_assets:
            continue
        asset = dict(asset)
        for k in path_keys(asset):
            v = asset[k]
            asset[k] = [normalize(x) for x in v] if isinstance(v, list) else normalize(v)
        if "thumbnail" in asset:
            asset["thumbnail"] = [normalize(x) for x in as_list(asset["thumbnail"])]
        assets[name] = asset
    entry["assets"] = assets
    entry["root_path"] = str(lib.root.resolve())
    return entry
