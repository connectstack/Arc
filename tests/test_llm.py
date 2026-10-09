"""Script -> spec: everything here is offline (fake clients, ``httpx.MockTransport``, scripted replies).

Covers the JSON plumbing (``extract_json`` / ``salvage_json`` / ``normalize_spec``), the prompt (built
from the live catalog, nothing invented), the three HTTP clients, the repair loop, the compact mode for
small context windows, the manual path, and the offline heuristic planner.
"""

from __future__ import annotations

import copy
import itertools
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from reel.core.catalog import CATALOG, Catalog
from reel.core.lint import LintOptions, lint_data
from reel.core.schema import build_schema
from reel.core.spec import ReelSpec
from reel.core.timeline import compute_timeline
from reel.llm import (
    AnthropicClient,
    GenerationResult,
    HeuristicSpecGenerator,
    JSONExtractionError,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMResponseError,
    LLMSpecGenerator,
    LLMUnavailable,
    ManualSpecGenerator,
    Message,
    OllamaClient,
    OpenAICompatClient,
    ReplayClient,
    SpecGenerationError,
    Truncated,
    build_system_prompt,
    build_user_message,
    enrich_spec,
    extract_json,
    generate_spec,
    get_client,
    json_schema_for_llm,
    normalize_spec,
    render_prompt_preview,
    salvage_json,
)
from reel.llm.base import COMPACT_BELOW_TOKENS, estimate_tokens
from reel.llm.enrich import RESTING
from reel.llm.generator import (
    LLM_MAX_SCENE_SEC,
    LLM_MIN_SCENE_SEC,
    MIN_PARTIAL_SCENES,
    fit_total_duration,
    format_repair_message,
    salvage,
    shorter_reply_request,
)
from reel.llm.heuristic import _describe, build_cast, parse_script
from reel.llm.prompt import example_parts, render_catalog

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
STYLES = ("flat_vector", "paper_cutout", "stickman")
STORY = (EXAMPLES / "script_story.txt").read_text(encoding="utf-8")
EXPLAINER = (EXAMPLES / "script_explainer.txt").read_text(encoding="utf-8")
TINY = "A cat sat on the mat."
LONG = " ".join(
    f'Ravi walks to the market on day {i} and meets a friend named Priya who laughs. "We should '
    f'open a shop together!" says Priya. Ravi thinks about it for a long time because the idea '
    f"is big and scary, but finally he smiles."
    for i in range(1, 16)
)
DIALOGUE = """Ana: Did you see the news?
Ben: No, what happened?
Ana: (excited) The robot won the cooking contest!
Ben: (laughs) You are joking.
Ana: I am not! Look at this photo.
Ben: (surprised) Wow! That is a lot of pancakes.
Ana: Let's go celebrate!
Ben: Yes! Race you to the cafe!
"""


def total_of(spec: dict[str, Any]) -> float:
    return compute_timeline(ReelSpec.model_validate(spec)).total_sec


@pytest.fixture(scope="module")
def good_spec() -> dict[str, Any]:
    """A lint-clean spec for the repair-loop tests (the offline planner's own output)."""
    return HeuristicSpecGenerator().generate(STORY, style="flat_vector", seed=3).spec


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def cut_off_reply(spec: dict[str, Any], keep: int, tail: int = 60) -> str:
    """``spec`` as a model reply that ran out of room inside scene ``keep`` + 1."""
    base = {k: v for k, v in spec.items() if k != "audio"}
    done = compact_json({**base, "scenes": spec["scenes"][:keep]})
    return done[:-2] + "," + compact_json(spec["scenes"][keep])[:tail]


# =============================================================================== extract_json
class TestExtractJson:
    def test_plain_object(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_with_prose_around(self) -> None:
        text = 'Sure! Here you go:\n```json\n{"scenes": [{"id": "s1"}]}\n```\nHope that helps {really}.'
        assert extract_json(text) == {"scenes": [{"id": "s1"}]}

    def test_fence_without_language_tag(self) -> None:
        assert extract_json('```\n{"meta": {}}\n```') == {"meta": {}}

    def test_prose_before_and_after_without_fence(self) -> None:
        assert extract_json('Result -> {"meta": {"title": "x"}} (done)') == {"meta": {"title": "x"}}

    def test_braces_and_quotes_inside_strings(self) -> None:
        text = r'{"scenes": [{"id": "a", "notes": "use } and { and \" freely"}]}'
        assert extract_json(text)["scenes"][0]["notes"] == 'use } and { and " freely'

    def test_trailing_commas_and_comments_are_tolerated(self) -> None:
        text = '{\n  "meta": {"title": "t",}, // the title\n  "scenes": [1, 2,],\n  /* x */\n}'
        assert extract_json(text) == {"meta": {"title": "t"}, "scenes": [1, 2]}

    def test_think_block_with_braces_is_ignored(self) -> None:
        text = '<think>maybe {"scenes": 3} ... {oops</think>\n{"meta": {"title": "real"}}'
        assert extract_json(text) == {"meta": {"title": "real"}}

    def test_prefers_the_object_that_looks_like_a_spec(self) -> None:
        text = 'a small example {"x": 1} and then the spec {"meta": {}, "scenes": []}'
        assert extract_json(text) == {"meta": {}, "scenes": []}

    def test_placeholder_braces_in_prose_are_skipped(self) -> None:
        assert extract_json('In style {style} we get {"meta": {}}') == {"meta": {}}

    def test_cut_off_reply_is_reported_not_returned_in_fragments(self) -> None:
        text = '{"meta": {"title": "T", "safe_area": {"left": 0, "right": 1.0}}, "scenes": [{"id": "s01", "lay'
        with pytest.raises(JSONExtractionError) as exc:
            extract_json(text)
        assert exc.value.cut_off and "cut off" in str(exc.value)

    @pytest.mark.parametrize("text", ["", "   ", "no json here at all", "{style} only"])
    def test_no_object(self, text: str) -> None:
        with pytest.raises(JSONExtractionError):
            extract_json(text)

    def test_malformed_object_names_line_and_column(self) -> None:
        with pytest.raises(JSONExtractionError, match=r"line \d+, column \d+"):
            extract_json('{"a": 1 "b": 2}')


# =============================================================================== salvage
class TestSalvage:
    def test_keeps_closed_blocks_and_complete_scenes(self, good_spec: dict[str, Any]) -> None:
        got = salvage_json(cut_off_reply(good_spec, 4))
        assert got is not None
        assert [s["id"] for s in got["scenes"]] == [s["id"] for s in good_spec["scenes"][:4]]
        assert got["meta"]["title"] == good_spec["meta"]["title"]
        assert got["characters"] == good_spec["characters"]

    def test_only_closing_braces_missing_counts_as_complete(self) -> None:
        text = '{"meta": {"title": "T"}, "scenes": [{"id": "a"}, {"id": "b"}]'
        got = salvage(text)
        assert got is not None
        data, complete = got
        assert complete and len(data["scenes"]) == 2

    def test_cut_off_inside_a_scene_is_not_complete(self) -> None:
        got = salvage('{"scenes": [{"id": "a"}, {"id": "b", "lay')
        assert got is not None
        data, complete = got
        assert not complete and [s["id"] for s in data["scenes"]] == ["a"]

    def test_unterminated_code_fence(self) -> None:
        got = salvage_json('Here:\n```json\n{"scenes": [{"id": "a"}, {"id": "b"')
        assert got is not None and [s["id"] for s in got["scenes"]] == ["a"]

    def test_nothing_complete_means_none(self) -> None:
        assert salvage_json('{"meta": {"title": "T"}, "scenes": [{"id": "a", "lay') is None
        assert salvage_json("no json") is None


# =============================================================================== normalize_spec
def scene(sid: str, dur: float, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": sid,
        "duration_sec": dur,
        "background": {"template": "abstract"},
        "layers": [{"character": "ana", "position": "center", "actions": []}],
        "captions": [{"text": "hello there", "t0": 0.5, "t1": 2.0, "style": "subtitle"}],
    }
    out.update(kw)
    return out


def skeleton(scenes: list[dict[str, Any]], **meta: Any) -> dict[str, Any]:
    return {
        "meta": {"title": "T", "style": "flat_vector", **meta},
        "characters": [{"id": "ana", "archetype": "everyman", "palette": {"shirt": "#e63946"}}],
        "scenes": scenes,
    }


class TestNormalize:
    def test_fills_defaults_and_forces_style_seed_target(self) -> None:
        raw = skeleton(
            [scene(f"s{i}", 5.0) for i in range(10)],
            style="oil_painting",
            seed=99,
            target_duration_sec=12,
            description="a made-up field",
        )
        notes: list[str] = []
        out = normalize_spec(raw, style="stickman", seed=4, target_duration=52, notes=notes)
        assert out["version"] == "1.0"
        assert out["meta"]["style"] == "stickman" and out["meta"]["seed"] == 4
        assert out["meta"]["target_duration_sec"] == 52.0
        assert "description" not in out["meta"] and out["meta"]["fps"] == 30
        assert out["meta"]["resolution"] == [1080, 1920] and out["meta"]["aspect"] == "9:16"
        assert any("oil_painting" in n for n in notes)

    def test_meta_overrides_models_must_not_set_are_dropped(self) -> None:
        raw = skeleton([scene("s1", 5.0)], safe_area={"left": 0, "right": 1.0}, fx={"grain": 2})
        out = normalize_spec(raw, style="flat_vector")
        assert "safe_area" not in out["meta"] and "fx" not in out["meta"]

    def test_budget_is_hit_including_crossfade_overlaps(self) -> None:
        scenes = [
            scene(f"s{i}", 3.0, transition_out={"type": "crossfade", "duration": 1.0})
            for i in range(10)
        ]
        raw = skeleton(scenes)
        assert total_of(
            raw | {"meta": {**raw["meta"], "target_duration_sec": 50}}
        ) == pytest.approx(21.0)
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        assert total_of(out) == pytest.approx(50.0, abs=0.06)
        assert lint_data(out).ok

    @pytest.mark.parametrize("target", [45, 50, 60])
    def test_budget_edges_stay_inside_45_60(self, target: float) -> None:
        raw = skeleton([scene(f"s{i}", 9.0) for i in range(9)])  # 81 s: far too long
        out = normalize_spec(raw, style="flat_vector", target_duration=target)
        assert 45.0 - 1e-6 <= total_of(out) <= 60.0 + 1e-6
        assert total_of(out) == pytest.approx(target, abs=0.06)

    def test_long_and_short_models_land_on_the_target(self) -> None:
        for dur in (1.0, 12.0):
            out = normalize_spec(
                skeleton([scene(f"s{i}", dur) for i in range(8)]), style="flat_vector"
            )
            assert total_of(out) == pytest.approx(50.0, abs=0.06)

    def test_scene_durations_are_clamped_to_the_hard_limits(self) -> None:
        raw = skeleton([scene("a", 100.0), scene("b", 0.1), scene("c", 5.0)])
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        assert all(0.5 <= s["duration_sec"] <= 30.0 for s in out["scenes"])

    def test_pacing_range_pulls_odd_scenes_in(self) -> None:
        raw = skeleton([scene(f"s{i}", d) for i, d in enumerate([1, 1, 1, 1, 1, 1, 20, 1])])
        out = normalize_spec(
            raw,
            style="flat_vector",
            min_scene_sec=LLM_MIN_SCENE_SEC,
            max_scene_sec=LLM_MAX_SCENE_SEC,
        )
        durs = [s["duration_sec"] for s in out["scenes"]]
        assert min(durs) >= LLM_MIN_SCENE_SEC - 1e-6 and max(durs) <= LLM_MAX_SCENE_SEC + 1e-6
        assert total_of(out) == pytest.approx(50.0, abs=0.06)

    def test_inner_times_follow_the_rescale(self) -> None:
        raw = skeleton([scene(f"s{i}", 2.0) for i in range(10)])
        raw["scenes"][0]["captions"][0].update(t0=1.0, t1=2.0)
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        s0 = out["scenes"][0]
        assert s0["captions"][0]["t0"] == pytest.approx(1.0 * s0["duration_sec"] / 2.0, abs=0.02)

    def test_zero_length_and_out_of_scene_windows(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        sc = raw["scenes"][0]
        sc["layers"][0]["actions"] = [
            {"name": "wave", "t0": 1.0, "t1": 1.0},  # zero length: dropped
            {"name": "wave", "t0": 4.0, "t1": 9.0},  # runs past the end: clamped
            {"name": "wave", "t0": 7.0, "t1": 8.0},  # after the scene: dropped
        ]
        sc["captions"] = [{"text": "late caption here", "t0": 4.9, "t1": 6.5, "style": "subtitle"}]
        sc["sfx"] = [{"name": "pop", "t": 99.0}] if "pop" in CATALOG.sfx else []
        sc["camera"] = {
            "moves": [{"type": "pan", "from": [0, 0], "to": [0.1, 0], "t0": 3.0, "t1": 3.0}]
        }
        notes: list[str] = []
        out = normalize_spec(raw, style="flat_vector", notes=notes, target_duration=50)
        o = out["scenes"][0]
        dur = o["duration_sec"]
        acts = o["layers"][0]["actions"]
        assert len(acts) == 1 and acts[0]["t1"] <= dur + 1e-6
        cap = o["captions"][0]
        assert 0 <= cap["t0"] < cap["t1"] <= dur + 1e-6 and cap["t1"] - cap["t0"] >= 0.3
        assert all(s["t"] <= dur for s in o.get("sfx", []))
        assert o["camera"]["moves"] == []
        assert lint_data(out).ok, lint_data(out).format_text()
        assert any("zero-length" in n for n in notes)

    def test_actions_get_their_minimum_length(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        raw["scenes"][0]["layers"][0]["actions"] = [{"name": "wave", "t0": 1.0, "t1": 1.2}]
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        a = out["scenes"][0]["layers"][0]["actions"][0]
        assert a["t1"] - a["t0"] >= CATALOG.actions.get("wave").min_duration - 1e-6

    def test_duplicate_ids_are_renamed(self) -> None:
        raw = skeleton([scene("s1", 5.0) for _ in range(3)])
        raw["characters"] = [raw["characters"][0], dict(raw["characters"][0])]
        out = normalize_spec(copy.deepcopy(raw), style="flat_vector")
        assert len({s["id"] for s in out["scenes"]}) == 3
        assert len({c["id"] for c in out["characters"]}) == 2

    def test_input_is_not_modified(self) -> None:
        raw = skeleton([scene("s1", 0.1)])
        before = copy.deepcopy(raw)
        normalize_spec(raw, style="flat_vector")
        assert raw == before

    def test_wrapped_envelope_is_unwrapped(self) -> None:
        out = normalize_spec({"spec": skeleton([scene("s1", 5.0)])}, style="flat_vector")
        assert len(out["scenes"]) == 1

    def test_common_slips_are_tidied(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        sc = raw["scenes"][0]
        sc["duration"] = sc.pop("duration_sec")  # wrong key
        sc["layers"][0]["actions"] = [{"name": "wave", "start": 0.5, "end": 2.0}]
        raw["characters"][0]["palette"] = {"shirt": "red", "pants": "264653"}
        raw["characters"][0]["props"] = ["hat", "jetpack"]
        sc["mood"] = "sad"  # unknown scene field
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        assert out["scenes"][0]["duration_sec"] > 0 and "mood" not in out["scenes"][0]
        assert out["scenes"][0]["layers"][0]["actions"][0]["t0"] == pytest.approx(0.5, abs=0.2)
        assert out["characters"][0]["palette"] == {"shirt": "#e63946", "pants": "#264653"}
        assert out["characters"][0]["props"] == ["hat"]
        assert lint_data(out).ok

    def test_meaningful_names_are_left_for_the_model_to_fix(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        raw["scenes"][0]["layers"][0]["actions"] = [{"name": "moonwalk", "t0": 0.5, "t1": 2.0}]
        raw["scenes"][1]["background"]["template"] = "spaceship"
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        assert out["scenes"][0]["layers"][0]["actions"][0]["name"] == "moonwalk"
        assert lint_data(out).missing().keys() >= {"action", "background"}

    def test_characters_used_but_not_defined_get_defined(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        del raw["characters"]
        raw["scenes"][0]["layers"][0]["character"] = "Mia"
        raw["scenes"][1]["layers"][0]["character"] = "grandpa"
        raw["scenes"][1]["captions"][0]["speaker"] = "Grandpa"
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        ids = {c["id"] for c in out["characters"]}
        assert ids == {"Mia", "grandpa", "ana"}
        assert all(c["palette"]["shirt"] for c in out["characters"])
        assert len({c["palette"]["shirt"] for c in out["characters"]}) == 3
        assert next(c for c in out["characters"] if c["id"] == "grandpa")["archetype"] == "elder"
        assert lint_data(out).ok

    def test_character_references_match_case_insensitively(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        raw["scenes"][0]["layers"][0]["character"] = "ANA"
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        assert out["scenes"][0]["layers"][0]["character"] == "ana"
        assert len(out["characters"]) == 1

    def test_unknown_slots_become_left_center_right(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        raw["scenes"][0]["layers"][0]["position"] = "stump_of_doom"
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        assert out["scenes"][0]["layers"][0]["position"] == "center"
        assert lint_data(out).ok

    def test_action_targets_the_scene_cannot_resolve_are_dropped(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        raw["characters"].append({"id": "bo", "archetype": "kid", "palette": {"shirt": "#2a9d8f"}})
        raw["scenes"][0]["layers"].append({"character": "bo", "position": "left", "actions": []})
        raw["scenes"][0]["layers"][0]["actions"] = [
            {"name": "look_at", "t0": 0.5, "t1": 2.0, "params": {"target": "BO"}},  # case slip
            {"name": "point", "t0": 2.1, "t1": 3.5, "params": {"target": "nobody"}},  # absent
            {"name": "look_at", "t0": 3.6, "t1": 4.6, "params": {"target": "camera"}},  # fine
        ]
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        acts = out["scenes"][0]["layers"][0]["actions"]
        assert acts[0]["params"] == {"target": "bo"}
        assert acts[1]["params"] == {}
        assert acts[2]["params"] == {"target": "camera"}

    def test_feet_stay_in_the_placement_band(self) -> None:
        raw = skeleton([scene(f"s{i}", 5.0) for i in range(10)])
        raw["scenes"][0]["layers"][0]["position"] = [0.02, 0.95]
        out = normalize_spec(raw, style="flat_vector", target_duration=50)
        x, y = out["scenes"][0]["layers"][0]["position"]
        assert 0.15 <= x <= 0.85 and 0.62 <= y <= 0.76

    def test_audio_block_is_replaced_not_trusted(self) -> None:
        raw = skeleton([scene("s1", 5.0)]) | {"audio": {"music": "/no/such/song.mp3"}}
        out = normalize_spec(raw, style="flat_vector", audio={"music": "procedural"})
        assert out["audio"] == {"music": "procedural"}

    def test_non_objects_are_rejected(self) -> None:
        with pytest.raises(TypeError):
            normalize_spec([1, 2], style="flat_vector")  # type: ignore[arg-type]

    def test_fit_total_duration_reports_the_total(self) -> None:
        scenes = [scene(f"s{i}", 4.0) for i in range(6)]
        assert fit_total_duration(scenes, 50.0) == pytest.approx(50.0, abs=0.06)


# =============================================================================== the prompt
def tiny_catalog() -> Catalog:
    """A catalog with names nothing else uses: the prompt must show these and only these."""
    c = Catalog()
    c.styles.register("ink", SimpleNamespace(summary="inked lines"))
    c.backgrounds.register(
        "dojo", SimpleNamespace(params_model=None, slot_names=lambda p: ["mat"], summary="A dojo")
    )
    for name, root in (("bow", False), ("chop", True)):
        c.actions.register(
            name,
            SimpleNamespace(
                params_model=None,
                moves_root=root,
                min_duration=0.5,
                default_duration=1.0,
                summary=name,
            ),
        )
    c.transitions.register("fadex", SimpleNamespace(params_model=None, summary="x"))
    c.camera_moves.register("glide", SimpleNamespace(check=None, summary="Glide", from_to="0..1"))
    c.caption_styles.register("plain", SimpleNamespace(summary="plain text"))
    c.sfx.register("gong", SimpleNamespace(summary="a gong"))
    c.archetypes.register("monk", SimpleNamespace(summary="a monk"))
    c.props.register("staff", SimpleNamespace(summary="a staff"))
    c.easings.register("linear", lambda u: u)
    return c


def registry_names(cat: Catalog) -> set[str]:
    return {n for reg in cat.all_registries().values() for n in reg.names()}


def reference_tokens(prompt: str) -> set[str]:
    """Names the prompt *refers to*: quoted, backticked, listed (``- name``) or in ``a|b|c`` choices."""
    refs: set[str] = set(re.findall(r'"([A-Za-z_][\w]*)"', prompt))
    refs |= {
        w for blob in re.findall(r"`([^`]+)`", prompt) for w in re.findall(r"[A-Za-z_]\w*", blob)
    }
    refs |= set(re.findall(r"^\s*- ([A-Za-z_]\w*)", prompt, re.M))
    for run in re.findall(r"[A-Za-z_]\w*(?:\|[A-Za-z_]\w*)+", prompt):
        refs |= set(run.split("|"))
    return refs


@pytest.fixture(scope="module")
def full_prompt() -> str:
    return build_system_prompt("paper_cutout", 50, verbatim=False)


@pytest.fixture(scope="module")
def compact_prompt() -> str:
    return build_system_prompt("paper_cutout", 50, compact=True, verbatim=False)


class TestPrompt:
    def test_every_registered_name_is_in_the_catalog_section(self, full_prompt: str) -> None:
        for kind, reg in CATALOG.all_registries().items():
            if kind in ("style", "easing"):
                continue  # one style is fixed; easings are listed in a sentence
            for name in reg.names():
                assert re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", full_prompt), (kind, name)
        for ease in CATALOG.easings.names():
            assert ease in full_prompt

    def test_real_backgrounds_actions_and_their_params(self, full_prompt: str) -> None:
        assert "abstract" in full_prompt and "walk" in full_prompt
        assert "blobs|circles" in full_prompt  # an enum param of abstract
        assert "pattern" in full_prompt

    def test_budget_arithmetic_is_spelled_out(self, full_prompt: str) -> None:
        assert "sum of every scene's duration_sec" in full_prompt and "45" in full_prompt
        assert "60" in full_prompt and "53.0 - 3.0 = 50.0 s" in full_prompt
        other = build_system_prompt("flat_vector", 55)
        assert "58.0 - 3.0 = 55.0 s" in other

    def test_target_is_clamped_into_the_budget(self) -> None:
        assert "meta.target_duration_sec" in build_system_prompt("flat_vector", 10)
        assert '"target_duration_sec": 45' in build_system_prompt("flat_vector", 10)
        assert '"target_duration_sec": 60' in build_system_prompt("flat_vector", 600)

    def test_hard_rules_are_stated(self, full_prompt: str) -> None:
        for phrase in (
            "ONE JSON object",
            "machine",
            "8-14 scenes",
            "3-8 s",
            "0.62",
            "0.76",
            "0.15",
            "0.85",
            "At most 3 characters",
            "SCENE-LOCAL",
            "unique",
            '"paper_cutout"',
        ):
            assert phrase in full_prompt, phrase
        assert 'Do not write an "audio" block' in full_prompt

    def test_style_summary_is_included(self, full_prompt: str) -> None:
        summary = next(r for r in CATALOG.styles.entries() if r.name == "paper_cutout").obj.summary
        assert summary.split(",")[0].split()[0] in full_prompt

    def test_no_name_outside_the_catalog_is_mentioned(self) -> None:
        universe = registry_names(CATALOG) | registry_names(tiny_catalog())
        for compact in (False, True):
            cat = tiny_catalog()
            prompt = build_system_prompt("ink", 50, cat, compact=compact)
            foreign = (reference_tokens(prompt) & universe) - registry_names(cat) - {"title"}
            assert not foreign, f"compact={compact}: {sorted(foreign)}"
            for mine in ("dojo", "bow", "chop", "fadex", "plain"):
                assert mine in prompt

    def test_mini_catalog_prompt_only_knows_the_mini_names(self, mini_catalog: Catalog) -> None:
        universe = registry_names(CATALOG) | registry_names(mini_catalog)
        prompt = build_system_prompt("flat_vector", 50, mini_catalog)
        foreign = (reference_tokens(prompt) & universe) - registry_names(mini_catalog) - {"title"}
        assert not foreign, sorted(foreign)
        assert "room" in prompt and "street" in prompt and "sofa" in prompt
        assert "enter_from" not in prompt and "abstract" not in prompt

    def test_conditional_rules_follow_the_catalog(self, full_prompt: str) -> None:
        assert "enter_from" in full_prompt and "never add a talk action" in full_prompt
        bare = build_system_prompt("ink", 50, tiny_catalog())
        for gone in ("enter_from", "exit_to", "talk", '"subtitle"', '"shout"', "ENTRANCES"):
            assert gone not in bare

    def test_unknown_placeholder_is_a_template_bug(self, tmp_path: Path) -> None:
        from reel.llm.prompt import render_template

        with pytest.raises(KeyError, match="nope"):
            render_template("hello {{nope}}", {}, CATALOG)
        with pytest.raises(ValueError, match="unbalanced"):
            render_template("{{#has action walk}} never closed", {}, CATALOG)

    def test_the_worked_example_lints(self) -> None:
        for compact in (False, True):
            characters, one_scene = example_parts(compact=compact)
            spec = {
                "meta": {"title": "x", "style": "flat_vector"},
                "characters": characters,
                "scenes": [one_scene],
            }
            report = lint_data(spec, options=LintOptions(check_duration=False))
            assert report.ok, (compact, report.format_text())

    def test_user_message_carries_the_script(self) -> None:
        msg = build_user_message("Hello. World!", style="stickman", target_duration=47, seed=9)
        assert "Hello. World!" in msg and '"stickman"' in msg and "47" in msg and "seed = 9" in msg

    def test_schema_is_the_pinned_one(self) -> None:
        assert json_schema_for_llm() == build_schema(enums=True)
        enum = json_schema_for_llm()["$defs"]["ActionSpec"]["properties"]["name"]["enum"]
        assert enum == CATALOG.actions.names()

    def test_preview_reports_sizes_and_both_messages(self) -> None:
        text = render_prompt_preview("flat_vector", 50, script="One two three.")
        assert "SYSTEM PROMPT" in text and "USER MESSAGE" in text and "One two three." in text
        assert "JSON SCHEMA" in render_prompt_preview(include_schema=True)

    def test_prompt_is_ascii_clean(self, full_prompt: str, compact_prompt: str) -> None:
        for text in (full_prompt, compact_prompt):
            assert not re.search("[\u2013\u2014\u2018\u2019\u201c\u201d\u2026\u2192\u00d7]", text)
            assert "{{" not in text  # no template leftovers


class TestCompactPrompt:
    def test_is_small_enough_for_an_8k_window(self, full_prompt: str, compact_prompt: str) -> None:
        assert len(compact_prompt) < len(full_prompt) / 2
        assert estimate_tokens(compact_prompt) <= 2500
        assert estimate_tokens(full_prompt) > 4500  # that is why the compact one exists

    def test_still_has_the_rules_and_the_names(self, compact_prompt: str) -> None:
        for phrase in ("ONE JSON object", "45-60", "8-10 scenes", "0 <= t0 < t1", "speaker"):
            assert phrase in compact_prompt, phrase
        for kind in ("background", "action", "transition", "caption_style", "archetype"):
            for name in CATALOG.registry(kind).names():
                if name == "talk":
                    continue  # the speaker talks by itself: deliberately left out
                assert re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", compact_prompt), name

    def test_leaves_out_what_small_models_do_not_need(self, compact_prompt: str) -> None:
        assert "CAMERA MOVES" not in compact_prompt and "SFX" not in compact_prompt
        assert "PROPS" not in compact_prompt and "EASING" not in compact_prompt
        assert "Do not write camera, sfx, props, ease or audio" in compact_prompt

    def test_arithmetic_example_and_arithmetic_of_the_target(self, compact_prompt: str) -> None:
        assert "52.0 - 2.0 = 50 s" in compact_prompt
        assert "57.0 - 2.0 = 55 s" in build_system_prompt("flat_vector", 55, compact=True)

    def test_compact_schema_removes_optional_weight(self) -> None:
        full, small = json_schema_for_llm(), json_schema_for_llm(compact=True)
        assert len(json.dumps(small)) < len(json.dumps(full))
        scene_props = small["$defs"]["SceneSpec"]["properties"]
        assert "camera" not in scene_props and "sfx" not in scene_props
        assert small["required"] == ["meta", "characters", "scenes"]
        assert small["$defs"]["SceneSpec"]["required"] == [
            "id",
            "duration_sec",
            "background",
            "layers",
            "captions",
        ]
        assert small["properties"]["characters"]["minItems"] == 1
        assert small["properties"]["scenes"]["minItems"] == 8
        assert scene_props["layers"]["maxItems"] == 2
        assert scene_props["duration_sec"]["enum"][0] == 4.0
        assert small["$defs"]["CaptionSpec"]["properties"]["text"]["minLength"] == 2
        assert (
            "audio" not in small["properties"]
            and "fx" not in small["$defs"]["MetaSpec"]["properties"]
        )
        # the registry pins survive
        assert small["$defs"]["ActionSpec"]["properties"]["name"]["enum"] == CATALOG.actions.names()

    def test_compact_catalog_rendering_is_stable_text(self) -> None:
        text = render_catalog(compact=True)
        assert text.startswith("BACKGROUNDS") and "min 0.6" in text
        assert len(text) < len(render_catalog()) / 2


# =============================================================================== clients
def json_response(body: dict[str, Any], status: int = 200, **kw: Any) -> httpx.Response:
    return httpx.Response(status, json=body, **kw)


class Recorder:
    """A MockTransport handler that records requests and answers from a function."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request) if callable(self.answer) else self.answer

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def body(self, i: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[i].content)


SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}}
MSGS = [Message("user", "make a reel"), Message("assistant", "{}"), Message("user", "again")]


class TestReplayClient:
    def test_records_requests_and_replays_in_order(self) -> None:
        c = ReplayClient(["one", {"two": 2}])
        assert (
            c.complete("sys", MSGS, json_schema=SCHEMA, temperature=0.1, seed=5, max_tokens=99)
            == "one"
        )
        assert c.complete("sys2", MSGS[:1]) == '{"two": 2}'
        assert [r.system for r in c.requests] == ["sys", "sys2"]
        first = c.requests[0]
        assert first.json_schema == SCHEMA and first.seed == 5 and first.max_tokens == 99
        assert first.temperature == 0.1 and first.messages == MSGS
        assert c.remaining == 0
        with pytest.raises(LLMError, match="no scripted reply"):
            c.complete("s", MSGS)

    def test_truncated_marker_sets_the_flag(self) -> None:
        c = ReplayClient([Truncated("{"), "{}"])
        c.complete("s", MSGS)
        assert c.last_truncated
        c.complete("s", MSGS)
        assert not c.last_truncated

    def test_message_roles_are_checked(self) -> None:
        with pytest.raises(ValueError, match="role"):
            Message("system", "x")


class TestOllama:
    def reply(self, content: str = '{"ok": true}', **extra: Any) -> dict[str, Any]:
        return {"message": {"role": "assistant", "content": content}, "done": True, **extra}

    def test_request_shape_and_reply(self) -> None:
        rec = Recorder(lambda r: json_response(self.reply()))
        client = OllamaClient("llama3.1:8b", num_ctx=None, transport=rec.transport)
        text = client.complete(
            "SYS", MSGS, json_schema=SCHEMA, temperature=0.3, seed=11, max_tokens=777
        )
        assert text == '{"ok": true}'
        (req,) = [r for r in rec.requests if r.url.path == "/api/chat"]
        assert str(req.url) == "http://localhost:11434/api/chat"
        body = json.loads(req.content)
        assert body["model"] == "llama3.1:8b" and body["stream"] is False
        assert body["format"] == SCHEMA
        assert body["options"] == {"temperature": 0.3, "seed": 11, "num_predict": 777}
        assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
        assert body["messages"][0]["content"] == "SYS"
        assert client.name == "ollama:llama3.1:8b"

    def test_no_schema_means_no_format(self) -> None:
        rec = Recorder(lambda r: json_response(self.reply()))
        OllamaClient(num_ctx=None, transport=rec.transport).complete("s", MSGS)
        assert "format" not in rec.body()

    def test_custom_server_address(self) -> None:
        rec = Recorder(lambda r: json_response(self.reply()))
        OllamaClient("m", "http://gpu-box:11500/", num_ctx=None, transport=rec.transport).complete(
            "s", MSGS
        )
        assert str(rec.requests[-1].url) == "http://gpu-box:11500/api/chat"

    def test_context_window_is_read_from_the_model_and_clamps_num_ctx(self) -> None:
        def answer(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/show":
                info = {"general.architecture": "gemma2", "gemma2.context_length": 8192}
                return json_response({"model_info": info})
            return json_response(self.reply())

        rec = Recorder(answer)
        client = OllamaClient("gemma2:2b", num_ctx=16384, transport=rec.transport)
        assert client.context_window == 8192
        client.complete("s", MSGS)
        client.complete("s", MSGS)
        assert rec.body()["options"]["num_ctx"] == 8192
        shows = [r for r in rec.requests if r.url.path == "/api/show"]
        assert len(shows) == 1  # asked once, then cached
        assert json.loads(shows[0].content)["model"] == "gemma2:2b"

    def test_a_roomy_model_keeps_the_requested_window(self) -> None:
        def answer(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/show":
                return json_response(
                    {
                        "model_info": {
                            "general.architecture": "llama",
                            "llama.context_length": 131072,
                        }
                    }
                )
            return json_response(self.reply())

        rec = Recorder(answer)
        client = OllamaClient("llama3.1", num_ctx=16384, transport=rec.transport)
        assert client.context_window == 16384
        client.complete("s", MSGS)
        assert rec.body()["options"]["num_ctx"] == 16384

    def test_unknown_model_info_falls_back_to_the_requested_window(self) -> None:
        rec = Recorder(
            lambda r: (
                json_response({"error": "nope"}, 404)
                if r.url.path == "/api/show"
                else json_response(self.reply())
            )
        )
        client = OllamaClient("m", num_ctx=16384, transport=rec.transport)
        assert client.context_window == 16384
        assert (
            OllamaClient(
                "m", num_ctx=None, transport=Recorder(lambda r: json_response({}, 404)).transport
            ).context_window
            is None
        )

    def test_truncation_is_reported(self) -> None:
        rec = Recorder(lambda r: json_response(self.reply(done_reason="length")))
        client = OllamaClient(num_ctx=None, transport=rec.transport)
        client.complete("s", MSGS)
        assert client.last_truncated
        rec.answer = lambda r: json_response(self.reply(done_reason="stop"))
        client.complete("s", MSGS)
        assert not client.last_truncated

    def test_server_down_is_a_helpful_unavailable(self) -> None:
        def boom(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("[Errno 61] Connection refused", request=req)

        client = OllamaClient(num_ctx=None, transport=httpx.MockTransport(boom))
        with pytest.raises(LLMUnavailable) as exc:
            client.complete("s", MSGS)
        msg = str(exc.value)
        assert "ollama serve" in msg and "localhost:11434" in msg and "Connection refused" in msg
        assert isinstance(exc.value, LLMError)

    def test_missing_model_says_how_to_pull_it(self) -> None:
        rec = Recorder(
            lambda r: json_response({"error": "model 'zzz' not found, try pulling it first"}, 404)
        )
        with pytest.raises(LLMUnavailable, match="ollama pull zzz") as exc:
            OllamaClient("zzz", num_ctx=None, transport=rec.transport).complete("s", MSGS)
        assert exc.value.status_code == 404

    def test_timeouts_become_unavailable(self) -> None:
        def slow(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("took too long", request=req)

        with pytest.raises(LLMUnavailable, match="did not answer within"):
            OllamaClient(num_ctx=None, transport=httpx.MockTransport(slow), timeout=3).complete(
                "s", MSGS
            )

    def test_transient_errors_are_retried_then_reported(self) -> None:
        sleeps: list[float] = []
        rec = Recorder(
            lambda r: (
                json_response({"error": "x"}, 404)
                if r.url.path == "/api/show"
                else json_response({"error": "overloaded"}, 503)
            )
        )
        client = OllamaClient(num_ctx=None, transport=rec.transport, sleep=sleeps.append)
        with pytest.raises(LLMUnavailable, match="server error"):
            client.complete("s", MSGS)
        assert len([r for r in rec.requests if r.url.path == "/api/chat"]) == 3
        assert len(sleeps) == 2

    def test_empty_reply_is_a_response_error(self) -> None:
        rec = Recorder(lambda r: json_response(self.reply(content="  ")))
        with pytest.raises(LLMResponseError, match="empty reply"):
            OllamaClient(num_ctx=None, transport=rec.transport).complete("s", MSGS)


class TestOpenAICompat:
    def ok(self, content: Any = '{"ok": 1}', **choice: Any) -> httpx.Response:
        return json_response({"choices": [{"message": {"content": content}, **choice}]})

    def test_request_shape_headers_and_reply(self) -> None:
        rec = Recorder(lambda r: self.ok())
        client = OpenAICompatClient("gpt-4o-mini", api_key="sk-test-123", transport=rec.transport)
        text = client.complete(
            "SYS", MSGS, json_schema=SCHEMA, temperature=0.2, seed=3, max_tokens=500
        )
        assert text == '{"ok": 1}'
        req = rec.requests[0]
        assert str(req.url) == "https://api.openai.com/v1/chat/completions"
        assert req.headers["authorization"] == "Bearer sk-test-123"
        body = rec.body()
        assert body["model"] == "gpt-4o-mini" and body["max_tokens"] == 500 and body["seed"] == 3
        assert body["temperature"] == 0.2
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"] == SCHEMA
        assert body["response_format"]["json_schema"]["strict"] is False
        assert body["messages"][0] == {"role": "system", "content": "SYS"}

    def test_falls_back_to_json_object_then_to_plain_text(self) -> None:
        def answer(req: httpx.Request) -> httpx.Response:
            rf = json.loads(req.content).get("response_format")
            if rf is None:
                return self.ok()
            return json_response({"error": {"message": "response_format is not supported"}}, 400)

        rec = Recorder(answer)
        client = OpenAICompatClient("m", "http://localhost:1234/v1", transport=rec.transport)
        assert client.complete("s", MSGS, json_schema=SCHEMA) == '{"ok": 1}'
        modes = [
            (json.loads(q.content).get("response_format") or {}).get("type") for q in rec.requests
        ]
        assert modes == ["json_schema", "json_object", None]
        client.complete("s", MSGS, json_schema=SCHEMA)  # remembered: straight to plain text
        assert len(rec.requests) == 4

    def test_json_object_fallback_succeeds_when_the_server_takes_it(self) -> None:
        def answer(req: httpx.Request) -> httpx.Response:
            rf = json.loads(req.content)["response_format"]["type"]
            return self.ok() if rf == "json_object" else json_response({"error": "bad schema"}, 400)

        rec = Recorder(answer)
        OpenAICompatClient("m", "http://localhost:1234/v1", transport=rec.transport).complete(
            "s", MSGS, json_schema=SCHEMA
        )
        assert len(rec.requests) == 2

    def test_models_that_refuse_max_tokens_or_temperature_are_adapted(self) -> None:
        def answer(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            if "max_tokens" in body:
                return json_response(
                    {"error": {"message": "Use 'max_completion_tokens' instead."}}, 400
                )
            if "temperature" in body:
                return json_response(
                    {"error": {"message": "temperature does not support 0.4"}}, 400
                )
            return self.ok()

        rec = Recorder(answer)
        client = OpenAICompatClient("o1", api_key="sk-x", transport=rec.transport)
        assert client.complete("s", MSGS, max_tokens=321) == '{"ok": 1}'
        final = rec.body()
        assert (
            final["max_completion_tokens"] == 321
            and "max_tokens" not in final
            and "temperature" not in final
        )

    def test_local_servers_need_no_key(self) -> None:
        rec = Recorder(lambda r: self.ok())
        client = OpenAICompatClient(
            "local", "http://localhost:1234/v1/", api_key=None, env={}, transport=rec.transport
        )
        client.complete("s", MSGS)
        assert "authorization" not in rec.requests[0].headers
        assert str(rec.requests[0].url) == "http://localhost:1234/v1/chat/completions"

    def test_hosted_openai_without_a_key_is_a_config_error(self) -> None:
        with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
            OpenAICompatClient("gpt-4o", env={})

    def test_content_parts_and_refusals(self) -> None:
        parts = [{"type": "text", "text": '{"a"'}, {"type": "text", "text": ": 1}"}]
        rec = Recorder(lambda r: self.ok(parts))
        assert (
            OpenAICompatClient("m", "http://x/v1", transport=rec.transport).complete("s", MSGS)
            == '{"a": 1}'
        )
        rec = Recorder(
            lambda r: json_response(
                {"choices": [{"message": {"content": None, "refusal": "no way"}}]}
            )
        )
        with pytest.raises(LLMResponseError, match="refused"):
            OpenAICompatClient("m", "http://x/v1", transport=rec.transport).complete("s", MSGS)

    def test_length_finish_is_reported(self) -> None:
        rec = Recorder(lambda r: self.ok(finish_reason="length"))
        client = OpenAICompatClient("m", "http://x/v1", transport=rec.transport)
        client.complete("s", MSGS)
        assert client.last_truncated

    def test_keys_never_leak_into_errors_or_repr(self) -> None:
        secret = "sk-very-secret-key-1234"
        rec = Recorder(
            lambda r: json_response(
                {"error": {"message": f"Incorrect API key provided: {secret}"}}, 401
            )
        )
        client = OpenAICompatClient("gpt-4o", api_key=secret, transport=rec.transport)
        with pytest.raises(LLMUnavailable) as exc:
            client.complete("s", MSGS)
        assert secret not in str(exc.value) and "***" in str(exc.value)
        assert secret not in repr(client) and "OPENAI_API_KEY" in str(exc.value)
        assert rec.requests[0].headers["authorization"].endswith(secret)  # it was sent, only there

    def test_server_down_names_the_server(self) -> None:
        def boom(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=req)

        client = OpenAICompatClient(
            "m", "http://localhost:1234/v1", transport=httpx.MockTransport(boom)
        )
        with pytest.raises(LLMUnavailable, match="LM Studio"):
            client.complete("s", MSGS)

    def test_window_can_be_declared(self) -> None:
        client = OpenAICompatClient("m", "http://x/v1", context_window=4096)
        assert client.context_window == 4096


class TestAnthropic:
    def tool_reply(self, **extra: Any) -> httpx.Response:
        return json_response(
            {
                "content": [
                    {"type": "text", "text": "Calling the tool."},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "emit_spec",
                        "input": {"meta": {"title": "x"}},
                    },
                ],
                "stop_reason": "tool_use",
                **extra,
            }
        )

    def test_request_shape_and_tool_use_reply(self) -> None:
        rec = Recorder(lambda r: self.tool_reply())
        client = AnthropicClient("claude-sonnet-5-5", api_key="sk-ant-xyz", transport=rec.transport)
        text = client.complete(
            "SYS", MSGS, json_schema=SCHEMA, temperature=0.4, seed=1, max_tokens=900
        )
        assert json.loads(text) == {"meta": {"title": "x"}}
        req = rec.requests[0]
        assert str(req.url) == "https://api.anthropic.com/v1/messages"
        assert req.headers["x-api-key"] == "sk-ant-xyz"
        assert req.headers["anthropic-version"] == "2023-06-01"
        body = rec.body()
        assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 900
        assert body["system"] == "SYS" and body["temperature"] == 0.4
        assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
        assert body["tools"] == [
            {
                "name": "emit_spec",
                "description": body["tools"][0]["description"],
                "input_schema": SCHEMA,
            }
        ]
        assert body["tool_choice"] == {"type": "tool", "name": "emit_spec"}
        assert "seed" not in body

    def test_text_fallback_and_no_tools_without_a_schema(self) -> None:
        rec = Recorder(lambda r: json_response({"content": [{"type": "text", "text": '{"a": 1}'}]}))
        client = AnthropicClient(api_key="k", transport=rec.transport)
        assert client.complete("s", MSGS) == '{"a": 1}'
        assert "tools" not in rec.body() and "tool_choice" not in rec.body()

    def test_max_tokens_stop_reason_is_a_truncation(self) -> None:
        rec = Recorder(lambda r: self.tool_reply(stop_reason="max_tokens"))
        client = AnthropicClient(api_key="k", transport=rec.transport)
        client.complete("s", MSGS, json_schema=SCHEMA)
        assert client.last_truncated

    def test_auth_errors_do_not_leak_the_key(self) -> None:
        secret = "sk-ant-top-secret-777"
        rec = Recorder(
            lambda r: json_response(
                {
                    "error": {
                        "type": "authentication_error",
                        "message": f"invalid x-api-key {secret}",
                    }
                },
                401,
            )
        )
        client = AnthropicClient(api_key=secret, transport=rec.transport)
        with pytest.raises(LLMUnavailable) as exc:
            client.complete("s", MSGS)
        assert secret not in str(exc.value) and "ANTHROPIC_API_KEY" in str(exc.value)
        assert secret not in repr(client)

    def test_overloaded_is_retried(self) -> None:
        answers = iter([json_response({"error": "overloaded"}, 529), self.tool_reply()])
        rec = Recorder(lambda r: next(answers))
        client = AnthropicClient(api_key="k", transport=rec.transport, sleep=lambda s: None)
        assert json.loads(client.complete("s", MSGS, json_schema=SCHEMA))["meta"]["title"] == "x"
        assert len(rec.requests) == 2

    def test_connection_errors(self) -> None:
        def boom(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns failure", request=req)

        client = AnthropicClient(api_key="k", transport=httpx.MockTransport(boom))
        with pytest.raises(LLMUnavailable, match="cannot reach Anthropic"):
            client.complete("s", MSGS)

    def test_missing_key(self) -> None:
        with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
            AnthropicClient(env={})


class TestGetClient:
    def test_ollama_specs(self) -> None:
        c = get_client("ollama:llama3.1")
        assert isinstance(c, OllamaClient) and c.model == "llama3.1"
        c2 = get_client("ollama:llama3.1:8b@http://box:1234")
        assert (
            isinstance(c2, OllamaClient)
            and c2.model == "llama3.1:8b"
            and c2.base_url == "http://box:1234"
        )
        assert get_client("ollama").name == "ollama:llama3.1"

    def test_openai_specs(self) -> None:
        c = get_client("openai:gpt-4o-mini", env={"OPENAI_API_KEY": "sk-1"})
        assert isinstance(c, OpenAICompatClient) and c.base_url == "https://api.openai.com/v1"
        local = get_client("openai:qwen2.5@http://localhost:1234/v1", env={})
        assert (
            isinstance(local, OpenAICompatClient) and local.base_url == "http://localhost:1234/v1"
        )
        assert local.model == "qwen2.5"

    def test_anthropic_spec(self) -> None:
        c = get_client("anthropic:claude-sonnet-5-5", env={"ANTHROPIC_API_KEY": "k"})
        assert isinstance(c, AnthropicClient) and c.model == "claude-sonnet-5-5"

    def test_helpful_errors(self) -> None:
        with pytest.raises(LLMConfigError, match=r"unknown LLM provider 'olama'.*ollama"):
            get_client("olama:llama3")
        with pytest.raises(LLMConfigError, match="no provider"):
            get_client("llama3.1")
        with pytest.raises(LLMConfigError, match="empty"):
            get_client("  ")
        with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
            get_client("openai:gpt-4o", env={})
        with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
            get_client("anthropic:claude-sonnet-5-5", env={})
        assert isinstance(LLMConfigError("x"), ValueError)

    def test_a_bare_provider_means_its_default_model(self) -> None:
        assert get_client("openai", env={"OPENAI_API_KEY": "k"}).name == "openai:gpt-4o-mini"
        assert get_client("openai:", env={"OPENAI_API_KEY": "k"}).name == "openai:gpt-4o-mini"
        assert get_client("anthropic", env={"ANTHROPIC_API_KEY": "k"}).name.startswith(
            "anthropic:claude-"
        )
        assert get_client("ollama").name == "ollama:llama3.1"

    def test_transport_is_injectable(self) -> None:
        rec = Recorder(lambda r: json_response({"message": {"content": "{}"}}))
        client = get_client("ollama:m", transport=rec.transport)
        assert client.complete("s", MSGS) == "{}"


# =============================================================================== the repair loop
def break_action(spec: dict[str, Any], name: str = "moonwalk") -> dict[str, Any]:
    bad = copy.deepcopy(spec)
    for sc in bad["scenes"]:
        for layer in sc["layers"]:
            if layer["actions"]:
                layer["actions"][0]["name"] = name
                return bad
    raise AssertionError("no action to break")


class TestLLMSpecGenerator:
    def test_valid_first_reply(self, good_spec: dict[str, Any]) -> None:
        client = ReplayClient([good_spec], name="fake")
        res = LLMSpecGenerator(client).generate(
            STORY, style="flat_vector", seed=3, target_duration=50
        )
        assert isinstance(res, GenerationResult) and res.ok and res.attempts == 1
        assert res.generator == "llm:fake" and res.lint.ok
        assert total_of(res.spec) == pytest.approx(50.0, abs=0.06)
        assert res.spec["audio"] == {"music": "procedural", "voiceover": "tts", "ducking": True}

    def test_what_the_client_is_asked(self, good_spec: dict[str, Any]) -> None:
        client = ReplayClient([good_spec])
        LLMSpecGenerator(client, temperature=0.25).generate(STORY, style="flat_vector", seed=8)
        (req,) = client.requests
        assert req.system == build_system_prompt("flat_vector", 50, verbatim=False)
        assert req.json_schema == json_schema_for_llm()
        assert req.temperature == 0.25 and req.seed == 8 and req.max_tokens == 12288
        assert [m.role for m in req.messages] == ["user"] and "Midnight Cookie" in req.messages[
            0
        ].content

    def test_unknown_action_is_repaired_on_the_second_reply(
        self, good_spec: dict[str, Any]
    ) -> None:
        client = ReplayClient([break_action(good_spec), good_spec])
        res = LLMSpecGenerator(client).generate(STORY, style="flat_vector", seed=3)
        assert res.attempts == 2 and res.ok
        second = client.requests[1]
        roles = [m.role for m in second.messages]
        assert roles == ["user", "assistant", "user"]
        complaint = second.messages[-1].content
        assert "Fix these problems" in complaint and "FULL corrected JSON" in complaint
        assert "moonwalk" in complaint and "REGISTRY_MISSING" in complaint
        assert "moonwalk" in second.messages[1].content  # the model sees its own reply
        assert any("repair round" in n for n in res.notes)

    def test_never_fixed_raises_with_the_last_report_and_reply(
        self, good_spec: dict[str, Any]
    ) -> None:
        bad = break_action(good_spec)
        client = ReplayClient([bad] * 4)
        with pytest.raises(SpecGenerationError) as exc:
            LLMSpecGenerator(client, max_repairs=3).generate(STORY, style="flat_vector", seed=3)
        err = exc.value
        assert err.attempts == 4 and len(client.requests) == 4
        assert (
            err.lint is not None and not err.lint.ok and "moonwalk" in err.lint.missing()["action"]
        )
        assert err.raw is not None and "moonwalk" in err.raw
        assert err.spec is not None
        text = str(err)
        assert "still has" in text and "REGISTRY_MISSING" in text and "moonwalk" in text

    def test_zero_repairs_means_one_call(self, good_spec: dict[str, Any]) -> None:
        client = ReplayClient([break_action(good_spec), good_spec])
        with pytest.raises(SpecGenerationError):
            LLMSpecGenerator(client, max_repairs=0).generate(STORY, style="flat_vector")
        assert len(client.requests) == 1

    def test_non_json_reply_is_repaired(self, good_spec: dict[str, Any]) -> None:
        client = ReplayClient(["I would love to help, but first let me say hello!", good_spec])
        res = LLMSpecGenerator(client).generate(STORY, style="flat_vector", seed=3)
        assert res.attempts == 2
        assert "could not be parsed" in client.requests[1].messages[-1].content

    def test_prose_and_fences_around_the_json_are_fine(self, good_spec: dict[str, Any]) -> None:
        client = ReplayClient(
            [f"Here is your reel!\n```json\n{json.dumps(good_spec, indent=1)}\n```\nEnjoy."]
        )
        assert LLMSpecGenerator(client).generate(STORY, style="flat_vector", seed=3).attempts == 1

    def test_the_model_cannot_pick_style_seed_or_audio(self, good_spec: dict[str, Any]) -> None:
        wild = copy.deepcopy(good_spec)
        wild["meta"].update(style="oil", seed=12345, target_duration_sec=7)
        wild["audio"] = {"music": "/definitely/missing.mp3", "voiceover": "file"}
        res = LLMSpecGenerator(ReplayClient([wild])).generate(
            STORY, style="stickman", seed=2, target_duration=48
        )
        assert res.spec["meta"]["style"] == "stickman" and res.spec["meta"]["seed"] == 2
        assert res.spec["meta"]["target_duration_sec"] == 48.0
        assert res.spec["audio"]["music"] == "procedural"
        assert total_of(res.spec) == pytest.approx(48.0, abs=0.06)

    def test_custom_audio_block(self, good_spec: dict[str, Any]) -> None:
        res = LLMSpecGenerator(ReplayClient([good_spec]), audio={}).generate(
            STORY, style="flat_vector"
        )
        assert res.spec["audio"] == {}

    def test_off_budget_totals_are_fixed_without_asking_the_model(
        self, good_spec: dict[str, Any]
    ) -> None:
        slow = copy.deepcopy(good_spec)
        for sc in slow["scenes"]:
            sc["duration_sec"] = round(sc["duration_sec"] * 2.4, 2)  # ~120 s
        res = LLMSpecGenerator(ReplayClient([slow])).generate(STORY, style="flat_vector", seed=3)
        assert res.attempts == 1 and total_of(res.spec) == pytest.approx(50.0, abs=0.06)

    def test_input_validation(self) -> None:
        gen = LLMSpecGenerator(ReplayClient([]))
        with pytest.raises(SpecGenerationError, match="empty"):
            gen.generate("   ", style="flat_vector")
        with pytest.raises(SpecGenerationError, match=r"unknown style 'flat_vektor'.*flat_vector"):
            gen.generate(STORY, style="flat_vektor")
        with pytest.raises(ValueError, match="max_repairs"):
            LLMSpecGenerator(ReplayClient([]), max_repairs=-1)

    def test_target_is_clamped_with_a_note(self, good_spec: dict[str, Any]) -> None:
        res = LLMSpecGenerator(ReplayClient([good_spec])).generate(
            STORY, style="flat_vector", target_duration=300
        )
        assert res.spec["meta"]["target_duration_sec"] == 60.0
        assert any("clamped" in n for n in res.notes)

    def test_client_errors_propagate(self) -> None:
        class Down(LLMClient):
            name = "down"

            def complete(self, system: str, messages: list[Message], **kw: Any) -> str:
                raise LLMUnavailable("server is gone")

        with pytest.raises(LLMUnavailable, match="gone"):
            LLMSpecGenerator(Down()).generate(STORY, style="flat_vector")

    def test_repair_message_lists_errors_warnings_and_the_budget(
        self, good_spec: dict[str, Any]
    ) -> None:
        broken = copy.deepcopy(good_spec)
        broken["scenes"] = broken["scenes"][:2]
        report = lint_data(broken)
        text = format_repair_message(report, truncated=True)
        assert "DURATION_BUDGET" in text and "must be 45-60 s" in text and "cut off" in text
        short = format_repair_message(
            report, max_errors=1, max_warnings=0, restart=True, hint_chars=20
        )
        assert "Write the whole spec again" in short


# =============================================================================== small context windows
class TestCompactMode:
    def test_small_window_picks_the_compact_prompt_and_fits_the_reply(
        self, good_spec: dict[str, Any]
    ) -> None:
        client = ReplayClient([good_spec], context_window=8192)
        res = LLMSpecGenerator(client).generate(STORY, style="paper_cutout", seed=3)
        (req,) = client.requests
        assert req.system == build_system_prompt("paper_cutout", 50, compact=True, verbatim=False)
        assert req.json_schema == json_schema_for_llm(compact=True)
        used = estimate_tokens(req.system) + estimate_tokens(req.messages[0].content)
        assert req.max_tokens < 8192 - used + 1 and req.max_tokens >= 768
        assert req.temperature <= 0.3
        assert "Keep it SHORT" in req.messages[0].content
        assert any("compact" in n and "8192" in n for n in res.notes)

    def test_big_windows_and_unknown_windows_keep_the_full_prompt(
        self, good_spec: dict[str, Any]
    ) -> None:
        for window in (None, COMPACT_BELOW_TOKENS, 16384, 131072):
            client = ReplayClient([good_spec], context_window=window)
            LLMSpecGenerator(client).generate(STORY, style="flat_vector", seed=3)
            assert client.requests[0].system == build_system_prompt(
                "flat_vector", 50, verbatim=False
            ), window

    def test_explicit_option_beats_the_window(self, good_spec: dict[str, Any]) -> None:
        small = build_system_prompt("flat_vector", 50, compact=True, verbatim=False)
        full = build_system_prompt("flat_vector", 50, verbatim=False)
        c1 = ReplayClient([good_spec], context_window=2048)
        LLMSpecGenerator(c1, compact=False).generate(STORY, style="flat_vector", seed=3)
        assert c1.requests[0].system == full
        c2 = ReplayClient([good_spec], context_window=None)
        LLMSpecGenerator(c2, compact=True).generate(STORY, style="flat_vector", seed=3)
        assert c2.requests[0].system == small

    def test_a_repair_round_in_compact_mode_does_not_echo_the_reply(
        self, good_spec: dict[str, Any]
    ) -> None:
        client = ReplayClient([break_action(good_spec), good_spec], context_window=8192)
        res = LLMSpecGenerator(client).generate(STORY, style="flat_vector", seed=3)
        assert res.attempts == 2
        second = client.requests[1]
        assert [m.role for m in second.messages] == [
            "user"
        ]  # no room for the cut-off... or any echo
        assert "moonwalk" in second.messages[0].content and "SCRIPT" in second.messages[0].content
        assert "avoid" in second.messages[0].content

    def test_cut_off_reply_restarts_compact_and_shorter(self, good_spec: dict[str, Any]) -> None:
        client = ReplayClient([Truncated(cut_off_reply(good_spec, 3)), good_spec])
        res = LLMSpecGenerator(client).generate(STORY, style="flat_vector", seed=3)
        assert res.attempts == 2 and res.ok
        first, second = client.requests
        assert first.system == build_system_prompt("flat_vector", 50, verbatim=False)
        assert second.system == build_system_prompt("flat_vector", 50, compact=True, verbatim=False)
        assert second.json_schema == json_schema_for_llm(compact=True)
        assert [m.role for m in second.messages] == ["user"]
        assert "cut off by the length limit after 3 complete scene(s)" in second.messages[0].content
        assert "MUCH SHORTER" in second.messages[0].content
        assert any("switching to the compact prompt" in n for n in res.notes)

    def test_each_cut_off_asks_for_an_even_shorter_spec(self, good_spec: dict[str, Any]) -> None:
        replies = [Truncated(cut_off_reply(good_spec, 0, 30))] * 3 + [good_spec]
        client = ReplayClient(replies, context_window=8192)
        res = LLMSpecGenerator(client).generate(STORY, style="flat_vector", seed=3)
        assert res.attempts == 4
        asks = [r.messages[0].content for r in client.requests]
        assert "scenes of 5-7 s" in asks[1] and "6 scenes of about 8 s" in asks[2] + asks[3]
        assert "6 scenes of about 8 s" not in asks[1] and "Keep it SHORT" in asks[0]
        assert shorter_reply_request(0, 1) != shorter_reply_request(0, 2)

    def test_reply_that_only_lacks_its_closing_braces_is_accepted(
        self, good_spec: dict[str, Any]
    ) -> None:
        text = compact_json({k: v for k, v in good_spec.items() if k != "audio"})[
            :-1
        ]  # drop the last }
        res = LLMSpecGenerator(ReplayClient([text])).generate(STORY, style="flat_vector", seed=3)
        assert res.attempts == 1 and res.ok

    def test_salvaged_scenes_are_the_result_when_nothing_better_comes(
        self, good_spec: dict[str, Any]
    ) -> None:
        client = ReplayClient([Truncated(cut_off_reply(good_spec, 5))] * 4)
        res = LLMSpecGenerator(client, max_repairs=3).generate(STORY, style="flat_vector", seed=3)
        assert res.ok and len(res.spec["scenes"]) == 5 and res.attempts == 4
        assert total_of(res.spec) == pytest.approx(50.0, abs=0.06)  # the 5 scenes were stretched
        assert any("cut off" in n and "5 complete" in n for n in res.notes)

    def test_too_little_salvaged_is_a_precise_error(self, good_spec: dict[str, Any]) -> None:
        assert MIN_PARTIAL_SCENES == 3
        client = ReplayClient(
            [Truncated(cut_off_reply(good_spec, 2))] * 2, name="tiny-model", context_window=4096
        )
        with pytest.raises(SpecGenerationError) as exc:
            LLMSpecGenerator(client, max_repairs=1).generate(STORY, style="flat_vector", seed=3)
        err = exc.value
        assert (
            "cut off" in err.message and "tiny-model" in err.message and "4096-token" in err.message
        )
        assert err.raw is not None and err.spec is not None and len(err.spec["scenes"]) == 2

    def test_nothing_salvageable_still_reports_the_raw_reply(self) -> None:
        client = ReplayClient(
            [Truncated('{"meta": {"title": "T"}, "scenes": [{"id": "s01", "lay')] * 2
        )
        with pytest.raises(SpecGenerationError) as exc:
            LLMSpecGenerator(client, max_repairs=1).generate(STORY, style="flat_vector")
        assert exc.value.spec is None and exc.value.raw is not None
        assert "cut off" in exc.value.message

    def test_reply_budget_never_exceeds_the_requested_max(self, good_spec: dict[str, Any]) -> None:
        client = ReplayClient([good_spec], context_window=131072)
        LLMSpecGenerator(client, max_tokens=5000).generate(STORY, style="flat_vector", seed=3)
        assert client.requests[0].max_tokens == 5000

    def test_generate_spec_passes_compact_through(self, good_spec: dict[str, Any]) -> None:
        client = ReplayClient([good_spec])
        generate_spec(STORY, "flat_vector", client, seed=3, compact=True)
        assert client.requests[0].system == build_system_prompt(
            "flat_vector", 50, compact=True, verbatim=False
        )


# =============================================================================== manual path
class TestManualSpecGenerator:
    def test_good_file_with_overrides(self, tmp_path: Path, good_spec: dict[str, Any]) -> None:
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(good_spec), encoding="utf-8")
        res = ManualSpecGenerator(path).generate("", style="stickman", seed=42)
        assert res.ok and res.spec["meta"]["style"] == "stickman" and res.spec["meta"]["seed"] == 42
        assert any("stickman" in n for n in res.notes) and res.generator == "manual"
        assert res.lint.source == str(path)

    def test_constructor_overrides_and_no_override(self, good_spec: dict[str, Any]) -> None:
        res = ManualSpecGenerator(good_spec, style="paper_cutout", seed=9).generate()
        assert res.spec["meta"]["style"] == "paper_cutout" and res.spec["meta"]["seed"] == 9
        plain = ManualSpecGenerator(good_spec).generate()
        assert plain.spec["meta"]["style"] == good_spec["meta"]["style"] and plain.notes == []
        assert good_spec["meta"]["seed"] == 3  # the dict we passed is never modified

    def test_bad_file_raises_with_a_readable_report(
        self, tmp_path: Path, good_spec: dict[str, Any]
    ) -> None:
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(break_action(good_spec)), encoding="utf-8")
        with pytest.raises(SpecGenerationError) as exc:
            ManualSpecGenerator(path).generate()
        assert "has 1 error" in exc.value.message and str(path) in exc.value.message
        text = str(exc.value)
        assert "REGISTRY_MISSING" in text and "moonwalk" in text and "FAILED" in text
        assert exc.value.lint is not None and not exc.value.lint.ok

    def test_json_syntax_errors_point_at_the_spot(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text('{\n  "meta": {"title": "x",}\n}', encoding="utf-8")
        with pytest.raises(SpecGenerationError) as exc:
            ManualSpecGenerator(path).generate()
        assert "not valid JSON" in exc.value.message and "JSON_SYNTAX" in str(exc.value)
        assert "line 2" in str(exc.value)

    def test_missing_file_and_wrong_shape(self, tmp_path: Path) -> None:
        with pytest.raises(SpecGenerationError, match="cannot read"):
            ManualSpecGenerator(tmp_path / "nope.json").generate()
        arr = tmp_path / "arr.json"
        arr.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(SpecGenerationError, match="JSON object"):
            ManualSpecGenerator(arr).generate()

    def test_relative_audio_files_resolve_next_to_the_spec(
        self, tmp_path: Path, good_spec: dict[str, Any]
    ) -> None:
        spec = copy.deepcopy(good_spec)
        spec["audio"] = {"music": "song.mp3"}
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        with pytest.raises(SpecGenerationError, match=r"FILE_MISSING|error"):
            ManualSpecGenerator(path).generate()
        (tmp_path / "song.mp3").write_bytes(b"x")
        assert ManualSpecGenerator(path).generate().ok


# =============================================================================== the offline planner
def run(script: str, style: str = "flat_vector", **kw: Any) -> GenerationResult:
    return HeuristicSpecGenerator().generate(script, style=style, **kw)


class TestHeuristic:
    @pytest.mark.parametrize("style", STYLES)
    @pytest.mark.parametrize(
        "script",
        [STORY, EXPLAINER, TINY, LONG, DIALOGUE],
        ids=["story", "explainer", "tiny", "long", "dialogue"],
    )
    def test_always_lint_clean_and_in_budget(self, script: str, style: str) -> None:
        res = run(script, style, seed=5)
        assert res.ok and res.attempts == 1 and res.generator == "heuristic"
        assert not res.lint.warnings, res.lint.format_text()
        spec = res.spec
        assert 45.0 <= total_of(spec) <= 60.0
        assert total_of(spec) == pytest.approx(50.0, abs=0.06)
        assert 6 <= len(spec["scenes"]) <= 16
        assert 1 <= len(spec["characters"]) <= 3
        assert spec["meta"]["style"] == style and spec["meta"]["seed"] == 5
        for sc in spec["scenes"]:
            assert 1 <= len(sc["layers"]) <= 3
        fresh = lint_data(json.loads(json.dumps(spec)))
        assert fresh.ok

    def test_deterministic_for_script_style_and_seed(self) -> None:
        a = json.dumps(run(STORY, "paper_cutout", seed=2).spec, sort_keys=True)
        b = json.dumps(run(STORY, "paper_cutout", seed=2).spec, sort_keys=True)
        c = json.dumps(run(STORY, "paper_cutout", seed=3).spec, sort_keys=True)
        d = json.dumps(run(STORY, "stickman", seed=2).spec, sort_keys=True)
        assert a == b and a != c and a != d

    def test_target_duration_is_honoured(self) -> None:
        for target in (45, 52.5, 60):
            assert total_of(run(STORY, target_duration=target).spec) == pytest.approx(
                target, abs=0.06
            )
        assert any("clamped" in n for n in run(STORY, target_duration=999).notes)

    def test_story_has_title_dialogue_and_a_shout(self) -> None:
        spec = run(STORY, seed=1).spec
        caps = [(i, c) for i, s in enumerate(spec["scenes"]) for c in s["captions"]]
        assert spec["meta"]["title"] == "The Midnight Cookie Mystery"
        titles = [(i, c) for i, c in caps if c["style"] == "title"]
        assert (
            len(titles) == 1
            and titles[0][0] == 0
            and titles[0][1]["text"] == "The Midnight Cookie Mystery"
        )
        shouts = [c for _, c in caps if c["style"] == "shout"]
        assert [c["text"] for c in shouts] == ["Again!"] and shouts[0]["speaker"] == "grandpa"
        speakers = {c.get("speaker") for _, c in caps}
        assert {"mia", "grandpa", "pixel"} <= speakers
        assert {c["id"]: c["archetype"] for c in spec["characters"]} == {
            "mia": "kid",
            "grandpa": "elder",
            "pixel": "robot",
        }

    def test_explainer_is_one_narrator_who_speaks_every_line(self) -> None:
        spec = run(EXPLAINER, seed=1).spec
        assert [c["id"] for c in spec["characters"]] == ["narrator"]
        spoken = [c for s in spec["scenes"] for c in s["captions"] if c["style"] == "subtitle"]
        assert spoken and all(c["speaker"] == "narrator" for c in spoken)
        assert spec["meta"]["title"] == "Why Do We Sleep?"
        assert [
            c["text"] for s in spec["scenes"] for c in s["captions"] if c["style"] == "shout"
        ] == ["Sweet dreams!"]

    def test_dialogue_format_scripts(self) -> None:
        spec = run(DIALOGUE, seed=1).spec
        assert {c["id"] for c in spec["characters"]} == {"ana", "ben"}
        by: dict[Any, list[str]] = {}
        for s in spec["scenes"]:
            for c in s["captions"]:
                by.setdefault(c.get("speaker"), []).append(c["text"])
        assert any("news" in t for t in by["ana"]) and any("pancakes" in t for t in by["ben"])
        texts = " ".join(t for ts in by.values() for t in ts)
        assert "excited" not in texts and "laughs" not in texts  # stage directions are not captions

    def test_short_scripts_are_padded_long_ones_condensed(self) -> None:
        tiny = run(TINY).spec
        assert len(tiny["scenes"]) >= 8 and sum(1 for s in tiny["scenes"] if s["captions"]) < len(
            tiny["scenes"]
        )
        assert tiny["scenes"][0]["captions"][0]["text"] == "A Cat Sat on the Mat"
        long_spec = run(LONG).spec
        assert len(long_spec["scenes"]) <= 14
        words = [len(c["text"].split()) for s in long_spec["scenes"] for c in s["captions"]]
        assert max(words) <= 16
        assert len(LONG.split()) > 400

    def test_names_must_exist_in_the_catalog(self) -> None:
        spec = run(STORY, "stickman", seed=4).spec
        for sc in spec["scenes"]:
            assert sc["background"]["template"] in CATALOG.backgrounds
            for layer in sc["layers"]:
                assert all(a["name"] in CATALOG.actions for a in layer["actions"])
            assert all(x["name"] in CATALOG.sfx for x in sc.get("sfx", []))
            assert sc.get("transition_out", {}).get("type", "cut") in CATALOG.transitions
            assert all(
                m["type"] in CATALOG.camera_moves for m in sc.get("camera", {}).get("moves", [])
            )

    def test_scenes_follow_the_story_setting(self) -> None:
        spec = run(STORY, seed=1).spec
        kitchen = spec["scenes"][0]["background"]
        assert kitchen["template"] == "room" and kitchen["params"]["room"] == "kitchen"
        assert kitchen["params"]["time_of_day"] == "night"
        assert "street" in {s["background"]["template"] for s in spec["scenes"]}
        assert len({s["background"]["template"] for s in spec["scenes"]}) >= 2

    def test_entrances_use_enter_from_for_new_characters(self) -> None:
        spec = run(STORY, seed=1).spec
        seen: set[str] = set()
        for sc in spec["scenes"]:
            here = {layer["character"] for layer in sc["layers"]}
            for layer in sc["layers"]:
                names = [a["name"] for a in layer["actions"]]
                if layer["character"] not in seen:
                    assert "enter_from" in names, (sc["id"], layer["character"])
            seen |= here

    def test_props_only_for_what_a_character_has_or_wears(self) -> None:
        umbrella = run(
            (EXAMPLES / "scripts" / "lost_umbrella.txt").read_text(encoding="utf-8")
        ).spec
        props = {c["id"]: c.get("props", []) for c in umbrella["characters"]}
        assert props == {
            "mia": ["backpack"],
            "pip": [],
            "bolt": ["umbrella"],
        }  # not "where is my umbrella?"
        assert all(
            "props" not in c for c in run(EXPLAINER).spec["characters"]
        )  # "put the phone down"

    def test_works_with_a_catalog_that_lacks_most_things(self, mini_catalog: Catalog) -> None:
        res = HeuristicSpecGenerator(catalog=mini_catalog).generate(
            STORY, style="flat_vector", seed=1
        )
        assert res.lint.ok
        used = {a["name"] for s in res.spec["scenes"] for ly in s["layers"] for a in ly["actions"]}
        assert used <= {"idle", "walk", "wave", "talk"}
        assert {s["background"]["template"] for s in res.spec["scenes"]} <= {"room", "street"}
        assert {c["archetype"] for c in res.spec["characters"]} <= {"everyman", "kid"}
        assert lint_data(res.spec, catalog=mini_catalog).ok

    def test_audio_block_is_configurable(self) -> None:
        assert run(TINY).spec["audio"]["voiceover"] == "tts"
        quiet = HeuristicSpecGenerator(audio={}).generate(TINY, style="flat_vector")
        assert quiet.spec["audio"] == {}

    def test_other_scripts_and_inputs(self) -> None:
        hindi = run("आज मौसम बहुत अच्छा है। राहुल पार्क में खेलने गया। वह बहुत खुश था।")
        assert hindi.ok and 45 <= total_of(hindi.spec) <= 60
        caps_only = run("THE END IS NEAR! RUN! HIDE! WE MUST ESCAPE BEFORE THE ROBOTS ARRIVE!")
        assert [c["id"] for c in caps_only.spec["characters"]] == ["narrator"]
        no_punct = run(
            "once upon a time there was a little fox who loved to dance in the moonlight every night"
        )
        assert no_punct.ok

    def test_bad_input(self) -> None:
        for text in ("", "   \n  ", "...!!!???"):
            with pytest.raises(SpecGenerationError):
                run(text)
        with pytest.raises(SpecGenerationError, match="unknown style"):
            run(STORY, "oil_painting")

    def test_script_variants_are_all_valid(self) -> None:
        for script in (
            "# My Reel\n\nTom: Hi!\nNARRATOR: Tom waves.\nTom: Bye!",
            'Title: Rainy Day\nIt rained. Sam jumped in a puddle and laughed! "Splash!" said Sam.',
            "1. First, wake up.\n2. Then, stretch.\n3. Finally, drink water!",
            'Dr. Smith, a wise old doctor, walks in. "Welcome," says Dr. Smith. Anna nods.',
        ):
            res = run(script)
            assert res.ok, (script, res.lint.format_text())


class TestCasting:
    """Archetypes come from how the script describes people (regressions on the shipped samples)."""

    def archetypes(self, path: Path, style: str = "paper_cutout") -> dict[str, str]:
        res = run(path.read_text(encoding="utf-8"), style, seed=7)
        return {c["id"]: c["archetype"] for c in res.spec["characters"]}

    def test_lost_umbrella(self) -> None:
        got = self.archetypes(EXAMPLES / "scripts" / "lost_umbrella.txt")
        assert got == {"mia": "kid", "pip": "elder", "bolt": "robot"}

    def test_why_we_sleep(self) -> None:
        got = self.archetypes(EXAMPLES / "scripts" / "why_we_sleep.txt")
        assert got["nova"] in ("boss", "everyman")
        assert got["kai"] == "kid"

    @pytest.mark.parametrize("style", STYLES)
    def test_the_samples_build_in_every_style(self, style: str) -> None:
        for name in ("lost_umbrella", "why_we_sleep"):
            res = run(
                (EXAMPLES / "scripts" / f"{name}.txt").read_text(encoding="utf-8"), style, seed=7
            )
            assert res.ok and total_of(res.spec) == pytest.approx(50.0, abs=0.06)

    def sents(self, text: str) -> list[Any]:
        return parse_script(text).sents

    def test_long_appositives_are_read(self) -> None:
        text = "Mia, a curious kid with a yellow backpack, skipped down the street."
        words = _describe("Mia", self.sents(text))
        assert "kid" in words and "backpack" in words
        assert (
            build_cast(parse_script(text + " Mia waved."), {"kid", "everyman"}, 0)
            .chars[0]
            .archetype
            == "kid"
        )

    def test_honorifics_do_not_hide_the_adjective(self) -> None:
        words = _describe("Pip", self.sents("Old Mr. Pip came shuffling out of his shop."))
        assert "old" in words and "mr" not in words  # the honorific is skipped, not described
        words = _describe("Pip", self.sents("It rained. Pip came out."))
        assert "rained" not in words  # a word from the previous sentence is not a description

    def test_what_a_character_says_about_themselves_counts(self) -> None:
        text = "Kai: So naps help me study?\nNova: Yes! Growing kids need extra sleep."
        sents = self.sents(text)
        assert "student" in _describe("Kai", sents)
        assert "student" not in _describe("Nova", sents)  # 'kids' in Nova's line is about others

    def test_names_are_not_verbs(self) -> None:
        text = "Mia met Bolt, a little robot. Bolt waved. Mia laughed."
        spec = run(text).spec
        acts = {a["name"] for s in spec["scenes"] for ly in s["layers"] for a in ly["actions"]}
        assert "run" not in acts  # "Bolt" is a name, not the verb "bolt"


# =============================================================================== enrichment
def weaken(spec: dict[str, Any], actions: str = "idle", cuts: bool = True) -> dict[str, Any]:
    """The kind of spec a small model writes: it lints clean and renders static - nothing but idle (or
    an entrance and a glance), no camera, no sfx, every scene change a cut (unless ``cuts`` is
    False), captions that vanish in a second."""
    out = copy.deepcopy(spec)
    for sc in out["scenes"]:
        dur = sc["duration_sec"]
        for layer in sc["layers"]:
            if actions == "idle":
                layer["actions"] = [
                    {"name": "idle", "t0": 0.2, "t1": round(min(dur - 0.2, 2.2), 2)}
                ]
            else:
                layer["actions"] = [
                    {"name": "enter_from", "t0": 0.0, "t1": 1.5},
                    {"name": "look_at", "t0": 1.6, "t1": 2.6, "params": {"target": "camera"}},
                ]
        sc.pop("camera", None)
        sc.pop("sfx", None)
        if cuts:
            sc["transition_out"] = {"type": "cut", "duration": 0}
        for cap in sc["captions"]:
            cap["t1"] = round(min(cap["t0"] + 1.0, dur - 0.1), 2)
            if cap["t1"] <= cap["t0"]:
                cap["t0"], cap["t1"] = 0.1, 1.0
    meta = out["meta"]  # the scenes (less the overlaps) must add up to the target
    return normalize_spec(
        out,
        style=meta["style"],
        seed=meta["seed"],
        target_duration=meta["target_duration_sec"],
        audio=out.get("audio"),
    )


def rich_spec() -> dict[str, Any]:
    """What a good model writes: every layer acts, every scene has a camera move and a sound, the
    captions stay up long enough, the scene changes differ.  Nothing for enrichment to add."""
    chars = [
        {"id": "ana", "archetype": "everyman", "palette": {"shirt": "#e63946"}},
        {"id": "ben", "archetype": "kid", "palette": {"shirt": "#2a9d8f"}},
    ]
    kinds = ("crossfade", "wipe", "page_flip", "cut")
    scenes = []
    for i in range(10):
        scenes.append(
            {
                "id": f"s{i + 1:02d}",
                "duration_sec": 5.4,
                "background": {"template": "abstract"},
                "camera": {
                    "moves": [{"type": "zoom", "from": 1.0, "to": 1.06, "t0": 0.0, "t1": 5.0}]
                },
                "layers": [
                    {
                        "character": "ana",
                        "position": "left",
                        "actions": [{"name": "wave", "t0": 0.6, "t1": 2.4}],
                    },
                    {
                        "character": "ben",
                        "position": "right",
                        "actions": [{"name": "think", "t0": 1.0, "t1": 3.5}],
                    },
                ],
                "captions": [
                    {"text": "Short line here", "t0": 0.5, "t1": 3.0, "speaker": "ana"},
                    {"text": "And another one", "t0": 3.2, "t1": 5.0, "speaker": "ben"},
                ],
                "sfx": [{"name": "pop", "t": 0.4}],
                "transition_out": {"type": kinds[i % 4], "duration": 0.0 if i % 4 == 3 else 0.5},
            }
        )
    return {
        "version": "1.0",
        "meta": {"title": "Rich", "style": "flat_vector", "seed": 1, "target_duration_sec": 50.0},
        "characters": chars,
        "scenes": scenes,
    }


def acted(layer: dict[str, Any]) -> list[str]:
    """The actions of a layer that do something beyond standing there."""
    return [a["name"] for a in layer["actions"] if a["name"] not in RESTING]


def lone_scene(sid: str, dur: float, captions: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    """A one-character scene (``mia``, centre) for the tests of single captions."""
    base = scene(sid, dur, captions=captions)
    base["layers"] = [{"character": "mia", "position": "center", "actions": []}]
    base.update(kw)
    return base


def mia_spec(captions_per_scene: list[list[dict[str, Any]]], dur: float = 5.0) -> dict[str, Any]:
    """Scenes of ``mia`` (and ``pip``) with the given captions, the scene changes already varied (so
    that nothing is rescaled).  Every scene gets its own copy of its captions."""
    scenes = [
        lone_scene(f"s{i:02d}", dur, copy.deepcopy(caps))
        for i, caps in enumerate(captions_per_scene)
    ]
    kinds = ("crossfade", "wipe", "page_flip", "cut")
    for i, sc in enumerate(scenes[:-1]):
        sc["transition_out"] = {"type": kinds[i % 4], "duration": 0.0 if i % 4 == 3 else 0.5}
    spec = skeleton(scenes)
    spec["characters"] = [
        {"id": "mia", "archetype": "kid", "palette": {"shirt": "#2a9d8f"}},
        {"id": "pip", "archetype": "elder", "palette": {"shirt": "#e63946"}},
    ]
    return spec


def sub(text: str, t0: float = 0.5, t1: float = 4.0, **kw: Any) -> dict[str, Any]:
    return {"text": text, "t0": t0, "t1": t1, "style": "subtitle", **kw}


class TestEnrich:
    def weak(self, good_spec: dict[str, Any], actions: str = "idle") -> dict[str, Any]:
        weak = weaken(good_spec, actions)
        assert lint_data(weak).ok
        return weak

    def varied(self, good_spec: dict[str, Any], actions: str = "idle") -> dict[str, Any]:
        """A weak spec whose scene changes the model did vary (the planner's own), so that
        enrichment has no reason to rotate them and rescale anything."""
        weak = weaken(good_spec, actions, cuts=False)
        assert lint_data(weak).ok
        assert len({s["transition_out"]["type"] for s in weak["scenes"][:-1]}) >= 2
        return weak

    def test_a_static_spec_gains_actions_camera_and_sfx(self, good_spec: dict[str, Any]) -> None:
        weak = self.weak(good_spec)
        notes: list[str] = []
        out = enrich_spec(weak, seed=3, notes=notes)
        for sc in out["scenes"]:
            assert any(acted(layer) for layer in sc["layers"]), sc["id"]
            assert sc["camera"]["moves"] and 1 <= len(sc["sfx"]) <= 2, sc["id"]
            assert all(
                x["name"] in CATALOG.sfx and 0 <= x["t"] < sc["duration_sec"] for x in sc["sfx"]
            )
        assert len({m["type"] for sc in out["scenes"] for m in sc["camera"]["moves"]}) >= 3
        (line,) = notes
        assert re.fullmatch(
            r"enriched \d+ scenes \(actions \d+, camera \d+, sfx \d+(, captions \d+)?(, transitions \d+)?\)",
            line,
        ), line
        assert str(len(out["scenes"])) in line

    def test_the_result_lints_clean_and_stays_in_budget(self, good_spec: dict[str, Any]) -> None:
        for actions in ("idle", "enter_look"):
            weak = self.weak(good_spec, actions)
            before = lint_data(weak)
            out = enrich_spec(weak, seed=3)
            after = lint_data(out)
            assert after.ok and len(after.warnings) <= len(before.warnings), after.format_text()
            assert 45.0 <= total_of(out) <= 60.0
            assert total_of(out) == pytest.approx(out["meta"]["target_duration_sec"], abs=0.1)

    def test_a_rich_spec_is_untouched(self) -> None:
        rich = rich_spec()
        assert lint_data(rich).ok and not lint_data(rich).warnings
        notes: list[str] = []
        assert enrich_spec(rich, notes=notes) == rich
        assert notes == []

    @pytest.mark.parametrize("actions", ["idle", "enter_look"])
    def test_a_second_pass_changes_nothing(self, good_spec: dict[str, Any], actions: str) -> None:
        once = enrich_spec(self.weak(good_spec, actions), seed=3)
        notes: list[str] = []
        assert enrich_spec(once, seed=3, notes=notes) == once
        assert notes == []

    def test_deterministic_seeded_and_the_input_is_left_alone(
        self, good_spec: dict[str, Any]
    ) -> None:
        weak = self.weak(good_spec)
        frozen = copy.deepcopy(weak)
        a = enrich_spec(weak, seed=3)
        assert weak == frozen
        assert enrich_spec(weak, seed=3) == a == enrich_spec(weak)  # meta.seed is 3 as well
        assert enrich_spec(weak, seed=4) != a

    def test_what_the_model_wrote_is_never_replaced(self, good_spec: dict[str, Any]) -> None:
        weak = self.varied(good_spec, "enter_look")
        out = enrich_spec(weak, seed=3)
        for before, after in zip(weak["scenes"], out["scenes"]):
            assert after["transition_out"] == before["transition_out"]
            assert after["duration_sec"] == before["duration_sec"]
            for lb, la in zip(before["layers"], after["layers"]):
                assert all(a in la["actions"] for a in lb["actions"])  # every action is still there
                assert (lb["character"], lb["position"]) == (la["character"], la["position"])
            for cb, ca in zip(before["captions"], after["captions"]):
                assert (cb["text"], cb.get("speaker"), cb["style"]) == (
                    ca["text"],
                    ca.get("speaker"),
                    ca["style"],
                )
                assert ca["t1"] >= cb["t1"] - 1e-9  # captions only ever grow

    def test_only_what_is_empty_is_filled(self, good_spec: dict[str, Any]) -> None:
        weak = self.varied(good_spec)
        sc0 = weak["scenes"][0]
        sc0["camera"] = {
            "moves": [{"type": "pan", "from": [0, 0], "to": [0.1, 0], "t0": 0, "t1": 2}]
        }
        sc0["sfx"] = [{"name": "pop", "t": 0.5, "volume": 0.3}]
        sc0["layers"][0]["actions"].append({"name": "wave", "t0": 2.4, "t1": 3.4})
        out = enrich_spec(weak, seed=3)
        o0 = out["scenes"][0]
        assert o0["camera"] == sc0["camera"] and o0["sfx"] == sc0["sfx"]
        assert o0["layers"][0]["actions"] == sc0["layers"][0]["actions"]  # it already acts
        assert all(acted(layer) for layer in out["scenes"][1]["layers"] if layer["actions"])

    def test_captions_are_acted_out(self) -> None:
        caps = [
            sub("Where is my umbrella?"),  # a question: think
            sub("She ran to the door."),  # a verb
            sub("Mia waved goodbye."),
            sub("Look at that!", speaker="mia"),  # "look!": point
            sub("Pip looked at the sky."),
            {"text": "Again!", "t0": 0.5, "t1": 2.5, "style": "shout", "speaker": "mia"},
        ]
        spec = mia_spec([[c] for c in caps] + [[sub("x y")]] * 6, dur=5.0)
        out = enrich_spec(spec, seed=1)
        layers = [sc["layers"][0] for sc in out["scenes"]]
        names = [acted(layer) for layer in layers[:6]]
        assert names[0] == ["think"] and names[1] == ["run"] and names[2] == ["wave"]
        assert names[3] == ["point"] and layers[3]["actions"][-1]["params"]["direction"] == "up"
        look = next(a for a in layers[4]["actions"] if a["name"] == "look_at")
        assert look["params"] == {"target": "up"}
        assert names[5] and names[5][0] in ("jump", "surprise", "wave")  # the punchline's flourish

    def test_gestures_come_after_the_entrance_and_inside_the_scene(
        self, good_spec: dict[str, Any]
    ) -> None:
        out = enrich_spec(self.weak(good_spec), seed=3)
        report = lint_data(out)
        assert not report.warnings, report.format_text()
        for sc in out["scenes"]:
            for layer in sc["layers"]:
                enter = [a for a in layer["actions"] if a["name"] == "enter_from"]
                for a in layer["actions"]:
                    assert 0 <= a["t0"] < a["t1"] <= sc["duration_sec"] - 0.1
                    if enter and a["name"] not in ("enter_from", "idle"):
                        assert a["t0"] >= enter[0]["t1"] - 1e-9, (sc["id"], a)

    def test_newcomers_enter_and_continuing_characters_do_not(self) -> None:
        spec = mia_spec([[sub("Hello, Pip!")]] * 10)
        for i in (1, 2, 3):  # pip joins mia in scenes 2-4
            spec["scenes"][i]["layers"].append(
                {"character": "pip", "position": "right", "actions": []}
            )
        spec["scenes"][1]["layers"][0]["position"] = "left"
        out = enrich_spec(spec, seed=2)

        def enters(i: int) -> dict[str, bool]:
            return {
                ly["character"]: any(a["name"] == "enter_from" for a in ly["actions"])
                for ly in out["scenes"][i]["layers"]
            }

        assert enters(0) == {"mia": True}  # the first scene: everyone arrives
        assert enters(1) == {"mia": False, "pip": True}  # pip is new, mia was already there
        assert enters(2) == {"mia": False, "pip": False}
        assert enters(4) == {"mia": False}

    def test_listeners_look_at_the_speaker(self) -> None:
        spec = mia_spec([[sub("Hello there!", speaker="mia")]] * 10)
        for sc in spec["scenes"]:
            sc["layers"].append({"character": "pip", "position": "right", "actions": []})
        out = enrich_spec(spec, seed=2)
        sc = out["scenes"][3]
        mia, pip = sc["layers"]
        looks = [a for a in pip["actions"] if a["name"] == "look_at"]
        assert looks and all(a["params"] == {"target": "mia"} for a in looks)
        assert not [
            a for a in mia["actions"] if a["name"] == "look_at"
        ]  # nobody watches themselves
        assert not [a for a in mia["actions"] if a["name"] == "talk"]  # talking is automatic

    def test_everyone_acts_when_the_narration_says_they_all_do(self) -> None:
        spec = mia_spec([[sub("They all laughed together!")]] * 10)
        for sc in spec["scenes"]:
            sc["layers"].append({"character": "pip", "position": "right", "actions": []})
        sc = enrich_spec(spec, seed=2)["scenes"][4]
        assert [acted(layer) for layer in sc["layers"]] == [["laugh"], ["laugh"]]

    def test_silent_scenes_get_business_that_is_staggered(self) -> None:
        spec = mia_spec([[]] * 10, dur=6.0)
        for sc in spec["scenes"]:
            sc["layers"].append({"character": "pip", "position": "right", "actions": []})
        sc = enrich_spec(spec, seed=2)["scenes"][5]
        mia, pip = sc["layers"]
        assert acted(mia) and acted(pip)

        def first(layer: dict[str, Any]) -> float:
            return min(a["t0"] for a in layer["actions"] if a["name"] not in RESTING)

        assert first(pip) > first(mia)  # not a chorus line

    def test_layers_that_already_act_are_not_touched(self) -> None:
        spec = mia_spec([[sub("Where is my umbrella?")]] * 10)
        mine = {"name": "wave", "t0": 3.0, "t1": 4.0}
        spec["scenes"][2]["layers"][0]["actions"] = [mine]
        out = enrich_spec(spec, seed=2)
        assert out["scenes"][2]["layers"][0]["actions"] == [mine]
        assert acted(out["scenes"][3]["layers"][0])

    def test_gestures_go_around_what_the_layer_already_does(self) -> None:
        spec = mia_spec([[sub("Where is my umbrella?")]] * 10)
        mine = [
            {"name": "enter_from", "t0": 0.0, "t1": 1.8},
            {"name": "look_at", "t0": 1.8, "t1": 3.0},
        ]
        spec["scenes"][0]["layers"][0]["actions"] = copy.deepcopy(mine)
        out = enrich_spec(spec, seed=2)
        acts = out["scenes"][0]["layers"][0]["actions"]
        assert all(a in acts for a in mine)  # the model's own are all still there, once
        assert sum(1 for a in acts if a["name"] == "enter_from") == 1
        assert sum(1 for a in acts if a["name"] == "look_at") == 1  # it already looks somewhere
        think = next(a for a in acts if a["name"] == "think")
        assert (
            think["t0"] >= 1.8
        )  # after the entrance, over the look (a head turn may be overlapped)

    # -- captions -----------------------------------------------------------------------------------
    def test_short_caption_windows_are_widened_to_the_speech(self) -> None:
        text = "one two three four five six"  # 6 words: 6 / 2.5 + 0.6 = 3.0 s
        spec = mia_spec([[sub(text, 0.5, 1.5)]] * 10, dur=6.0)
        out = enrich_spec(spec, seed=1)
        cap = out["scenes"][0]["captions"][0]
        assert (cap["t0"], cap["t1"]) == (0.5, 3.5)
        spec = mia_spec([[sub(text, 0.5, 1.5)]] * 10, dur=3.0)  # no room: up to the end - 0.3 s
        cap = enrich_spec(spec, seed=1)["scenes"][0]["captions"][0]
        assert (cap["t0"], cap["t1"]) == (0.5, 2.7)

    def test_captions_that_are_long_enough_keep_their_own_numbers(self) -> None:
        spec = mia_spec([[sub("two words", 0.4137, 2.9321)]] * 10)
        out = enrich_spec(spec, seed=1)
        assert out["scenes"][0]["captions"] == spec["scenes"][0]["captions"]

    def test_widening_pushes_the_next_caption_of_the_same_style_later(self) -> None:
        a, b = "one two three four five six", "seven eight nine ten eleven twelve"
        roomy = mia_spec([[sub(a, 0.5, 1.5), sub(b, 1.6, 2.6)]] * 10, dur=12.0)
        caps = enrich_spec(roomy, seed=1)["scenes"][0]["captions"]
        assert (caps[0]["t0"], caps[0]["t1"]) == (0.5, 3.5)
        assert (caps[1]["t0"], caps[1]["t1"]) == (3.55, 6.55)  # moved later, not overlapped
        tight = mia_spec([[sub(a, 0.5, 1.5), sub(b, 1.6, 2.6)]] * 10, dur=6.0)
        caps = enrich_spec(tight, seed=1)["scenes"][0]["captions"]
        assert (
            caps[0]["t1"] <= caps[1]["t0"] - 0.05 + 1e-9
        )  # no room to move: each grows into its gap
        assert caps[1]["t0"] == 1.6 and caps[1]["t1"] == 4.6
        assert caps[0]["t1"] >= 1.5 and caps[1]["t1"] <= 6.0 - 0.3 + 1e-9

    def test_widening_never_creates_same_style_overlaps(self, good_spec: dict[str, Any]) -> None:
        out = enrich_spec(weaken(good_spec), seed=3)
        for sc in out["scenes"]:
            caps = sorted(sc["captions"], key=lambda c: c["t0"])
            for x, y in itertools.pairwise(caps):
                assert x["style"] != y["style"] or y["t0"] >= x["t1"] - 1e-6, sc["id"]
            assert all(c["t1"] <= sc["duration_sec"] + 1e-9 for c in caps)

    def test_captions_of_another_style_may_overlap(self) -> None:
        title = {"text": "A Rather Long Title Indeed", "t0": 0.2, "t1": 0.8, "style": "title"}
        spec = mia_spec([[title, sub("hello there my friend", 0.5, 1.0)]] * 10, dur=6.0)
        caps = enrich_spec(spec, seed=1)["scenes"][0]["captions"]
        assert caps[0]["t1"] == pytest.approx(0.2 + 5 / 2.5 + 0.6)
        assert caps[1]["t1"] == pytest.approx(0.5 + 4 / 2.5 + 0.6)

    # -- scene changes ------------------------------------------------------------------------------
    def test_all_cuts_are_rotated_and_the_length_refitted(self, good_spec: dict[str, Any]) -> None:
        weak = self.weak(good_spec)
        assert {s["transition_out"]["type"] for s in weak["scenes"]} == {"cut"}
        notes: list[str] = []
        out = enrich_spec(weak, seed=3, notes=notes)
        kinds = [s["transition_out"]["type"] for s in out["scenes"][:-1]]
        assert len(set(kinds)) >= 3 and set(kinds) <= {"cut", "crossfade", "wipe", "page_flip"}
        assert "transitions" in notes[0]
        assert total_of(out) == pytest.approx(50.0, abs=0.06)
        assert sum(s["duration_sec"] for s in out["scenes"]) > sum(
            s["duration_sec"] for s in weak["scenes"]
        )  # the overlaps had to be paid for
        assert out["scenes"][-1]["transition_out"]["type"] == "cut"

    def test_the_same_soft_transition_everywhere_is_rotated_too(self) -> None:
        spec = skeleton(
            [
                scene(f"s{i}", 5.0, transition_out={"type": "wipe", "duration": 0.5})
                for i in range(10)
            ]
        )
        out = enrich_spec(spec, seed=1)
        kinds = [s["transition_out"] for s in out["scenes"][:-1]]
        assert len({k["type"] for k in kinds}) >= 2
        assert all(k["type"] == "cut" or k["duration"] >= 0.5 for k in kinds)  # never shorter
        assert "cut" not in {k["type"] for k in kinds}  # nor a soft change turned into a cut
        assert lint_data(out).ok and total_of(out) == pytest.approx(total_of(spec), abs=0.01)

    def test_rotating_scene_changes_only_lengthens_scenes_and_keeps_the_reel_as_long(self) -> None:
        rich = rich_spec()
        for sc in rich["scenes"]:
            sc["transition_out"] = {"type": "cut", "duration": 0}
            sc["duration_sec"] = 5.0  # all cuts: ten scenes make fifty seconds
        before = total_of(rich)
        assert before == pytest.approx(50.0)
        out = enrich_spec(rich, seed=2)
        assert len({s["transition_out"]["type"] for s in out["scenes"][:-1]}) >= 3
        assert total_of(out) == pytest.approx(before, abs=1e-6)
        for old, new in zip(rich["scenes"], out["scenes"]):
            assert new["duration_sec"] >= old["duration_sec"]
            for key in ("layers", "captions", "camera", "sfx"):  # nothing inside a scene moved
                assert new[key] == old[key], key
        assert lint_data(out).ok and not lint_data(out).warnings

    def test_varied_transitions_are_left_alone(self, good_spec: dict[str, Any]) -> None:
        weak = self.varied(good_spec)
        assert len({s["transition_out"]["type"] for s in weak["scenes"][:-1]}) >= 2
        out = enrich_spec(weak, seed=3)
        assert [s["transition_out"] for s in out["scenes"]] == [
            s["transition_out"] for s in weak["scenes"]
        ]
        assert [s["duration_sec"] for s in out["scenes"]] == [
            s["duration_sec"] for s in weak["scenes"]
        ]

    def test_too_few_scenes_are_not_rotated(self) -> None:
        spec = skeleton([scene("a", 16.0), scene("b", 16.0), scene("c", 18.0)])
        out = enrich_spec(spec, seed=1)
        assert [s.get("transition_out") for s in out["scenes"]] == [None, None, None]

    # -- camera and sound ---------------------------------------------------------------------------
    def test_camera_moves_vary_from_scene_to_scene(self, good_spec: dict[str, Any]) -> None:
        out = enrich_spec(self.weak(good_spec), seed=3)
        moves = [sc["camera"]["moves"][0] for sc in out["scenes"]]
        assert all(m["t1"] <= sc["duration_sec"] for m, sc in zip(moves, out["scenes"]))
        assert all(
            a["type"] != b["type"] or a["to"] != b["to"] for a, b in itertools.pairwise(moves)
        )

    def test_a_shout_gets_a_shake_and_a_push_in(self) -> None:
        shout = {"text": "Again!", "t0": 0.5, "t1": 2.5, "style": "shout"}
        spec = mia_spec([[sub("hello there")]] * 9 + [[shout]])
        kinds = [m["type"] for m in enrich_spec(spec, seed=1)["scenes"][9]["camera"]["moves"]]
        assert kinds == ["shake", "zoom"]

    def test_sound_effects_fit_the_scene_and_the_catalog(self, good_spec: dict[str, Any]) -> None:
        out = enrich_spec(self.weak(good_spec), seed=3)
        names = {x["name"] for sc in out["scenes"] for x in sc["sfx"]}
        assert names <= set(CATALOG.sfx.names()) and len(names) >= 3
        for sc in out["scenes"]:
            times = [x["t"] for x in sc["sfx"]]
            assert times == sorted(times)
            if len(times) == 2:
                assert times[1] - times[0] >= 0.5  # not two sounds on top of each other

    def test_the_jump_gets_its_boing(self) -> None:
        spec = mia_spec([[sub("She jumped for joy!")]] * 10)
        sc = enrich_spec(spec, seed=1)["scenes"][0]
        jump = next(a for a in sc["layers"][0]["actions"] if a["name"] == "jump")
        assert any(
            x["name"] in ("boing", "pop") and jump["t0"] <= x["t"] <= jump["t1"] for x in sc["sfx"]
        )

    # -- other catalogs and odd input ---------------------------------------------------------------
    def test_a_catalog_without_the_usual_names_gets_only_what_it_has(
        self, mini_catalog: Catalog
    ) -> None:
        base = HeuristicSpecGenerator(catalog=mini_catalog).generate(
            STORY, style="flat_vector", seed=1
        )
        weak = weaken(base.spec)
        notes: list[str] = []
        out = enrich_spec(weak, mini_catalog, seed=1, notes=notes)
        assert lint_data(out, catalog=mini_catalog).ok and notes
        used = {a["name"] for s in out["scenes"] for ly in s["layers"] for a in ly["actions"]}
        assert used <= {"idle", "walk", "wave", "talk"}
        assert {x["name"] for s in out["scenes"] for x in s.get("sfx", [])} <= {"pop", "whoosh"}
        assert {
            m["type"] for s in out["scenes"] for m in s.get("camera", {}).get("moves", [])
        } <= set(mini_catalog.camera_moves.names())

    def test_without_a_sound_registry_nothing_is_added(self, mini_catalog: Catalog) -> None:
        base = HeuristicSpecGenerator(catalog=mini_catalog).generate(
            STORY, style="flat_vector", seed=1
        )
        quiet = Catalog()
        for kind, reg in mini_catalog.all_registries().items():
            if kind != "sfx":
                for entry in reg.entries():
                    quiet.registry(kind).register(entry.name, entry.obj)
        out = enrich_spec(weaken(base.spec), quiet, seed=1)
        assert not [s for s in out["scenes"] if s.get("sfx")]

    def test_malformed_parts_are_skipped_not_fatal(self) -> None:
        spec = mia_spec([[sub("hello there")]] * 10)
        spec["scenes"][0]["layers"][0].pop("actions")  # the schema default
        spec["scenes"][1]["layers"] = []
        spec["scenes"][2]["captions"] = [{"text": "", "t0": 0, "t1": 1}, {"text": "no times"}, 5]
        spec["scenes"][3]["layers"].append("junk")
        spec["scenes"][4]["camera"] = "zoom"
        spec["scenes"][5]["duration_sec"] = "long"
        out = enrich_spec(spec, seed=1)
        assert len(out["scenes"]) == 10
        assert any(a["name"] == "enter_from" for a in out["scenes"][0]["layers"][0]["actions"])
        assert enrich_spec({"meta": {}}) == {"meta": {}}
        assert enrich_spec({"scenes": ["x"]}) == {"scenes": ["x"]}
        with pytest.raises(TypeError, match="JSON object"):
            enrich_spec([1, 2])  # type: ignore[arg-type]

    @pytest.mark.parametrize("style", STYLES)
    @pytest.mark.parametrize(
        "script", [STORY, EXPLAINER, DIALOGUE], ids=["story", "explainer", "dialogue"]
    )
    def test_the_contract_holds_on_the_planners_scripts(self, script: str, style: str) -> None:
        base = run(script, style, seed=5).spec
        for actions in ("idle", "enter_look"):
            weak = weaken(base, actions)
            before = lint_data(weak)
            out = enrich_spec(weak, seed=5)
            after = lint_data(out)
            assert after.ok and len(after.warnings) <= len(before.warnings), after.format_text()
            assert total_of(out) == pytest.approx(total_of(weak), abs=1e-6)  # as long as before
            assert enrich_spec(out, seed=5) == out
            # (a scene shorter than the entrance it already has leaves no room for a gesture)
            assert all(
                any(acted(layer) for layer in sc["layers"])
                for sc in out["scenes"]
                if sc["duration_sec"] >= 3.0
            )

    # -- inside the generator ----------------------------------------------------------------------
    def test_the_generator_enriches_what_it_accepts(self, good_spec: dict[str, Any]) -> None:
        weak = self.weak(good_spec)
        res = LLMSpecGenerator(ReplayClient([weak])).generate(STORY, style="flat_vector", seed=3)
        assert res.ok and res.lint.ok and res.attempts == 1
        assert any(re.match(r"enriched \d+ scenes \(actions", n) for n in res.notes), res.notes
        assert all(sc["camera"]["moves"] and sc["sfx"] for sc in res.spec["scenes"])
        plain = LLMSpecGenerator(ReplayClient([weak]), enrich=False).generate(
            STORY, style="flat_vector", seed=3
        )
        assert total_of(res.spec) == pytest.approx(total_of(plain.spec), abs=1e-6)

    def test_it_can_be_switched_off(self, good_spec: dict[str, Any]) -> None:
        weak = self.weak(good_spec)
        plain = LLMSpecGenerator(ReplayClient([weak]), enrich=False).generate(
            STORY, style="flat_vector", seed=3
        )
        assert not any("enriched" in n for n in plain.notes)
        assert not any(sc.get("camera") or sc.get("sfx") for sc in plain.spec["scenes"])
        via_front_door = generate_spec(
            STORY, "flat_vector", ReplayClient([weak]), seed=3, enrich=False
        )
        assert via_front_door.spec == plain.spec
        on = generate_spec(STORY, "flat_vector", ReplayClient([weak]), seed=3)
        assert on.spec == enrich_spec(plain.spec, seed=3)

    def test_the_offline_planner_is_not_enriched(self) -> None:
        res = run(STORY, "flat_vector", seed=3)
        assert not any("enriched" in n for n in res.notes)

    def test_the_repair_loop_sees_the_models_own_reply_not_the_enriched_one(
        self, good_spec: dict[str, Any]
    ) -> None:
        broken = break_action(self.weak(good_spec))
        client = ReplayClient([broken, self.weak(good_spec)])
        res = LLMSpecGenerator(client).generate(STORY, style="flat_vector", seed=3)
        assert res.attempts == 2 and res.ok
        complaint = client.requests[1].messages[-1].content
        assert "moonwalk" in complaint and "enriched" not in complaint
        assert sum("enriched" in n for n in res.notes) == 1

    def test_a_failing_enrichment_never_costs_the_spec(
        self, good_spec: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import reel.llm.enrich as enrich_module

        def boom(*args: Any, **kw: Any) -> dict[str, Any]:
            raise RuntimeError("kaput")

        monkeypatch.setattr(enrich_module, "enrich_spec", boom)
        weak = self.weak(good_spec)
        res = LLMSpecGenerator(ReplayClient([weak])).generate(STORY, style="flat_vector", seed=3)
        assert res.ok and any("enrichment skipped" in n and "kaput" in n for n in res.notes)
        assert not any(sc.get("camera") for sc in res.spec["scenes"])

    def test_enrichment_that_would_worsen_the_lint_is_dropped(
        self, good_spec: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import reel.llm.enrich as enrich_module

        def worse(
            spec: dict[str, Any], cat: Catalog, seed: int, notes: list[str], **kw: Any
        ) -> dict[str, Any]:
            out = copy.deepcopy(spec)
            out["scenes"][0]["layers"][0]["actions"].append(
                {"name": "moonwalk", "t0": 0.5, "t1": 1.5}
            )
            notes.append("enriched 1 scene (actions 1, camera 0, sfx 0)")
            return out

        monkeypatch.setattr(enrich_module, "enrich_spec", worse)
        weak = self.weak(good_spec)
        res = LLMSpecGenerator(ReplayClient([weak])).generate(STORY, style="flat_vector", seed=3)
        assert res.ok and any("worse" in n for n in res.notes)
        assert "moonwalk" not in json.dumps(res.spec)
        assert not any("enriched" in n for n in res.notes)

    def test_a_cut_off_reply_is_enriched_after_it_is_accepted(
        self, good_spec: dict[str, Any]
    ) -> None:
        weak = self.weak(good_spec)
        client = ReplayClient([Truncated(cut_off_reply(weak, 5))] * 4)
        res = LLMSpecGenerator(client, max_repairs=3).generate(STORY, style="flat_vector", seed=3)
        assert res.ok and len(res.spec["scenes"]) == 5
        assert sum("enriched" in n for n in res.notes) == 1
        assert all(sc["camera"]["moves"] for sc in res.spec["scenes"])
        assert total_of(res.spec) == pytest.approx(50.0, abs=0.06)

    def test_it_is_part_of_the_public_surface(self) -> None:
        import reel.llm as llm

        assert llm.enrich_spec is enrich_spec and "enrich_spec" in llm.__all__


# =============================================================================== shared stagecraft
class TestStagecraft:
    """The pieces the offline planner and the enrichment pass share."""

    def test_business_for_a_silent_scene_does_not_depend_on_who_is_asked_about(self) -> None:
        import random

        from reel.llm.stagecraft import CatalogView, bridge_requests

        view = CatalogView(CATALOG)
        everyone = bridge_requests(view, ["a", "b", "c"], 6.0, random.Random(1))
        only_b = bridge_requests(view, ["a", "b", "c"], 6.0, random.Random(1), who={"b"})
        assert only_b["b"] == everyone["b"] and only_b["a"] == [] and only_b["c"] == []
        assert len(everyone["a"]) == 2  # two beats each: near the start and in the middle
        calm = bridge_requests(view, ["a", "b"], 6.0, random.Random(2), skip=("idle", "look_at"))
        assert {r.name for rs in calm.values() for r in rs} <= {"think", "wave"}

    def test_requests_are_laid_out_around_busy_windows(self) -> None:
        from reel.llm.stagecraft import ActionRequest, CatalogView, schedule_requests

        view = CatalogView(CATALOG)
        reqs = [ActionRequest("wave", 0.5, 1.8), ActionRequest("think", 0.6, 2.4)]
        placed = schedule_requests(view, reqs, 8.0, busy=[(0.0, 1.5)])
        assert [p["name"] for p in placed] == ["wave", "think"]
        assert placed[0]["t0"] >= 1.55  # waits for the busy window ...
        assert placed[1]["t0"] >= placed[0]["t1"]  # ... and for the wave
        assert all(p["t1"] <= 8.0 - 0.15 for p in placed)
        assert schedule_requests(view, reqs, 8.0, busy=[(0.0, 8.0)]) == []  # nowhere to go

    def test_beats_the_catalog_cannot_play_are_not_requested(self, mini_catalog: Catalog) -> None:
        import random

        from reel.llm.stagecraft import (
            CatalogView,
            enter_request,
            flourish_request,
            listener_request,
        )

        view = CatalogView(mini_catalog)
        assert enter_request(view, "kid", 0.2, random.Random(1)) is None  # no enter_from here
        assert listener_request(view, "mia", 0.5, 2.0) is None  # no look_at
        flourish = flourish_request(view, 1.0)
        assert flourish is not None and flourish.name == "wave"  # no jump, no surprise
        real = CatalogView(CATALOG)
        assert enter_request(real, "robot", 0.2, random.Random(1)) is not None
        look = listener_request(real, "mia", 0.5, 9.0)
        assert look is not None and look.dur == 2.4 and look.params == {"target": "mia"}
        jump = flourish_request(real, 1.0)
        assert jump is not None and jump.name == "jump" and jump.params == {"style": "cheer"}

    def test_a_flavor_without_glances_is_always_a_gesture(self) -> None:
        import random

        from reel.llm.stagecraft import flavor_cue

        kinds = {
            flavor_cue(text, "mia", random.Random(i), lively=True, glance=False).kind
            for i in range(40)
            for text in ("It rained.", "Why?", "Wow!")
        }
        assert "look" not in kinds and {"think", "point"} <= kinds


# =============================================================================== front door & examples
class TestGenerateSpec:
    def test_no_client_means_the_offline_planner(self) -> None:
        res = generate_spec(TINY, "flat_vector")
        assert res.generator == "heuristic" and res.ok

    def test_a_client_means_the_model(self, good_spec: dict[str, Any]) -> None:
        res = generate_spec(STORY, "flat_vector", ReplayClient([good_spec], name="r"), seed=3)
        assert res.generator == "llm:r"

    def test_unreachable_models_can_fall_back(self) -> None:
        class Down(LLMClient):
            name = "down"

            def complete(self, system: str, messages: list[Message], **kw: Any) -> str:
                raise LLMUnavailable("connection refused")

        with pytest.raises(LLMUnavailable):
            generate_spec(STORY, "flat_vector", Down())
        res = generate_spec(STORY, "flat_vector", Down(), fallback=True)
        assert (
            res.generator == "heuristic"
            and "not usable" in res.notes[0]
            and "refused" in res.notes[0]
        )

    def test_bad_client_strings_are_config_errors(self) -> None:
        with pytest.raises(LLMConfigError):
            generate_spec(STORY, "flat_vector", "nope:model")
        res = generate_spec(STORY, "flat_vector", "nope:model", fallback=True)
        assert res.generator == "heuristic"


class TestExamples:
    def test_word_counts_match_the_brief(self) -> None:
        story, explainer = len(STORY.split()), len(EXPLAINER.split())
        assert 100 <= story <= 170 and 100 <= explainer <= 160

    @pytest.mark.parametrize("style", STYLES)
    def test_example_scripts_build(self, style: str) -> None:
        for text in (STORY, EXPLAINER):
            res = run(text, style, seed=7)
            assert res.ok and not res.lint.warnings

    def test_story_has_the_promised_shape(self) -> None:
        cast = {c["id"] for c in run(STORY).spec["characters"]}
        assert len(cast) == 3 and STORY.count('"') >= 8  # dialogue, 2-3 characters


def test_public_surface() -> None:
    import reel.llm as llm

    for name in llm.__all__:
        assert hasattr(llm, name), name
    assert callable(llm.generate_spec) and callable(llm.get_client)
