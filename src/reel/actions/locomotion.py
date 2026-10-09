"""Whole-body movement: idle, walk, run, jump, fall, enter_from, exit_to."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from pydantic import Field

from reel.actions.base import ParamsBase, register_action
from reel.core.animation import OFFSCREEN_X, ActionContext, Clip, Point, Sampled
from reel.core.easing import smoothstep

TargetT = list[float] | str | None


# ============================================================================== idle
class IdleParams(ParamsBase):
    mood: Literal["calm", "bored", "nervous", "happy", "tired", "angry", "sad", "proud"] = Field(
        "calm", description="body language to hold"
    )
    intensity: float = Field(1.0, ge=0.0, le=2.0, description="how pronounced the mood is")


@register_action(
    "idle",
    params_schema=IdleParams,
    category="posture",
    summary="Stand and breathe; `mood` shades the body language (bored, nervous, happy, ...)",
    min_duration=0.3,
    default_duration=2.0,
)
def idle(ctx: ActionContext, p: IdleParams) -> Clip:
    D, k = ctx.duration, p.intensity
    c = ctx.clip(blend_in=0.3, blend_out=0.3)
    m = p.mood
    if m == "calm":
        c.hold("torso_lean", 0.0)
        c.hold("head_tilt", 0.0)
        c.hold("eye_open", 1.0)
        c.hold("mouth_smile", 0.3)
    elif m == "bored":
        c.key("torso_lean", [(0, 0), (0.6, 5 * k)])
        c.key("head_tilt", [(0, 0), (0.6, 7 * k)])
        c.key("eye_open", [(0, 1), (0.6, 1 - 0.28 * k)])
        c.hold("mouth_smile", -0.15)
        c.key("arm_r_sh", [(0, 5), (0.6, 2)])
        c.key("arm_l_sh", [(0, -5), (0.6, -2)])
        c.osc("eye_dx", amp=0.25 * k, freq=0.16)
        c.key(
            "breath",
            [(0, 0), (D * 0.5, 0.2), (D * 0.62, 1.0 * k, "ease_in_out"), (D * 0.85, 0.0)],
            mode="add",
        )
    elif m == "nervous":
        c.hold("brow_angle", 0.5 * k)
        c.key("sweat", [(0, 0), (0.8, 0.8 * k)])
        c.hold("mouth_smile", -0.1)
        c.osc("eye_dx", amp=0.5 * k, freq=0.9, harmonics=((2.3, 0.4),))
        c.osc("hip_dx", amp=5 * k, freq=0.55)
        c.osc("arm_r_el", amp=14 * k, freq=1.7, center=26 * k, mode="add")
        c.osc("arm_l_el", amp=14 * k, freq=1.9, center=26 * k, phase=1.3, mode="add")
        c.osc("hand_r_wr", amp=18 * k, freq=1.7, mode="add")
    elif m == "happy":
        c.hold("mouth_smile", 0.95)
        c.hold("brow_raise", 0.3 * k)
        c.key("blush", [(0, 0), (0.8, 0.25 * k)])
        c.osc("bob", amp=5 * k, freq=0.95, center=3 * k, mode="add")
        c.osc("arm_r_sh", amp=7 * k, freq=0.95, phase=1.0)
        c.osc("arm_l_sh", amp=7 * k, freq=0.95, phase=1.0 + math.pi)
        c.hold("head_tilt", -3 * k)
    elif m == "tired":
        c.key("torso_lean", [(0, 0), (0.8, 9 * k)])
        c.key("head_tilt", [(0, 0), (0.8, 10 * k)])
        c.key("eye_open", [(0, 1), (0.8, 1 - 0.45 * k)])
        c.hold("mouth_smile", -0.25)
        c.osc("breath", amp=0.9 * k, freq=0.2, mode="add")
        c.key("arm_r_el", [(0, 10), (0.8, 24)])
        c.key("arm_l_el", [(0, 10), (0.8, 24)])
        c.key(
            "mouth_open", [(0, 0), (D * 0.45, 0), (D * 0.6, 0.85 * k), (D * 0.8, 0.0)], mode="add"
        )
    elif m == "angry":
        c.hold("brow_angle", -1.0 * k)
        c.hold("mouth_smile", -0.8)
        c.key("torso_lean", [(0, 0), (0.4, 6 * k)])
        c.key("arm_r_el", [(0, 10), (0.4, 62 * k)])
        c.key("arm_l_el", [(0, 10), (0.4, 62 * k)])
        c.osc("hip_dx", amp=1.6 * k, freq=13.0, fade=0.3)
        c.key("blush", [(0, 0), (0.8, 0.5 * k)])
    elif m == "sad":
        c.hold("brow_angle", 0.85 * k)
        c.hold("mouth_smile", -0.7)
        c.key("head_tilt", [(0, 0), (0.7, 12 * k)])
        c.key("torso_lean", [(0, 0), (0.7, 5 * k)])
        c.key("eye_open", [(0, 1), (0.7, 1 - 0.2 * k)])
        c.key("arm_r_sh", [(0, 5), (0.7, 1)])
        c.key("arm_l_sh", [(0, -5), (0.7, -1)])
    else:  # proud
        c.key("torso_lean", [(0, 0), (0.5, -4 * k)])
        c.key("head_tilt", [(0, 0), (0.5, -6 * k)])
        c.hold("mouth_smile", 0.7)
        c.key("arm_r_el", [(0, 10), (0.5, 28)])
        c.key("arm_l_el", [(0, 10), (0.5, 28)])
        c.key("arm_r_sh", [(0, 5), (0.5, 16)])
        c.key("arm_l_sh", [(0, -5), (0.5, -16)])
        c.hold("brow_raise", 0.25 * k)
    return c


# ============================================================================== gaits
@dataclass(frozen=True)
class Gait:
    stride: float  # stride length (one full cycle) as a fraction of body height
    knee: float  # swing-leg knee flexion peak (deg)
    base_knee: float  # constant knee bend (crouch)
    arm: float  # arm swing amplitude (deg)
    elbow: float  # resting elbow flexion while moving
    elbow_swing: float  # extra elbow flexion on the forward swing
    lean: float  # torso lean (deg)
    head: float  # head tilt (deg)
    hop: float = 0.0  # airborne hop height as fraction of leg length (running)
    sway: float = 3.0  # lateral hip sway (px)
    stomp: float = 0.0  # squash pulse at each footfall
    cadence: float = 1.0  # cycles/s when walking in place
    smile: float | None = None
    brow_angle: float = 0.0
    eye_open: float = 1.0
    extra: tuple[tuple[str, float], ...] = ()


WALK_GAITS: dict[str, Gait] = {
    "normal": Gait(0.54, 55, 4, 22, 14, 14, 3, 0, sway=3, cadence=1.05),
    "sneak": Gait(0.36, 38, 34, 6, 40, 8, 16, -12, sway=1, cadence=0.8),
    "tired": Gait(0.40, 24, 10, 7, 20, 6, 14, 12, sway=5, cadence=0.8, smile=-0.4, eye_open=0.6),
    "happy": Gait(0.58, 62, 6, 32, 24, 20, -2, -3, hop=0.025, sway=6, cadence=1.2, smile=0.95),
    "stomp": Gait(
        0.46,
        74,
        8,
        36,
        34,
        12,
        8,
        4,
        stomp=0.045,
        sway=7,
        cadence=0.95,
        smile=-0.6,
        brow_angle=-0.8,
    ),
    "march": Gait(0.50, 84, 2, 40, 85, 4, 0, -2, sway=0, cadence=1.2),
    "strut": Gait(0.52, 50, 3, 16, 22, 10, -3, -4, sway=14, cadence=1.0, smile=0.7),
}
RUN_GAITS: dict[str, Gait] = {
    "sprint": Gait(1.15, 100, 14, 55, 95, 10, 18, -6, hop=0.085, sway=2, cadence=2.4, smile=0.0),
    "jog": Gait(0.9, 82, 10, 36, 80, 8, 10, -3, hop=0.06, sway=2, cadence=1.9),
    "panic": Gait(
        1.05,
        95,
        12,
        62,
        60,
        30,
        8,
        -4,
        hop=0.07,
        sway=3,
        cadence=2.2,
        smile=-0.5,
        eye_open=1.3,
        extra=(("sweat", 0.8), ("mouth_open", 0.55), ("brow_raise", 0.8)),
    ),
}


def gait_wave(phi: np.ndarray, lam: float = 0.85) -> np.ndarray:
    """Hip-swing waveform in [-1, 1]: +1 at heel strike (phi = pi/2), -1 at toe-off (3*pi/2).

    A pure sine moves a planted foot 1.57x too fast mid-stance (it slides back along the floor).
    Blending in a triangle wave gives the stance leg a near-constant sweep speed, which equals
    the body speed - so planted feet stay planted - while keeping the motion soft.
    """
    x = np.mod(phi - math.pi / 2, 2 * math.pi)  # 0 at heel strike, pi at toe-off
    tri = np.where(x < math.pi, 1 - 2 * x / math.pi, -1 + 2 * (x - math.pi) / math.pi)
    return np.asarray((1 - lam) * np.sin(phi) + lam * tri)


def locomote(
    ctx: ActionContext,
    start: Point,
    end: Point,
    g: Gait,
    *,
    ramp: float = 0.22,
    turn: bool = True,
) -> Clip:
    """Procedural gait driven by *distance travelled*, so feet never slide on the ground."""
    D = ctx.duration
    c = ctx.clip(blend_in=0.18, blend_out=0.22)
    dt = 1.0 / (ctx.fps * 2)
    ts = np.arange(0.0, D + dt * 0.5, dt)
    S = ctx.px(end[0] - start[0], end[1] - start[1])  # screen px to cover
    body = ctx.body_px
    leg_px = ctx.dims.leg_len * ctx.px_scale

    ta = max(0.05, min(0.30, ramp * D))
    td = max(0.05, min(0.35, ramp * D))
    v = smoothstep(ts / ta) * smoothstep((D - ts) / td)
    s_norm = np.cumsum(v)
    s_norm = s_norm / s_norm[-1] if s_norm[-1] > 0 else s_norm
    stride_nom = g.stride * body
    travelling = S >= 12.0
    if travelling:
        steps = max(1, round(2 * S / stride_nom))
        stride = 2 * S / steps
    else:
        cycles = max(0.5, g.cadence * D * 0.85)
        steps = max(1, round(2 * cycles))
        stride = stride_nom
    phi = math.pi * steps * s_norm
    sp, cp = gait_wave(phi), np.cos(phi)
    speed = v / max(float(np.max(v)), 1e-9)

    amp = math.degrees(math.asin(min(0.80, stride / (4 * leg_px))))
    knee_r = g.base_knee + g.knee * np.maximum(0, cp) ** 0.85
    knee_l = g.base_knee + g.knee * np.maximum(0, -cp) ** 0.85

    def S_(vals: np.ndarray) -> Sampled:
        return Sampled(0.0, dt, vals)

    c.curve("leg_r_hip", S_(amp * sp + g.base_knee * 0.35))
    c.curve("leg_l_hip", S_(-amp * sp + g.base_knee * 0.35))
    c.curve("leg_r_kn", S_(knee_r))
    c.curve("leg_l_kn", S_(knee_l))
    c.curve("arm_r_sh", S_(5 - g.arm * sp * speed))
    c.curve("arm_l_sh", S_(-5 + g.arm * sp * speed))
    c.curve("arm_r_el", S_(g.elbow + g.elbow_swing * np.maximum(0, -sp) * speed))
    c.curve("arm_l_el", S_(g.elbow + g.elbow_swing * np.maximum(0, sp) * speed))
    c.curve(
        "torso_lean",
        S_(g.lean * np.minimum(1.0, speed * 2.2 + 0.0) + 1.4 * np.sin(2 * phi - 1.2) * speed),
    )
    c.curve(
        "head_tilt", S_(g.head * np.minimum(1.0, speed * 2.2) - 1.2 * np.sin(2 * phi - 1.2) * speed)
    )
    c.curve("hip_dx", S_(g.sway * sp * speed), mode="add")
    if g.hop:
        lift = g.hop * ctx.dims.leg_len * (cp**2) * speed
        c.curve("lift", S_(lift), mode="add")
    if g.stomp:
        plant = np.exp(-(((np.abs(sp) - 1.0) / 0.10) ** 2)) * speed
        c.curve("squash", S_(g.stomp * plant), mode="add")
    if g.smile is not None:
        c.hold("mouth_smile", g.smile)
    if g.brow_angle:
        c.hold("brow_angle", g.brow_angle)
    if g.eye_open != 1.0:
        c.hold("eye_open", g.eye_open)
    for ch, val in g.extra:
        c.hold(ch, val)
    if g.base_knee > 25:  # sneaking: scan the surroundings
        c.osc("eye_dx", amp=0.6, freq=0.7)

    if travelling:
        px = start[0] + (end[0] - start[0]) * s_norm
        py = start[1] + (end[1] - start[1]) * s_norm
        c.move_root(S_(px), S_(py))
    else:
        c.move_root(
            Sampled(0, dt, np.full_like(ts, start[0])), Sampled(0, dt, np.full_like(ts, start[1]))
        )

    if turn:
        dx = end[0] - start[0]
        target = (1.0 if dx > 0 else -1.0) if abs(dx) * ctx.frame_w > 6 else ctx.face0
        tt = min(0.18, 0.4 * D)
        c.key("face", [(0, ctx.face0), (tt, target)], persist=True)

    # footsteps: right foot plants at phi = pi/2 + 2*pi*k, left at 3*pi/2 + 2*pi*k
    k = 0
    while True:
        target_phi = math.pi / 2 + k * math.pi
        if target_phi > phi[-1] - 1e-6:
            break
        c.event(float(np.interp(target_phi, phi, ts)), "footstep", foot="r" if k % 2 == 0 else "l")
        k += 1
    if travelling:  # the trailing foot steps up to meet the other one as the walk ends
        c.event(D * 0.97, "footstep", foot="r" if k % 2 == 0 else "l")
    return c


class WalkParams(ParamsBase):
    to: TargetT = Field(
        None, description="Destination: [x,y] screen fractions, a slot name or a character id"
    )
    dx: float = Field(
        0.0,
        ge=-2.0,
        le=2.0,
        description="Relative move in frame widths (+ right) when `to` is not set",
    )
    dy: float = Field(0.0, ge=-1.0, le=1.0, description="Relative move in frame heights (+ down)")
    style: Literal["normal", "sneak", "tired", "happy", "stomp", "march", "strut"] = Field(
        "normal",
        description="gait: sneak=crouched and slow, tired=slumped shuffle, happy=bouncy, stomp=angry, march=stiff, strut=swaggering",
    )
    in_place: bool = Field(
        False, description="walk on the spot (the camera or background moves instead)"
    )


def _destination(ctx: ActionContext, to: TargetT, dx: float, dy: float, in_place: bool) -> Point:
    if in_place:
        return ctx.pos0
    if to is not None:
        return ctx.resolve_point(to)
    return (ctx.pos0[0] + dx, ctx.pos0[1] + dy)


@register_action(
    "walk",
    params_schema=WalkParams,
    category="locomotion",
    summary="Walk to a point or slot (or on the spot); gait style via `style`",
    moves_root=True,
    min_duration=0.6,
    default_duration=2.5,
)
def walk(ctx: ActionContext, p: WalkParams) -> Clip:
    end = _destination(ctx, p.to, p.dx, p.dy, p.in_place)
    return locomote(ctx, ctx.pos0, end, WALK_GAITS[p.style])


class RunParams(ParamsBase):
    to: TargetT = Field(None, description="Destination: [x,y], a slot name or a character id")
    dx: float = Field(0.0, ge=-2.0, le=2.0)
    dy: float = Field(0.0, ge=-1.0, le=1.0)
    style: Literal["jog", "sprint", "panic"] = Field(
        "sprint", description="jog=relaxed, sprint=all-out, panic=flailing arms, wide eyes, sweat"
    )
    in_place: bool = Field(
        False, description="run on the spot (the camera or background moves instead)"
    )


@register_action(
    "run",
    params_schema=RunParams,
    category="locomotion",
    summary="Run to a point or slot; sprint, jog or panic",
    moves_root=True,
    min_duration=0.6,
    default_duration=2.0,
)
def run(ctx: ActionContext, p: RunParams) -> Clip:
    end = _destination(ctx, p.to, p.dx, p.dy, p.in_place)
    c = locomote(ctx, ctx.pos0, end, RUN_GAITS[p.style])
    if p.style == "panic":
        c.osc("arm_r_sh", amp=22, freq=3.1)
        c.osc("arm_l_sh", amp=22, freq=2.7, phase=1.1)
    return c


# ============================================================================== jump
class JumpParams(ParamsBase):
    height: float = Field(
        0.30, ge=0.05, le=1.5, description="peak height as a fraction of the character's height"
    )
    dx: float = Field(
        0.0, ge=-1.0, le=1.0, description="drift forward during the jump, in frame widths"
    )
    style: Literal["normal", "cheer", "tuck", "star"] = Field(
        "normal",
        description="normal=arms swing up, cheer=both arms overhead, tuck=knees to chest, star=star jump",
    )


@register_action(
    "jump",
    params_schema=JumpParams,
    category="locomotion",
    summary="Crouch (anticipation), leap, land with squash and settle",
    moves_root=True,
    min_duration=0.5,
    default_duration=1.0,
)
def jump(ctx: ActionContext, p: JumpParams) -> Clip:
    D = ctx.duration
    c = ctx.clip(blend_in=0.08, blend_out=0.12)
    ta, tl = 0.20 * D, 0.28 * D
    air = 0.42 * D
    tland = tl + air
    tapex = tl + air / 2
    th = ctx.dims.height * p.height
    dt = 1.0 / (ctx.fps * 2)
    ts = np.arange(0.0, D + dt / 2, dt)
    u = np.clip((ts - tl) / air, 0, 1)
    c.curve("lift", Sampled(0.0, dt, th * 4 * u * (1 - u)), mode="add")

    c.key(
        "squash",
        [
            (0, 0),
            (ta, 0.17),
            (tl, -0.10, "ease_out"),
            (tapex, -0.02),
            (tland - 0.02, -0.07),
            (tland + 0.04 * D, 0.20, "ease_out"),
            (0.84 * D, -0.03, "overshoot"),
            (D, 0),
        ],
        mode="add",
    )
    for s in ("r", "l"):
        c.key(
            f"leg_{s}_hip",
            [
                (0, 0),
                (ta, 38),
                (tl, 2, "ease_out"),
                (tapex, ((34 if s == "r" else -34) if p.style == "star" else 28)),
                (tland - 0.02, 10),
                (tland + 0.04 * D, 36),
                (0.84 * D, 14),
                (D, 0),
            ],
        )
        c.key(
            f"leg_{s}_kn",
            [
                (0, 0),
                (ta, 82),
                (tl, 4, "ease_out"),
                (tapex, 46 if p.style == "tuck" else 28),
                (tland - 0.02, 8),
                (tland + 0.04 * D, 78),
                (0.84 * D, 30),
                (D, 0),
            ],
        )
    c.key(
        "torso_lean",
        [
            (0, 0),
            (ta, 24),
            (tl, -6, "ease_out"),
            (tapex, -3),
            (tland, 4),
            (tland + 0.04 * D, 22),
            (0.84 * D, 6),
            (D, 0),
        ],
    )
    c.key("head_tilt", [(0, 0), (ta, -8), (tl, -10), (tapex, -6), (tland + 0.04 * D, -8), (D, 0)])
    if p.style == "cheer":
        up = [
            (0, 5),
            (ta, -50),
            (tl, 168, "overshoot"),
            (tapex, 160),
            (tland, 150),
            (tland + 0.05 * D, 60),
            (D, 5),
        ]
        c.key("arm_r_sh", up)
        c.key(
            "arm_l_sh",
            [
                (0, -5),
                (ta, -50),
                (tl, -168, "overshoot"),
                (tapex, -160),
                (tland, -150),
                (tland + 0.05 * D, -60),
                (D, -5),
            ],
        )
        c.key("arm_r_el", [(0, 10), (tl, 10), (tapex, 25), (tland, 25), (D, 10)])
        c.key("arm_l_el", [(0, 10), (tl, 10), (tapex, 25), (tland, 25), (D, 10)])
        c.key("mouth_open", [(0, 0), (tl, 0.9, "ease_out"), (tland, 0.7), (tland + 0.1 * D, 0)])
        c.hold("mouth_smile", 1.0)
    elif p.style == "star":
        for s, sg in (("r", 1), ("l", -1)):
            c.key(
                f"arm_{s}_sh",
                [
                    (0, 5 * sg),
                    (ta, -45 * sg),
                    (tl, 150 * sg, "overshoot"),
                    (tland, 140 * sg),
                    (tland + 0.06 * D, 30 * sg),
                    (D, 5 * sg),
                ],
            )
            c.key(f"arm_{s}_el", [(0, 10), (tl, 5), (tland, 5), (D, 10)])
        c.hold("mouth_smile", 1.0)
    else:
        for s, sg in (("r", 1), ("l", -1)):
            c.key(
                f"arm_{s}_sh",
                [
                    (0, 5 * sg),
                    (ta, -52),
                    (tl, 120, "overshoot"),
                    (tapex, 100),
                    (tland, 70),
                    (tland + 0.05 * D, 28),
                    (D, 5 * sg),
                ],
            )
            c.key(f"arm_{s}_el", [(0, 10), (ta, 25), (tapex, 30), (tland + 0.05 * D, 40), (D, 10)])
        c.key(
            "mouth_open",
            [
                (0, 0),
                (tl, 0.45, "ease_out"),
                (tl + 0.12 * D, 0),
                (tland + 0.03 * D, 0.3),
                (tland + 0.12 * D, 0),
            ],
        )
    c.key("eye_open", [(0, 1), (ta, 0.75), (tl, 1.2), (tland + 0.04 * D, 0.7), (D, 1)])
    c.key("brow_raise", [(0, 0), (ta, -0.2), (tl, 0.6), (tland + 0.04 * D, -0.2), (D, 0)])
    c.event(tl, "jump")
    c.event(tland, "land")
    x0, y0 = ctx.pos0
    if p.dx:
        x_curve = Sampled(0.0, dt, x0 + p.dx * smoothstep((ts - tl) / air))
        c.move_root(x_curve, Sampled(0.0, dt, np.full_like(ts, y0)))
        if abs(p.dx) * ctx.frame_w > 6:
            c.key("face", [(0, ctx.face0), (0.15, 1.0 if p.dx > 0 else -1.0)], persist=True)
    return c


# ============================================================================== fall
class FallParams(ParamsBase):
    kind: Literal["slip", "trip", "faint"] = Field("slip", description="pratfall type")
    get_up: bool = Field(False, description="stand back up at the end instead of staying down")
    dizzy: bool = Field(True, description="swirly eyes while down")


@register_action(
    "fall",
    params_schema=FallParams,
    category="locomotion",
    summary="Slip, trip or faint: flail, hit the ground with a bounce, lie there (optionally get up)",
    min_duration=0.8,
    default_duration=1.6,
)
def fall(ctx: ActionContext, p: FallParams) -> Clip:
    D = ctx.duration
    c = ctx.clip(blend_in=0.06, blend_out=0.2)
    hip_h = ctx.dims.hip_height
    hit = 0.46 * D  # impact time
    down = 0.52 * D
    rise = 0.82 * D
    sign = -1.0 if p.kind in ("slip", "faint") else 1.0  # slip falls backwards, trip falls forwards
    lie_bob = -(
        hip_h - (ctx.dims.head_ry + 10)
    )  # hips just high enough for the head to rest on the floor

    def rest_or_stay(
        stay: float, back: float
    ) -> list[tuple[float, float] | tuple[float, float, str]]:
        return (
            [(rise, stay), (min(D, rise + 0.16 * D), back, "overshoot"), (D, back)]
            if p.get_up
            else []
        )

    c.key(
        "ground_lock",
        [(0, 1), (0.10 * D, 1), (0.16 * D, 0), ((rise if p.get_up else D), 0)]
        + ([(min(D, rise + 0.14 * D), 1, "hold")] if p.get_up else []),
        persist=not p.get_up,
    )
    c.key(
        "rot_hip",
        [
            (0, 0),
            (0.1 * D, -sign * 7, "ease_out"),
            (hit, sign * 88, "ease_in"),
            (hit + 0.05 * D, sign * 80, "ease_out"),
            (down, sign * 88, "ease_in"),
            *rest_or_stay(sign * 88, 0),
        ],
        persist=not p.get_up,
    )
    c.key(
        "bob",
        [
            (0, 0),
            (0.1 * D, 8),
            (0.28 * D, 55, "ease_out"),
            (hit, lie_bob, "ease_in"),
            (hit + 0.05 * D, lie_bob + 26, "ease_out"),
            (down, lie_bob, "bounce"),
            *rest_or_stay(lie_bob, 0),
        ],
        persist=not p.get_up,
    )
    c.key(
        "squash",
        [
            (0, 0),
            (hit - 0.02 * D, -0.06),
            (hit + 0.02 * D, 0.18, "ease_out"),
            (down, 0, "overshoot"),
            (D, 0),
        ],
        mode="add",
    )
    legs_up = 62 if p.kind == "slip" else (12 if p.kind == "trip" else 20)
    for s in ("r", "l"):
        sg = 1 if s == "r" else -1
        c.key(
            f"leg_{s}_hip",
            [
                (0, 0),
                (0.1 * D, 6),
                (0.28 * D, legs_up + 8 * sg, "ease_out"),
                (hit, legs_up * 0.9),
                (down, legs_up * 0.55 + 8 * sg, "bounce"),
                *rest_or_stay(legs_up * 0.55, 0),
            ],
            persist=not p.get_up,
        )
        c.key(
            f"leg_{s}_kn",
            [(0, 0), (0.28 * D, 12), (hit, 10), (down, 30 + 10 * sg), *rest_or_stay(30, 0)],
            persist=not p.get_up,
        )
        c.key(
            f"arm_{s}_sh",
            [
                (0, 5 * sg),
                (0.14 * D, 70 * sg, "ease_out"),
                (hit, 120 * sg),
                (down, 150 * sg, "bounce"),
                *rest_or_stay(150 * sg, 5 * sg),
            ],
            persist=not p.get_up,
        )
        c.key(
            f"arm_{s}_el",
            [(0, 10), (0.14 * D, 30), (hit, 18), (down, 40), *rest_or_stay(40, 10)],
            persist=not p.get_up,
        )
    c.osc("arm_r_sh", amp=34, freq=3.6, t0=0.04 * D, t1=hit, fade=0.05)
    c.osc("arm_l_sh", amp=34, freq=3.6, t0=0.04 * D, t1=hit, phase=math.pi, fade=0.05)
    c.key(
        "eye_open",
        [
            (0, 1),
            (0.10 * D, 1.4),
            (hit, 1.4),
            (hit + 0.04 * D, 0.2),
            (down, 0.8 if p.dizzy else 1.0),
            (rise, 0.8),
            (D, 1),
        ],
    )
    c.key(
        "mouth_open",
        [
            (0, 0),
            (0.10 * D, 0.9),
            (hit, 0.9),
            (hit + 0.05 * D, 0.2),
            (down, 0.4),
            (rise, 0.4),
            (D, 0),
        ],
    )
    c.key("brow_raise", [(0, 0), (0.1 * D, 1), (hit, 1), (down, -0.2), (D, 0)])
    c.key("brow_angle", [(0, 0), (down, 0.7), (rise, 0.7), (D, 0)])
    c.event(hit, "thud")
    return c


# ============================================================================== enter / exit
def _edge(ctx: ActionContext, side: str, ref_x: float) -> float:
    if side == "auto":
        side = "left" if ref_x < 0.5 else "right"
    return -OFFSCREEN_X if side == "left" else 1.0 + OFFSCREEN_X


class EnterParams(ParamsBase):
    side: Literal["left", "right", "auto"] = Field(
        "auto", description="which edge to come in from (auto = nearest to the destination)"
    )
    to: TargetT = Field(None, description="where to end up (default: the layer's position)")
    style: Literal["walk", "run", "slide", "drop"] = Field(
        "walk",
        description="walk/run in from the edge, slide in gliding, or drop in from above with a bounce",
    )


@register_action(
    "enter_from",
    params_schema=EnterParams,
    category="locomotion",
    summary="Enter the frame from an edge (or drop in from above) and arrive at the layer's position",
    moves_root=True,
    min_duration=0.6,
    default_duration=2.0,
)
def enter_from(ctx: ActionContext, p: EnterParams) -> Clip:
    dest = ctx.resolve_point(p.to, ctx.home)
    D = ctx.duration
    if p.style == "drop":
        c = ctx.clip(blend_in=0.0, blend_out=0.2)
        c.pre["lift"] = 2400.0
        c.key(
            "lift",
            [
                (0, 2400),
                (0.55 * D, 0, "ease_in"),
                (0.66 * D, 70, "ease_out"),
                (0.78 * D, 0, "ease_in"),
                (0.86 * D, 22, "ease_out"),
                (0.93 * D, 0, "ease_in"),
                (D, 0),
            ],
            persist=True,
        )
        c.key(
            "squash",
            [
                (0, -0.2),
                (0.55 * D, -0.2),
                (0.60 * D, 0.24, "ease_out"),
                (0.70 * D, -0.04),
                (0.78 * D, 0.1, "ease_out"),
                (0.9 * D, 0, "overshoot"),
                (D, 0),
            ],
            mode="add",
        )
        c.key("arm_r_sh", [(0, 150), (0.55 * D, 150), (0.64 * D, 20, "ease_out"), (D, 5)])
        c.key("arm_l_sh", [(0, -150), (0.55 * D, -150), (0.64 * D, -20, "ease_out"), (D, -5)])
        c.key("eye_open", [(0, 1.3), (0.55 * D, 1.3), (0.62 * D, 0.5), (D, 1)])
        c.key("mouth_open", [(0, 0.8), (0.55 * D, 0.8), (0.64 * D, 0.0)])
        c.move_root(
            Sampled(0, 1 / ctx.fps, np.full(int(D * ctx.fps) + 2, dest[0])),
            Sampled(0, 1 / ctx.fps, np.full(int(D * ctx.fps) + 2, dest[1])),
        )
        c.event(0.55 * D, "thud")
        return c
    x_start = _edge(ctx, p.side, dest[0])
    start: Point = (x_start, dest[1])
    if p.style == "slide":
        c = locomote(ctx, start, dest, Gait(0.01, 6, 0, 3, 10, 2, 5, 0, sway=0), ramp=0.35)
        c.rekey("leg_r_hip", [(0, 4), (D, 4)])  # a glide: legs stay still, only the root moves
        c.rekey("leg_l_hip", [(0, -4), (D, -4)])
        c.rekey("leg_r_kn", [(0, 0), (D, 0)])
        c.rekey("leg_l_kn", [(0, 0), (D, 0)])
        return c
    return locomote(
        ctx, start, dest, (RUN_GAITS["jog"] if p.style == "run" else WALK_GAITS["normal"])
    )


class ExitParams(ParamsBase):
    side: Literal["left", "right", "auto"] = Field(
        "auto", description="edge to leave through (auto = nearest)"
    )
    style: Literal["walk", "run", "sneak", "stomp"] = Field(
        "walk", description="how the character leaves"
    )


@register_action(
    "exit_to",
    params_schema=ExitParams,
    category="locomotion",
    summary="Leave the frame through an edge",
    moves_root=True,
    min_duration=0.6,
    default_duration=2.0,
)
def exit_to(ctx: ActionContext, p: ExitParams) -> Clip:
    x_end = _edge(ctx, p.side, ctx.pos0[0])
    gait = {
        "walk": WALK_GAITS["normal"],
        "run": RUN_GAITS["jog"],
        "sneak": WALK_GAITS["sneak"],
        "stomp": WALK_GAITS["stomp"],
    }[p.style]
    return locomote(ctx, ctx.pos0, (x_end, ctx.pos0[1]), gait)
