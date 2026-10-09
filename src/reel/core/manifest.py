"""Describe what is registered, as plain data.

Used by `reel list ...`, by the template manifest the LLM reads (so it only picks things
that exist), and by documentation.  Everything here is derived from the live registries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from reel.core.archetypes import RigDims
from reel.core.catalog import CATALOG, Catalog

#: a standing person's height in design px: library things are sized against it
PERSON_HEIGHT = RigDims().height


def model_params(model: type[BaseModel] | None) -> list[dict[str, Any]]:
    """Flatten a params model into [{name, type, default, enum, description, ...}]."""
    if model is None:
        return []
    schema = model.model_json_schema()
    required = set(schema.get("required", []))
    out: list[dict[str, Any]] = []
    for name, p in schema.get("properties", {}).items():
        entry: dict[str, Any] = {"name": name, "required": name in required}
        t = p.get("type")
        if t is None and "anyOf" in p:
            t = " | ".join(str(a.get("type", a.get("$ref", "?"))) for a in p["anyOf"])
        if t is None and "enum" in p:
            t = "enum"
        entry["type"] = t or "any"
        for key in ("default", "enum", "minimum", "maximum", "description", "exclusiveMinimum"):
            if key in p:
                entry[key] = p[key]
        out.append(entry)
    return out


def _first_line(text: str | None) -> str:
    return (text or "").strip().splitlines()[0].strip() if text and text.strip() else ""


def describe_entry(kind: str, name: str, obj: Any, meta: dict[str, Any]) -> dict[str, Any]:
    """One registry entry as JSON-friendly data."""
    d: dict[str, Any] = {"name": name}
    summary = (
        meta.get("summary")
        or getattr(obj, "summary", None)
        or _first_line(getattr(obj, "__doc__", None))
    )
    d["summary"] = summary or ""
    pm = getattr(obj, "params_model", None)
    if pm is not None or kind in ("action", "background", "transition"):
        d["params"] = model_params(pm)
    for attr in ("category", "moves_root", "tags", "min_duration", "default_duration", "height"):
        v = getattr(obj, attr, None)
        if v is not None and v != ():
            d[attr] = list(v) if isinstance(v, tuple) else v
    if kind == "background":
        slots = getattr(obj, "slot_names", None)
        if callable(slots):
            d["slots"] = sorted(slots({}))
    if kind == "camera_move":
        d["from_to"] = getattr(obj, "from_to", "")
    if kind == "object":
        d["anchor"] = list(getattr(obj, "anchor", (0.5, 1.0)))
    asset = obj if kind == "object" else (getattr(obj, "features", None) or {}).get("asset")
    if asset is not None and hasattr(
        asset, "roles"
    ):  # a library asset: what a script may recolour, how big it is
        if asset.roles:
            d["roles"] = list(asset.roles)
        d["size"] = round(asset.height / PERSON_HEIGHT, 2)
        d["aspect"] = asset.aspect
    return d


def _asset_origin(cat: Catalog, kind: str, name: str) -> str | None:
    """``builtin`` / ``user`` when this registry entry comes from the asset library, else ``None`` (the engine's own)."""
    if name not in cat.assets:
        return None
    a = cat.assets.get(name)
    mine = {"object": "object", "archetype": "character", "background": "place"}.get(kind)
    return a.origin if mine == a.kind else None


def _user_asset(cat: Catalog, kind: str, name: str) -> bool:
    """Is this registry entry one the user added from their own asset folders (not the engine's or the built-in library)?"""
    return _asset_origin(cat, kind, name) == "user"


def describe(
    kind: str, catalog: Catalog | None = None, *, builtin_only: bool = False
) -> list[dict[str, Any]]:
    """The entries of one registry as data.  ``builtin_only`` leaves out the user's own assets (for files kept in the repo)."""
    cat = catalog or CATALOG
    reg = cat.registry(kind)
    rows: list[dict[str, Any]] = []
    for e in reg.entries():
        if builtin_only and _user_asset(cat, kind, e.name):
            continue
        row = describe_entry(reg.kind, e.name, e.obj, dict(e.meta))
        origin = _asset_origin(cat, kind, e.name)
        if origin:
            row["library"] = (
                origin  # a character, place or object of the asset library (not the engine's own)
            )
        rows.append(row)
    return rows


def build_manifest(catalog: Catalog | None = None, *, builtin_only: bool = False) -> dict[str, Any]:
    """The catalog the LLM (or a human) reads to know what can be put in a spec.

    ``builtin_only`` is for the files kept in the repository: they must not change with whatever asset folders a machine has.
    """
    cat = catalog or CATALOG

    def d(kind: str) -> list[dict[str, Any]]:
        return describe(kind, cat, builtin_only=builtin_only)

    return {
        "version": 1,
        "styles": d("style"),
        "backgrounds": d("background"),
        "actions": d("action"),
        "transitions": d("transition"),
        "camera_moves": d("camera_move"),
        "caption_styles": d("caption_style"),
        "archetypes": d("archetype"),
        "props": d("prop"),
        "objects": d("object"),
        "sfx": d("sfx"),
        "easings": [e["name"] for e in d("easing")],
    }


def manifest_path() -> Path:
    """Where the committed catalog lives: next to the background templates it describes."""
    return Path(__file__).resolve().parents[1] / "templates" / "manifest.json"
