from __future__ import annotations

import numpy as np
import pytest

from reel.core.camera import (
    BLEED_X,
    BLEED_Y,
    DOLLY_RANGE,
    PAN_LIMIT,
    PLANES,
    ZOOM_RANGE,
    Camera,
    CameraState,
    clamp_move_values,
    max_plane_scale,
    plane_xform,
)
from reel.core.catalog import CATALOG
from reel.core.spec import CameraMoveSpec


def mv(
    kind: str, f: object, t: object, t0: float = 0.0, t1: float = 2.0, ease: str = "linear"
) -> CameraMoveSpec:
    return CameraMoveSpec.model_validate(
        {"type": kind, "from": f, "to": t, "t0": t0, "t1": t1, "ease": ease}
    )


def test_pan_interpolates_and_holds_its_end_value() -> None:
    cam = Camera([mv("pan", [0, 0], [0.2, -0.1])], seed=1)
    assert cam.state(0.0).pan_x == 0
    assert cam.state(1.0).pan_x == pytest.approx(0.1) and cam.state(1.0).pan_y == pytest.approx(
        -0.05
    )
    assert cam.state(5.0).pan_x == pytest.approx(0.2)  # holds after the move ends


def test_moves_have_no_effect_before_they_start() -> None:
    cam = Camera([mv("zoom", 1.0, 1.5, t0=1.0, t1=2.0)], seed=1)
    assert cam.state(0.5).zoom == 1.0
    assert cam.state(1.5).zoom == pytest.approx(1.25)


def test_sequential_moves_compose() -> None:
    cam = Camera(
        [mv("zoom", 1.0, 1.2, 0, 1), mv("zoom", None, 1.5, 1, 2)], seed=1
    )  # second starts from where the first ended
    assert cam.state(1.0).zoom == pytest.approx(1.2)
    assert cam.state(2.0).zoom == pytest.approx(1.5)


def test_parallax_far_layers_move_less_than_near_ones() -> None:
    cam = Camera([mv("pan", [0, 0], [0.2, 0])], seed=1)
    st = cam.state(2.0)
    shifts = {name: abs(plane_xform(st, name, 1080, 1920, 0.0).tx) for name, *_ in PLANES}
    assert shifts["sky"] < shifts["far"] < shifts["back"] < shifts["mid"] < shifts["near"]


def test_dolly_grows_near_layers_faster() -> None:
    st = Camera([mv("dolly", 0, 1.0)], seed=1).state(2.0)
    s = {name: plane_xform(st, name, 1080, 1920, 0.0).scale for name, *_ in PLANES}
    assert s["sky"] < s["far"] < s["mid"] < s["near"]
    assert s["mid"] == pytest.approx(1.35)


def test_shake_is_windowed_deterministic_and_smooth() -> None:
    cam = Camera([mv("shake", 0.0, 1.0, 1.0, 3.0)], seed=9)
    assert cam.state(0.5).shake_x == 0 and cam.state(3.5).shake_x == 0
    xs = np.array([cam.state(1.0 + i / 30).shake_x for i in range(60)])
    assert np.abs(xs).max() > 1e-4  # something happens
    assert (
        np.abs(np.diff(xs)).max() < 0.5 * np.abs(xs).max() + 1e-6
    )  # band-limited, not white noise
    again = Camera([mv("shake", 0.0, 1.0, 1.0, 3.0)], seed=9)
    assert again.state(1.7).shake_x == cam.state(1.7).shake_x
    assert (
        Camera([mv("shake", 0.0, 1.0, 1.0, 3.0)], seed=10).state(1.7).shake_x
        != cam.state(1.7).shake_x
    )


def test_handheld_base_shake_applies_all_the_time() -> None:
    cam = Camera([], seed=3, base_shake=0.4)
    assert any(abs(cam.state(t).shake_x) > 0 for t in (0.3, 0.9, 1.7))


def test_rack_focus_blurs_planes_away_from_the_focus() -> None:
    cam = Camera([mv("rack_focus", "midground", "foreground", 0, 2, "linear")], seed=1)
    st0, st1 = cam.state(0.0), cam.state(2.0)
    blur0 = {n: plane_xform(st0, n, 1080, 1920, 0.0).blur for n, *_ in PLANES}
    blur1 = {n: plane_xform(st1, n, 1080, 1920, 0.0).blur for n, *_ in PLANES}
    assert blur0["mid"] == 0 and blur0["far"] > 0  # focus on mid: background soft
    assert (
        blur1["near"] == 0 and blur1["mid"] > 0 and blur1["sky"] > blur1["mid"]
    )  # focus pulled to the foreground
    plain = plane_xform(Camera([], seed=1).state(0.0), "far", 1080, 1920, 0.0)
    assert plain.blur == 0  # no rack focus and no DoF => everything sharp


def test_rack_focus_does_not_snap_when_the_move_starts() -> None:
    """The lens already sits on the move's `from` before `t0`, so focus is continuous in time."""
    cam = Camera([mv("rack_focus", "background", "foreground", 0.5, 2.5, "linear")], seed=1)
    ts = np.arange(0.0, 3.0, 1 / 30)
    focus = np.array([cam.state(float(t)).focus for t in ts])
    assert focus[0] == pytest.approx(0.0)  # on the background from the first frame
    assert (
        np.abs(np.diff(focus)).max() < 0.1
    )  # no jump anywhere (a linear 2-unit move over 60 frames)
    assert focus[-1] == pytest.approx(2.0)
    # only the first rack move gets the pre-hold; a later move continues from where the first ended
    two = Camera(
        [
            mv("rack_focus", "background", "midground", 0.0, 1.0),
            mv("rack_focus", None, "foreground", 2.0, 3.0),
        ],
        seed=1,
    )
    assert two.state(1.5).focus == pytest.approx(1.0)


def test_rack_focus_can_target_a_character_depth() -> None:
    cam = Camera(
        [mv("rack_focus", None, "bob", 0, 1)],
        seed=1,
        focus_of=lambda cid: 2.0 if cid == "bob" else None,
    )
    assert cam.state(1.0).focus == pytest.approx(2.0)


def test_dof_strength_blurs_without_a_rack_move() -> None:
    st = Camera([], seed=1).state(0.0)
    assert plane_xform(st, "far", 1080, 1920, 0.5).blur > 0
    assert plane_xform(st, "mid", 1080, 1920, 0.5).blur == 0


def test_max_plane_scale_covers_zoom_and_dolly() -> None:
    assert max_plane_scale(Camera([], seed=1), 3.0, 30) == 1.0
    assert max_plane_scale(Camera([mv("zoom", 1.0, 1.5)], seed=1), 3.0, 30) >= 1.5
    assert (
        max_plane_scale(Camera([mv("dolly", 0, 1.0)], seed=1), 3.0, 30) > 1.7
    )  # near plane grows 1 + 0.35*2.2


def test_registered_moves_and_their_value_checks() -> None:
    assert {"pan", "zoom", "shake", "dolly", "rack_focus"} <= set(CATALOG.camera_moves.names())
    assert CATALOG.camera_moves.get("pan").check({"from": [0, 0], "to": [0.1, 0]}) == []
    assert CATALOG.camera_moves.get("pan").check({"from": 3, "to": [0, 0]})  # `from` must be [x, y]
    assert CATALOG.camera_moves.get("zoom").check({"from": 1, "to": 9})  # absurd zoom
    assert CATALOG.camera_moves.get("rack_focus").check({"to": "foreground"}) == []


# ---------------------------------------------------------------- the camera never leaves the set
def _visible(
    st: CameraState, plane: str, w: float = 1080.0, h: float = 1920.0
) -> tuple[float, ...]:
    """The part of the world (design px) that the frame shows of one plane: (x0, x1, y0, y1)."""
    xf = plane_xform(st, plane, w, h, 0.0)
    cx, cy = w / 2, h / 2
    return (
        cx - (cx + xf.tx) / xf.scale,
        cx + (cx - xf.tx) / xf.scale,
        cy - (cy + xf.ty) / xf.scale,
        cy + (cy - xf.ty) / xf.scale,
    )


@pytest.mark.parametrize(
    "state",
    [
        CameraState(
            pan_x=0.8, pan_y=0.5
        ),  # what a model writes when it takes a pan for a screen position
        CameraState(pan_x=-3.0, pan_y=2.0),
        CameraState(zoom=0.1),
        CameraState(dolly=-5.0),
        CameraState(pan_x=0.4, pan_y=-0.3, zoom=0.9, shake_x=0.05, shake_y=-0.05),
    ],
)
def test_no_plane_ever_shows_beyond_the_set(state: CameraState) -> None:
    """Regression: a pan of [0.8, 0.5] carried the whole set out of the frame and left a white band at the bottom."""
    for name, *_ in PLANES:
        x0, x1, y0, y1 = _visible(state, name)
        assert x0 >= -BLEED_X * 1080 - 1e-6 and x1 <= (1 + BLEED_X) * 1080 + 1e-6, name
        assert y0 >= -BLEED_Y * 1920 - 1e-6 and y1 <= (1 + BLEED_Y) * 1920 + 1e-6, name


def test_the_camera_itself_stays_inside_the_limits_whatever_the_spec_says() -> None:
    """A project saved before the limits existed (a pan of [0.2, 0.5] -> [0.8, 0.5]) still previews sanely: the move is taken at its limit."""
    cam = Camera(
        [
            mv("pan", [0.2, 0.5], [0.8, 0.5]),
            mv("zoom", 1.0, 9.0, t0=0.0, t1=2.0),
            mv("dolly", 0.0, -4.0, t0=0.0, t1=2.0),
        ],
        seed=1,
    )
    for t in (0.0, 0.5, 1.0, 2.0, 5.0):
        st = cam.state(t)
        assert abs(st.pan_x) <= PAN_LIMIT[0] and abs(st.pan_y) <= PAN_LIMIT[1]
        assert (
            ZOOM_RANGE[0] <= st.zoom <= ZOOM_RANGE[1]
            and DOLLY_RANGE[0] <= st.dolly <= DOLLY_RANGE[1]
        )
    end = cam.state(2.0)
    assert end.pan_x == PAN_LIMIT[0] and end.pan_y == PAN_LIMIT[1]
    assert end.zoom == ZOOM_RANGE[1] and end.dolly == DOLLY_RANGE[0]


def test_the_bleed_matches_the_set_the_templates_draw() -> None:
    from reel.templates.base import X0, X1, Y0, Y1

    assert (BLEED_X * 1080, BLEED_Y * 1920) == (-X0, -Y0)
    assert (X1 - 1080, Y1 - 1920) == (-X0, -Y0)


def test_ordinary_moves_are_untouched_by_the_limit() -> None:
    """A drift, a push-in and a handheld shake stay far inside the set: the limit only bites on absurd values."""
    for st in (
        CameraState(pan_x=0.12, pan_y=0.02),
        CameraState(zoom=1.4, pan_x=-0.1),
        CameraState(dolly=1.0, shake_x=0.01),
    ):
        for name, par, _dk, _d in PLANES:
            xf = plane_xform(st, name, 1080.0, 1920.0, 0.0)
            assert xf.tx == pytest.approx(
                -(st.pan_x * par) * 1080 + st.shake_x * (0.55 + 0.45 * par) * 1080
            )


def test_a_pan_written_as_screen_positions_becomes_a_gentle_drift() -> None:
    move: dict[str, object] = {"type": "pan", "from": [0.2, 0.5], "to": [0.8, 0.5]}
    notes = clamp_move_values(move)
    assert move["from"] == [-0.15, 0.0] and move["to"] == [0.15, 0.0] and notes
    again = clamp_move_values(move)
    assert again == []  # idempotent: what is already fine is left alone


def test_clamping_leaves_a_legitimate_move_alone_and_reins_in_the_rest() -> None:
    fine = {"type": "pan", "from": [0.0, 0.0], "to": [0.06, 0.0]}
    assert clamp_move_values(fine) == [] and fine["to"] == [0.06, 0.0]
    wide = {
        "type": "pan",
        "from": [0.12, 0],
        "to": [0.9, 0],
    }  # a zero among the numbers: an offset, not a position
    assert clamp_move_values(wide) and wide["to"] == [0.25, 0.0]
    zoom = {"type": "zoom", "from": 0.2, "to": 7}
    assert clamp_move_values(zoom) and zoom == {"type": "zoom", "from": 0.85, "to": 2.5}
    junk = {"type": "dolly", "from": "far", "to": 0.5}
    assert clamp_move_values(junk) and "from" not in junk
    focus = {"type": "rack_focus", "to": "foreground"}
    assert clamp_move_values(focus) == []


def test_the_lint_refuses_a_pan_that_leaves_the_set() -> None:
    pan = CATALOG.camera_moves.get("pan")
    assert pan.check({"from": [0.5, 0.5], "to": [0.5, 0.6]})
    assert pan.check({"from": [0, 0], "to": [0.06, 0.02]}) == []
    assert CATALOG.camera_moves.get("zoom").check(
        {"from": 1, "to": 0.5}
    )  # pulled back past the edge of the set
    assert CATALOG.camera_moves.get("dolly").check({"from": 0, "to": -1})
