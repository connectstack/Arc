from __future__ import annotations

import pytest

from reel.core.fx import FxConfig
from reel.core.matchcut import match_cut
from reel.core.planner import CHAR_UNIT
from reel.core.spec import ReelSpec
from reel.core.stage import RenderConfig, SceneStage


def two_scenes() -> ReelSpec:
    return ReelSpec.model_validate(
        {
            "meta": {"title": "t", "style": "flat_vector"},
            "characters": [{"id": "mia", "archetype": "kid"}],
            "scenes": [
                {
                    "id": "a",
                    "duration_sec": 4,
                    "background": {"template": "abstract"},
                    "camera": {
                        "moves": [{"type": "zoom", "from": 1.0, "to": 1.3, "t0": 0, "t1": 4}]
                    },
                    "layers": [
                        {
                            "character": "mia",
                            "position": [0.25, 0.70],
                            "scale": 1.0,
                            "actions": [
                                {"name": "walk", "t0": 0.5, "t1": 3.5, "params": {"dx": 0.4}}
                            ],
                        }
                    ],
                    "transition_out": {"type": "crossfade", "duration": 0.6},
                },
                {
                    "id": "b",
                    "duration_sec": 4,
                    "background": {"template": "abstract", "params": {"time_of_day": "dusk"}},
                    "layers": [{"character": "mia", "position": [0.8, 0.8], "scale": 2.0}],
                },
            ],
        }
    )


def test_incoming_scene_opens_where_the_character_ended() -> None:
    res = match_cut(two_scenes(), "a", "b", "mia")
    b = res.spec.scenes[1].layers[0]
    assert b.position == pytest.approx([0.65, 0.70], abs=0.02)  # walked 0.4 from x=0.25
    assert (
        res.spec.scenes[0].transition_out.type == "cut"
        and res.spec.scenes[0].transition_out.duration == 0
    )
    assert any("camera" in n for n in res.notes)  # the 1.3x zoom carries across the cut
    first = res.spec.scenes[1].camera.moves[0]
    assert first.type in ("pan", "zoom")


def test_on_screen_size_is_preserved_across_different_perspective() -> None:
    spec = two_scenes()
    data = spec.model_dump(by_alias=True)
    data["scenes"][1]["background"] = {"template": "abstract"}
    res = match_cut(ReelSpec.model_validate(data), 0, 1, "mia")
    cfg = RenderConfig((54, 96), 30, 0, "flat_vector", True, FxConfig(), (0.1, 0.2, 0.07, 0.07))
    sa = SceneStage(res.spec, 0, cfg)
    sb = SceneStage(res.spec, 1, cfg)
    la, lb = sa.layers[0], sb.layers[0]
    x, y = la.baked.root(la.baked.n - 1)
    size_a = la.plan.px_scale_at(y)
    size_b = lb.plan.px_scale_at(lb.baked.root(0)[1])
    assert size_b == pytest.approx(size_a, rel=0.02)
    assert (x, y) == pytest.approx(lb.baked.root(0), abs=0.002)
    _ = CHAR_UNIT


def test_only_consecutive_scenes_and_known_characters() -> None:
    spec = two_scenes()
    with pytest.raises(ValueError):
        match_cut(spec, "b", "a", "mia")
    with pytest.raises(KeyError):
        match_cut(spec, "a", "b", "nobody")
    with pytest.raises(KeyError):
        match_cut(spec, "zzz", "b", "mia")
