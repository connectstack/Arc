"""Lint: the report must name exactly what is missing and never crash."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from reel.core.catalog import Catalog
from reel.core.lint import LintOptions, Severity, lint_data, lint_file, lint_text


def codes(rep: Any) -> set[str]:
    return {i.code for i in rep.issues}


def test_clean_spec_passes(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert rep.ok, rep.format_text()
    assert rep.total_sec == pytest.approx(50.0)
    assert rep.n_scenes == 10


# ------------------------------------------------------------------ registry references
def test_unknown_action_is_reported_with_path_name_and_hint(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    spec_dict["scenes"][3]["layers"][0]["actions"][0]["name"] = "moonwalk"
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert not rep.ok
    miss = rep.missing()
    assert list(miss) == ["action"]
    assert miss["action"]["moonwalk"] == ["scenes[3].layers[0].actions[0].name"]
    issue = next(i for i in rep.issues if i.code == "REGISTRY_MISSING")
    assert "reel new-action moonwalk" in (issue.hint or "")
    assert issue.kind == "action" and issue.name == "moonwalk"


def test_every_registry_kind_is_checked(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    sc = spec_dict["scenes"]
    spec_dict["meta"]["style"] = "oil_painting"
    spec_dict["characters"][0]["archetype"] = "dragon"
    spec_dict["characters"][0]["props"] = ["hat", "jetpack"]
    sc[0]["background"]["template"] = "spaceship"
    sc[0]["camera"]["moves"] = [
        {"type": "orbit", "from": 0, "to": 1, "t0": 0, "t1": 1, "ease": "wobbly"}
    ]
    sc[0]["captions"][0]["style"] = "neon"
    sc[0]["sfx"][0]["name"] = "kaboom"
    sc[0]["transition_out"] = {"type": "star_wipe", "duration": 0.5}
    rep = lint_data(spec_dict, catalog=mini_catalog)
    miss = rep.missing()
    assert set(miss) == {
        "style",
        "archetype",
        "prop",
        "background",
        "camera_move",
        "easing",
        "caption_style",
        "sfx",
        "transition",
    }
    assert "jetpack" in miss["prop"] and "hat" not in miss["prop"]


def test_style_override_replaces_meta_style(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    spec_dict["meta"]["style"] = "not_a_style"
    assert "style" in lint_data(spec_dict, catalog=mini_catalog).missing()
    rep = lint_data(spec_dict, catalog=mini_catalog, options=LintOptions(style_override="stickman"))
    assert "style" not in rep.missing()


def test_registry_check_survives_schema_errors(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    """One run shows everything: schema problems AND missing registry entries."""
    spec_dict["scenes"][0]["duration"] = 3  # schema error
    spec_dict["scenes"][1]["layers"][0]["actions"][0]["name"] = "teleport"  # registry error
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert "SCHEMA_UNKNOWN_FIELD" in codes(rep)
    assert "teleport" in rep.missing().get("action", {})


# ------------------------------------------------------------------ schema errors
def test_json_syntax_error_is_a_report_not_a_crash(mini_catalog: Catalog) -> None:
    rep = lint_text('{"meta": {"title": "x",}}', catalog=mini_catalog)
    assert codes(rep) == {"JSON_SYNTAX"}
    assert "line 1" in rep.issues[0].message


def test_non_object_top_level(mini_catalog: Catalog) -> None:
    assert not lint_data([1, 2], catalog=mini_catalog).ok
    assert not lint_data("nope", catalog=mini_catalog).ok


def test_typo_gets_did_you_mean(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["scenes"][0]["duratoin_sec"] = 5
    rep = lint_data(spec_dict, catalog=mini_catalog)
    issue = next(i for i in rep.issues if i.code == "SCHEMA_UNKNOWN_FIELD")
    assert issue.path == "scenes[0].duratoin_sec"
    assert "duration_sec" in (issue.hint or "")


def test_missing_required_field(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    del spec_dict["scenes"][2]["background"]
    rep = lint_data(spec_dict, catalog=mini_catalog)
    issue = next(i for i in rep.issues if i.code == "SCHEMA_MISSING")
    assert issue.path == "scenes[2].background"


def test_union_errors_are_collapsed_to_one_issue(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    spec_dict["scenes"][0]["layers"][0]["position"] = 42
    rep = lint_data(spec_dict, catalog=mini_catalog)
    pos = [i for i in rep.issues if i.path == "scenes[0].layers[0].position"]
    assert len(pos) == 1


# ------------------------------------------------------------------ duration budget
@pytest.mark.parametrize(
    "n_scenes,expected_ok", [(8, False), (9, True), (10, True), (12, True), (13, False)]
)
def test_duration_budget_45_to_60(
    spec_dict: dict[str, Any],
    mini_catalog: Catalog,
    make_scene: Any,
    n_scenes: int,
    expected_ok: bool,
) -> None:
    spec_dict["scenes"] = [make_scene(f"s{i}", 5.0) for i in range(n_scenes)]
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert ("DURATION_BUDGET" not in codes(rep)) is expected_ok
    assert rep.total_sec == pytest.approx(5.0 * n_scenes)


def test_budget_boundaries_are_inclusive(
    spec_dict: dict[str, Any], mini_catalog: Catalog, make_scene: Any
) -> None:
    spec_dict["scenes"] = [make_scene(f"s{i}", 4.5) for i in range(10)]  # exactly 45
    assert "DURATION_BUDGET" not in codes(lint_data(spec_dict, catalog=mini_catalog))
    spec_dict["scenes"] = [make_scene(f"s{i}", 6.0) for i in range(10)]  # exactly 60
    spec_dict["meta"]["target_duration_sec"] = 60
    assert "DURATION_BUDGET" not in codes(lint_data(spec_dict, catalog=mini_catalog))
    spec_dict["scenes"][0]["duration_sec"] = 6.2  # 60.2
    assert "DURATION_BUDGET" in codes(lint_data(spec_dict, catalog=mini_catalog))


def test_transition_overlaps_count_against_total(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    # 10 scenes x 5 s with a 1 s crossfade after 9 of them -> 50 - 9 = 41 s
    for sc in spec_dict["scenes"][:-1]:
        sc["transition_out"] = {"type": "crossfade", "duration": 1.0}
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert rep.total_sec == pytest.approx(41.0)
    assert "DURATION_BUDGET" in codes(rep)
    msg = next(i.message for i in rep.issues if i.code == "DURATION_BUDGET")
    assert "41.00s" in msg and "45-60" in msg


def test_duration_check_can_be_disabled(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["scenes"] = spec_dict["scenes"][:2]
    rep = lint_data(spec_dict, catalog=mini_catalog, options=LintOptions(check_duration=False))
    assert rep.ok, rep.format_text()


def test_target_duration_mismatch_warns(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["meta"]["target_duration_sec"] = 58
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert rep.ok
    assert "DURATION_TARGET" in codes(rep)


# ------------------------------------------------------------------ semantics
def test_undefined_character_and_duplicates(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    spec_dict["scenes"][0]["layers"][0]["character"] = "ghost"
    spec_dict["scenes"][1]["id"] = "s0"
    spec_dict["characters"].append(copy.deepcopy(spec_dict["characters"][0]))
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert {"CHARACTER_UNDEFINED", "DUPLICATE_ID"} <= codes(rep)


def test_position_slot_must_exist_in_background(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    spec_dict["scenes"][0]["layers"][0]["position"] = "sofa"  # room has a sofa slot
    assert "POSITION_SLOT" not in codes(lint_data(spec_dict, catalog=mini_catalog))
    spec_dict["scenes"][0]["layers"][0]["position"] = "throne"
    rep = lint_data(spec_dict, catalog=mini_catalog)
    issue = next(i for i in rep.issues if i.code == "POSITION_SLOT")
    assert "sofa" in (issue.hint or "") and "center" in (issue.hint or "")
    spec_dict["scenes"][0]["layers"][0]["position"] = "left"  # universal
    assert "POSITION_SLOT" not in codes(lint_data(spec_dict, catalog=mini_catalog))


def test_time_range_and_overflow(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    a = spec_dict["scenes"][0]["layers"][0]["actions"][0]
    a["t0"], a["t1"] = 2.0, 1.0
    spec_dict["scenes"][1]["captions"][0]["t1"] = 9.0
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert "TIME_RANGE" in codes(rep)
    assert "TIME_OVERFLOW" in codes(rep)
    assert next(i for i in rep.issues if i.code == "TIME_OVERFLOW").severity is Severity.WARNING


def test_action_params_are_validated(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    act = spec_dict["scenes"][0]["layers"][0]["actions"][0]  # wave
    act["params"] = {"wavs": 3}
    rep = lint_data(spec_dict, catalog=mini_catalog)
    issue = next(i for i in rep.issues if i.code == "PARAMS_INVALID")
    assert issue.path == "scenes[0].layers[0].actions[0].params.wavs"
    assert "waves" in (issue.hint or "")
    act["params"] = {"waves": 99}
    assert "PARAMS_INVALID" in codes(lint_data(spec_dict, catalog=mini_catalog))


def test_background_params_are_validated(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["scenes"][0]["background"]["params"] = {"density": 7}
    assert "PARAMS_INVALID" in codes(lint_data(spec_dict, catalog=mini_catalog))


def test_overlapping_root_motion_warns(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["scenes"][0]["layers"][0]["actions"] = [
        {"name": "walk", "t0": 0.0, "t1": 3.0, "params": {"to": "right"}},
        {"name": "walk", "t0": 2.0, "t1": 4.0, "params": {"to": "left"}},
    ]
    assert "ACTION_OVERLAP" in codes(lint_data(spec_dict, catalog=mini_catalog))


def test_caption_readability_warnings(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["scenes"][0]["captions"] = [
        {"text": "word " * 40, "t0": 0, "t1": 1.0, "style": "subtitle"},
        {"text": "overlap", "t0": 0.5, "t1": 2.0, "style": "subtitle"},
    ]
    c = codes(lint_data(spec_dict, catalog=mini_catalog))
    assert {"CAPTION_LONG", "CAPTION_OVERLAP"} <= c


def test_transition_checks(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["scenes"][0]["transition_out"] = {"type": "crossfade", "duration": 0}
    rep = lint_data(spec_dict, catalog=mini_catalog)
    assert "TRANSITION_ZERO" in codes(rep)
    spec_dict["scenes"][1]["transition_out"] = {"type": "wipe", "duration": 3.0}
    spec_dict["scenes"][1]["duration_sec"] = 2.0  # shorter than the transition
    assert "TRANSITION_CLAMPED" in codes(lint_data(spec_dict, catalog=mini_catalog))
    spec_dict["scenes"][1]["transition_out"] = {"type": "wipe", "duration": 9}  # schema caps at 3 s
    assert "SCHEMA" in codes(lint_data(spec_dict, catalog=mini_catalog))


def test_palette_validation(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["characters"][0]["palette"] = {"shirt": "red", "tie": "#fff"}
    c = codes(lint_data(spec_dict, catalog=mini_catalog))
    assert {"PALETTE_COLOR", "PALETTE_ROLE"} <= c


def test_audio_file_references(
    spec_dict: dict[str, Any], mini_catalog: Catalog, tmp_path: Any
) -> None:
    spec_dict["audio"] = {"music": "nope.mp3", "voiceover": "file", "ducking": True}
    rep = lint_data(spec_dict, catalog=mini_catalog, options=LintOptions(base_dir=tmp_path))
    assert {"FILE_MISSING", "AUDIO_CONFIG"} <= codes(rep)
    (tmp_path / "nope.mp3").write_bytes(b"x")
    spec_dict["audio"] = {"music": "nope.mp3", "voiceover": "none"}
    assert lint_data(spec_dict, catalog=mini_catalog, options=LintOptions(base_dir=tmp_path)).ok
    spec_dict["audio"] = {"music": "procedural:upbeat", "voiceover": "none"}
    assert lint_data(spec_dict, catalog=mini_catalog).ok


def test_procedural_music_moods_are_checked(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    for ok in ("procedural", "procedural:calm", " Procedural : EPIC "):
        spec_dict["audio"] = {"music": ok, "voiceover": "none"}
        assert lint_data(spec_dict, catalog=mini_catalog).ok, ok
    spec_dict["audio"] = {"music": "procedural:mysterius", "voiceover": "none"}
    rep = lint_data(spec_dict, catalog=mini_catalog)
    (issue,) = [i for i in rep.errors if i.path == "audio.music"]
    assert issue.code == "AUDIO_CONFIG" and "mysterious" in (
        issue.hint or ""
    )  # did-you-mean + the list
    assert "playful" in (issue.hint or "")
    # "proceduralish" is not the built-in generator: it is treated (and reported) as a missing file
    spec_dict["audio"] = {"music": "proceduralish", "voiceover": "none"}
    assert "FILE_MISSING" in codes(lint_data(spec_dict, catalog=mini_catalog))


# ------------------------------------------------------------------ report output
def test_report_serialises(spec_dict: dict[str, Any], mini_catalog: Catalog) -> None:
    spec_dict["scenes"][0]["layers"][0]["actions"][0]["name"] = "moonwalk"
    rep = lint_data(spec_dict, catalog=mini_catalog)
    data = json.loads(rep.to_json())
    assert data["ok"] is False
    assert data["missing"]["action"]["moonwalk"]
    assert data["counts"]["errors"] >= 1
    text = rep.format_text()
    assert "Missing from the registry" in text and "moonwalk" in text


def test_lint_file_reports_unreadable(mini_catalog: Catalog, tmp_path: Any) -> None:
    rep = lint_file(tmp_path / "missing.json", catalog=mini_catalog)
    assert codes(rep) == {"FILE"}


def test_lint_file_resolves_relative_audio_from_spec_dir(
    spec_dict: dict[str, Any], mini_catalog: Catalog, tmp_path: Any
) -> None:
    (tmp_path / "bed.mp3").write_bytes(b"x")
    spec_dict["audio"]["music"] = "bed.mp3"
    p = tmp_path / "s.json"
    p.write_text(json.dumps(spec_dict))
    assert lint_file(p, catalog=mini_catalog).ok


# ------------------------------------------------------------------ robustness: lint never crashes
def _paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = [prefix] if prefix else []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _paths(v, (*prefix, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _paths(v, (*prefix, i))
    return out


def _mutate(doc: Any, path: tuple[Any, ...], how: int) -> Any:
    doc = copy.deepcopy(doc)
    parent = doc
    for key in path[:-1]:
        parent = parent[key]
    last = path[-1]
    junk = [None, "x", -7, 3.5, [], {}, [1, 2, 3], {"a": 1}, True, "", 10**9]
    if how == 0:
        del parent[last]
    else:
        parent[last] = junk[how % len(junk)]
    return doc


def test_lint_never_raises_on_mutated_documents(
    spec_dict: dict[str, Any], mini_catalog: Catalog
) -> None:
    import random

    rng = random.Random(1234)
    paths = _paths(spec_dict)
    for _ in range(400):
        doc = _mutate(spec_dict, rng.choice(paths), rng.randrange(0, 12))
        try:
            rep = lint_data(doc, catalog=mini_catalog)
        except Exception as exc:  # pragma: no cover - this is the failure we are guarding against
            raise AssertionError(f"lint crashed on a mutated spec: {exc!r}") from exc
        rep.format_text()
        rep.to_json()


def test_lint_handles_non_spec_inputs(mini_catalog: Catalog) -> None:
    for junk in (
        None,
        5,
        "nope",
        [],
        [1, {"a": 2}],
        {"meta": 5, "scenes": "x", "characters": {"a": 1}},
        {"scenes": [None, 3, {"layers": 5}]},
    ):
        rep = lint_data(junk, catalog=mini_catalog)
        assert not rep.ok
