"""Generic contract tests every registered background template must pass."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from reel.core.catalog import CATALOG
from reel.core.ir import bbox
from reel.templates.base import PLANES, X0, X1, Y0, Y1, build_graph
from reel.templates.palette import MOODS, TIMES

NAMES = CATALOG.backgrounds.names()


@pytest.mark.parametrize("name", NAMES)
def test_template_metadata(name: str) -> None:
    d = CATALOG.backgrounds.get(name)
    assert d.summary, f"{name}: add a one-line summary (shown to the LLM)"
    assert 0.55 <= d.ground_y <= 0.9, (
        f"{name}: ground_y {d.ground_y} should leave room for captions (0.62-0.78 typical)"
    )
    for slot, (x, y) in d.slots.items():
        assert -0.2 <= x <= 1.2 and 0.3 <= y <= 1.0, f"{name}: slot {slot} {x, y} is off the set"
    assert d.parse({}).time_of_day == "day"  # every param has a default


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("tod", TIMES)
@pytest.mark.parametrize("mood", MOODS)
def test_template_builds_for_every_time_and_mood(name: str, tod: str, mood: str) -> None:
    d = CATALOG.backgrounds.get(name)
    g = build_graph(d, {"time_of_day": tod, "mood": mood, "density": 0.7}, seed=5, scene_id="t")
    n_static = sum(1 for p in g.planes.values() for s in p if s.anim is None)
    n_dyn = sum(1 for p in g.planes.values() for s in p if s.anim is not None)
    assert 3 <= n_static <= 1500, f"{name}: {n_static} static shapes"
    assert n_dyn <= 400, f"{name}: {n_dyn} animated shapes (keep it light, use particles)"
    assert set(g.planes) == set(PLANES)
    for plane, shapes in g.planes.items():
        for s in shapes:
            x0, y0, x1, y1 = bbox(s.geom)
            assert all(math.isfinite(v) for v in (x0, y0, x1, y1)), (
                f"{name}/{plane}: non-finite geometry"
            )
            if s.fill:
                g.scheme.base(s.fill)  # colour roles must exist
            if s.stroke:
                g.scheme.base(s.stroke)


@pytest.mark.parametrize("name", NAMES)
def test_template_is_deterministic_per_seed(name: str) -> None:
    d = CATALOG.backgrounds.get(name)

    def sig(seed: int) -> list[tuple[Any, ...]]:
        g = build_graph(d, {"density": 0.8}, seed=seed, scene_id="x")
        return [
            (pl, round(bbox(s.geom)[0], 3), round(bbox(s.geom)[1], 3), s.fill)
            for pl, ss in g.planes.items()
            for s in ss
        ]

    assert sig(1) == sig(1)


@pytest.mark.parametrize("name", NAMES)
def test_template_params_reject_garbage(name: str) -> None:
    d = CATALOG.backgrounds.get(name)
    with pytest.raises(ValidationError):
        d.parse({"definitely_not_a_param": 1})
    with pytest.raises(ValidationError):
        d.parse({"time_of_day": "teatime"})


@pytest.mark.render
@pytest.mark.parametrize("name", NAMES)
def test_template_renders_with_characters_in_every_style(name: str) -> None:
    from reel.core.render import Renderer, RenderOptions
    from reel.core.spec import ReelSpec

    for style in CATALOG.styles.names():
        spec = ReelSpec.model_validate(
            {
                "meta": {"title": "t", "style": style, "seed": 2},
                "characters": [
                    {"id": "a", "archetype": "everyman"},
                    {"id": "b", "archetype": "robot"},
                ],
                "scenes": [
                    {
                        "id": "s",
                        "duration_sec": 2,
                        "background": {
                            "template": name,
                            "params": {"time_of_day": "dusk", "density": 1.0},
                        },
                        "camera": {
                            "moves": [
                                {"type": "dolly", "from": 0, "to": 0.4, "t0": 0, "t1": 2},
                                {"type": "pan", "from": [0, 0], "to": [0.2, 0], "t0": 0, "t1": 2},
                            ]
                        },
                        "layers": [
                            {
                                "character": "a",
                                "position": "left",
                                "actions": [
                                    {"name": "walk", "t0": 0.2, "t1": 1.8, "params": {"dx": 0.2}}
                                ],
                            },
                            {
                                "character": "b",
                                "position": "right",
                                "actions": [{"name": "wave", "t0": 0.2, "t1": 1.8}],
                            },
                        ],
                    }
                ],
            }
        )
        r = Renderer(spec, RenderOptions(scale=0.25, cache=False))
        f = r.frame(30)
        assert f.shape == (480, 270, 4) and f.dtype == np.uint8
        assert f[..., :3].std() > 3, f"{name}/{style}: frame is blank"
        assert np.array_equal(
            f, Renderer(spec, RenderOptions(scale=0.25, cache=False)).frame(30)
        ), f"{name}/{style}: not deterministic"


def test_background_extents_cover_the_stage_for_camera_moves() -> None:
    """Every template must paint the whole stage (so a pan never reveals the void)."""
    from reel.core.render import Renderer, RenderOptions
    from reel.core.spec import ReelSpec

    for name in NAMES:
        spec = ReelSpec.model_validate(
            {
                "meta": {"title": "t", "style": "flat_vector"},
                "characters": [],
                "scenes": [
                    {
                        "id": "s",
                        "duration_sec": 1,
                        "background": {"template": name},
                        "camera": {
                            "moves": [
                                {
                                    "type": "pan",
                                    "from": [-0.45, -0.05],
                                    "to": [-0.45, -0.05],
                                    "t0": 0,
                                    "t1": 1,
                                }
                            ]
                        },
                    }
                ],
            }
        )
        f = Renderer(spec, RenderOptions(scale=0.2, cache=False)).frame(0)
        # panning hard left must not expose pure white (the canvas clear colour) at the right-hand edge
        edge = f[:, -6:, :3].reshape(-1, 3)
        assert not (edge > 250).all(), f"{name}: stage does not extend far enough for pans"
    _ = (X0, X1, Y0, Y1)
