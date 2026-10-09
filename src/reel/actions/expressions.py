"""Face and emotion actions: talk, laugh, cry, think, surprise."""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import Field

from reel.actions.base import ParamsBase, register_action, speech_envelope
from reel.core.animation import ActionContext, Clip, FuncCurve, Sampled


# ============================================================================== talk
class TalkParams(ParamsBase):
    text: str | None = Field(
        None, description="what is said (sets the syllable rhythm of the mouth)"
    )
    words: list[tuple[float, float, str]] | None = Field(
        None,
        description="word timings [t0, t1, word] relative to the action start (filled in from TTS alignment)",
    )
    energy: float = Field(1.0, ge=0.2, le=1.8, description="speech animation intensity")
    gestures: bool = Field(True, description="add small hand/arm gestures while talking")


@register_action(
    "talk",
    params_schema=TalkParams,
    category="expression",
    summary="Lip-flap in syllable rhythm with head nods, brow flicks and small gestures",
    min_duration=0.3,
    default_duration=2.0,
)
def talk(ctx: ActionContext, p: TalkParams) -> Clip:
    D = ctx.duration
    c = ctx.clip(blend_in=0.08, blend_out=0.18)
    ts, env, stress = speech_envelope(ctx, p.text, p.words, p.energy)
    dt = float(ts[1] - ts[0]) if len(ts) > 1 else 1.0
    c.curve("mouth_open", Sampled(0.0, dt, env))
    c.curve("mouth_wide", Sampled(0.0, dt, np.clip(env * 0.35 - 0.05, 0, 1)))

    st = np.array(stress) if stress else np.array([-10.0])

    def pulses(amp: float, width: float) -> FuncCurve:
        def fn(t: np.ndarray) -> np.ndarray:
            return amp * np.exp(-(((t[:, None] - st[None, :]) / width) ** 2)).sum(axis=1)

        return FuncCurve(fn)

    c.curve("head_dy", pulses(-2.8 * p.energy, 0.09), mode="add")
    c.curve("head_tilt", pulses(2.6 * p.energy, 0.12), mode="add")
    c.curve("brow_raise", pulses(0.28 * p.energy, 0.10), mode="add")
    c.osc("head_tilt", amp=2.2, freq=0.31, fade=0.2)
    c.osc("torso_lean", amp=0.9, freq=0.5, fade=0.2)
    if p.gestures and D > 1.0:
        rng = ctx.rng
        t = float(rng.uniform(0.35, 0.8))
        while t < D - 0.55:
            free = ctx.free_hand()
            s = free if rng.random() < 0.8 else ("l" if free == "r" else "r")
            sg = 1 if s == "r" else -1
            a_sh = float(rng.uniform(28, 55)) * sg
            a_el = float(rng.uniform(26, 52))
            hold = float(rng.uniform(0.18, 0.4))
            c.key(
                f"arm_{s}_sh",
                [
                    (t - 0.28, 0),
                    (t, a_sh, "ease_out"),
                    (t + hold, a_sh * 0.88),
                    (t + hold + 0.32, 0, "ease_in_out"),
                ],
                mode="add",
            )
            c.key(
                f"arm_{s}_el",
                [
                    (t - 0.28, 0),
                    (t, a_el, "ease_out"),
                    (t + hold, a_el * 0.9),
                    (t + hold + 0.32, 0, "ease_in_out"),
                ],
                mode="add",
            )
            c.key(
                f"hand_{s}_wr",
                [(t - 0.28, 0), (t, 14 * sg, "overshoot"), (t + hold + 0.32, 0)],
                mode="add",
            )
            t += float(rng.uniform(1.0, 1.8))
    return c


# ============================================================================== laugh
class LaughParams(ParamsBase):
    style: Literal["chuckle", "belly", "cackle"] = Field(
        "belly", description="chuckle=small, belly=hand on stomach, cackle=head back"
    )
    intensity: float = Field(1.0, ge=0.3, le=1.6, description="how hard they laugh")


@register_action(
    "laugh",
    params_schema=LaughParams,
    category="expression",
    summary="Squinting, shaking laugh: chuckle, belly laugh or cackle",
    min_duration=0.8,
    default_duration=2.0,
)
def laugh(ctx: ActionContext, p: LaughParams) -> Clip:
    D, k = ctx.duration, p.intensity
    c = ctx.clip(blend_in=0.12, blend_out=0.3)
    f = {"chuckle": 4.0, "belly": 5.4, "cackle": 7.2}[p.style]
    on = min(0.22, 0.25 * D)
    off = max(on, D - min(0.4, 0.3 * D))
    lean = {"chuckle": -4, "belly": -9, "cackle": -14}[p.style] * k
    c.key("mouth_open", [(0, 0), (on, 0.6), (off, 0.6), (D, 0.0)])
    c.osc("mouth_open", amp=0.28 * k, freq=f, t0=on, t1=off, fade=0.1)
    c.key("mouth_wide", [(0, 0), (on, 0.85), (off, 0.85), (D, 0)])
    c.key("mouth_smile", [(0, 0.3), (on, 1.0), (off, 1.0), (D, 0.5)])
    c.key("eye_open", [(0, 1), (on, 0.38), (off, 0.38), (D, 1)])
    c.key("brow_raise", [(0, 0), (on, 0.45), (off, 0.45), (D, 0)])
    c.key("blush", [(0, 0), (on * 2, 0.4 * k), (off, 0.4 * k), (D, 0)])
    c.key("tear", [(0, 0), (on * 3, 0.35 * k), (off, 0.35 * k), (D, 0)])
    c.key("torso_lean", [(0, 0), (on, lean), (off, lean), (D, 0)])
    c.key("head_tilt", [(0, 0), (on, -12 * k), (off, -12 * k), (D, 0)])
    c.osc("torso_lean", amp=2.6 * k, freq=f, t0=on, t1=off, phase=0.4)
    c.osc("squash", amp=0.035 * k, freq=f, t0=on, t1=off, phase=math.pi / 2)
    c.osc("bob", amp=4.5 * k, freq=f, t0=on, t1=off, phase=math.pi / 2)
    c.osc("head_tilt", amp=3.0 * k, freq=f * 0.5, t0=on, t1=off)
    pose = ctx.pose(torso_lean=lean, head_tilt=-12)
    if p.style == "belly":
        for s in ("r", "l"):
            sg = 1 if s == "r" else -1
            ik = ctx.rig.reach(pose, s, "belly", offset=(14 * sg, 6))
            c.key(
                f"arm_{s}_sh",
                [(0, 5 * sg), (on, ik[f"arm_{s}_sh"]), (off, ik[f"arm_{s}_sh"]), (D, 5 * sg)],
            )
            c.key(
                f"arm_{s}_el", [(0, 10), (on, ik[f"arm_{s}_el"]), (off, ik[f"arm_{s}_el"]), (D, 10)]
            )
    elif p.style == "cackle":
        c.osc("arm_r_sh", amp=14 * k, freq=f * 0.5, t0=on, t1=off, center=0)
        c.osc("arm_l_sh", amp=14 * k, freq=f * 0.5, t0=on, t1=off, phase=math.pi)
        c.key("head_tilt", [(0, 0), (on, -20 * k), (off, -20 * k), (D, 0)], mode="add")
    else:
        s = ctx.free_hand()  # a hand to the mouth, never the one holding something
        sg = 1 if s == "r" else -1
        ik = ctx.rig.reach(pose, s, "mouth", offset=(18, 10))
        c.key(
            f"arm_{s}_sh",
            [(0, 5 * sg), (on, ik[f"arm_{s}_sh"]), (off, ik[f"arm_{s}_sh"]), (D, 5 * sg)],
        )
        c.key(f"arm_{s}_el", [(0, 10), (on, ik[f"arm_{s}_el"]), (off, ik[f"arm_{s}_el"]), (D, 10)])
    c.event(on, "laugh")
    return c


# ============================================================================== cry
class CryParams(ParamsBase):
    style: Literal["sob", "weep", "bawl"] = Field(
        "sob", description="sob=hand to eye, weep=both hands, bawl=open-mouthed wailing"
    )
    intensity: float = Field(1.0, ge=0.3, le=1.6, description="how hard they cry")


@register_action(
    "cry",
    params_schema=CryParams,
    category="expression",
    summary="Tears, trembling shoulders and a hand wiping the eye",
    min_duration=1.0,
    default_duration=2.5,
)
def cry(ctx: ActionContext, p: CryParams) -> Clip:
    D, k = ctx.duration, p.intensity
    c = ctx.clip(blend_in=0.2, blend_out=0.35)
    on = min(0.5, 0.25 * D)
    off = max(on, D - min(0.5, 0.25 * D))
    f = {"sob": 3.2, "weep": 1.6, "bawl": 4.4}[p.style]
    c.key("tear", [(0, 0), (on, 1.0), (off, 1.0), (D, 0.4)])
    c.key("eye_open", [(0, 1), (on, 0.5), (off, 0.5), (D, 0.9)])
    c.key("brow_angle", [(0, 0), (on, 1.0), (off, 1.0), (D, 0)])
    c.key("brow_raise", [(0, 0), (on, 0.25), (off, 0.25), (D, 0)])
    c.key("mouth_smile", [(0, 0.3), (on, -1.0), (off, -1.0), (D, -0.2)])
    base_open = {"sob": 0.12, "weep": 0.0, "bawl": 0.75}[p.style]
    c.key("mouth_open", [(0, 0), (on, base_open), (off, base_open), (D, 0)])
    c.osc("mouth_open", amp=0.22 * k if p.style != "weep" else 0.05, freq=f, t0=on, t1=off)
    c.key("blush", [(0, 0), (on, 0.45), (off, 0.45), (D, 0)])
    c.key("head_tilt", [(0, 0), (on, 14 * k), (off, 14 * k), (D, 0)])
    c.key("torso_lean", [(0, 0), (on, 10 * k), (off, 10 * k), (D, 0)])
    c.osc("bob", amp=4.0 * k, freq=f, t0=on, t1=off)
    c.osc("torso_lean", amp=2.4 * k, freq=f, t0=on, t1=off, phase=0.7)
    c.osc("head_tilt", amp=2.0 * k, freq=f, t0=on, t1=off, phase=1.4)
    pose = ctx.pose(torso_lean=10, head_tilt=14)
    s = ctx.free_hand()  # wipe the eye with the hand that is free
    o = "l" if s == "r" else "r"
    offs = {"r": (14, 16), "l": (-6, 18)}
    sgn = {"r": 1, "l": -1}
    ik = ctx.rig.reach(pose, s, "eye_c", offset=offs[s])
    if p.style == "weep":  # both hands to the face
        ik_o = ctx.rig.reach(pose, o, "eye_c", offset=offs[o])
        c.key(
            f"arm_{o}_sh",
            [
                (0, 5 * sgn[o]),
                (on, ik_o[f"arm_{o}_sh"]),
                (off, ik_o[f"arm_{o}_sh"]),
                (D, 5 * sgn[o]),
            ],
        )
        c.key(
            f"arm_{o}_el", [(0, 10), (on, ik_o[f"arm_{o}_el"]), (off, ik_o[f"arm_{o}_el"]), (D, 10)]
        )
    c.key(
        f"arm_{s}_sh",
        [(0, 5 * sgn[s]), (on, ik[f"arm_{s}_sh"]), (off, ik[f"arm_{s}_sh"]), (D, 5 * sgn[s])],
    )
    c.key(f"arm_{s}_el", [(0, 10), (on, ik[f"arm_{s}_el"]), (off, ik[f"arm_{s}_el"]), (D, 10)])
    if p.style != "weep":  # wiping motion
        c.osc(f"arm_{s}_sh", amp=4.5, freq=1.1, t0=on, t1=off)
    return c


# ============================================================================== think
class ThinkParams(ParamsBase):
    style: Literal["chin", "scratch", "ponder"] = Field(
        "chin", description="hand to chin, scratching the head, or finger on lips"
    )
    aha: bool = Field(
        False, description="finish with a light-bulb moment (finger up, wide eyes, burst)"
    )


@register_action(
    "think",
    params_schema=ThinkParams,
    category="expression",
    summary="Hand to chin, eyes up, thought bubble; optional 'aha!' ending",
    min_duration=1.0,
    default_duration=2.5,
)
def think(ctx: ActionContext, p: ThinkParams) -> Clip:
    D = ctx.duration
    c = ctx.clip(blend_in=0.2, blend_out=0.3)
    on = min(0.55, 0.25 * D)
    aha_t = D - min(0.7, 0.3 * D) if p.aha else D + 1.0
    off = min(D - 0.25, aha_t) if p.aha else max(on, D - 0.3)
    pose = ctx.pose(torso_lean=2, head_tilt=9)
    s = ctx.free_hand()  # the hand without a prop does the thinking
    o = "l" if s == "r" else "r"
    sg, og = (1 if s == "r" else -1), (1 if o == "r" else -1)
    if p.style == "scratch":
        ik = ctx.rig.reach(pose, s, "head_top", offset=(12, 24))
    elif p.style == "ponder":
        ik = ctx.rig.reach(pose, s, "mouth", offset=(20, 4))
    else:
        ik = ctx.rig.reach(pose, s, "chin", offset=(10, 16))
    c.key(
        f"arm_{s}_sh",
        [(0, 5 * sg), (on, ik[f"arm_{s}_sh"], "overshoot"), (off, ik[f"arm_{s}_sh"]), (D, 5 * sg)],
    )
    c.key(
        f"arm_{s}_el",
        [(0, 10), (on, ik[f"arm_{s}_el"], "overshoot"), (off, ik[f"arm_{s}_el"]), (D, 10)],
    )
    c.key(
        f"arm_{o}_sh", [(0, 5 * og), (on, 26 * og), (D, 5 * og)]
    )  # the other hand rests on the hip
    c.key(f"arm_{o}_el", [(0, 10), (on, 58), (D, 10)])
    if p.style == "scratch":
        c.osc(f"hand_{s}_wr", amp=22, freq=3.4, t0=on, t1=off)
        c.osc(f"arm_{s}_sh", amp=2.5, freq=3.4, t0=on, t1=off)
    c.key("head_tilt", [(0, 0), (on, 9), (off, 9), (D, 0)])
    c.key("head_turn", [(0, 0), (on, 0.5), (off, 0.5), (D, 0)])
    c.key("eye_dx", [(0, 0), (on, 0.55), (off, 0.55), (D, 0)])
    c.key("eye_dy", [(0, 0), (on, -0.7), (off, -0.7), (D, 0)])
    c.key("brow_raise", [(0, 0), (on, 0.4), (off, 0.4), (D, 0)])
    c.key("brow_angle", [(0, 0), (on, -0.3), (off, -0.3), (D, 0)])
    c.key("mouth_smile", [(0, 0.3), (on, -0.15), (off, -0.15), (D, 0.3)])
    c.key("torso_lean", [(0, 0), (on, 2), (off, 2), (D, 0)])
    c.osc("torso_lean", amp=1.2, freq=0.42, t0=on, t1=off)
    c.key(
        "fx_think",
        [(0, 0), (on, 1.0, "overshoot"), (off - 0.1, 1.0), (off + 0.15, 0.0)]
        if p.aha
        else [(0, 0), (on, 1.0, "overshoot"), (off, 1.0), (D, 0)],
    )
    if p.aha:
        up = ctx.rig.reach(ctx.pose(torso_lean=-3, head_tilt=-4), s, "head_top", offset=(28, -90))
        t1 = aha_t + 0.18
        c.key(
            f"arm_{s}_sh",
            [
                (aha_t, ik[f"arm_{s}_sh"]),
                (t1, up[f"arm_{s}_sh"], "overshoot"),
                (D, up[f"arm_{s}_sh"]),
            ],
            mode="add",
        )
        c.key(
            f"arm_{s}_el",
            [
                (aha_t, ik[f"arm_{s}_el"]),
                (t1, up[f"arm_{s}_el"], "overshoot"),
                (D, up[f"arm_{s}_el"]),
            ],
            mode="add",
        )
        c.key(f"hand_{s}_point", [(aha_t, 0), (t1, 1), (D, 1)], ease="linear")
        c.key("eye_open", [(0, 1), (aha_t, 1), (t1, 1.3), (D, 1.2)])
        c.key("brow_raise", [(aha_t, 0.4), (t1, 1.0), (D, 0.8)], mode="add")
        c.key("mouth_open", [(aha_t, 0), (t1, 0.45), (D, 0.3)])
        c.key("mouth_smile", [(aha_t, -0.15), (t1, 1.0), (D, 1.0)], mode="add")
        c.key("fx_surprise", [(aha_t, 0), (t1, 1.0, "overshoot"), (D, 1.0)])
        c.key("head_tilt", [(aha_t, 9), (t1, -5), (D, -3)], mode="add")
        c.key("torso_lean", [(aha_t, 2), (t1, -4), (D, -3)], mode="add")
    return c


# ============================================================================== surprise
class SurpriseParams(ParamsBase):
    intensity: float = Field(1.0, ge=0.3, le=1.8, description="how big the jolt is")
    hold: float | None = Field(
        None,
        ge=0.0,
        le=4.0,
        description="comedic freeze at the peak, in seconds (default ~45% of the action)",
    )


@register_action(
    "surprise",
    params_schema=SurpriseParams,
    category="expression",
    summary="Jolt, recoil with wide eyes and an exclamation burst, then a comedic hold",
    min_duration=0.5,
    default_duration=1.2,
)
def surprise(ctx: ActionContext, p: SurpriseParams) -> Clip:
    D, k = ctx.duration, p.intensity
    c = ctx.clip(blend_in=0.05, blend_out=0.25)
    hold = p.hold if p.hold is not None else 0.45 * D
    t_a = min(0.08 * D, 0.15)  # tiny anticipation dip
    t_peak = t_a + min(0.14 * D, 0.2)
    t_rel = min(D - 0.15, t_peak + hold)  # when the hold ends
    c.key(
        "squash",
        [
            (0, 0),
            (t_a, 0.06),
            (t_peak, -0.16 * k, "ease_out"),
            (t_peak + 0.12, -0.04),
            (t_rel, -0.04),
            (D, 0, "overshoot"),
        ],
        mode="add",
    )
    c.key(
        "lift",
        [(0, 0), (t_a, 0), (t_peak, 16 * k, "ease_out"), (t_peak + 0.14, 0, "bounce"), (D, 0)],
        mode="add",
    )
    c.key(
        "hip_dx",
        [(0, 0), (t_peak, -20 * k, "ease_out"), (t_rel, -20 * k), (D, 0, "ease_in_out")],
        mode="add",
    )
    c.key(
        "torso_lean", [(0, 0), (t_a, 3), (t_peak, -13 * k, "overshoot"), (t_rel, -11 * k), (D, 0)]
    )
    c.key("head_tilt", [(0, 0), (t_peak, -8 * k, "overshoot"), (t_rel, -7 * k), (D, 0)])
    for s, sg in (("r", 1), ("l", -1)):
        c.key(
            f"arm_{s}_sh",
            [
                (0, 5 * sg),
                (t_a, 0),
                (t_peak, 52 * sg * k, "overshoot"),
                (t_rel, 46 * sg * k),
                (D, 5 * sg),
            ],
        )
        c.key(f"arm_{s}_el", [(0, 10), (t_peak, 26, "overshoot"), (t_rel, 24), (D, 10)])
        c.key(f"hand_{s}_wr", [(0, 0), (t_peak, 24 * sg, "overshoot"), (t_rel, 20 * sg), (D, 0)])
    c.key("eye_open", [(0, 1), (t_a, 0.9), (t_peak, 1.38, "overshoot"), (t_rel, 1.32), (D, 1)])
    c.key("brow_raise", [(0, 0), (t_peak, 1.0, "overshoot"), (t_rel, 0.95), (D, 0)])
    c.key("mouth_open", [(0, 0), (t_peak, 0.85 * k, "overshoot"), (t_rel, 0.8 * k), (D, 0)])
    c.key("mouth_wide", [(0, 0), (t_peak, 0.0), (D, 0.0)])
    c.key("mouth_smile", [(0, 0.3), (t_peak, -0.2), (t_rel, -0.2), (D, 0.3)])
    c.key("sweat", [(0, 0), (t_peak + 0.1, 0.0), (t_peak + 0.5, 0.7), (D, 0)])
    c.key(
        "fx_surprise", [(0, 0), (t_peak, 1.0, "overshoot"), (t_rel, 1.0), (min(D, t_rel + 0.2), 0)]
    )
    c.event(t_peak, "gasp")
    return c
