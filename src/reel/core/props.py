"""Props (worn, held, or lying on the floor) and expression FX markers, as shape IR."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from reel.core.archetypes import PICKUP_FORWARD, PICKUP_REACH, PropDef
from reel.core.figure import SB
from reel.core.geometry import Pt, Rot2D, clamp, darken, ellipse_pts, lighten
from reel.core.rig import PosedFigure


@dataclass
class PropCtx:
    sb: SB
    fig: PosedFigure
    pd: PropDef
    pose: dict[str, float]
    palette: dict[str, str]
    t: float
    worn: bool  # attached at home (held/worn) vs lying on the floor
    F: Rot2D  # frame whose origin is the grip/attach point
    H: Rot2D  # head frame
    T: Rot2D  # torso frame at the hips
    sway: float
    gx: float  # x of the floor spot in front of the feet
    colors: tuple[str, ...]


# ------------------------------------------------------------------------------- held props
def _briefcase(c: PropCtx) -> None:
    F, sb = c.F, c.sb
    body, band = c.colors[0], c.colors[1] if len(c.colors) > 1 else darken(c.colors[0], 0.3)
    sb.path(
        [
            ("M", *F.loc(-17, 6)),
            ("L", *F.loc(-17, -8)),
            ("L", *F.loc(17, -8)),
            ("L", *F.loc(17, 6)),
        ],
        None,
        band,
        8,
        tag="prop_handle",
        lod=1,
        shadow=False,
    )
    sb.poly(
        F.locs([(-46, 4), (46, 4), (46, 70), (-46, 70)]),
        body,
        smooth=0.1,
        material="fabric",
        tag="prop",
        elev=4,
    )
    sb.poly(
        F.locs([(-46, 22), (46, 22), (46, 29), (-46, 29)]),
        band,
        tag="prop_band",
        lod=1,
        shadow=False,
    )
    sb.poly(
        F.locs([(-8, 20), (8, 20), (8, 34), (-8, 34)]),
        "#e0b24a",
        smooth=0.2,
        tag="prop_clasp",
        lod=2,
        shadow=False,
        material="metal",
    )


def _coffee(c: PropCtx) -> None:
    F, sb = c.F, c.sb
    sb.poly(
        F.locs([(-22, 8), (22, 8), (17, 64), (-17, 64)]),
        c.colors[0],
        smooth=0.1,
        tag="prop",
        elev=4,
    )
    sb.poly(
        F.locs([(-20, 26), (20, 26), (18.5, 46), (-18.5, 46)]),
        c.colors[1],
        tag="prop_sleeve",
        lod=1,
        shadow=False,
    )
    sb.poly(
        F.locs([(-26, -2), (26, -2), (24, 12), (-24, 12)]),
        "#3b2a20",
        smooth=0.3,
        tag="prop_lid",
        lod=1,
        shadow=False,
    )


def _flower(c: PropCtx) -> None:
    F, sb = c.F, c.sb
    green = "#43a047"
    sb.limb(F.loc(0, 34), F.loc(0, -92), 8, 6, green, tag="prop_stem", elev=3)
    sb.ellipse(*F.loc(14, -30), 20, 8, green, rot=F.deg - 30, tag="prop_leaf", lod=1, shadow=False)
    ctr = F.loc(0, -102)
    for i in range(6):
        a = i * math.pi / 3 + 0.3
        sb.ellipse(
            ctr[0] + math.cos(a) * 20,
            ctr[1] + math.sin(a) * 20,
            15,
            15,
            c.colors[0],
            tag="prop_petal",
            elev=4,
        )
    sb.ellipse(ctr[0], ctr[1], 12, 12, "#ffd23f", tag="prop", elev=5)


def _umbrella(c: PropCtx) -> None:
    F, sb = c.F, c.sb
    canopy, ribs = c.colors[0], c.colors[1] if len(c.colors) > 1 else "#202028"
    sb.path(
        [("M", *F.loc(0, 36)), ("L", *F.loc(0, 62)), ("Q", *F.loc(0, 84), *F.loc(-20, 78))],
        None,
        ribs,
        8,
        tag="prop_handle",
        lod=1,
        shadow=False,
    )
    sb.line(F.loc(0, 40), F.loc(0, -190), 7, ribs, tag="prop_shaft", lod=0)
    pts: list[Pt] = ellipse_pts(0, -190, 150, 92, 180, 360, 14)
    n = 5
    for i in range(n):  # scalloped hem
        x0, x1 = 150 - 300 * i / n, 150 - 300 * (i + 1) / n
        pts.append(((x0 + x1) / 2, -190 + 16))
        pts.append((x1, -190))
    sb.poly(F.locs(pts), canopy, smooth=0.08, material="fabric", tag="prop", elev=5)
    for k in (-0.55, 0.0, 0.55):
        sb.line(
            F.loc(0, -280),
            F.loc(150 * k * 1.0, -192),
            3.5,
            darken(canopy, 0.25),
            tag="prop_rib",
            lod=2,
        )
    sb.ellipse(*F.loc(0, -284), 7, 7, ribs, tag="prop_tip", lod=1, shadow=False)


def _phone(c: PropCtx) -> None:
    F, sb = c.F, c.sb
    sb.poly(
        F.locs([(-17, -8), (17, -8), (17, 50), (-17, 50)]),
        c.colors[0],
        smooth=0.18,
        material="metal",
        tag="prop",
        elev=4,
    )
    sb.poly(
        F.locs([(-13, -3), (13, -3), (13, 41), (-13, 41)]),
        c.colors[1],
        smooth=0.12,
        material="emissive",
        glow=10,
        tag="prop_screen",
        lod=1,
        shadow=False,
    )


def _book(c: PropCtx) -> None:
    F, sb = c.F, c.sb
    sb.poly(
        F.locs([(-36, -2), (36, -2), (36, 90), (-36, 90)]),
        c.colors[0],
        smooth=0.06,
        material="fabric",
        tag="prop",
        elev=4,
    )
    sb.poly(
        F.locs([(32, 2), (36, 2), (36, 88), (32, 88)]),
        c.colors[1],
        tag="prop_pages",
        lod=1,
        shadow=False,
    )
    sb.poly(
        F.locs([(-36, 8), (-30, 8), (-30, 82), (-36, 82)]),
        darken(c.colors[0], 0.25),
        tag="prop_spine",
        lod=1,
        shadow=False,
    )
    sb.poly(
        F.locs([(-18, 24), (18, 24), (18, 36), (-18, 36)]),
        lighten(c.colors[0], 0.55),
        smooth=0.2,
        tag="prop_title",
        lod=2,
        shadow=False,
    )


def _balloon(c: PropCtx) -> None:
    F, sb = c.F, c.sb
    k = c.sway * 30
    base = F.loc(0, 0)
    bx, by = base[0] + k, base[1] - 232
    sb.line(base, (bx, by + 62), 3, "#9aa0a6", tag="prop_string", lod=1)
    sb.ellipse(bx, by, 54, 68, c.colors[0], tag="prop", material="paper", elev=5)
    sb.poly(
        [(bx - 9, by + 66), (bx + 9, by + 66), (bx, by + 78)],
        darken(c.colors[0], 0.15),
        smooth=0.2,
        tag="prop_knot",
        lod=1,
        shadow=False,
    )
    sb.ellipse(
        bx - 18,
        by - 24,
        10,
        17,
        "#ffffff",
        rot=24,
        alpha=0.5,
        tag="prop_glint",
        lod=1,
        shadow=False,
    )


# ------------------------------------------------------------------------------- worn props
def _hat(c: PropCtx) -> None:
    H, sb = c.H, c.sb
    # head-local sizes follow the head: rx/ry from fig dims
    rx, ry = c.fig.dims.head_rx, c.fig.dims.head_ry
    brim, band = c.colors[0], c.colors[1] if len(c.colors) > 1 else darken(c.colors[0], 0.3)
    sb.ellipse(
        *H.loc(0, -ry * 0.80),
        rx * 1.28,
        ry * 0.17,
        brim,
        rot=H.deg,
        material="fabric",
        tag="prop",
        elev=6,
    )
    sb.poly(
        H.locs(
            [
                (-rx * 0.64, -ry * 0.80),
                (-rx * 0.56, -ry * 1.42),
                (rx * 0.56, -ry * 1.42),
                (rx * 0.64, -ry * 0.80),
            ]
        ),
        brim,
        smooth=0.3,
        material="fabric",
        tag="prop",
        elev=6,
    )
    sb.poly(
        H.locs(
            [
                (-rx * 0.63, -ry * 0.86),
                (rx * 0.63, -ry * 0.86),
                (rx * 0.61, -ry * 1.03),
                (-rx * 0.61, -ry * 1.03),
            ]
        ),
        band,
        tag="prop_band",
        lod=1,
        shadow=False,
    )


def _cap(c: PropCtx) -> None:
    H, sb = c.H, c.sb
    rx, ry = c.fig.dims.head_rx, c.fig.dims.head_ry
    dome = ellipse_pts(0, -ry * 0.60, rx * 1.03, ry * 0.52, 180, 360, 12)
    sb.poly(H.locs(dome), c.colors[0], smooth=0.1, material="fabric", tag="prop", elev=6)
    sb.poly(
        H.locs(
            [
                (rx * 0.25, -ry * 0.68),
                (rx * 1.55, -ry * 0.56),
                (rx * 1.50, -ry * 0.46),
                (rx * 0.2, -ry * 0.55),
            ]
        ),
        darken(c.colors[0], 0.18),
        smooth=0.3,
        material="fabric",
        tag="prop_visor",
        elev=6,
    )
    sb.ellipse(
        *H.loc(0, -ry * 1.12),
        9,
        9,
        c.colors[1] if len(c.colors) > 1 else "#ffffff",
        tag="prop_button",
        lod=2,
        shadow=False,
    )


def glasses(sb: SB, H: Rot2D, rx: float, ry: float, pose: dict[str, float], color: str) -> None:
    shift = (0.20 + 0.26 * pose["head_turn"]) * rx
    ey = -ry * 0.08
    for sx in (-1, 1):
        c = H.loc(shift + sx * rx * 0.31, ey)
        sb.ellipse(
            c[0],
            c[1],
            rx * 0.245,
            ry * 0.30,
            None,
            rot=H.deg,
            stroke=color,
            sw=6.5,
            tag="glasses",
            lod=1,
            shadow=False,
        )
    sb.line(
        H.loc(shift - rx * 0.07, ey), H.loc(shift + rx * 0.07, ey), 5, color, tag="glasses", lod=1
    )
    sb.line(
        H.loc(shift + rx * 0.56, ey),
        H.loc(rx * 0.98, ey - ry * 0.02),
        5,
        color,
        tag="glasses",
        lod=2,
    )


def _glasses_prop(c: PropCtx) -> None:
    glasses(c.sb, c.H, c.fig.dims.head_rx, c.fig.dims.head_ry, c.pose, c.colors[0])


def _scarf(c: PropCtx) -> None:
    sb, d, T = c.sb, c.fig.dims, c.T
    col = c.colors[0]
    neck = c.fig.pts["neck_top"]
    sb.ellipse(
        neck[0],
        neck[1] + 8,
        d.chest_w * 0.30,
        22,
        col,
        rot=T.deg,
        material="fabric",
        tag="prop",
        elev=6,
    )
    k = c.sway * 46
    tail = [
        T.loc(d.chest_w * 0.10, -d.torso_len * 0.96),
        T.loc(d.chest_w * 0.30, -d.torso_len * 0.94),
        (
            T.loc(d.chest_w * 0.30, -d.torso_len * 0.45)[0] + k,
            T.loc(0, -d.torso_len * 0.45)[1] + 10,
        ),
        (
            T.loc(d.chest_w * 0.12, -d.torso_len * 0.40)[0] + k * 1.2,
            T.loc(0, -d.torso_len * 0.38)[1] + 22,
        ),
    ]
    sb.poly(tail, darken(col, 0.08), smooth=0.2, material="fabric", tag="prop", elev=6)
    sb.poly(
        [
            (tail[2][0], tail[2][1] - 4),
            (tail[3][0], tail[3][1] - 4),
            (tail[3][0], tail[3][1] + 4),
            (tail[2][0], tail[2][1] + 4),
        ],
        lighten(col, 0.4),
        tag="prop_stripe",
        lod=2,
        shadow=False,
    )


def _backpack(c: PropCtx) -> None:
    sb, d, T = c.sb, c.fig.dims, c.T
    col, strap = c.colors[0], c.colors[1] if len(c.colors) > 1 else darken(c.colors[0], 0.3)
    x0 = -d.chest_w * 0.5 - 40
    sb.poly(
        T.locs(
            [
                (x0, -d.torso_len * 0.92),
                (x0 + 62, -d.torso_len * 0.92),
                (x0 + 62, -d.torso_len * 0.08),
                (x0, -d.torso_len * 0.08),
            ]
        ),
        col,
        smooth=0.22,
        material="fabric",
        tag="prop",
        elev=1,
    )
    sb.poly(
        T.locs(
            [
                (x0 + 4, -d.torso_len * 0.45),
                (x0 + 40, -d.torso_len * 0.45),
                (x0 + 40, -d.torso_len * 0.15),
                (x0 + 4, -d.torso_len * 0.15),
            ]
        ),
        strap,
        smooth=0.2,
        tag="prop_pocket",
        lod=1,
        shadow=False,
        material="fabric",
    )


_HELD: dict[str, tuple[Callable[[PropCtx], None], float, float]] = {
    # name: (builder, hang = distance from grip to bottom of prop, ground rotation deg)
    "briefcase": (_briefcase, 70.0, 0.0),
    "coffee": (_coffee, 64.0, 0.0),
    "flower": (_flower, 34.0, 0.0),
    "umbrella": (_umbrella, 80.0, -76.0),
    "phone": (_phone, 50.0, 0.0),
    "book": (_book, 90.0, 0.0),
    "balloon": (_balloon, 0.0, 0.0),
}
_WORN: dict[str, Callable[[PropCtx], None]] = {
    "hat": _hat,
    "cap": _cap,
    "glasses": _glasses_prop,
    "scarf": _scarf,
    "backpack": _backpack,
}
#: held props that stay upright (gravity) instead of rotating with the forearm: how much of the hand angle to follow
_UPRIGHT = {
    "umbrella": 0.25,
    "coffee": 0.2,
    "balloon": 0.0,
    "flower": 0.3,
    "briefcase": 0.12,
    "book": 0.5,
    "phone": 0.6,
}


def build_prop(
    pd: PropDef,
    fig: PosedFigure,
    pose: dict[str, float],
    palette: dict[str, str],
    weight: float,
    t: float,
    sb: SB,
    layer: str,
) -> None:
    """Add one prop's shapes to ``sb``.  weight >= 0.5: at its home (held/worn); else on the floor."""
    d = fig.dims
    worn = weight >= 0.5
    H = Rot2D(fig.pts["head_c"], fig.ang["head"])
    T = Rot2D(fig.pts["hip_c"], pose["torso_lean"])
    gx = PICKUP_REACH * d.height + PICKUP_FORWARD
    colors = pd.colors or ("#cccccc", "#888888")
    if pd.name in _HELD:
        builder, hang, ground_rot = _HELD[pd.name]
        if worn:
            side = "l" if pd.home == "hand_l" else "r"
            hp = fig.pts[f"hand_{side}"]
            ang = fig.ang[f"hand_{side}"] * _UPRIGHT.get(pd.name, 1.0)
            F = _hand_frame(hp, ang)
        else:
            F = _hand_frame((gx, -hang), ground_rot)
        c = PropCtx(sb, fig, pd, pose, palette, t, worn, F, H, T, pose["sway"], gx, colors)
        builder(c)
        return
    builder_w = _WORN.get(pd.name)
    if builder_w is None:
        return
    if worn:
        builder_w(
            PropCtx(
                sb,
                fig,
                pd,
                pose,
                palette,
                t,
                True,
                _hand_frame((0.0, 0.0), 0.0),
                H,
                T,
                pose["sway"],
                gx,
                colors,
            )
        )
    else:  # dropped on the floor: a simple stand-in blob so nothing vanishes
        sb.ellipse(gx, -18, pd.size * 0.42, 18, colors[0], tag="prop", elev=3)


def _hand_frame(origin: Pt, hand_deg: float) -> Rot2D:
    """Frame at a grip point: local +y follows the hand direction (down when the arm hangs).

    The hand direction (sin a, cos a) is the y-down rotation by -a of the local +y axis."""
    return Rot2D(origin, -hand_deg)


# ------------------------------------------------------------------------------- fx markers
def fx_markers(
    sb: SB,
    H: Rot2D,
    hc: Pt,
    rx: float,
    ry: float,
    pose: dict[str, float],
    t: float,
    palette: dict[str, str],
) -> None:
    th = pose["fx_think"]
    if th > 0.04:
        s = 0.35 + 0.65 * clamp(th, 0, 1.2)
        bx, by = hc[0] + rx * 1.25, hc[1] - ry * 1.55
        for r, ox, oy in ((10, -118, 100), (16, -88, 62), (24, -52, 26)):
            sb.ellipse(
                bx + ox * s,
                by + oy * s,
                r * s,
                r * s,
                "#ffffff",
                tag="thought",
                stroke="#9aa3b2",
                sw=3.5,
                alpha=clamp(th * 1.4, 0, 1),
                lod=1,
                shadow=False,
            )
        for cx, cy, r in ((0, 0, 58), (-52, 8, 42), (52, 8, 44), (-26, -26, 40), (28, -28, 40)):
            sb.ellipse(
                bx + cx * s,
                by + cy * s,
                r * s,
                r * s * 0.85,
                "#ffffff",
                tag="thought",
                alpha=clamp(th * 1.4, 0, 1),
                lod=1,
                shadow=False,
            )
        for i in range(3):
            a = clamp(th * 1.4 - i * 0.25, 0, 1)
            sb.ellipse(
                bx + (-30 + i * 30) * s,
                by + 4 * s,
                7 * s,
                7 * s,
                "#6b7280",
                tag="thought",
                alpha=a,
                lod=1,
                shadow=False,
            )
    su = pose["fx_surprise"]
    if su > 0.04:
        pop = 0.45 + 0.55 * clamp(su, 0, 1.2)
        ex, ey = hc[0] + rx * 0.95, hc[1] - ry * 1.50
        col, edge = "#ff3b30", "#ffffff"
        sb.poly(
            [
                (ex - 17 * pop, ey - 78 * pop),
                (ex + 17 * pop, ey - 78 * pop),
                (ex + 9 * pop, ey + 12 * pop),
                (ex - 9 * pop, ey + 12 * pop),
            ],
            col,
            smooth=0.3,
            stroke=edge,
            sw=5,
            tag="exclaim",
            lod=1,
            shadow=False,
        )
        sb.ellipse(
            ex,
            ey + 38 * pop,
            12 * pop,
            12 * pop,
            col,
            stroke=edge,
            sw=5,
            tag="exclaim",
            lod=1,
            shadow=False,
        )
        for ang in (-62, -30, 0, 30, 62):
            a = math.radians(ang - 90)
            sb.line(
                (ex + math.cos(a) * 60 * pop, ey - 20 * pop + math.sin(a) * 56 * pop),
                (ex + math.cos(a) * 84 * pop, ey - 20 * pop + math.sin(a) * 80 * pop),
                6,
                "#ffcc00",
                tag="burst",
                lod=1,
                alpha=clamp(su, 0, 1),
            )


def known_props() -> tuple[Any, ...]:
    return tuple(_HELD) + tuple(_WORN)
