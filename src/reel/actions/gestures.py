"""Arm and head gestures: wave, point, pick_up, look_at."""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from pydantic import Field

from reel.actions.base import ParamsBase, register_action
from reel.core.animation import ActionContext, Clip
from reel.core.archetypes import PICKUP_FORWARD, PICKUP_REACH

TargetT = list[float] | str | None


def _side(s: str) -> str:
    return "r" if s.startswith("r") else "l"


# ============================================================================== wave
class WaveParams(ParamsBase):
    hand: Literal["auto", "right", "left", "both"] = Field(
        "auto", description="which hand waves (auto = the right hand unless it is holding a prop)"
    )
    speed: float = Field(1.0, ge=0.4, le=2.5, description="wave tempo multiplier")
    energy: float = Field(1.0, ge=0.3, le=1.6, description="how big the wave is")


@register_action(
    "wave",
    params_schema=WaveParams,
    category="gesture",
    summary="Raise a hand beside the head and wave hello",
    min_duration=0.8,
    default_duration=2.0,
)
def wave(ctx: ActionContext, p: WaveParams) -> Clip:
    D = ctx.duration
    c = ctx.clip(blend_in=0.14, blend_out=0.25)
    up = min(0.38, 0.3 * D)
    down = min(0.40, 0.3 * D)
    pose = ctx.pose(torso_lean=-2, head_tilt=4)
    hc = ctx.rig.landmark(pose, "head_c")
    d = ctx.dims
    sides = (
        ["r", "l"] if p.hand == "both" else [ctx.free_hand() if p.hand == "auto" else _side(p.hand)]
    )
    freq = 2.5 * p.speed
    for i, s in enumerate(sides):
        sgn = 1 if s == "r" else -1
        tgt = (hc[0] + sgn * (d.head_rx + 40), hc[1] - 0.12 * d.head_ry)
        ik = ctx.rig.reach(pose, s, tgt)
        rest_sh = 5.0 * sgn
        c.key(
            f"arm_{s}_sh",
            [
                (0, rest_sh),
                (0.07 * D, rest_sh - 9 * sgn),
                (up, ik[f"arm_{s}_sh"], "overshoot"),
                (D - down, ik[f"arm_{s}_sh"]),
                (D, rest_sh, "ease_in_out"),
            ],
        )
        c.key(
            f"arm_{s}_el",
            [(0, 10), (up, ik[f"arm_{s}_el"], "overshoot"), (D - down, ik[f"arm_{s}_el"]), (D, 10)],
        )
        c.osc(
            f"arm_{s}_el",
            amp=24 * p.energy,
            freq=freq,
            t0=up,
            t1=D - down,
            phase=0.6 * i,
            fade=0.15,
        )
        c.osc(
            f"hand_{s}_wr",
            amp=20 * p.energy,
            freq=freq,
            t0=up,
            t1=D - down,
            phase=0.9 + 0.6 * i,
            fade=0.15,
        )
        c.osc(f"arm_{s}_sh", amp=4 * p.energy, freq=freq * 0.5, t0=up, t1=D - down, fade=0.2)
    c.key("head_tilt", [(0, 0), (up, 5), (D - down, 5), (D, 0)])
    c.key("torso_lean", [(0, 0), (up, -2), (D - down, -2), (D, 0)])
    c.key("mouth_smile", [(0, 0.3), (up, 0.95), (D - down, 0.95), (D, 0.3)])
    c.key("brow_raise", [(0, 0), (up, 0.35), (D - down, 0.35), (D, 0)])
    c.key("mouth_open", [(0, 0), (up * 0.8, 0), (up * 1.2, 0.35), (up * 1.9, 0.0)])
    return c


# ============================================================================== point
class PointParams(ParamsBase):
    direction: Literal["auto", "left", "right", "up", "down", "forward", "back"] = Field(
        "auto", description="screen direction to point (ignored when `target` is set)"
    )
    target: TargetT = Field(None, description="point at: [x,y], a slot name, or a character id")
    hand: Literal["auto", "right", "left"] = Field(
        "auto", description="which arm points (auto = the right arm unless it is holding a prop)"
    )
    jabs: int = Field(0, ge=0, le=6, description="emphatic jabs while pointing")
    turn: bool = Field(True, description="turn to face the pointing direction")


@register_action(
    "point",
    params_schema=PointParams,
    category="gesture",
    summary="Extend an arm and point at a direction, slot or character",
    min_duration=0.5,
    default_duration=1.4,
)
def point(ctx: ActionContext, p: PointParams) -> Clip:
    D = ctx.duration
    c = ctx.clip(blend_in=0.12, blend_out=0.22)
    s = ctx.free_hand() if p.hand == "auto" else _side(p.hand)
    sgn = 1 if s == "r" else -1
    x0, y0 = ctx.pos0
    if p.target is not None:
        tx, ty = ctx.resolve_point(p.target)
        if (
            isinstance(p.target, str)
            and ctx.world.slot(p.target) is None
            and not p.target.startswith("off_")
        ):
            ty -= 0.10  # aim at a character's chest, not their feet
        vx, vy = (tx - x0) * ctx.frame_w, (ty - (y0 - 0.1)) * ctx.frame_h
    else:
        vx, vy = {
            "left": (-1.0, 0.0),
            "right": (1.0, 0.0),
            "up": (0.35 * ctx.face0, -1.0),
            "down": (0.35 * ctx.face0, 1.0),
            "forward": (ctx.face0, -0.1),
            "back": (-ctx.face0, -0.1),
            "auto": ((1.0 if x0 < 0.5 else -1.0), -0.12),
        }[p.direction]
    norm = math.hypot(vx, vy) or 1.0
    vx, vy = vx / norm, vy / norm
    new_face = ctx.face0
    if p.turn and abs(vx) > 0.35:
        new_face = 1.0 if vx > 0 else -1.0
    if new_face != ctx.face0:
        c.key("face", [(0, ctx.face0), (min(0.2, 0.3 * D), new_face)], persist=True)
    fig_dir = (vx * new_face, vy)
    pose = ctx.pose(torso_lean=4 * abs(vx))
    sh = ctx.rig.solve(pose).pts[f"shoulder_{s}"]
    reach_len = ctx.dims.arm_len * 0.97
    tgt = (sh[0] + fig_dir[0] * reach_len, sh[1] + fig_dir[1] * reach_len)
    ik = ctx.rig.reach(pose, s, tgt)
    up = min(0.28, 0.3 * D)
    down = min(0.34, 0.3 * D)
    rest_sh = 5.0 * sgn
    c.key(
        f"arm_{s}_sh",
        [
            (0, rest_sh),
            (0.06 * D, rest_sh - 8 * sgn),
            (up, ik[f"arm_{s}_sh"], "overshoot"),
            (D - down, ik[f"arm_{s}_sh"]),
            (D, rest_sh),
        ],
    )
    c.key(
        f"arm_{s}_el",
        [(0, 10), (up, ik[f"arm_{s}_el"], "overshoot"), (D - down, ik[f"arm_{s}_el"]), (D, 10)],
    )
    c.key(f"hand_{s}_point", [(0, 0), (up * 0.8, 1), (D - down, 1), (D, 0)], ease="linear")
    if p.jabs:
        f = max(1.5, p.jabs / max(D - up - down, 0.5))
        c.osc(f"arm_{s}_sh", amp=5.0, freq=f, t0=up, t1=D - down, harmonics=((2, 0.3),))
    c.key("torso_lean", [(0, 0), (up, 4 * abs(vx)), (D - down, 4 * abs(vx)), (D, 0)])
    c.key("head_turn", [(0, 0), (up, 0.7), (D - down, 0.7), (D, 0)])
    c.key("eye_dx", [(0, 0), (up, 0.6), (D - down, 0.6), (D, 0)])
    c.key("eye_dy", [(0, 0), (up, vy * 0.6), (D - down, vy * 0.6), (D, 0)])
    c.key("head_tilt", [(0, 0), (up, -vy * 8), (D - down, -vy * 8), (D, 0)])
    c.key("brow_raise", [(0, 0), (up, 0.25), (D - down, 0.25), (D, 0)])
    return c


# ============================================================================== pick_up
class PickUpParams(ParamsBase):
    prop: str | None = Field(
        None, description="name of a prop the character owns; it ends up in their hand"
    )
    side: Literal["auto", "right", "left"] = Field(
        "auto", description="which hand grabs (auto = the prop's usual hand)"
    )


def _pick_up_check(params: dict[str, Any], character: object) -> list[str]:
    prop = params.get("prop")
    props = getattr(character, "props", []) or []
    if prop and prop not in props:
        return [
            f"pick_up of {prop!r} but the character does not list it in `props`; nothing will appear in the hand"
        ]
    return []


@register_action(
    "pick_up",
    params_schema=PickUpParams,
    category="gesture",
    summary="Crouch, grab an item from the floor (a prop the character owns) and stand up with it",
    min_duration=1.0,
    default_duration=1.8,
    check=_pick_up_check,
)
def pick_up(ctx: ActionContext, p: PickUpParams) -> Clip:
    D = ctx.duration
    c = ctx.clip(blend_in=0.14, blend_out=0.25)
    prop_def = next((d for d in ctx.char.prop_defs if d.name == p.prop), None)
    side = p.side
    if side == "auto":
        side = "left" if (prop_def and prop_def.home == "hand_l") else "right"
    s = _side(side)
    sgn = 1 if s == "r" else -1
    d = ctx.dims
    crouch = ctx.pose(
        torso_lean=40, head_tilt=-14, leg_r_hip=44, leg_l_hip=44, leg_r_kn=88, leg_l_kn=88
    )
    hip = ctx.rig.solve(crouch).pts["hip_c"]
    target = (hip[0] + PICKUP_REACH * d.height + PICKUP_FORWARD, -d.hand_r * 0.7)
    ik = ctx.rig.reach(crouch, s, target)
    carry_pose = ctx.pose()
    carry = ctx.rig.reach(
        carry_pose,
        s,
        (
            ctx.rig.solve(carry_pose).pts["hip_c"][0] + 62,
            ctx.rig.solve(carry_pose).pts["hip_c"][1] + 8,
        ),
    )
    t_down, t_grab, t_up = 0.38 * D, 0.46 * D, 0.82 * D
    c.key(
        "torso_lean",
        [
            (0, 0),
            (0.08 * D, -3),
            (t_down, 40, "ease_in_out"),
            (t_grab, 40),
            (t_up, 0, "overshoot"),
            (D, 0),
        ],
    )
    for leg in ("r", "l"):
        c.key(f"leg_{leg}_hip", [(0, 0), (t_down, 44), (t_grab, 44), (t_up, 0), (D, 0)])
        c.key(f"leg_{leg}_kn", [(0, 0), (t_down, 88), (t_grab, 88), (t_up, 0), (D, 0)])
    c.key("head_tilt", [(0, 0), (t_down, -14), (t_grab, -14), (t_up, -2), (D, 0)])
    c.key("eye_dy", [(0, 0), (0.15 * D, 0.7), (t_grab, 0.7), (t_up, 0), (D, 0)])
    c.key(
        f"arm_{s}_sh",
        [
            (0, 5.0 * sgn),
            (0.22 * D, 10 * sgn),
            (t_down, ik[f"arm_{s}_sh"]),
            (t_grab, ik[f"arm_{s}_sh"]),
            (t_up, carry[f"arm_{s}_sh"], "overshoot"),
            (D, carry[f"arm_{s}_sh"]),
        ],
    )
    c.key(
        f"arm_{s}_el",
        [
            (0, 10),
            (t_down, ik[f"arm_{s}_el"]),
            (t_grab, ik[f"arm_{s}_el"]),
            (t_up, carry[f"arm_{s}_el"], "overshoot"),
            (D, carry[f"arm_{s}_el"]),
        ],
    )
    other = "l" if s == "r" else "r"
    osg = -sgn
    c.key(
        f"arm_{other}_sh",
        [
            (0, 5.0 * osg),
            (t_down, -35 * osg),
            (t_grab, -35 * osg),
            (t_up, 5.0 * osg),
            (D, 5.0 * osg),
        ],
    )
    c.key("mouth_smile", [(0, 0.3), (t_up, 0.3), (D, 0.8)])
    c.key("brow_raise", [(0, 0), (0.15 * D, 0.4), (t_grab, 0.4), (D, 0.2)])
    if p.prop and p.prop in ctx.char.props:
        c.prop(p.prop, [(0, 0), (t_grab, 0), (t_grab + 0.01, 1)], before=0.0)
    c.event(t_grab, "pickup")
    return c


# ============================================================================== look_at
class LookAtParams(ParamsBase):
    target: TargetT = Field(
        "camera",
        description="'camera', 'up'/'down'/'left'/'right', [x,y], a slot or a character id",
    )
    eyes_only: bool = Field(False, description="move only the eyes, keep the head still")
    turn: bool = Field(True, description="turn the body round if the target is behind")


@register_action(
    "look_at",
    params_schema=LookAtParams,
    category="gesture",
    summary="Turn head and eyes towards a target (camera, direction, slot or character)",
    min_duration=0.3,
    default_duration=1.2,
)
def look_at(ctx: ActionContext, p: LookAtParams) -> Clip:
    D = ctx.duration
    c = ctx.clip(blend_in=0.14, blend_out=0.25)
    x0, y0 = ctx.pos0
    head_y = y0 - 0.82 * ctx.body_px / ctx.frame_h
    t = p.target
    camera = t == "camera"
    if camera:
        vx, vy = 0.0, 0.0
    elif isinstance(t, str) and t in ("up", "down", "left", "right"):
        vx, vy = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}[
            t
        ]
        vx *= 0.35
        vy *= 0.25
    else:
        tx, ty = ctx.resolve_point(t)
        if isinstance(t, str) and ctx.world.slot(t) is None and not t.startswith("off_"):
            ty -= 0.8 * ctx.body_px / ctx.frame_h  # their head, not their feet
        vx, vy = tx - x0, ty - head_y
    new_face = ctx.face0
    if p.turn and not camera and abs(vx) > 0.04 and vx * ctx.face0 < 0:
        new_face = -ctx.face0
    if new_face != ctx.face0:
        c.key("face", [(0, ctx.face0), (min(0.2, 0.35 * D), new_face)], persist=True)
    fwd = vx * new_face  # >0: target is in front of the character
    eye_dx = float(np.clip(fwd / 0.22, -1.0, 1.0))
    eye_dy = float(np.clip(vy / 0.18, -1.0, 1.0))
    turn_amt = -1.0 if camera else float(np.clip(0.15 + fwd / 0.35, -1.0, 1.0))
    ramp = min(0.28, 0.4 * D)
    hold_to = max(ramp, D - ramp)
    c.key(
        "eye_dx",
        [
            (0, 0),
            (ramp * 0.6, eye_dx if not camera else 0.0),
            (hold_to, eye_dx if not camera else 0.0),
            (D, 0),
        ],
    )
    c.key("eye_dy", [(0, 0), (ramp * 0.6, eye_dy), (hold_to, eye_dy), (D, 0)])
    if not p.eyes_only:
        c.key("head_turn", [(0, 0), (ramp, turn_amt), (hold_to, turn_amt), (D, 0)])
        c.key(
            "head_tilt",
            [
                (0, 0),
                (ramp, float(np.clip(vy * 40, -14, 14))),
                (hold_to, float(np.clip(vy * 40, -14, 14))),
                (D, 0),
            ],
        )
    if camera:
        c.key("eye_open", [(0, 1), (ramp, 1.12), (hold_to, 1.12), (D, 1)])
        c.key("brow_raise", [(0, 0), (ramp, 0.25), (hold_to, 0.25), (D, 0)])
    return c
