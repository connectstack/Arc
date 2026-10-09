from __future__ import annotations

import json

from reel.core.catalog import Catalog
from reel.core.schema import build_schema, default_schema_path, schema_json


def test_schema_uses_from_alias_and_is_draft_2020() -> None:
    s = build_schema()
    assert s["$schema"].endswith("2020-12/schema")
    cm = s["$defs"]["CameraMoveSpec"]["properties"]
    assert "from" in cm and "from_" not in cm
    assert s["properties"]["scenes"]["minItems"] == 1
    assert s["properties"]["meta"]["$ref"].endswith("MetaSpec")


def test_every_field_is_documented_for_the_llm() -> None:
    s = build_schema()
    meta = s["$defs"]["MetaSpec"]["properties"]
    assert (
        meta["target_duration_sec"]["minimum"] == 45
        and meta["target_duration_sec"]["maximum"] == 60
    )
    assert "description" in meta["style"]


def test_enums_pin_registry_names(mini_catalog: Catalog) -> None:
    s = build_schema(mini_catalog, enums=True)
    assert s["$defs"]["ActionSpec"]["properties"]["name"]["enum"] == [
        "idle",
        "talk",
        "walk",
        "wave",
    ]
    assert "page_flip" in s["$defs"]["TransitionSpec"]["properties"]["type"]["enum"]
    assert s["$defs"]["BackgroundSpec"]["properties"]["template"]["enum"] == ["room", "street"]
    assert "ease_in_out" in s["$defs"]["CameraMoveSpec"]["properties"]["ease"]["enum"]
    plain = build_schema(mini_catalog, enums=False)
    assert "enum" not in plain["$defs"]["ActionSpec"]["properties"]["name"]


def test_committed_schema_is_fresh() -> None:
    p = default_schema_path()
    assert p.exists(), "run: reel schema -o schema/scene_spec.schema.json"
    assert json.loads(p.read_text()) == json.loads(schema_json(enums=False)), (
        "schema/scene_spec.schema.json is stale - run: reel schema -o schema/scene_spec.schema.json"
    )
