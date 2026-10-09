"""Describe what is registered, as plain data.

Used by `reel list ...`, by the template manifest the LLM reads (so it only picks things
that exist), and by documentation.  Everything here is derived from the live registries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from reel.core.catalog import CATALOG, Catalog


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
    for attr in ("category", "moves_root", "tags", "min_duration", "default_duration"):
        v = getattr(obj, attr, None)
        if v is not None and v != ():
            d[attr] = list(v) if isinstance(v, tuple) else v
    if kind == "background":
        slots = getattr(obj, "slot_names", None)
        if callable(slots):
            d["slots"] = sorted(slots({}))
    if kind == "camera_move":
        d["from_to"] = getattr(obj, "from_to", "")
    return d


def describe(kind: str, catalog: Catalog | None = None) -> list[dict[str, Any]]:
    cat = catalog or CATALOG
    reg = cat.registry(kind)
    return [describe_entry(reg.kind, e.name, e.obj, dict(e.meta)) for e in reg.entries()]


def build_manifest(catalog: Catalog | None = None) -> dict[str, Any]:
    """The catalog the LLM (or a human) reads to know what can be put in a spec."""
    cat = catalog or CATALOG
    return {
        "version": 1,
        "styles": describe("style", cat),
        "backgrounds": describe("background", cat),
        "actions": describe("action", cat),
        "transitions": describe("transition", cat),
        "camera_moves": describe("camera_move", cat),
        "caption_styles": describe("caption_style", cat),
        "archetypes": describe("archetype", cat),
        "props": describe("prop", cat),
        "sfx": describe("sfx", cat),
        "easings": [e["name"] for e in describe("easing", cat)],
    }


def manifest_path() -> Path:
    """Where the committed catalog lives: next to the background templates it describes."""
    return Path(__file__).resolve().parents[1] / "templates" / "manifest.json"
