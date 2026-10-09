"""JSON Schema export for the scene spec.

Pydantic models are the source of truth; this module turns them into a JSON Schema and
(optionally) pins every registry-bound field (action names, background templates,
transitions, ...) to an `enum` of what is actually registered right now.  The
enum-pinned schema is what the LLM prompt embeds, so a model constrained by it can
only pick things that exist.  The committed `schema/scene_spec.schema.json` is the
*plain* flavour (no enums, since plugins can add names), for editors and CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reel.core.catalog import CATALOG, Catalog
from reel.core.spec import SCHEMA_ENUM_FIELDS, SPEC_VERSION, ReelSpec

SCHEMA_ID = "https://reel.local/schema/scene_spec.schema.json"


def build_schema(catalog: Catalog | None = None, *, enums: bool = False) -> dict[str, Any]:
    """JSON Schema of :class:`ReelSpec`.  ``enums=True`` pins registry-bound names."""
    cat = catalog or CATALOG
    schema = ReelSpec.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = "Reel scene spec"
    schema["description"] = (
        f"JSON scene spec v{SPEC_VERSION}: everything the offline renderer needs to produce a "
        "45-60 s 1080x1920 reel. All times are scene-local seconds; positions are screen fractions."
    )
    if enums:
        defs = schema.get("$defs", {})
        for def_name, (prop, kind) in SCHEMA_ENUM_FIELDS.items():
            names = cat.registry(kind).names()
            props = defs.get(def_name, {}).get("properties", {})
            if names and prop in props:
                props[prop]["enum"] = names
        ease_names = cat.easings.names()
        cm = defs.get("CameraMoveSpec", {}).get("properties", {})
        if ease_names and "ease" in cm:
            cm["ease"]["enum"] = ease_names
    return schema


def schema_json(catalog: Catalog | None = None, *, enums: bool = False) -> str:
    return json.dumps(build_schema(catalog, enums=enums), indent=2, sort_keys=False) + "\n"


def default_schema_path(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parents[3]
    return base / "schema" / "scene_spec.schema.json"
