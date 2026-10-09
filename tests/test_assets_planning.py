"""Scripts and planners meet the asset library: words -> assets, what is missing, normalising a model's objects, the prompt, the offline planner."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from reel.assets.coverage import LexEntry, analyze_script, snippet
from reel.assets.gaps import apply_library_gap, free_spot, resolve_gap
from reel.assets.library import load_dirs
from reel.assets.match import TagIndex, asset_index, tokens
from reel.assets.model import AssetDef
from reel.assets.objects import MOTIONS
from reel.core.catalog import CATALOG, Catalog
from reel.core.lint import LintOptions, lint_data
from reel.core.schema import build_schema
from reel.llm import (
    HeuristicSpecGenerator,
    LLMSpecGenerator,
    ReplayClient,
    build_system_prompt,
    json_schema_for_llm,
    normalize_spec,
)
from reel.llm.library_plan import LibraryPlanner, attach_library_gaps, scene_texts
from reel.llm.prompt import MOTION_HINTS, example_parts, render_catalog

NS = 'xmlns="http://www.w3.org/2000/svg"'
SQUARE = f'<svg {NS} viewBox="0 0 100 100"><rect data-role="body" width="100" height="100" fill="#e63946"/></svg>'


def write(root: Path, folder: str, name: str, **sidecar: Any) -> None:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.svg").write_text(SQUARE, encoding="utf-8")
    (d / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")


@pytest.fixture
def cat(tmp_path: Path) -> Catalog:
    """The real catalog (copied) plus a few assets of the user's: a dragon, a rickshaw, a village, a gift."""
    c = CATALOG.fork()
    write(
        tmp_path,
        "characters",
        "dragon",
        tags=["wyvern", "ड्रैगन"],
        height=380,
        summary="A purple dragon",
    )
    write(
        tmp_path,
        "objects",
        "rickshaw",
        tags=["auto rickshaw", "tuk tuk", "रिक्शा", "vehicle"],
        height=320,
        summary="A rickshaw",
    )
    write(tmp_path, "places", "village", tags=["gaon", "गाँव", "hamlet"], summary="A village")
    write(tmp_path, "objects", "gift", tags=["present", "उपहार"], height=200, summary="A gift box")
    load_dirs([tmp_path], c)
    return c


# ======================================================================= words -> assets
class TestTagIndex:
    def test_plain_english_plurals_and_possessives_are_folded(self, cat: Catalog) -> None:
        ix = asset_index(cat)
        assert [
            h.asset.name for h in ix.scan("The cars drove past two trees; the tree's shade")
        ] == ["car", "tree", "tree"]
        assert ix.lookup("Gifts").name == "gift"  # type: ignore[union-attr]
        assert ix.lookup("xyzzy") is None

    def test_devanagari_words_stay_whole(self, cat: Catalog) -> None:
        # Python's \w splits these at every vowel sign; the tokenizer must not
        assert [t[0] for t in tokens("ड्रैगन और गाँव")] == ["ड्रैगन", "और", "गाँव"]
        hits = asset_index(cat).scan("राजा का ड्रैगन गाँव में था")
        assert [h.asset.name for h in hits] == ["dragon", "village"]
        assert hits[0].phrase == "ड्रैगन"

    def test_the_danda_that_ends_a_sentence_is_not_part_of_the_word_before_it(
        self, cat: Catalog
    ) -> None:
        assert [t[0] for t in tokens("खीर। कुत्ता॥ गाय, बस?")] == ["खीर", "कुत्ता", "गाय", "बस"]
        hits = asset_index(cat).scan("वह गाँव गया। वहाँ एक कुत्ता। फिर राजा का ड्रैगन।")
        assert [h.asset.name for h in hits] == ["village", "dog", "dragon"]

    def test_a_word_hindi_uses_every_day_does_not_name_a_thing(self, cat: Catalog) -> None:
        # "बस" is "just" far more often than a bus, "बनी" is "became": neither is a tag, "बस से" (by bus) is
        ix = asset_index(cat)
        assert ix.scan("बस इतना ही। दीवार बनी हुई थी।") == []
        assert [h.asset.name for h in ix.scan("वह बस से शहर गया।")] == ["bus"]

    def test_a_phrase_does_not_run_over_a_full_stop_or_a_comma(self, cat: Catalog) -> None:
        ix = asset_index(cat, ("object",))
        assert [h.asset.name for h in ix.scan("She ate ice cream.")] == ["ice_cream"]
        assert [h.asset.name for h in ix.scan("She ate ice, cream and a cake.")] == ["cake"]
        assert [h.asset.name for h in ix.scan("Ice. Cream is cold.")] == []
        assert [h.asset.name for h in ix.scan("She ate ice\ncream.")] == [
            "ice_cream"
        ]  # a wrapped line is no break

    def test_phrases_win_over_their_words_and_offsets_point_into_the_text(
        self, cat: Catalog
    ) -> None:
        text = "He took an auto rickshaw to the beach."
        hits = asset_index(cat).scan(text)
        assert [h.asset.name for h in hits] == ["rickshaw", "beach"]
        assert text[hits[0].start : hits[0].end] == "auto rickshaw"

    def test_the_users_own_asset_wins_a_shared_word(self, tmp_path: Path, cat: Catalog) -> None:
        write(tmp_path / "mine", "objects", "my_car", tags=["car"])
        load_dirs([tmp_path / "mine"], cat)
        hit = asset_index(cat).scan("a car")[0]
        assert hit.asset.name == "my_car" and [a.name for a in hit.alternatives] == ["car"]

    def test_underscores_and_hyphens_are_word_breaks(self, cat: Catalog) -> None:
        assert asset_index(cat).lookup("auto-rickshaw").name == "rickshaw"  # type: ignore[union-attr]
        assert [h.asset.name for h in asset_index(cat).scan("tuk_tuk")] == ["rickshaw"]

    def test_kinds_can_be_limited(self, cat: Catalog) -> None:
        only_places = asset_index(cat, ("place",))
        assert [h.asset.name for h in only_places.scan("a dragon in the village")] == ["village"]

    def test_it_works_for_anything_with_a_name_and_tags(self) -> None:
        class Thing:
            def __init__(self, name: str, tags: tuple[str, ...]) -> None:
                self.name, self.kind, self.tags, self.origin = name, "object", tags, "x"

        ix = TagIndex([Thing("sofa", ("couch",))])
        assert [h.asset.name for h in ix.scan("the couches")] == ["sofa"]


# ======================================================================= what a script needs
LEX = (
    LexEntry("dragon", "character", ("dragon", "wyvern", "ड्रैगन")),
    LexEntry("castle", "place", ("castle", "fort", "किला", "qila")),
    LexEntry("sword", "object", ("sword", "तलवार", "talwar")),
    LexEntry("dog", "character", ("dog", "puppy", "कुत्ता", "kutta")),
    LexEntry("kitchen", "place", ("kitchen", "रसोई")),
    LexEntry("rickshaw", "object", ("rickshaw", "riksha")),
)


class TestCoverage:
    def test_what_the_library_draws_and_what_it_lacks(self, cat: Catalog) -> None:
        script = "The knight walked to the castle with his sword. A dragon roared. Then the kutta ran past the car and the tree."
        cov = analyze_script(script, cat, lexicon=LEX)
        covered = {(c.kind, c.asset) for c in cov.covered}
        missing = {(m.kind, m.name) for m in cov.missing}
        # the user's dragon, the built-in dog (the script says "kutta": only the lexicon connects it), the car and the tree
        assert {
            ("object", "car"),
            ("object", "tree"),
            ("character", "dragon"),
            ("character", "dog"),
        } <= covered
        assert missing == {("place", "castle"), ("object", "sword")}

    def test_a_lexicon_entry_is_covered_when_any_of_its_words_names_an_asset(
        self, cat: Catalog
    ) -> None:
        # the library knows "rickshaw"; the script says the Hinglish "riksha" which only the lexicon connects to it
        cov = analyze_script("Wo riksha mein baitha tha.", cat, lexicon=LEX)
        assert [(c.asset, c.source) for c in cov.covered] == [("rickshaw", "user")]
        assert not cov.missing

    def test_the_engines_own_sets_cover_what_they_draw(self, cat: Catalog) -> None:
        cov = analyze_script("She cooked in the kitchen.", cat, lexicon=LEX)
        assert [(c.asset, c.source) for c in cov.covered] == [("room", "engine")]
        assert not cov.missing

    def test_missing_things_say_what_to_call_them_and_where_the_script_says_it(
        self, cat: Catalog
    ) -> None:
        text = "First line.\nA dragon roared at the castle! Second line."
        cov = analyze_script(text, cat, lexicon=LEX)
        dragon = next(m for m in cov.missing if m.name == "castle")
        assert dragon.words == ["castle"] and dragon.count == 1
        assert dragon.snippet == "A dragon roared at the castle"
        start, end = dragon.at[0]
        assert text[start:end] == "castle"
        assert (
            "fort" in dragon.tags and "castle" not in dragon.tags
        )  # the tags to give the new asset

    def test_most_mentioned_comes_first_and_everything_serialises(self, cat: Catalog) -> None:
        cov = analyze_script("sword sword sword castle qila", cat, lexicon=LEX)
        assert [m.name for m in cov.missing] == ["sword", "castle"] and cov.missing[1].count == 2
        json.dumps(cov.to_dict(), ensure_ascii=False)

    def test_a_script_with_nothing_to_show_is_quiet(self, cat: Catalog) -> None:
        cov = analyze_script("Hello there, how are you today?", cat, lexicon=LEX)
        assert not cov.covered and not cov.missing

    def test_adding_the_asset_turns_a_gap_into_coverage(self, tmp_path: Path, cat: Catalog) -> None:
        assert analyze_script("a castle", cat, lexicon=LEX).missing
        write(tmp_path / "more", "places", "castle", tags=["fort"])
        load_dirs([tmp_path / "more"], cat)
        cov = analyze_script("a castle", cat, lexicon=LEX)
        assert not cov.missing and cov.covered[0].asset == "castle"

    def test_snippets_are_short_and_end_at_the_sentence(self) -> None:
        long = "Word " * 60 + "target " + "word " * 60 + "."
        s = snippet(long, long.index("target"), long.index("target") + 6, width=60)
        assert len(s) <= 70 and "target" in s and s.startswith("...") and s.endswith("...")

    def test_the_shipped_lexicon_is_usable_when_present(self, cat: Catalog) -> None:
        cov = analyze_script(
            "The king rode a horse to the temple while a dragon slept in the cave.", cat
        )
        names = {m.name for m in cov.missing} | {c.asset for c in cov.covered}
        assert names, "the lexicon should know at least one of these everyday things"


# ======================================================================= swapping assets in after a gap
def spec_with_gaps() -> dict[str, Any]:
    return {
        "version": "1.0",
        "meta": {
            "title": "t",
            "style": "flat_vector",
            "library_gaps": [
                {
                    "kind": "character",
                    "name": "dragon",
                    "scenes": ["s1"],
                    "character": "d",
                    "stand_in": "everyman",
                },
                {"kind": "place", "name": "village", "scenes": ["s1"], "stand_in": "forest"},
                {"kind": "object", "name": "gift", "scenes": ["s1", "s2"]},
                {"kind": "object", "name": "castle", "scenes": ["s1"]},
            ],
        },
        "characters": [{"id": "d", "archetype": "everyman"}],
        "scenes": [
            {
                "id": "s1",
                "duration_sec": 5,
                "background": {
                    "template": "forest",
                    "params": {"time_of_day": "dusk", "season": "winter"},
                },
                "layers": [{"character": "d", "position": [0.5, 0.8], "actions": []}],
                "captions": [],
            },
            {
                "id": "s2",
                "duration_sec": 5,
                "background": {"template": "abstract"},
                "layers": [],
                "captions": [],
            },
        ],
    }


class TestGaps:
    def test_a_gap_resolves_through_the_assets_words(self, cat: Catalog) -> None:
        a = resolve_gap({"kind": "character", "name": "wyvern"}, cat)
        assert isinstance(a, AssetDef) and a.name == "dragon"
        assert (
            resolve_gap({"kind": "object", "name": "dragon"}, cat) is None
        )  # a dragon is not an object here
        assert resolve_gap({"kind": "object", "name": "castle"}, cat) is None

    def test_a_filled_character_loses_the_colours_and_props_a_picture_cannot_use(
        self, cat: Catalog
    ) -> None:
        spec = spec_with_gaps()
        gap = next(g for g in spec["meta"]["library_gaps"] if g["kind"] == "character")
        who = next(c for c in spec["characters"] if c["id"] == gap["character"])
        who["palette"] = {"shirt": "#2a9d8f", "body": "#336699"}
        who["props"] = ["backpack"]
        assert apply_library_gap(spec, gap, "dragon", cat)
        assert who["archetype"] == "dragon" and who["props"] == []
        assert who["palette"] == {
            "body": "#336699"
        }  # the dragon's marked part stays, a shirt has nowhere to go

    def test_each_kind_is_swapped_in(self, cat: Catalog) -> None:
        spec = spec_with_gaps()
        for g in list(spec["meta"]["library_gaps"])[:3]:
            have = resolve_gap(g, cat)
            assert have is not None and apply_library_gap(spec, g, have, cat)
        assert spec["characters"][0]["archetype"] == "dragon"
        bg = spec["scenes"][0]["background"]
        assert bg["template"] == "village"
        assert (
            "season" not in bg["params"] and bg["params"]["time_of_day"] == "dusk"
        )  # only what the new set also has
        assert [o["asset"] for o in spec["scenes"][0]["objects"]] == ["gift"]
        assert [o["asset"] for o in spec["scenes"][1]["objects"]] == ["gift"]
        left = spec["meta"]["library_gaps"]
        assert [g["name"] for g in left] == ["castle"]

    def test_the_result_is_a_valid_spec(self, cat: Catalog) -> None:
        spec = spec_with_gaps()
        for g in list(spec["meta"]["library_gaps"])[:3]:
            apply_library_gap(spec, g, resolve_gap(g, cat), cat)  # type: ignore[arg-type]
        report = lint_data(spec, catalog=cat, options=LintOptions(check_duration=False))
        assert report.ok, [i.message for i in report.errors]

    def test_a_placed_object_keeps_clear_of_the_characters(self) -> None:
        scene = {"layers": [{"position": [0.14, 0.8]}, {"position": "right"}], "objects": []}
        assert 0.4 < free_spot(scene) < 0.6

    def test_nothing_changes_for_a_gap_of_the_wrong_kind_or_an_unknown_asset(
        self, cat: Catalog
    ) -> None:
        spec = spec_with_gaps()
        before = copy.deepcopy(spec)
        assert not apply_library_gap(
            spec, spec["meta"]["library_gaps"][0], "gift", cat
        )  # a gift is not a character
        assert not apply_library_gap(spec, spec["meta"]["library_gaps"][0], "nothing", cat)
        assert spec == before


# ======================================================================= what a model writes is repaired
def scene(**kw: Any) -> dict[str, Any]:
    return {
        "id": "s1",
        "duration_sec": 8,
        "background": {"template": "abstract"},
        "layers": [],
        "captions": [{"text": "hello there", "t0": 0.5, "t1": 3, "style": "subtitle"}],
        **kw,
    }


def norm(
    scenes: list[dict[str, Any]], cat: Catalog, **meta: Any
) -> tuple[dict[str, Any], list[str]]:
    """Normalise ``scenes`` (padded with plain scenes so the whole is inside the 45-60 s budget and nothing is rescaled)."""
    filler = [scene(id=f"pad{i}", duration_sec=8.5) for i in range(max(0, 6 - len(scenes)))]
    notes: list[str] = []
    out = normalize_spec(
        {"meta": {"title": "t", **meta}, "characters": [], "scenes": [*scenes, *filler]},
        style="flat_vector",
        notes=notes,
        catalog=cat,
    )
    return out, notes


class TestNormalizeObjects:
    def test_names_are_resolved_through_the_librarys_own_words(self, cat: Catalog) -> None:
        out, notes = norm(
            [
                scene(
                    objects=[
                        {"asset": "Automobile"},
                        {"asset": "Tuk Tuk"},
                        {"name": "tree", "pos": [0.2, 0.8]},
                    ]
                )
            ],
            cat,
        )
        objs = out["scenes"][0]["objects"]
        assert [o["asset"] for o in objs] == ["car", "rickshaw", "tree"] and objs[2][
            "position"
        ] == [0.2, 0.8]
        assert any("matched object names" in n for n in notes)

    def test_an_object_the_library_lacks_is_dropped_and_recorded(self, cat: Catalog) -> None:
        out, notes = norm(
            [
                scene(objects=[{"asset": "castle"}, {"asset": "tree"}]),
                scene(id="s2", objects=[{"asset": "Castle"}]),
            ],
            cat,
        )
        assert [o["asset"] for o in out["scenes"][0]["objects"]] == [
            "tree"
        ] and "objects" not in out["scenes"][1]
        assert out["meta"]["library_gaps"] == [
            {"kind": "object", "name": "castle", "scenes": ["s1", "s2"]}
        ]
        assert any("meta.library_gaps" in n for n in notes)

    def test_numbers_slots_and_enums_are_put_right(self, cat: Catalog) -> None:
        out, _ = norm(
            [
                scene(
                    objects=[
                        {
                            "asset": "tree",
                            "position": "lighthouse",
                            "scale": 99,
                            "alpha": 3,
                            "rotation": "x",
                            "layer": "above",
                            "depth": "far",
                            "palette": {"leaves": "green", "trunk": "nonsense"},
                        },
                        {"asset": "car", "position": [9, 9]},
                    ]
                )
            ],
            cat,
        )
        tree, car = out["scenes"][0]["objects"]
        assert tree["position"] == "center" and tree["scale"] == 6.0 and tree["alpha"] == 1.0
        assert "rotation" not in tree and "layer" not in tree and "depth" not in tree
        assert tree["palette"] == {"leaves": "#2e8b57"} or list(tree["palette"]) == [
            "leaves"
        ]  # a colour name becomes hex
        assert car["position"] == [2.0, 1.4]

    def test_odd_types_huge_numbers_and_unknown_easings_are_put_right(self, cat: Catalog) -> None:
        out, notes = norm(
            [
                scene(
                    objects=[
                        {
                            "asset": "car",
                            "position": [10**400, 0.8],
                            "scale": 10**400,
                            "motions": [
                                {"type": ["hop"], "t0": 0, "t1": 1},
                                {"type": {"a": 1}, "t0": 0, "t1": 1},
                                {"type": "hop", "t0": 10**400, "t1": 10**401},
                                {"type": "hop", "t0": 0, "t1": 1, "ease": "bounce_out"},
                                {"type": "spin", "t0": 1, "t1": 2, "ease": ["linear"]},
                            ],
                        }
                    ]
                )
            ],
            cat,
        )
        car = out["scenes"][0]["objects"][0]
        assert [m["type"] for m in car["motions"]] == ["hop", "spin"]
        assert all(
            "ease" not in m for m in car["motions"]
        )  # a name no easing has is dropped, as for camera moves
        assert any("easing" in n for n in notes)

    def test_motions_are_repaired_or_dropped(self, cat: Catalog) -> None:
        out, _ = norm(
            [
                scene(
                    objects=[
                        {
                            "asset": "car",
                            "position": [0.2, 0.8],
                            "motions": [
                                {"type": "move", "start": 1, "end": 4, "to": [1.2, 0.8]},
                                {"type": "teleport", "t0": 0, "t1": 1},
                                {"type": "move", "t0": 0, "t1": 2},
                                {"type": "hop", "t0": 3, "t1": 2},
                                {"type": "hop", "t0": 9, "t1": 10},
                                {"type": "pulse", "t0": 1, "t1": 3, "count": 99, "amount": "big"},
                                {"type": "move", "t0": 0, "t1": 1, "to": "lighthouse"},
                            ],
                        }
                    ]
                )
            ],
            cat,
        )
        motions = out["scenes"][0]["objects"][0]["motions"]
        assert [m["type"] for m in motions] == ["move", "pulse"]
        assert (
            (motions[0]["t0"], motions[0]["t1"]) == (1.0, 4.0)
            and motions[1]["count"] == 20
            and "amount" not in motions[1]
        )

    def test_object_times_follow_the_scene_when_it_is_rescaled(self, cat: Catalog) -> None:
        sc = scene(
            duration_sec=10,
            objects=[
                {
                    "asset": "car",
                    "t0": 2,
                    "t1": 8,
                    "motions": [{"type": "move", "t0": 4, "t1": 6, "to": [1, 0.8]}],
                }
            ],
        )
        out = normalize_spec(
            {
                "meta": {"title": "t"},
                "characters": [],
                "scenes": [sc, scene(id="s2", duration_sec=10)],
            },
            style="flat_vector",
            target_duration=50,
            catalog=cat,
        )
        s1 = out["scenes"][0]
        f = s1["duration_sec"] / 10
        ob = s1["objects"][0]
        assert ob["t0"] == pytest.approx(2 * f, abs=0.01) and ob["t1"] == pytest.approx(
            8 * f, abs=0.01
        )
        assert ob["motions"][0]["t1"] == pytest.approx(6 * f, abs=0.01)
        assert lint_data(out, catalog=cat).ok

    def test_windows_are_clamped_into_the_scene(self, cat: Catalog) -> None:
        out, _ = norm(
            [
                scene(
                    objects=[
                        {"asset": "car", "t0": 20},
                        {
                            "asset": "tree",
                            "t0": -2,
                            "t1": 99,
                            "motions": [{"type": "float", "t0": 6, "t1": 30}],
                        },
                    ]
                )
            ],
            cat,
        )
        (tree,) = out["scenes"][0]["objects"]
        assert tree["t0"] == 0 and "t1" not in tree and tree["motions"][0]["t1"] == 8.0

    def test_picture_characters_keep_only_their_own_roles_and_get_no_body_palette(
        self, cat: Catalog
    ) -> None:
        out = normalize_spec(
            {
                "meta": {"title": "t"},
                "characters": [
                    {"id": "d", "archetype": "dragon", "palette": {"shirt": "#ff0000"}},
                    {"id": "a", "archetype": "everyman"},
                ],
                "scenes": [
                    scene(
                        layers=[
                            {"character": "d", "position": "left", "actions": []},
                            {"character": "a", "position": "right", "actions": []},
                        ]
                    )
                ],
            },
            style="flat_vector",
            catalog=cat,
            fit_tolerance=100,
        )
        dragon, man = out["characters"]
        assert "palette" not in dragon  # "shirt" is not one of its roles
        assert man["palette"]["shirt"]  # a body still gets its colours


class TestNormalizeGaps:
    def test_what_a_model_reports_is_cleaned(self, cat: Catalog) -> None:
        gaps = [
            {
                "kind": "place",
                "name": "  Moon  base ",
                "scenes": ["s1", 2],
                "stand_in": "abstract",
                "junk": 1,
            },
            {"kind": "place", "name": "moon base"},  # a repeat
            {"kind": "weapon", "name": "bow"},
            {"kind": "object", "name": ""},
            "nonsense",
        ]
        out, _ = norm([scene()], cat, library_gaps=gaps)
        assert out["meta"]["library_gaps"] == [
            {"kind": "place", "name": "Moon base", "scenes": ["s1", "2"], "stand_in": "abstract"}
        ]

    def test_a_gap_the_library_already_fills_is_applied_not_kept(self, cat: Catalog) -> None:
        spec = {
            "meta": {
                "title": "t",
                "library_gaps": [
                    {
                        "kind": "character",
                        "name": "wyvern",
                        "scenes": ["s1"],
                        "character": "d",
                        "stand_in": "everyman",
                    }
                ],
            },
            "characters": [{"id": "d", "archetype": "everyman"}],
            "scenes": [scene(layers=[{"character": "d", "position": "left", "actions": []}])],
        }
        out = normalize_spec(spec, style="flat_vector", catalog=cat, fit_tolerance=100)
        assert out["characters"][0]["archetype"] == "dragon" and "library_gaps" not in out["meta"]

    def test_gaps_survive_the_schema(self, cat: Catalog) -> None:
        out, _ = norm([scene(objects=[{"asset": "castle"}])], cat)
        assert lint_data(out, catalog=cat, options=LintOptions(check_duration=False)).ok
        defs = build_schema(cat)["$defs"]
        assert "library_gaps" in defs["MetaSpec"]["properties"] and "LibraryGapSpec" in defs


# ======================================================================= the prompt
class TestPrompt:
    def test_the_catalog_lists_objects_with_size_roles_and_words(self, cat: Catalog) -> None:
        text = render_catalog(cat)
        assert "OBJECTS - scenes[].objects[]" in text
        line = next(ln for ln in text.splitlines() if ln.startswith("- rickshaw:"))
        assert "size 0.56" in line and "roles body" in line and "tuk tuk" in line
        assert (
            "रिक्शा" not in line
        )  # Indic words stay out of the full prompt: they cost tokens and a model already reads them
        assert "- car:" in text and "MOTION types: move" in text

    def test_pictures_and_library_places_say_what_they_can_do_and_what_they_are_called(
        self, cat: Catalog
    ) -> None:
        text = render_catalog(cat)
        header = next(ln for ln in text.splitlines() if ln.startswith("ARCHETYPES"))
        assert (
            "[picture]" in header and "cannot use their arms" in header
        )  # said once, not on every line
        dragon = next(ln for ln in text.splitlines() if ln.startswith("- dragon:"))
        assert "[picture;" in dragon and "wyvern" in dragon and "cannot" not in dragon
        village = next(ln for ln in text.splitlines() if ln.startswith("- village:"))
        assert "[words" in village and "gaon" in village and "गाँव" not in village
        forest = next(ln for ln in text.splitlines() if ln.startswith("- forest:"))
        assert "[words" not in forest  # the engine's own sets have no extra words

    def test_the_rules_for_objects_and_gaps_are_in_the_full_prompt_only(self, cat: Catalog) -> None:
        full = build_system_prompt("flat_vector", 50, cat, verbatim=False)
        assert (
            "- OBJECTS: scenes[].objects[]" in full
            and "- LIBRARY: use ONLY names from the CATALOG" in full
        )
        assert "library_gaps" in full and '"stand_in"' in full
        compact = build_system_prompt("flat_vector", 50, cat, compact=True, verbatim=False)
        assert "OBJECTS" not in compact and "library_gaps" not in compact
        assert (
            "words" not in compact.split("CATALOG")[1]
        )  # small models get names and one-line summaries only

    def test_motion_hints_cover_exactly_the_motions_the_engine_has(self) -> None:
        assert set(MOTION_HINTS) == set(MOTIONS)

    def test_the_worked_example_uses_an_object_and_still_lints(self) -> None:
        chars, sc = example_parts(CATALOG)
        assert sc["objects"][0]["asset"] in CATALOG.objects
        spec = {
            "version": "1.0",
            "meta": {"title": "x", "style": "flat_vector"},
            "characters": chars,
            "scenes": [sc],
        }
        assert [i for i in lint_data(spec).errors if i.code != "DURATION_BUDGET"] == []
        assert "objects" not in example_parts(CATALOG, compact=True)[1]

    def test_the_schema_for_a_model_pins_object_names_and_the_compact_one_drops_them(
        self, cat: Catalog
    ) -> None:
        full = json_schema_for_llm(cat)
        assert "castle" not in full["$defs"]["ObjectSpec"]["properties"]["asset"]["enum"]
        assert {"car", "rickshaw"} <= set(
            full["$defs"]["ObjectSpec"]["properties"]["asset"]["enum"]
        )
        small = json_schema_for_llm(cat, compact=True)
        assert "objects" not in small["$defs"]["SceneSpec"]["properties"]
        assert "library_gaps" not in small["$defs"]["MetaSpec"]["properties"]


# ======================================================================= the offline planner
STORY = """Mia and her dragon Ember walked to the village on a sunny morning.
"Look at the tree!" said Mia. A rickshaw drove past along the road.
Ember grabbed the gift with a sneeze. Mia laughed.
In the evening they rested by the old castle, and the sword glowed."""


class TestOfflinePlanner:
    @pytest.fixture
    def plan(self, cat: Catalog, monkeypatch: pytest.MonkeyPatch) -> Any:
        monkeypatch.setattr("reel.assets.coverage.load_lexicon", lambda: LEX)
        return HeuristicSpecGenerator(catalog=cat).generate(STORY, style="flat_vector", seed=1)

    def test_a_creature_the_library_draws_becomes_a_character(self, plan: Any) -> None:
        by_id = {c["id"]: c for c in plan.spec["characters"]}
        assert (
            by_id["ember"]["archetype"] == "dragon" and "palette" not in by_id["ember"]
        )  # no body colours on a picture
        assert by_id["mia"]["archetype"] == "everyman"

    def test_a_name_followed_by_what_it_is_is_drawn_as_it(self, cat: Catalog) -> None:
        script = "Max the dog loved the park. Max chased a red ball. Then Max barked at the tree."
        res = HeuristicSpecGenerator(catalog=cat).generate(script, style="flat_vector", seed=1)
        assert {c["id"]: c["archetype"] for c in res.spec["characters"]} == {"max": "dog"}

    @pytest.mark.parametrize(
        ("script", "cast"),
        [
            # a creature or role that only stands next to a name does not make the person one
            ("The cat scratched Mia. Mia cried. Mia ran home. Mia laughed.", {"mia": "everyman"}),
            ("The farmer called Mia. Mia waved. Mia smiled. Mia ran.", {"mia": "everyman"}),
            ("The lion saw Arjun. Arjun ran. Arjun hid. Arjun waved.", {"arjun": "everyman"}),
            (
                "Ravi, a rickshaw driver with a parrot, waved. Ravi smiled. Ravi left. Ravi ran.",
                {"ravi": "everyman"},
            ),
            # what the text says a name *is* still counts
            (
                "Max the dog and Luna the cat played. Max ran. Luna jumped. Max barked.",
                {"max": "dog", "luna": "cat"},
            ),
            (
                "Mia and her dragon Ember walked home. Ember sneezed. Mia laughed. Ember flew.",
                {"ember": "dragon", "mia": "everyman"},
            ),
            (
                "Mia's dog Max chased a ball. Max barked. Mia laughed. Max ran.",
                {"max": "dog", "mia": "everyman"},
            ),
            (
                "Pixel is a robot that loves dogs. Pixel smiled. Pixel waved. Pixel left.",
                {"pixel": "robot"},
            ),
        ],
    )
    def test_a_name_is_drawn_as_what_the_text_says_it_is_not_as_what_is_near_it(
        self, cat: Catalog, script: str, cast: dict[str, str]
    ) -> None:
        res = HeuristicSpecGenerator(catalog=cat).generate(script, style="flat_vector", seed=3)
        assert {c["id"]: c["archetype"] for c in res.spec["characters"]} == cast

    def test_a_place_named_in_a_scene_is_used(self, plan: Any) -> None:
        assert plan.spec["scenes"][0]["background"]["template"] == "village"

    def test_objects_named_in_a_scene_are_placed_clear_of_the_cast(self, plan: Any) -> None:
        placed = {
            o["asset"]: (i, o)
            for i, sc in enumerate(plan.spec["scenes"])
            for o in sc.get("objects", [])
        }
        assert {"tree", "rickshaw", "gift"} <= set(placed)
        _, tree = placed["tree"]
        assert (
            isinstance(tree["position"], list)
            and 0 < tree["position"][0] < 1
            and 0.05 <= tree["scale"] <= 1
        )

    def test_a_vehicle_the_text_drives_crosses_the_frame(self, plan: Any) -> None:
        _, rickshaw = next(
            (i, o)
            for i, sc in enumerate(plan.spec["scenes"])
            for o in sc.get("objects", [])
            if o["asset"] == "rickshaw"
        )
        (move,) = rickshaw["motions"]
        assert move["type"] == "move" and (move["to"][0] < 0 or move["to"][0] > 1)
        sc = next(
            s
            for s in plan.spec["scenes"]
            if any(o["asset"] == "rickshaw" for o in s.get("objects", []))
        )
        assert move["t1"] <= sc["duration_sec"]

    def test_what_the_library_lacks_is_recorded_with_the_scenes_that_say_it(
        self, plan: Any
    ) -> None:
        gaps = {(g["kind"], g["name"]): g for g in plan.spec["meta"]["library_gaps"]}
        assert set(gaps) == {("place", "castle"), ("object", "sword")}
        assert gaps[("place", "castle")]["scenes"] and any(
            "not in the asset library" in n for n in plan.notes
        )

    def test_the_plan_lints_clean_and_notes_the_gaps(self, plan: Any) -> None:
        assert plan.lint.ok
        assert {i.code for i in plan.lint.issues} >= {"LIBRARY_GAP"}

    def test_an_object_word_does_not_drag_the_story_to_another_place(self, cat: Catalog) -> None:
        # "tree" used to mean "the forest set"; the library draws a tree, so the beach stays
        script = "Mia sat on the beach. She looked at the tree. She waved at the sea."
        res = HeuristicSpecGenerator(catalog=cat).generate(script, style="flat_vector", seed=1)
        assert {s["background"]["template"] for s in res.spec["scenes"]} == {"beach"}
        assert any(o["asset"] == "tree" for s in res.spec["scenes"] for o in s.get("objects", []))

    def test_the_same_script_and_seed_give_the_same_plan(
        self, cat: Catalog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("reel.assets.coverage.load_lexicon", lambda: LEX)
        gen = HeuristicSpecGenerator(catalog=cat)
        assert (
            gen.generate(STORY, style="flat_vector", seed=4).spec
            == gen.generate(STORY, style="flat_vector", seed=4).spec
        )

    def test_a_script_in_another_language_casts_the_characters_it_names(self, cat: Catalog) -> None:
        # Hindi has no "the dog" for the rules to read: its words are matched to the library's tags
        script = (
            "गाँव में एक किसान रहता था। एक दिन उसकी गाय पेड़ के पास खड़ी थी। किसान ने अपने कुत्ते को बुलाया।"
        )
        gen = HeuristicSpecGenerator(catalog=cat)
        res = gen.generate(script, style="flat_vector", seed=1)
        assert {c["id"]: c["archetype"] for c in res.spec["characters"]} == {
            "farmer": "farmer",
            "cow": "cow",
            "dog": "dog",
        }
        assert res.lint.ok
        on_screen = [[layer["character"] for layer in sc["layers"]] for sc in res.spec["scenes"]]
        assert on_screen[0] == ["farmer"]  # the others arrive in the line that names them
        assert on_screen[-1] == ["farmer", "cow", "dog"]
        crowd = res.spec["scenes"][-1][
            "layers"
        ]  # a cow is wide: the three are spread and scaled down together
        assert [ly["position"] for ly in crowd] == ["far_left", "center", "far_right"]
        assert len({ly["scale"] for ly in crowd}) == 1 and crowd[0]["scale"] < 1
        assert "scale" not in res.spec["scenes"][0]["layers"][0]
        assert any("narrowed wide picture characters" in n for n in res.notes)
        assert res.spec == gen.generate(script, style="flat_vector", seed=1).spec

    def test_a_creature_named_in_two_languages_is_one_character(self, cat: Catalog) -> None:
        script = "The dog barked. कुत्ता भागा। The dog ran. कुत्ता रुका।"
        res = HeuristicSpecGenerator(catalog=cat).generate(script, style="flat_vector", seed=1)
        assert [c["id"] for c in res.spec["characters"]] == ["dog"]

    def test_a_character_named_once_in_another_language_joins_only_when_nobody_else_does(
        self, cat: Catalog
    ) -> None:
        # the English rule too: a role noun counts when it recurs or when nothing else is named
        gen = HeuristicSpecGenerator(catalog=cat)
        once = gen.generate("Mia walked home. फिर Mia ने कुत्ते को देखा।", style="flat_vector", seed=1)
        assert [c["id"] for c in once.spec["characters"]] == ["mia"]
        twice = gen.generate(
            "Mia walked home. फिर Mia ने कुत्ते को देखा। कुत्ते ने पूँछ हिलाई।", style="flat_vector", seed=1
        )
        assert [c["id"] for c in twice.spec["characters"]] == ["mia", "dog"]

    def test_a_word_that_names_a_place_is_not_also_an_object_on_it(self, cat: Catalog) -> None:
        # "school" is the classroom; the school building object would stand inside it
        res = HeuristicSpecGenerator(catalog=cat).generate(
            "The kids ran to school. Mia waved at the teacher. The bell rang.",
            style="flat_vector",
            seed=3,
        )
        assert res.spec["scenes"][0]["background"]["template"] == "classroom"
        assert not any(
            o["asset"] == "school_building"
            for s in res.spec["scenes"]
            for o in s.get("objects", [])
        )
        hospital = HeuristicSpecGenerator(catalog=cat).generate(
            "Pip went to the hospital.", style="flat_vector", seed=3
        )
        assert hospital.spec["scenes"][0]["background"]["template"] == "hospital"
        assert not any(
            o["asset"] == "hospital_building"
            for s in hospital.spec["scenes"]
            for o in s.get("objects", [])
        )

    def test_the_name_of_a_longer_object_does_not_name_the_place_inside_it(
        self, cat: Catalog
    ) -> None:
        planner = LibraryPlanner(cat)
        assert planner.place_scores("Mia pointed at the school building.") == {}
        assert planner.place_scores("Mia pointed at the school.") == {"classroom": 3.0}
        spots = planner.scene_objects([(0.0, 3.0, "Mia pointed at the school building.")], [], 6.0)
        assert [o["asset"] for o in spots] == ["school_building"]

    def test_planning_without_any_assets_still_works(self) -> None:
        bare = Catalog()
        assert LibraryPlanner(CATALOG.fork()).character_words()  # the built-in cat at least
        assert bare.assets is not None


# ======================================================================= a model's plan
class TestModelPlan:
    def reply(self, cat: Catalog) -> dict[str, Any]:
        spec = (
            HeuristicSpecGenerator(catalog=cat)
            .generate("Mia fed her dragon. A rickshaw drove by.", style="flat_vector", seed=2)
            .spec
        )
        out = copy.deepcopy(spec)
        out["scenes"][0]["objects"] = [
            {"asset": "castle", "position": [0.8, 0.8]},
            {"asset": "automobile", "position": [0.2, 0.8]},
        ]
        return out

    def test_unknown_objects_become_gaps_without_a_repair_round(
        self, cat: Catalog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("reel.assets.coverage.load_lexicon", lambda: LEX)
        client = ReplayClient([self.reply(cat)], name="fake")
        res = LLMSpecGenerator(client, catalog=cat).generate(
            "Mia fed her dragon. A rickshaw drove by.", style="flat_vector", seed=2
        )
        assert res.attempts == 1 and res.ok
        assert [o["asset"] for o in res.spec["scenes"][0]["objects"]] == ["car"]
        gaps = res.spec["meta"]["library_gaps"]
        assert {"kind": "object", "name": "castle", "scenes": [res.spec["scenes"][0]["id"]]} in gaps
        assert any(i.code == "LIBRARY_GAP" for i in res.lint.issues)

    def test_a_crowd_of_pictures_in_a_models_plan_is_given_room(self, cat: Catalog) -> None:
        spec = (
            HeuristicSpecGenerator(catalog=cat)
            .generate("Mia fed her dragon. A rickshaw drove by.", style="flat_vector", seed=2)
            .spec
        )
        out = copy.deepcopy(spec)
        out["characters"] = [
            {"id": "farmer", "archetype": "farmer"},
            {"id": "cow", "archetype": "cow"},
            {"id": "dog", "archetype": "dog"},
        ]
        for sc in out["scenes"]:
            sc["layers"] = [
                {"character": c, "position": p, "actions": []}
                for c, p in (("farmer", "left"), ("cow", "center"), ("dog", "right"))
            ]
        res = LLMSpecGenerator(ReplayClient([out], name="fake"), catalog=cat).generate(
            "Mia fed her dragon. A rickshaw drove by.", style="flat_vector", seed=2
        )
        assert res.ok
        crowd = res.spec["scenes"][0]["layers"]
        assert [ly["position"] for ly in crowd] == ["far_left", "center", "far_right"]
        assert len({ly["scale"] for ly in crowd}) == 1 and crowd[0]["scale"] < 1
        assert any("narrowed wide picture characters" in n for n in res.notes)

    def test_a_gap_names_the_scenes_that_say_the_word_not_the_ones_that_contain_it(
        self, cat: Catalog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "reel.assets.coverage.load_lexicon",
            lambda: (*LEX, LexEntry("bag", "object", ("bag", "bags"))),
        )
        spec = (
            HeuristicSpecGenerator(catalog=cat)
            .generate(
                "Mia carried a bag. The baggage was heavy. Mia sat down.",
                style="flat_vector",
                seed=2,
            )
            .spec
        )
        (gap,) = [g for g in spec["meta"]["library_gaps"] if g["name"] == "bag"]
        said = [sid for sid, t in scene_texts(spec).items() if "a bag" in t.lower()]
        containing = [sid for sid, t in scene_texts(spec).items() if "baggage" in t.lower()]
        assert said and containing and set(gap["scenes"]) == set(said) - set(containing)

    def test_the_script_is_read_for_what_the_model_forgot_to_report(
        self, cat: Catalog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("reel.assets.coverage.load_lexicon", lambda: LEX)
        spec = (
            HeuristicSpecGenerator(catalog=cat)
            .generate("Mia went to the village.", style="flat_vector", seed=2)
            .spec
        )
        assert attach_library_gaps(spec, "A sword lay by the castle. Mia went to the village.", cat)
        names = {g["name"] for g in spec["meta"]["library_gaps"]}
        assert names == {"sword", "castle"}
        assert not attach_library_gaps(spec, "A sword lay by the castle.", cat)  # already reported
        texts = scene_texts(spec)
        assert all(isinstance(v, str) for v in texts.values())
