from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from reel.core.spec import ReelSpec
from reel.core.stage import RenderConfig, SceneStage
from reel.styles.flat_vector import FlatVector


def make_stage(
    actions: list[dict[str, Any]] | None = None,
    camera: list[dict[str, Any]] | None = None,
    style: str = "flat_vector",
    pos: Any = (0.4, 0.74),
    scale: float = 0.2,
) -> SceneStage:
    spec = ReelSpec.model_validate(
        {
            "meta": {"title": "t", "style": style, "seed": 3},
            "characters": [{"id": "a", "archetype": "everyman"}],
            "scenes": [
                {
                    "id": "s",
                    "duration_sec": 4,
                    "background": {"template": "abstract"},
                    "camera": {"moves": camera or []},
                    "layers": [{"character": "a", "position": pos, "actions": actions or []}],
                }
            ],
        }
    )
    size = (round(1080 * scale), round(1920 * scale))
    return SceneStage(
        spec, 0, RenderConfig(size, 30, 3, style, False, FlatVector.fx, (0.1, 0.2, 0.07, 0.07))
    )


def test_render_returns_an_opaque_bgra_frame_of_the_configured_size() -> None:
    st = make_stage()
    f = st.render(10)
    assert f.shape == (384, 216, 4) and f.dtype == np.uint8 and (f[..., 3] == 255).all()


def test_signature_follows_what_changes_the_pixels() -> None:
    st = make_stage([{"name": "wave", "t0": 1.0, "t1": 3.0}])
    assert st.signature(5) == st.signature(5)
    assert st.signature(5) != st.signature(
        6
    )  # time is part of the key (dynamic backgrounds, blinks)
    other = make_stage([{"name": "wave", "t0": 2.0, "t1": 3.0}])
    assert st.signature(75) != other.signature(
        75
    )  # at t=2.5 s the two waves are in different phases
    assert st.signature(2) == other.signature(
        2
    )  # before either action starts: identical pixels, identical key


def test_signature_changes_with_the_camera() -> None:
    a = make_stage(
        camera=[{"type": "zoom", "from": 1.0, "to": 1.4, "t0": 0, "t1": 4, "ease": "linear"}]
    )
    b = make_stage()
    assert a.signature(60) != b.signature(60)


def test_static_scene_frames_are_stable_except_for_living_details() -> None:
    st = make_stage()
    f1, f2 = st.render(40), st.render(40)
    assert np.array_equal(f1, f2)  # pure function of the frame number


def test_characters_far_off_stage_are_culled_but_still_baked() -> None:
    st = make_stage(pos=[-1.4, 0.74])
    assert st.layers and st.render(3).std() > 1  # background still renders, no crash


def test_zoom_changes_the_image_and_plates_are_reused() -> None:
    st = make_stage(
        camera=[{"type": "zoom", "from": 1.0, "to": 1.5, "t0": 0, "t1": 4, "ease": "linear"}]
    )
    a, b = st.render(0), st.render(100)
    assert not np.array_equal(a, b)
    assert st.ov >= 1.5  # plates are rendered with enough resolution for the deepest zoom
    plates_before = dict(st._plates)
    st.render(50)
    assert all(st._plates[k] is v for k, v in plates_before.items())


def test_rack_focus_to_the_foreground_softens_the_characters_plane() -> None:
    def detail(
        f: np.ndarray,
    ) -> float:  # second-difference energy of the character region: blur kills it
        h, w = f.shape[:2]
        g = (
            f[int(h * 0.38) : int(h * 0.75), int(w * 0.15) : int(w * 0.6), :3]
            .astype(float)
            .mean(axis=2)
        )
        return float(np.abs(np.diff(g, 2, axis=1)).mean())

    base = make_stage(scale=0.25)
    racked = make_stage(
        camera=[{"type": "rack_focus", "from": "midground", "to": "foreground", "t0": 0, "t1": 1}],
        scale=0.25,
    )
    assert detail(racked.render(60)) < 0.7 * detail(
        base.render(60)
    )  # focus moved off the characters: they go soft


def test_overlay_and_captions_do_not_affect_the_world_signature() -> None:
    spec = ReelSpec.model_validate(
        {
            "meta": {"title": "t", "style": "flat_vector"},
            "characters": [{"id": "a", "archetype": "kid"}],
            "scenes": [
                {
                    "id": "s",
                    "duration_sec": 3,
                    "background": {"template": "abstract"},
                    "layers": [{"character": "a", "position": "left"}],
                    "captions": [{"text": "hi", "t0": 0, "t1": 1}],
                },
            ],
        }
    )
    cfg = RenderConfig(
        (108, 192), 30, 0, "flat_vector", False, FlatVector.fx, (0.1, 0.2, 0.07, 0.07)
    )
    a = SceneStage(spec, 0, cfg)
    data = spec.model_dump(by_alias=True)
    data["scenes"][0]["captions"][0]["text"] = "something else"
    b = SceneStage(ReelSpec.model_validate(data), 0, cfg)
    assert a.signature(10) == b.signature(10)  # captions are drawn after the cached stage
    assert a.caption_key(10) != b.caption_key(10)


@pytest.mark.parametrize("style", ["flat_vector", "paper_cutout", "stickman"])
def test_each_style_renders_the_same_scene(style: str) -> None:
    from reel.core.catalog import CATALOG

    st = make_stage([{"name": "wave", "t0": 0.2, "t1": 3.0}], style=style)
    st.cfg = RenderConfig(
        st.cfg.size, 30, 3, style, False, CATALOG.styles.get(style).fx, st.cfg.safe
    )
    assert st.render(30)[..., :3].std() > 3
