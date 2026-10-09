"""Keeping a cast in the frame: wide pictures make room for each other, bodies are never touched."""

from __future__ import annotations

import copy
from itertools import pairwise
from pathlib import Path
from typing import Any

from reel.assets.gaps import apply_library_gap
from reel.assets.layout import (
    BODY_HALF_WIDTH,
    FRAME_W,
    MAX_FRAME_SHARE,
    MIN_SCALE,
    OVERLAP_ALLOWED,
    fit_scene,
    fit_spec,
    picture_width,
)
from reel.assets.library import load_dirs
from reel.core.catalog import CATALOG
from reel.core.planner import UNIVERSAL_SLOT_X

NS = 'xmlns="http://www.w3.org/2000/svg"'
ARCH = {
    "farmer": "farmer",
    "cow": "cow",
    "dog": "dog",
    "mia": "everyman",
    "bob": "everyman",
}


def scene(*layers: tuple[str, Any], scale: dict[str, float] | None = None) -> dict[str, Any]:
    return {
        "id": "s01",
        "layers": [
            {"character": c, "position": p, **({"scale": scale[c]} if scale and c in scale else {})}
            for c, p in layers
        ],
    }


def spots(sc: dict[str, Any]) -> list[tuple[float, float]]:
    """(x, half width px) of each layer as it stands now."""
    out = []
    for ly in sc["layers"]:
        pos = ly["position"]
        x = UNIVERSAL_SLOT_X[pos] if isinstance(pos, str) else pos[0]
        w = picture_width(ARCH[ly["character"]], CATALOG)
        out.append((x, (w / 2 if w else BODY_HALF_WIDTH) * ly.get("scale", 1.0)))
    return sorted(out)


def test_only_pictures_have_a_width() -> None:
    assert picture_width("everyman", CATALOG) is None
    assert picture_width("no_such_archetype", CATALOG) is None
    cow, dog = picture_width("cow", CATALOG), picture_width("dog", CATALOG)
    assert cow is not None and dog is not None and cow > 2 * BODY_HALF_WIDTH


def test_a_scene_of_bodies_is_never_touched() -> None:
    sc = scene(("mia", "left"), ("bob", "center"), ("mia", "right"))
    before = copy.deepcopy(sc)
    assert fit_scene(sc, ARCH, CATALOG) == []
    assert sc == before


def test_pictures_that_fit_are_left_alone() -> None:
    sc = scene(("farmer", "left"), ("dog", "right"))
    before = copy.deepcopy(sc)
    assert fit_scene(sc, ARCH, CATALOG) == []
    assert sc == before


def test_a_crowd_of_pictures_is_spread_and_scaled_down_together() -> None:
    sc = scene(("farmer", "left"), ("cow", "center"), ("dog", "right"))
    changed = fit_scene(sc, ARCH, CATALOG)
    assert changed == ["farmer", "cow", "dog"]
    assert [ly["position"] for ly in sc["layers"]] == ["far_left", "center", "far_right"]
    scales = {ly["scale"] for ly in sc["layers"]}
    assert len(scales) == 1, "one scale for all, so a farmer stays taller than his dog"
    (s,) = scales
    assert MIN_SCALE <= s < 1
    row = spots(sc)
    for (xa, ha), (xb, hb) in pairwise(row):
        assert ha + hb <= OVERLAP_ALLOWED * (xb - xa) * FRAME_W + 1e-6, (
            "neighbours no longer hide each other"
        )
    for x, half in row:
        assert half <= min(x, 1 - x) * FRAME_W + 1e-6, "nobody is cut off by the frame"


def test_the_outer_slots_are_taken_only_when_they_give_the_cast_more_room() -> None:
    # a cow on the left slot would be pushed off the frame by the far one: it stays, and is scaled to fit
    sc = scene(("cow", "left"), ("dog", "right"))
    fit_scene(sc, ARCH, CATALOG)
    assert [ly["position"] for ly in sc["layers"]] == ["left", "right"]
    for x, half in spots(sc):
        assert half <= min(x, 1 - x) * FRAME_W + 1e-6


def test_a_lone_picture_stays_inside_the_frame(tmp_path: Path) -> None:
    sc = scene(("cow", "center"))
    assert fit_scene(sc, ARCH, CATALOG) == []  # a cow fits
    d = tmp_path / "characters"
    d.mkdir()
    (d / "titan.svg").write_text(
        f'<svg {NS} viewBox="0 0 400 100"><rect width="400" height="100" fill="#556677"/></svg>',
        encoding="utf-8",
    )
    (d / "titan.json").write_text('{"height": 300}', encoding="utf-8")
    cat = CATALOG.fork()
    load_dirs([tmp_path], cat)
    sc = scene(("titan", "center"))
    assert fit_scene(sc, {"titan": "titan"}, cat) == ["titan"]
    w = picture_width("titan", cat)
    assert w is not None and w > FRAME_W
    assert w * sc["layers"][0]["scale"] <= MAX_FRAME_SHARE * FRAME_W + 1


def test_a_scale_the_plan_gave_is_kept_and_never_enlarged() -> None:
    sc = scene(("cow", "center"), scale={"cow": 0.6})
    assert fit_scene(sc, ARCH, CATALOG) == []
    assert sc["layers"][0]["scale"] == 0.6
    crowd = scene(("farmer", "left"), ("cow", "center"), ("dog", "right"), scale={"cow": 0.5})
    fit_scene(crowd, ARCH, CATALOG)
    assert all(ly["scale"] <= 1.0 for ly in crowd["layers"])
    assert crowd["layers"][1]["scale"] <= 0.5


def test_fitting_twice_changes_nothing_more() -> None:
    spec = {
        "characters": [{"id": c, "archetype": a} for c, a in ARCH.items()],
        "scenes": [scene(("farmer", "left"), ("cow", "center"), ("dog", "right"))],
    }
    first = fit_spec(spec, CATALOG)
    once = copy.deepcopy(spec)
    assert first == [("s01", "farmer"), ("s01", "cow"), ("s01", "dog")]
    assert fit_spec(spec, CATALOG) == [] and spec == once


def test_layers_off_stage_or_on_unknown_slots_are_not_measured() -> None:
    sc = scene(("cow", "center"), ("dog", "stump"), ("farmer", "off_left"))
    before = copy.deepcopy(sc)
    assert fit_scene(sc, ARCH, CATALOG) == []
    assert sc == before


def test_a_point_position_works_like_a_slot() -> None:
    """Specs written by hand or by a model place layers at [x, y]: it is only the outer slots that can move apart."""
    sc = scene(("farmer", [0.27, 0.8]), ("cow", [0.5, 0.82]), ("dog", [0.73, 0.8]))
    changed = fit_scene(sc, ARCH, CATALOG)
    assert changed == ["farmer", "cow", "dog"]
    assert [ly["position"] for ly in sc["layers"]] == [[0.27, 0.8], [0.5, 0.82], [0.73, 0.8]]
    assert len({ly["scale"] for ly in sc["layers"]}) == 1 and sc["layers"][0]["scale"] < 1
    for x, half in spots(sc):
        assert half <= min(x, 1 - x) * FRAME_W + 1e-6


def test_fitting_settles_even_when_the_floor_gets_in_the_way() -> None:
    """Three elephants cannot all fit: the floor stops the shrinking, and a second call must not shrink them again."""
    arch = {"e1": "elephant", "e2": "elephant", "e3": "elephant"}
    sc = scene(("e1", "left"), ("e2", "center"), ("e3", "right"))
    fit_scene(sc, arch, CATALOG)
    scales = [ly["scale"] for ly in sc["layers"]]
    assert all(s >= MIN_SCALE - 1e-9 for s in scales)
    assert fit_scene(sc, arch, CATALOG) == []
    assert [ly["scale"] for ly in sc["layers"]] == scales


def test_a_plan_that_is_already_small_is_not_shrunk_below_what_it_gave() -> None:
    sc = scene(("cow", "center"), ("dog", "right"), scale={"cow": 0.4, "dog": 0.3})
    fit_scene(sc, ARCH, CATALOG)
    assert sc["layers"][0].get("scale", 1.0) <= 0.4 and sc["layers"][1].get("scale", 1.0) <= 0.3
    assert sc["layers"][0].get("scale", 1.0) > 0.35, "no more than needed: it already nearly fit"


def test_a_swapped_in_picture_gets_room_too() -> None:
    """The stand-in is a body; the asset the user adds for it may be a cow: the swap makes room."""
    spec: dict[str, Any] = {
        "characters": [
            {"id": "mia", "archetype": "everyman"},
            {"id": "bob", "archetype": "everyman"},
        ],
        "scenes": [scene(("mia", "left"), ("bob", "right"))],
        "meta": {
            "library_gaps": [
                {"kind": "character", "name": "cow", "character": "mia", "scenes": ["s01"]}
            ]
        },
    }
    assert apply_library_gap(spec, spec["meta"]["library_gaps"][0], "cow", CATALOG)
    assert spec["characters"][0]["archetype"] == "cow"
    layers = spec["scenes"][0]["layers"]
    assert any(ly.get("scale", 1.0) < 1 or ly["position"].startswith("far_") for ly in layers)
