from __future__ import annotations

import math

import pytest

from reel.core.catalog import CATALOG
from reel.core.figure import build_figure
from reel.core.ir import bbox
from reel.core.rig import default_pose, solve

#: bodies (jointed, drawn from a skeleton); a character made from library art is one picture and is tested in test_assets_*
ARCHS = [n for n in CATALOG.archetypes.names() if CATALOG.archetypes.get(n).category != "sprite"]
PROPS = CATALOG.props.names()


def build(
    arch: str = "everyman",
    props: tuple[str, ...] = (),
    weights: dict[str, float] | None = None,
    **pose: float,
):
    a = CATALOG.archetypes.get(arch)
    p = default_pose() | a.rest | pose
    fig = solve(a.dims, p)
    pl = [(CATALOG.props.get(n), (weights or {}).get(n, 1.0)) for n in props]
    return build_figure(fig, a, a.palette, pl, 0.5), fig


@pytest.mark.parametrize("arch", ARCHS)
def test_every_archetype_builds_finite_ordered_shapes(arch: str) -> None:
    b, _ = build(arch, props=("hat", "glasses", "scarf", "backpack", "briefcase", "umbrella"))
    zs = [s.z for s in b.shapes]
    assert zs == sorted(zs) and len(set(zs)) == len(zs)  # strict back-to-front order
    for s in b.shapes:
        assert all(math.isfinite(v) for v in bbox(s.geom)), s.tag
    tags = {s.tag for s in b.shapes}
    assert {"head", "torso", "limb", "eye", "mouth", "hand", "foot"} <= tags
    assert (
        ("visor" in tags) if arch == "robot" else ("pupil" in tags)
    )  # robots have a visor instead of pupils


@pytest.mark.parametrize("arch", ARCHS)
@pytest.mark.parametrize(
    "expr",
    [
        {},
        {"mouth_open": 1.0, "mouth_wide": 1.0, "eye_open": 1.4},
        {"blink": 1.0, "mouth_smile": 1.0},
        {"tear": 1.0, "sweat": 1.0, "blush": 1.0, "brow_angle": 1.0},
    ],
)
def test_expressions_never_break_the_face(arch: str, expr: dict[str, float]) -> None:
    b, _ = build(arch, **expr)
    assert any(s.tag == "mouth" for s in b.shapes)
    assert all(all(math.isfinite(v) for v in bbox(s.geom)) for s in b.shapes)


def test_expression_channels_change_the_drawn_face() -> None:
    calm, _ = build()
    open_, _ = build(mouth_open=1.0)
    assert any(s.tag == "teeth" or s.tag == "tongue" for s in open_.shapes)
    assert not any(s.tag in ("teeth", "tongue") for s in calm.shapes)
    closed, _ = build(blink=1.0)
    assert any(s.tag == "lid" for s in closed.shapes) and not any(
        s.tag == "pupil" for s in closed.shapes
    )
    crying, _ = build(tear=1.0)
    assert any(s.tag == "tear" for s in crying.shapes)
    thinking, _ = build(fx_think=1.0)
    assert any(s.tag == "thought" for s in thinking.shapes)
    shocked, _ = build(fx_surprise=1.0)
    assert any(s.tag == "exclaim" for s in shocked.shapes)


def test_held_prop_follows_the_hand_and_floor_prop_waits_in_front_of_the_feet() -> None:
    held, fig = build(props=("briefcase",), arm_r_sh=90.0, arm_r_el=30.0)
    hx, hy = fig.pts["hand_r"]
    prop = next(s for s in held.shapes if s.tag == "prop")
    px0, py0, px1, _py1 = bbox(prop.geom)
    assert abs((px0 + px1) / 2 - hx) < 120 and py0 > hy - 40  # the case hangs from the hand

    floor, _fig2 = build(props=("briefcase",), weights={"briefcase": 0.0})
    prop = next(s for s in floor.shapes if s.tag == "prop")
    x0, _y0, _x1, y1 = bbox(prop.geom)
    assert (
        y1 == pytest.approx(0.0, abs=2.0) and x0 > 0
    )  # sitting on the ground, in front of the feet


def test_worn_props_sit_on_the_head() -> None:
    b, fig = build(props=("hat",))
    hat = [s for s in b.shapes if s.tag == "prop"]
    hc = fig.pts["head_c"]
    assert hat and all(bbox(s.geom)[3] < hc[1] + 10 for s in hat)  # above (or at) the head centre


def test_shadow_info_widens_when_lying_and_fades_with_height() -> None:
    stand, _ = build()
    lying, _ = build(rot_hip=-88.0, bob=-120.0, ground_lock=0.0)
    assert lying.shadow.rx > stand.shadow.rx * 1.5
    high, _ = build(lift=400.0)
    assert high.shadow.strength < stand.shadow.strength


def test_mirror_free_sway_moves_capes_and_scarves_behind() -> None:
    still, _ = build("hero")
    moving, _ = build("hero", sway=-1.5)
    cape_still = next(s for s in still.shapes if s.tag == "cape")
    cape_move = next(s for s in moving.shapes if s.tag == "cape")
    assert (
        bbox(cape_move.geom)[0] < bbox(cape_still.geom)[0]
    )  # trails to -x (behind a figure facing +x)
