from __future__ import annotations

import numpy as np
import pytest

from reel.core.animation import (
    Clip,
    Keyframes,
    LayerPlan,
    Oscillator,
    PlacedAction,
    Sampled,
    apply_clip,
    bake_layer,
    spring_follow,
)
from reel.core.catalog import CATALOG
from reel.core.planner import CHAR_UNIT, SimpleWorld
from reel.core.registry import UnknownEntryError
from reel.core.rig import CHANNELS, Rig

FPS = 30


def make_plan(
    position: tuple[float, float] = (0.4, 0.8), props: tuple[str, ...] = ()
) -> tuple[LayerPlan, SimpleWorld]:
    from reel.core.animation import CharacterInfo

    world = SimpleWorld()
    arch = CATALOG.archetypes.get("everyman")
    info = CharacterInfo("c", "everyman", props, tuple(CATALOG.props.get(p) for p in props))
    plan = LayerPlan(info, Rig(arch), position, lambda y: CHAR_UNIT * world.depth_scale(y), seed=5)
    return plan, world


def place(plan: LayerPlan, name: str, t0: float, t1: float, **params: object) -> None:
    d = CATALOG.actions.get(name)
    plan.actions.append(
        PlacedAction(len(plan.actions), name, t0, t1, d.parse(params), d.fn, d.moves_root)
    )


# ------------------------------------------------------------------ curves
def test_keyframes_hit_their_keys_and_hold_outside() -> None:
    k = Keyframes.build([(0.0, 0.0), (1.0, 10.0), (2.0, 4.0)], "linear")
    t = np.array([-1.0, 0.0, 0.5, 1.0, 1.5, 2.0, 5.0])
    np.testing.assert_allclose(k.sample(t), [0, 0, 5, 10, 7, 4, 4])


def test_keyframe_easings_overshoot_and_hold() -> None:
    k = Keyframes.build([(0, 0), (1, 100, "overshoot")])
    s = k.sample(np.linspace(0, 1, 101))
    assert s.max() > 100 and s[-1] == pytest.approx(100)  # overshoots then settles on the key
    h = Keyframes.build([(0, 0), (1, 100, "hold"), (2, 100)]).sample(np.array([0.5, 0.99, 1.01]))
    assert h[0] == 0 and h[1] == 0 and h[2] == pytest.approx(100, abs=1)


def test_instant_jump_keys_do_not_break() -> None:
    k = Keyframes.build([(0, 0), (1, 0), (1, 5), (2, 5)], "linear")  # duplicate time
    assert np.isfinite(k.sample(np.linspace(0, 2, 50))).all()


def test_oscillator_is_windowed() -> None:
    o = Oscillator(amp=2.0, freq=1.0, t0=1.0, t1=3.0, fade=0.2)
    s = o.sample(np.array([0.5, 2.0, 2.25, 4.0]))
    assert s[0] == 0 and s[3] == 0 and abs(s[1]) < 2.0 + 1e-9


def test_spring_follow_overshoots_a_step() -> None:
    step = np.concatenate([np.zeros(10), np.ones(90)])
    y = spring_follow(step, 3.0, 0.3, FPS)
    assert y.max() > 1.05 and abs(y[-1] - 1) < 0.1  # follow-through, then settles


# ------------------------------------------------------------------ clips
def test_clip_rejects_unknown_channel_with_suggestion() -> None:
    c = Clip(1.0)
    with pytest.raises(UnknownEntryError) as ei:
        c.key("arm_r_shoulder", [(0, 0), (1, 1)])
    assert "arm_r_sh" in str(ei.value) or "arm_r_sh" in ei.value.suggestions


def test_clip_second_base_curve_on_same_channel_is_an_error() -> None:
    c = Clip(1.0)
    c.key("torso_lean", [(0, 0), (1, 5)])
    with pytest.raises(ValueError):
        c.key("torso_lean", [(0, 0), (1, 9)])
    c.key("torso_lean", [(0, 0), (1, 9)], mode="add")  # but layering is fine


def test_apply_clip_blends_in_and_out() -> None:
    n = 90
    t = np.arange(n) / FPS
    arr = {"torso_lean": np.zeros(n)}
    c = Clip(1.0, blend_in=0.2, blend_out=0.2)
    c.hold("torso_lean", 10.0)
    apply_clip(arr, c, t, 1.0, 2.0, fps=FPS)
    v = arr["torso_lean"]
    assert v[0] == 0 and v[89] == pytest.approx(0, abs=1e-6)  # untouched outside the window
    assert v[45] == pytest.approx(10.0)  # full strength mid-window
    assert 0 < v[32] < 10  # ramping in


def test_persistent_channels_stay_and_pre_value_applies_before() -> None:
    n = 120
    t = np.arange(n) / FPS
    arr = {"face": np.ones(n), "prop:hat": np.ones(n)}
    c = Clip(1.0, blend_in=0.0)
    c.key("face", [(0, 1), (0.1, -1)], persist=True)
    c.prop("hat", [(0, 0), (0.5, 0), (0.51, 1)], before=0.0)
    touched: set[str] = set()
    apply_clip(arr, c, t, 2.0, 3.0, fps=FPS, touched=touched)
    assert arr["face"][-1] == -1.0 and arr["face"][0] == 1.0
    assert arr["prop:hat"][0] == 0.0 and arr["prop:hat"][-1] == 1.0  # on the floor, then carried


# ------------------------------------------------------------------ baking
def test_bake_is_deterministic_and_finite() -> None:
    def run() -> np.ndarray:
        plan, world = make_plan()
        place(plan, "walk", 0.5, 3.0, to=[0.7, 0.8])
        place(plan, "wave", 3.0, 5.0)
        return bake_layer(plan, world, fps=FPS, duration=6.0).matrix

    a, b = run(), run()
    assert np.array_equal(a, b)
    assert np.isfinite(a).all()


def test_idle_baseline_keeps_a_still_character_alive() -> None:
    plan, world = make_plan()
    baked = bake_layer(plan, world, fps=FPS, duration=8.0)
    assert baked.channel("breath").std() > 0.1
    assert baked.channel("blink").max() > 0.9  # blinks happened
    assert baked.channel("eye_dx").std() > 0.02


def test_walk_chains_root_motion_and_faces_travel_direction() -> None:
    plan, world = make_plan(position=(0.3, 0.8))
    place(plan, "walk", 1.0, 3.0, to=[0.7, 0.8])
    place(plan, "walk", 3.5, 5.5, to=[0.2, 0.8])  # starts from where the first one ended
    b = bake_layer(plan, world, fps=FPS, duration=6.0)
    assert b.root_x[0] == pytest.approx(0.3) and b.root_x[int(1.0 * FPS) - 1] == pytest.approx(
        0.3, abs=0.01
    )
    assert b.root_x[int(3.2 * FPS)] == pytest.approx(0.7, abs=0.01)
    assert b.root_x[-1] == pytest.approx(0.2, abs=0.01)
    face = b.channel("face")
    assert (
        face[int(2.0 * FPS)] > 0.9 and face[int(5.0 * FPS)] < -0.9
    )  # turned round for the return walk


def test_enter_from_is_offscreen_until_it_starts_and_exit_to_leaves_for_good() -> None:
    plan, world = make_plan(position=(0.5, 0.8))
    place(plan, "enter_from", 1.0, 3.0, side="left")
    b = bake_layer(plan, world, fps=FPS, duration=4.0)
    assert b.root_x[0] < -0.4 and b.root_x[int(0.9 * FPS)] < -0.4
    assert b.root_x[-1] == pytest.approx(0.5, abs=0.01)

    plan, world = make_plan(position=(0.5, 0.8))
    place(plan, "exit_to", 1.0, 3.0, side="right")
    b = bake_layer(plan, world, fps=FPS, duration=5.0)
    assert b.root_x[-1] > 1.4  # stays off-screen afterwards


def test_drop_in_starts_high_above_the_frame() -> None:
    plan, world = make_plan()
    place(plan, "enter_from", 0.5, 2.0, style="drop")
    b = bake_layer(plan, world, fps=FPS, duration=3.0)
    assert b.channel("lift")[0] > 1500 and b.channel("lift")[-1] == pytest.approx(0, abs=1e-6)


def test_pick_up_moves_the_prop_from_floor_to_hand() -> None:
    plan, world = make_plan(props=("briefcase",))
    place(plan, "pick_up", 1.0, 2.8, prop="briefcase")
    b = bake_layer(plan, world, fps=FPS, duration=4.0)
    ch = b.channel("prop:briefcase")
    assert ch[0] == 0.0 and ch[int(1.5 * FPS)] == 0.0  # lying on the floor
    assert ch[-1] == 1.0  # in the hand afterwards


def test_fall_stays_down_unless_get_up() -> None:
    plan, world = make_plan()
    place(plan, "fall", 0.5, 2.1)
    stays = bake_layer(plan, world, fps=FPS, duration=4.0)
    assert abs(stays.channel("rot_hip")[-1]) > 80

    plan, world = make_plan()
    place(plan, "fall", 0.5, 2.1, get_up=True)
    gets_up = bake_layer(plan, world, fps=FPS, duration=4.0)
    assert abs(gets_up.channel("rot_hip")[-1]) < 5


def test_secondary_motion_exists_and_is_bounded() -> None:
    plan, world = make_plan()
    place(plan, "wave", 0.5, 2.5)
    place(plan, "walk", 3.0, 5.0, to=[0.8, 0.8])
    b = bake_layer(plan, world, fps=FPS, duration=6.0)
    assert np.abs(b.channel("hand_lag_r")).max() > 1.0  # the hand trails the swinging forearm
    assert np.abs(b.channel("sway")).max() > 0.2  # hair/cloth react to walking
    for name, spec in CHANNELS.items():
        v = b.channel(name)
        assert v.min() >= spec.lo - 1e-9 and v.max() <= spec.hi + 1e-9, name


def test_actions_past_the_scene_end_are_cut_off_safely() -> None:
    plan, world = make_plan()
    place(plan, "wave", 1.5, 6.0)  # scene only lasts 2.5 s
    b = bake_layer(plan, world, fps=FPS, duration=2.5)
    assert b.n == 75 and np.isfinite(b.matrix).all()


def test_stop_motion_quantisation_picks_earlier_frame() -> None:
    plan, world = make_plan()
    place(plan, "wave", 0.0, 2.0)
    b = bake_layer(plan, world, fps=FPS, duration=2.0)
    assert b.frame_index(0.51, quantize_fps=12) == b.frame_index(0.5, quantize_fps=12)
    assert b.frame_index(0.51) >= b.frame_index(0.51, quantize_fps=12)


def test_signature_changes_with_pose_only() -> None:
    plan, world = make_plan()
    place(plan, "wave", 1.0, 3.0)
    b = bake_layer(plan, world, fps=FPS, duration=4.0)
    assert b.signature_bytes(10) == b.signature_bytes(10)
    assert b.signature_bytes(10) != b.signature_bytes(60)


def test_sampled_curve_interpolates() -> None:
    s = Sampled(0.0, 0.5, np.array([0.0, 1.0, 0.0]))
    np.testing.assert_allclose(s.sample(np.array([0.25, 0.5, 0.75])), [0.5, 1.0, 0.5])
