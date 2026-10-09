"""The rig: animatable channels, forward kinematics and two-bone IK.

A *pose* is just ``dict[channel, float]``.  `solve()` turns a pose + body proportions
into a :class:`PosedFigure` - joint positions and world angles in "figure space"
(x right, y down, origin = ground contact point between the feet, figure facing +x).
Style packs never see channels; they see the solved figure and decide how to draw
it, which is what keeps every action style-agnostic.

Angle convention (degrees, sagittal-plane like a side-view puppet): 0 = limb hanging
straight down, +90 = pointing forward (+x), +180 = straight up.  Joint angles are
relative to the parent, except `torso_lean` (relative to vertical) - so arms follow
the torso when it leans.  Knee/elbow flexion is always >= 0 (shin goes back, forearm
comes forward).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from reel.core.archetypes import Archetype, RigDims


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    default: float
    lo: float
    hi: float
    unit: str  # deg | px | norm
    group: str  # root | body | arm | leg | face | fx | secondary
    doc: str = ""


CHANNELS: dict[str, ChannelSpec] = {}


def _ch(
    name: str, default: float, lo: float, hi: float, unit: str, group: str, doc: str = ""
) -> None:
    CHANNELS[name] = ChannelSpec(name, default, lo, hi, unit, group, doc)


# -- root / whole body -------------------------------------------------------------------
_ch("face", 1.0, -1, 1, "norm", "root", "facing: +1 right, -1 left (animates through 0 as a turn)")
_ch("lift", 0.0, 0, 2000, "px", "root", "height above the ground (jumps, hops)")
_ch("rot", 0.0, -180, 180, "deg", "root", "whole-body rotation about the feet, + = forward")
_ch(
    "squash",
    0.0,
    -0.6,
    0.6,
    "norm",
    "root",
    "squash(+) compresses vertically, stretch(-) elongates; volume preserved",
)
_ch("ground_lock", 1.0, 0, 1, "norm", "root", "1 = keep the lowest foot on the ground")
# -- body ---------------------------------------------------------------------------------
_ch("bob", 0.0, -260, 120, "px", "body", "hips up(+)/down(-)")
_ch(
    "rot_hip",
    0.0,
    -180,
    180,
    "deg",
    "body",
    "whole-body rotation about the hips (pratfalls, flips)",
)
_ch("hip_dx", 0.0, -120, 120, "px", "body", "hips sideways shift")
_ch("torso_lean", 0.0, -90, 90, "deg", "body", "lean forward(+)/back(-) from vertical")
_ch("breath", 0.0, -1, 1, "norm", "body", "chest expansion")
_ch("head_tilt", 0.0, -60, 60, "deg", "body", "head tilt forward(+)/back(-), relative to torso")
_ch("head_dx", 0.0, -40, 40, "px", "body")
_ch("head_dy", 0.0, -40, 40, "px", "body", "head up(+)/down(-)")
_ch("head_turn", 0.0, -1, 1, "norm", "body", "face turns forward-facing(+) / away(-)")
# -- limbs ---------------------------------------------------------------------------------
for _s, _sign in (("r", 1), ("l", -1)):
    _ch(f"arm_{_s}_sh", 5.0 * _sign, -220, 220, "deg", "arm", "shoulder swing (relative to torso)")
    _ch(f"arm_{_s}_el", 10.0, -15, 170, "deg", "arm", "elbow flexion")
    _ch(f"hand_{_s}_wr", 0.0, -90, 90, "deg", "arm", "wrist")
    _ch(f"hand_{_s}_point", 0.0, 0, 1, "norm", "arm", "pointing finger")
    _ch(f"leg_{_s}_hip", 0.0, -120, 120, "deg", "leg", "thigh swing")
    _ch(f"leg_{_s}_kn", 0.0, 0, 150, "deg", "leg", "knee flexion")
    _ch(f"foot_{_s}_an", 0.0, -50, 50, "deg", "leg", "ankle (toes up +)")
# -- face ---------------------------------------------------------------------------------
_ch("eye_open", 1.0, 0, 1.4, "norm", "face", "eyelid opening (1 = normal, >1 = wide)")
_ch("blink", 0.0, 0, 1, "norm", "face", "automatic blink closure")
_ch("brow_raise", 0.0, -1, 1, "norm", "face")
_ch("brow_angle", 0.0, -1, 1, "norm", "face", "-1 angry .. +1 worried/sad")
_ch("mouth_open", 0.0, 0, 1, "norm", "face")
_ch("mouth_smile", 0.3, -1, 1, "norm", "face", "frown(-) .. smile(+)")
_ch("mouth_wide", 0.0, 0, 1, "norm", "face", "stretch the mouth wider (laugh/shout)")
_ch("eye_dx", 0.0, -1, 1, "norm", "face", "gaze left/right")
_ch("eye_dy", 0.0, -1, 1, "norm", "face", "gaze up(-)/down(+)")
_ch("blush", 0.0, 0, 1, "norm", "face")
_ch("tear", 0.0, 0, 1, "norm", "face")
_ch("sweat", 0.0, 0, 1, "norm", "face")
# -- fx markers (drawn by the style) -----------------------------------------------------------
_ch("fx_think", 0.0, 0, 1, "norm", "fx", "thought bubble")
_ch("fx_surprise", 0.0, 0, 1, "norm", "fx", "exclamation burst")
# -- secondary motion: written by the animator, not by actions ---------------------------------
_ch("sway", 0.0, -2, 2, "norm", "secondary", "hair/scarf/cape sway from movement")
_ch("hand_lag_r", 0.0, -60, 60, "deg", "secondary", "follow-through of the hand")
_ch("hand_lag_l", 0.0, -60, 60, "deg", "secondary")
_ch("head_lag", 0.0, -20, 20, "deg", "secondary", "follow-through of the head")

CHANNEL_NAMES: tuple[str, ...] = tuple(CHANNELS)
PROP_PREFIX = "prop:"


def is_channel(name: str) -> bool:
    return name in CHANNELS or name.startswith(PROP_PREFIX)


def squash_scale(squash: float) -> tuple[float, float]:
    """(sx, sy) for a squash value: + compresses vertically, - stretches; area is preserved."""
    sy = max(0.25, 1.0 - squash)
    return 1.0 / sy, sy


def default_pose() -> dict[str, float]:
    return {n: c.default for n, c in CHANNELS.items()}


Vec = tuple[float, float]


@dataclass
class PosedFigure:
    """Solved skeleton in figure space (y down, origin at the ground between the feet)."""

    pts: dict[str, Vec]
    ang: dict[str, float]  # world angles in degrees
    pose: dict[str, float]
    dims: RigDims
    ground_shift: float = 0.0  # vertical shift applied by ground lock/lift (for shadows)
    notes: dict[str, float] = field(default_factory=dict)

    @property
    def lift(self) -> float:
        return self.pose.get("lift", 0.0)


def _dir(a: float) -> Vec:
    """Unit vector of a limb at angle ``a`` (rad): 0 = down, +pi/2 = forward (+x)."""
    return (math.sin(a), math.cos(a))


def _up(a: float) -> Vec:
    return (math.sin(a), -math.cos(a))


def _add(p: Vec, q: Vec, k: float = 1.0) -> Vec:
    return (p[0] + q[0] * k, p[1] + q[1] * k)


def solve(dims: RigDims, pose: Mapping[str, float]) -> PosedFigure:
    """Forward kinematics + ground lock."""
    P = default_pose()
    P.update(pose)
    d = dims
    rad = math.radians

    th = rad(P["torso_lean"])
    hip_c: Vec = (P["hip_dx"], -(d.hip_height + P["bob"]))
    pts: dict[str, Vec] = {"hip_c": hip_c}
    ang: dict[str, float] = {"torso": P["torso_lean"]}

    # legs ------------------------------------------------------------------------------
    sole_ys: list[float] = []
    for s, sx in (("r", 1.0), ("l", -1.0)):
        hip = (hip_c[0] + sx * d.hip_w / 2, hip_c[1])
        a_hip = rad(P[f"leg_{s}_hip"])
        knee = _add(hip, _dir(a_hip), d.thigh)
        a_shin = a_hip - rad(P[f"leg_{s}_kn"])
        ankle = _add(knee, _dir(a_shin), d.shin)
        pitch = rad(max(-70.0, min(70.0, P[f"foot_{s}_an"] + 0.6 * math.degrees(a_shin))))
        fwd = (math.cos(pitch), -math.sin(pitch))
        dn = (math.sin(pitch), math.cos(pitch))
        heel = _add(_add(ankle, fwd, -d.heel), dn, d.foot_h)
        toe = _add(_add(ankle, fwd, d.toe), dn, d.foot_h)
        pts.update(
            {
                f"hip_{s}": hip,
                f"knee_{s}": knee,
                f"ankle_{s}": ankle,
                f"heel_{s}": heel,
                f"toe_{s}": toe,
            }
        )
        ang.update(
            {
                f"thigh_{s}": math.degrees(a_hip),
                f"shin_{s}": math.degrees(a_shin),
                f"foot_{s}": math.degrees(pitch),
            }
        )
        sole_ys += [heel[1], toe[1]]

    # torso -----------------------------------------------------------------------------
    tl = d.torso_len * (1.0 + 0.012 * P["breath"])
    shoulder_c = _add(hip_c, _up(th), tl)
    perp = (math.cos(th), math.sin(th))
    pts["shoulder_c"] = shoulder_c
    pts["shoulder_r"] = _add(shoulder_c, perp, d.shoulder_w / 2)
    pts["shoulder_l"] = _add(shoulder_c, perp, -d.shoulder_w / 2)
    pts["belly"] = _add(hip_c, _up(th), tl * 0.38)
    pts["chest"] = _add(hip_c, _up(th), tl * 0.72)

    # arms ------------------------------------------------------------------------------
    for s in ("r", "l"):
        a1 = th + rad(P[f"arm_{s}_sh"])
        elbow = _add(pts[f"shoulder_{s}"], _dir(a1), d.upper_arm)
        a2 = a1 + rad(P[f"arm_{s}_el"])
        wrist = _add(elbow, _dir(a2), d.fore_arm)
        a3 = a2 + rad(P[f"hand_{s}_wr"] + P[f"hand_lag_{s}"])
        hand = _add(wrist, _dir(a3), d.hand_r * 0.85)
        pts.update({f"elbow_{s}": elbow, f"wrist_{s}": wrist, f"hand_{s}": hand})
        ang.update(
            {
                f"upper_arm_{s}": math.degrees(a1),
                f"fore_arm_{s}": math.degrees(a2),
                f"hand_{s}": math.degrees(a3),
            }
        )

    # head ------------------------------------------------------------------------------
    neck_top = _add(shoulder_c, _up(th), d.neck)
    ha = th + rad(P["head_tilt"] + P["head_lag"])
    head_c = _add(neck_top, _up(ha), d.head_ry)
    head_c = (head_c[0] + P["head_dx"], head_c[1] - P["head_dy"])
    pts["neck_top"] = neck_top
    pts["head_c"] = head_c
    pts["chin"] = _add(head_c, _up(ha), -d.head_ry)
    pts["head_top"] = _add(head_c, _up(ha), d.head_ry)
    ax, ay = _up(ha)  # head "up" axis; "forward" axis is (cos, sin) of the same angle
    fx_, fy_ = math.cos(ha), math.sin(ha)
    pts["mouth"] = (
        head_c[0] + fx_ * d.head_rx * 0.22 - ax * d.head_ry * 0.42,
        head_c[1] + fy_ * d.head_rx * 0.22 - ay * d.head_ry * 0.42,
    )
    pts["eye_c"] = (
        head_c[0] + fx_ * d.head_rx * 0.22 + ax * d.head_ry * 0.08,
        head_c[1] + fy_ * d.head_rx * 0.22 + ay * d.head_ry * 0.08,
    )
    ang["head"] = math.degrees(ha)

    # ground lock + lift --------------------------------------------------------------------
    lock = max(0.0, min(1.0, P["ground_lock"]))
    shift = lock * (-max(sole_ys)) - P["lift"]
    if shift:
        pts = {k: (x, y + shift) for k, (x, y) in pts.items()}
    # rotation about the hips (pratfalls): turn every point about the hip joint centre
    rh = P["rot_hip"]
    if rh:
        c, s_ = math.cos(math.radians(rh)), math.sin(math.radians(rh))
        hx, hy = pts["hip_c"]
        pts = {
            k: (hx + (x - hx) * c - (y - hy) * s_, hy + (x - hx) * s_ + (y - hy) * c)
            for k, (x, y) in pts.items()
        }
        ang = {k: v + rh for k, v in ang.items()}
    return PosedFigure(pts=pts, ang=ang, pose=P, dims=d, ground_shift=shift)


# ------------------------------------------------------------------------------- IK
def arm_ik(
    dims: RigDims, shoulder: Vec, target: Vec, torso_lean_deg: float, *, bend: float = 1.0
) -> tuple[float, float]:
    """Two-bone IK. Returns (shoulder angle relative to torso, elbow flexion) in degrees that
    puts the wrist at ``target`` (clamped to reachable distance)."""
    l1, l2 = dims.upper_arm, dims.fore_arm
    dx, dy = target[0] - shoulder[0], target[1] - shoulder[1]
    dist = max(abs(l1 - l2) + 1e-3, min(math.hypot(dx, dy), l1 + l2 - 1e-3))
    phi = math.atan2(dx, dy)
    alpha = math.acos(max(-1.0, min(1.0, (l1 * l1 + dist * dist - l2 * l2) / (2 * l1 * dist))))
    interior = math.acos(max(-1.0, min(1.0, (l1 * l1 + l2 * l2 - dist * dist) / (2 * l1 * l2))))
    flex = math.pi - interior
    a1 = phi - bend * alpha
    return math.degrees(a1) - torso_lean_deg, math.degrees(flex) * (1.0 if bend >= 0 else -1.0)


class Rig:
    """A body (archetype) you can pose, solve and ask questions about."""

    def __init__(self, archetype: Archetype) -> None:
        self.archetype = archetype
        self.dims = archetype.dims

    def rest_pose(self) -> dict[str, float]:
        p = default_pose()
        p.update(self.archetype.rest)
        return p

    def solve(self, pose: Mapping[str, float]) -> PosedFigure:
        return solve(self.dims, pose)

    def landmark(self, pose: Mapping[str, float], name: str) -> Vec:
        """A point of interest on the posed body in figure space (``chin``, ``belly``, ...)."""
        fig = self.solve(pose)
        if name in fig.pts:
            return fig.pts[name]
        raise KeyError(f"unknown landmark {name!r}; known: {sorted(fig.pts)}")

    def reach(
        self,
        pose: Mapping[str, float],
        side: str,
        target: Vec | str,
        *,
        offset: Vec = (0.0, 0.0),
    ) -> dict[str, float]:
        """Arm channels (``arm_<side>_sh``/``arm_<side>_el``) that bring the wrist to ``target``
        (a figure-space point or a landmark name) given the rest of ``pose``."""
        fig = self.solve(pose)
        tgt = fig.pts[target] if isinstance(target, str) else target
        tgt = (tgt[0] + offset[0], tgt[1] + offset[1])
        sh, el = arm_ik(self.dims, fig.pts[f"shoulder_{side}"], tgt, fig.pose["torso_lean"])
        return {f"arm_{side}_sh": sh, f"arm_{side}_el": el}

    def ground_point(self, forward: float, pose: Mapping[str, float] | None = None) -> Vec:
        """Point on the floor ``forward`` px in front of the feet (for reaching down)."""
        return (forward, -self.dims.hand_r * 0.6)
