from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from reel.core.spec import ReelSpec


def test_valid_spec_parses_and_roundtrips(spec_dict: dict[str, Any]) -> None:
    spec = ReelSpec.model_validate(spec_dict)
    assert spec.meta.style == "flat_vector"
    assert len(spec.scenes) == 10
    again = ReelSpec.model_validate(json.loads(spec.to_json()))
    assert again == spec


def test_unknown_field_is_rejected(spec_dict: dict[str, Any]) -> None:
    spec_dict["scenes"][0]["duration"] = 3  # typo for duration_sec
    with pytest.raises(ValidationError) as ei:
        ReelSpec.model_validate(spec_dict)
    assert any(e["type"] == "extra_forbidden" for e in ei.value.errors())


def test_target_duration_must_be_in_budget(spec_dict: dict[str, Any]) -> None:
    spec_dict["meta"]["target_duration_sec"] = 30
    with pytest.raises(ValidationError):
        ReelSpec.model_validate(spec_dict)
    spec_dict["meta"]["target_duration_sec"] = 61
    with pytest.raises(ValidationError):
        ReelSpec.model_validate(spec_dict)


def test_camera_move_from_alias(spec_dict: dict[str, Any]) -> None:
    spec_dict["scenes"][0]["camera"]["moves"] = [
        {"type": "zoom", "from": 1.0, "to": 1.2, "t0": 0, "t1": 2, "ease": "ease_in_out"}
    ]
    spec = ReelSpec.model_validate(spec_dict)
    mv = spec.scenes[0].camera.moves[0]
    assert mv.from_ == 1.0 and mv.to == 1.2
    dumped = json.loads(spec.to_json())
    assert "from" in dumped["scenes"][0]["camera"]["moves"][0]


def test_position_may_be_vector_or_slot(spec_dict: dict[str, Any]) -> None:
    spec_dict["scenes"][0]["layers"][0]["position"] = "left"
    assert ReelSpec.model_validate(spec_dict).scenes[0].layers[0].position == "left"
    spec_dict["scenes"][0]["layers"][0]["position"] = [0.2, 0.9]
    assert ReelSpec.model_validate(spec_dict).scenes[0].layers[0].position == [0.2, 0.9]
    spec_dict["scenes"][0]["layers"][0]["position"] = [0.2]
    with pytest.raises(ValidationError):
        ReelSpec.model_validate(spec_dict)


def test_scenes_required(spec_dict: dict[str, Any]) -> None:
    spec_dict["scenes"] = []
    with pytest.raises(ValidationError):
        ReelSpec.model_validate(spec_dict)


def test_negative_times_rejected(spec_dict: dict[str, Any]) -> None:
    spec_dict["scenes"][0]["captions"][0]["t0"] = -1
    with pytest.raises(ValidationError):
        ReelSpec.model_validate(spec_dict)
