"""Posed figure -> shape IR.

Everything here is style-agnostic: it knows the *anatomy* (limbs, torso, head, face, hair,
outfit) and emits `Shape`s tagged with what they are.  Style packs decide how to paint them.
Coordinates are figure space (y down, origin = ground contact, figure faces +x); the stage
applies position/scale/mirroring when it draws.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from reel.core.archetypes import Archetype, PropDef
from reel.core.geometry import Pt, Rot2D, clamp, darken, lighten
from reel.core.ir import Ellipse, Geom, Limb, Line, PathG, Poly, Rect, Shape
from reel.core.rig import PosedFigure

SPRITE_Z = 1000.0  # figure shapes are ordered by list position; z is a relative hint for styles


@dataclass
class ShadowInfo:
    cx: float
    rx: float
    ry: float
    strength: float  # 0..1 (falls with height above the ground)


@dataclass
class FigureBuild:
    shapes: list[Shape]
    shadow: ShadowInfo
    extent: tuple[float, float, float, float] = (
        0.0,
        0.0,
        0.0,
        0.0,
    )  # x0,y0,x1,y1 of the posed body
    meta: dict[str, Any] = field(default_factory=dict)


class SB:
    """Tiny shape-list builder that stamps ascending z."""

    def __init__(self, base_z: float = 0.0) -> None:
        self.shapes: list[Shape] = []
        self.z = base_z

    def add(self, geom: Geom, fill: str | None = None, **kw: Any) -> Shape:
        self.z += 1.0
        s = Shape(geom, fill, z=self.z, **kw)
        self.shapes.append(s)
        return s

    def ellipse(
        self,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        fill: str | None,
        rot: float = 0.0,
        **kw: Any,
    ) -> Shape:
        return self.add(Ellipse(cx, cy, rx, ry, rot), fill, **kw)

    def limb(self, a: Pt, b: Pt, w0: float, w1: float, fill: str, **kw: Any) -> Shape:
        return self.add(Limb(a, b, w0, w1), fill, **kw)

    def poly(
        self,
        pts: Sequence[Pt],
        fill: str | None,
        smooth: float = 0.0,
        curve: bool = False,
        closed: bool = True,
        **kw: Any,
    ) -> Shape:
        return self.add(Poly(tuple(pts), closed, smooth, curve), fill, **kw)

    def line(self, a: Pt, b: Pt, w: float, color: str, **kw: Any) -> Shape:
        kw.setdefault("shadow", False)
        return self.add(Line(a, b, w), None, stroke=color, sw=w, **kw)

    def path(
        self,
        cmds: Sequence[tuple[Any, ...]],
        fill: str | None = None,
        stroke: str | None = None,
        sw: float = 0.0,
        **kw: Any,
    ) -> Shape:
        return self.add(PathG(tuple(cmds)), fill, stroke=stroke, sw=sw, **kw)

    def rect(
        self, x: float, y: float, w: float, h: float, fill: str | None, r: float = 0.0, **kw: Any
    ) -> Shape:
        return self.add(Rect(x, y, w, h, r), fill, **kw)


# ------------------------------------------------------------------------------- hair
def _hair(
    sb_back: SB, sb_front: SB, style: str, color: str, H: Rot2D, rx: float, ry: float, shade: str
) -> None:
    """Hair in head-local coordinates (origin = head centre, y down). `back` sits behind the head."""

    def arc(a0: float, a1: float, k: float = 1.04, n: int = 10) -> list[Pt]:
        return [
            (
                rx * k * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                ry * k * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
            )
            for i in range(n + 1)
        ]

    if style == "none":
        return
    if style == "long":
        back = [
            (-rx * 1.12, -ry * 0.25),
            (-rx * 1.3, ry * 0.7),
            (-rx * 1.0, ry * 1.7),
            (rx * 1.0, ry * 1.7),
            (rx * 1.3, ry * 0.7),
            (rx * 1.12, -ry * 0.25),
            (0, -ry * 1.12),
        ]
        sb_back.poly(H.locs(back), shade, curve=True, material="fabric", tag="hair", elev=1)
    if style in ("short", "side_part", "long", "kid", "messy"):
        outer = arc(192, 348, 1.045)
        hairline = [
            (rx * 0.82, -ry * 0.36),
            (rx * 0.42, -ry * 0.66),
            (-rx * 0.12, -ry * 0.50),
            (-rx * 0.56, -ry * 0.68),
            (-rx * 0.86, -ry * 0.34),
        ]
        if style == "side_part":
            hairline = [
                (rx * 0.86, -ry * 0.30),
                (rx * 0.40, -ry * 0.52),
                (-rx * 0.30, -ry * 0.72),
                (-rx * 0.62, -ry * 0.58),
                (-rx * 0.88, -ry * 0.30),
            ]
        if style == "long":
            hairline = [
                (rx * 0.86, -ry * 0.12),
                (rx * 0.30, -ry * 0.60),
                (-rx * 0.20, -ry * 0.64),
                (-rx * 0.86, -ry * 0.10),
            ]
        sb_front.poly(
            H.locs(outer + hairline), color, smooth=0.14, material="fabric", tag="hair", elev=3
        )
        if style == "messy":
            for i, (ang, ln) in enumerate(((235, 0.34), (265, 0.42), (298, 0.36), (205, 0.28))):
                bx, by = (
                    rx * 1.02 * math.cos(math.radians(ang)),
                    ry * 1.02 * math.sin(math.radians(ang)),
                )
                tip = (
                    bx + math.cos(math.radians(ang + 14 * (-1) ** i)) * rx * ln,
                    by + math.sin(math.radians(ang + 14 * (-1) ** i)) * ry * ln,
                )
                sb_front.poly(
                    H.locs([(bx - 22, by + 10), tip, (bx + 22, by + 10)]),
                    color,
                    smooth=0.3,
                    tag="hair",
                    elev=3,
                )
    elif style == "spiky":
        pts: list[Pt] = []
        n = 7
        for i in range(n + 1):
            a = 196 + (344 - 196) * i / n
            pts.append(
                (rx * 1.04 * math.cos(math.radians(a)), ry * 1.04 * math.sin(math.radians(a)))
            )
            if i < n:
                a2 = 196 + (344 - 196) * (i + 0.5) / n
                k = 1.42 if i % 2 == 0 else 1.28
                pts.append(
                    (rx * k * math.cos(math.radians(a2)), ry * k * math.sin(math.radians(a2)))
                )
        pts += [
            (rx * 0.82, -ry * 0.40),
            (rx * 0.2, -ry * 0.64),
            (-rx * 0.4, -ry * 0.58),
            (-rx * 0.84, -ry * 0.36),
        ]
        sb_front.poly(H.locs(pts), color, smooth=0.05, material="fabric", tag="hair", elev=3)
    elif style == "balding":
        for sx in (-1, 1):
            sb_front.ellipse(
                *H.loc(sx * rx * 0.93, -ry * 0.10),
                rx * 0.14,
                ry * 0.30,
                color,
                rot=sx * 12,
                tag="hair",
                material="fabric",
                elev=2,
            )
        sb_front.poly(H.locs(arc(215, 325, 1.02, 6)), None, tag="hair", lod=2, material="fabric")
        sb_front.line(
            H.loc(-rx * 0.5, -ry * 0.97), H.loc(rx * 0.5, -ry * 0.97), 6, color, tag="hair", lod=1
        )


# ------------------------------------------------------------------------------- figure
def build_figure(
    fig: PosedFigure,
    arch: Archetype,
    palette: dict[str, str],
    props: Sequence[tuple[PropDef, float]] = (),
    t: float = 0.0,
) -> FigureBuild:
    """All shapes of one posed character, in back-to-front order, plus its ground shadow."""
    if (
        arch.category == "sprite"
    ):  # a character made from library art: the whole picture is posed, not a body
        from reel.assets.sprite import build_sprite

        return build_sprite(fig, arch, palette, props, t)
    from reel.core import props as prop_lib  # local import: props needs SB from this module

    d = fig.dims
    P = fig.pts
    pose = fig.pose
    ft = arch.features
    skin = palette.get("skin", "#f0c29a")
    skin_sh = darken(skin, 0.10)
    hair_c = palette.get("hair", "#3b2a20")
    shirt = palette.get("shirt", "#e4572e")
    shirt2 = palette.get("shirt2", "#f3a712")
    pants = palette.get("pants", "#2d4a6b")
    shoes = palette.get("shoes", "#2a2a2e")
    accent = palette.get("accent", "#f3a712")
    eye_c = palette.get("eye", "#202028")
    outline = palette.get("outline", "#1c1c24")
    outfit = ft.get("outfit", "tee")
    long_sleeves = outfit in ("hoodie", "suit", "cardigan", "robot")
    is_robot = arch.category == "robot"
    skin_arm = skin if not is_robot else darken(skin, 0.05)

    th = pose["torso_lean"]
    T = Rot2D(P["hip_c"], th)  # torso frame at the hips
    ha = fig.ang["head"]
    hc = P["head_c"]
    H = Rot2D(hc, ha)
    rx, ry = d.head_rx, d.head_ry
    sway = pose["sway"]

    back = SB(0)
    mid = SB(100)
    front = SB(300)
    top = SB(500)

    # ---- worn/held props that sit BEHIND the body (backpack, cape, antenna base, long hair) ------
    acc = ft.get("accessory", "none")
    if acc == "cape":
        k = sway * 55
        pts = [
            T.loc(-d.shoulder_w * 0.46, -d.torso_len * 0.96),
            T.loc(d.shoulder_w * 0.20, -d.torso_len * 0.96),
            (T.loc(d.waist_w * 0.5, 0)[0] + k * 0.6, P["knee_l"][1] + 20),
            (T.loc(-d.waist_w * 0.5, 0)[0] + k * 1.15 - 30, P["knee_l"][1] + 70),
            (
                T.loc(-d.chest_w * 0.5, -d.torso_len * 0.4)[0] + k * 0.7 - 24,
                T.loc(0, -d.torso_len * 0.4)[1] + 20,
            ),
        ]
        back.poly(
            pts,
            palette.get("accent", "#d7263d"),
            smooth=0.22,
            material="fabric",
            tag="cape",
            elev=1,
        )
    for pd, w in props:
        if pd.home == "back" and w > 0.5:
            prop_lib.build_prop(pd, fig, pose, palette, w, t, back, "behind")

    # ---- far (back) arm and leg ------------------------------------------------------------------
    sleeve_far = darken(shirt, 0.16)
    far = {"upper": sleeve_far, "fore": sleeve_far if long_sleeves else darken(skin, 0.1)}
    hl, hcen = P["hand_l"], P["head_c"]
    # when the far hand comes up to the face or crosses the body it must be drawn in FRONT of them
    far_in_front = (
        math.dist(hl, hcen) < 1.35 * max(d.head_rx, d.head_ry) or hl[0] > P["shoulder_c"][0] - 6
    )

    def far_arm(sb: SB, elev: float) -> None:
        sb.limb(P["shoulder_l"], P["elbow_l"], d.arm_w * 1.05, d.arm_w * 0.95, far["upper"],
                material="fabric", tag="limb", elev=elev)  # fmt: skip
        sb.limb(P["elbow_l"], P["wrist_l"], d.arm_w * 0.92, d.arm_w * 0.72, far["fore"],
                material="fabric" if long_sleeves else "skin", tag="limb", elev=elev)  # fmt: skip
        sb.ellipse(*hl, d.hand_r * 0.95, d.hand_r * 0.95, darken(skin, 0.1), material="skin",
                   tag="hand", elev=elev)  # fmt: skip

    if not far_in_front:
        far_arm(mid, 1)
    pants_far = darken(pants, 0.16)
    mid.limb(
        P["hip_l"],
        P["knee_l"],
        d.leg_w,
        d.leg_w * 0.92,
        pants_far,
        material="fabric",
        tag="limb",
        elev=1,
    )
    mid.limb(
        P["knee_l"],
        P["ankle_l"],
        d.leg_w * 0.9,
        d.leg_w * 0.74,
        pants_far,
        material="fabric",
        tag="limb",
        elev=1,
    )
    _shoe(mid, P["ankle_l"], fig.ang["foot_l"], d, darken(shoes, 0.1), 1)

    # ---- near leg + pelvis + torso -----------------------------------------------------------------
    front.limb(
        P["hip_r"],
        P["knee_r"],
        d.leg_w,
        d.leg_w * 0.92,
        pants,
        material="fabric",
        tag="limb",
        elev=2,
    )
    front.limb(
        P["knee_r"],
        P["ankle_r"],
        d.leg_w * 0.9,
        d.leg_w * 0.74,
        pants,
        material="fabric",
        tag="limb",
        elev=2,
    )
    _shoe(front, P["ankle_r"], fig.ang["foot_r"], d, shoes, 2)
    pel_w = d.hip_w + d.leg_w * 0.95
    front.poly(
        T.locs([(-pel_w / 2, -8), (pel_w / 2, -8), (pel_w / 2 - 2, 46), (-pel_w / 2 + 2, 46)]),
        pants,
        smooth=0.3,
        material="fabric",
        tag="pelvis",
        elev=2,
    )

    tl = d.torso_len * (1 + 0.012 * pose["breath"])
    cw, ww = d.chest_w, d.waist_w
    torso_pts = [
        (-ww / 2, 26),
        (ww / 2, 26),
        (ww / 2 + 4, -tl * 0.38),
        (cw / 2, -tl + 10),
        (cw * 0.28, -tl - 2),
        (-cw * 0.28, -tl - 2),
        (-cw / 2, -tl + 10),
        (-ww / 2 - 4, -tl * 0.38),
    ]
    if outfit == "hoodie":
        front.ellipse(
            *T.loc(0, -tl + 6),
            cw * 0.34,
            26,
            darken(shirt, 0.1),
            tag="hood",
            material="fabric",
            elev=2,
            rot=th,
        )
    # neck (below head, above shirt)
    front.limb(
        T.loc(0, -tl + 4),
        (
            hc[0] - math.sin(math.radians(ha)) * ry * 0.35,
            hc[1] + math.cos(math.radians(ha)) * ry * 0.35,
        ),
        d.arm_w * 1.15,
        d.arm_w * 1.05,
        skin_sh,
        material="skin",
        tag="neck",
        elev=2,
    )
    front.poly(T.locs(torso_pts), shirt, smooth=0.2, material="fabric", tag="torso", elev=3)
    _outfit(front, T, outfit, d, tl, shirt, shirt2, accent, palette, pose)
    # ---- head -----------------------------------------------------------------------------------------
    box_head = ft.get("head_shape") == "box"
    if box_head:
        top.rect(
            hc[0] - rx,
            hc[1] - ry,
            rx * 2,
            ry * 2,
            skin,
            r=rx * 0.28,
            material="metal",
            tag="head",
            elev=4,
        ).xf = (0.0, 0.0, ha, 1.0, 1.0, hc[0], hc[1])
    else:
        top.ellipse(hc[0], hc[1], rx, ry, skin, rot=ha, material="skin", tag="head", elev=4)
    if ft.get("ears", True) and not box_head:
        for sx in (-1, 1):
            cx, cy = H.loc(sx * rx * 0.98, ry * 0.02)
            top.ellipse(
                cx,
                cy,
                rx * 0.15,
                ry * 0.2,
                skin_sh if sx < 0 else skin,
                rot=ha,
                material="skin",
                tag="ear",
                elev=3,
                lod=1,
            )
    _face(top, H, d, pose, ft, palette, eye_c, skin, hair_c, outline, t, box_head, ha)
    _hair(back, top, ft.get("hair", "short"), hair_c, H, rx, ry, darken(hair_c, 0.1))
    if acc == "antenna":
        base = H.loc(0, -ry * 0.98)
        tip = H.loc(sway * 24 + 6, -ry * 1.72)
        top.limb(
            base,
            tip,
            8,
            6,
            darken(palette.get("hair", "#6c7a89"), 0.1),
            material="metal",
            tag="antenna",
            lod=1,
        )
        top.ellipse(tip[0], tip[1], 13, 13, accent, material="emissive", glow=26, tag="antenna_tip")
    if acc == "glasses":
        prop_lib.glasses(top, H, rx, ry, pose, eye_c)
    if acc == "tie" and outfit == "suit":
        pass  # drawn in _outfit

    if far_in_front:
        far_arm(top, 4.5)
    # ---- near arm (front) -------------------------------------------------------------------------------
    sleeve = shirt
    top.limb(
        P["shoulder_r"],
        P["elbow_r"],
        d.arm_w * 1.05,
        d.arm_w * 0.95,
        sleeve,
        material="fabric",
        tag="limb",
        elev=5,
    )
    top.limb(
        P["elbow_r"],
        P["wrist_r"],
        d.arm_w * 0.92,
        d.arm_w * 0.72,
        shirt if long_sleeves else skin_arm,
        material="fabric" if long_sleeves else "skin",
        tag="limb",
        elev=5,
    )
    if long_sleeves:
        wr = P["wrist_r"]
        top.ellipse(
            wr[0],
            wr[1],
            d.arm_w * 0.42,
            d.arm_w * 0.42,
            darken(shirt, 0.12),
            tag="cuff",
            lod=1,
            material="fabric",
            elev=5,
        )
    if pose["hand_r_point"] > 0.5:
        ang = math.radians(fig.ang["hand_r"])
        hx, hy = P["hand_r"]
        top.limb(
            (hx, hy),
            (hx + math.sin(ang) * d.hand_r * 2.0, hy + math.cos(ang) * d.hand_r * 2.0),
            d.hand_r * 0.62,
            d.hand_r * 0.5,
            skin_arm,
            material="skin",
            tag="finger",
            elev=5,
        )
    top.ellipse(*P["hand_r"], d.hand_r, d.hand_r, skin_arm, material="skin", tag="hand", elev=5)

    # ---- props (worn / held / on the ground) ----------------------------------------------------------------
    for pd, w in props:
        if pd.home != "back":
            prop_lib.build_prop(pd, fig, pose, palette, w, t, top, "front")

    # ---- fx markers ------------------------------------------------------------------------------------------
    prop_lib.fx_markers(top, H, hc, rx, ry, pose, t, palette)

    shapes = back.shapes + mid.shapes + front.shapes + top.shapes
    for i, s in enumerate(shapes):
        s.z = SPRITE_Z + i

    xs = [p[0] for p in P.values()]
    ys = [p[1] for p in P.values()]
    x0, x1 = min(xs) - d.leg_w, max(xs) + d.leg_w
    lift = max(0.0, pose["lift"])
    strength = clamp(0.42 / (1.0 + lift / 180.0), 0.05, 0.42)
    lying = abs(pose["rot_hip"]) > 45 or abs(pose["rot"]) > 45
    if lying:
        cx, rxs = (x0 + x1) / 2, (x1 - x0) / 2 + 20
    else:
        cx = (P["ankle_r"][0] + P["ankle_l"][0]) / 2
        rxs = max(d.chest_w * 0.78, abs(P["ankle_r"][0] - P["ankle_l"][0]) / 2 + d.toe + 14)
    shadow = ShadowInfo(cx, rxs, max(10.0, rxs * 0.17), strength)
    return FigureBuild(
        shapes, shadow, (x0, min(ys), x1, max(ys)), {"palette": palette, "arch": arch}
    )


def _shoe(sb: SB, ankle: Pt, pitch_deg: float, d: Any, color: str, elev: float) -> None:
    F = Rot2D(ankle, -pitch_deg)
    h, hl, tl = d.foot_h, d.heel, d.toe
    pts = [
        (-hl, h * 0.1),
        (-hl - 1, h),
        (tl - 8, h),
        (tl + 2, h * 0.55),
        (tl - 6, h * 0.02),
        (tl * 0.5, -h * 0.4),
        (6, -h * 0.62),
        (-hl * 0.6, -h * 0.58),
    ]
    sb.poly(F.locs(pts), color, smooth=0.28, material="fabric", tag="foot", elev=elev)
    sb.poly(
        F.locs(
            [(-hl - 1, h * 0.74), (tl - 6, h * 0.74), (tl + 1, h * 0.62), (tl - 8, h), (-hl - 1, h)]
        ),
        lighten(color, 0.75),
        smooth=0.2,
        tag="sole",
        lod=1,
        shadow=False,
        elev=elev,
    )


def _outfit(
    sb: SB,
    T: Rot2D,
    outfit: str,
    d: Any,
    tl: float,
    shirt: str,
    shirt2: str,
    accent: str,
    palette: dict[str, str],
    pose: dict[str, float],
) -> None:
    cw = d.chest_w
    if outfit == "tee":
        sb.ellipse(
            *T.loc(0, -tl + 4),
            cw * 0.17,
            11,
            darken(shirt, 0.18),
            rot=T.deg,
            tag="collar",
            lod=1,
            shadow=False,
            material="fabric",
        )
        sb.poly(
            T.locs(
                [
                    (-cw * 0.47, -tl * 0.52),
                    (cw * 0.47, -tl * 0.52),
                    (cw * 0.49, -tl * 0.40),
                    (-cw * 0.49, -tl * 0.40),
                ]
            ),
            shirt2,
            tag="stripe",
            lod=1,
            shadow=False,
            material="fabric",
        )
    elif outfit == "hoodie":
        sb.poly(
            T.locs(
                [(-cw * 0.30, -tl * 0.30), (cw * 0.30, -tl * 0.30), (cw * 0.34, 6), (-cw * 0.34, 6)]
            ),
            darken(shirt, 0.1),
            smooth=0.25,
            tag="pocket",
            lod=1,
            shadow=False,
            material="fabric",
        )
        for sx in (-1, 1):
            sb.line(
                T.loc(sx * 12, -tl + 14),
                T.loc(sx * 15, -tl * 0.62),
                5,
                lighten(shirt, 0.55),
                tag="string",
                lod=2,
            )
    elif outfit == "suit":
        sb.poly(
            T.locs([(-cw * 0.20, -tl), (cw * 0.20, -tl), (0, -tl * 0.30)]),
            shirt2,
            tag="shirt",
            lod=1,
            shadow=False,
            material="fabric",
        )
        sb.poly(
            T.locs(
                [(-7, -tl + 8), (7, -tl + 8), (11, -tl * 0.30), (0, -tl * 0.24), (-11, -tl * 0.30)]
            ),
            palette.get("accent", "#c62828"),
            smooth=0.15,
            tag="tie",
            lod=1,
            shadow=False,
            material="fabric",
        )
        for sx in (-1, 1):
            sb.line(
                T.loc(sx * cw * 0.22, -tl + 2),
                T.loc(sx * 5, -tl * 0.26),
                4,
                darken(shirt, 0.25),
                tag="lapel",
                lod=1,
            )
    elif outfit == "cardigan":
        sb.line(T.loc(0, -tl + 6), T.loc(0, 18), 5, darken(shirt, 0.22), tag="placket", lod=1)
        for i in range(3):
            c = T.loc(10, -tl * 0.72 + i * tl * 0.24)
            sb.ellipse(c[0], c[1], 5, 5, lighten(shirt, 0.6), tag="button", lod=2, shadow=False)
    elif outfit == "robot":
        sb.rect(
            *T.loc(-cw * 0.26, -tl * 0.78),
            cw * 0.52,
            tl * 0.40,
            "#22313f",
            r=8,
            tag="panel",
            lod=1,
            shadow=False,
            material="metal",
        ).xf = (0.0, 0.0, T.deg, 1.0, 1.0, *T.loc(-cw * 0.26, -tl * 0.78))
        for i, lamp in enumerate((accent, "#ff5252", "#69f0ae")):
            p = T.loc(-cw * 0.14 + i * cw * 0.14, -tl * 0.60)
            sb.ellipse(
                p[0],
                p[1],
                7,
                7,
                lamp,
                material="emissive",
                glow=14,
                tag="light",
                lod=1,
                shadow=False,
            )


def _face(
    sb: SB,
    H: Rot2D,
    d: Any,
    pose: dict[str, float],
    ft: dict[str, Any],
    palette: dict[str, str],
    eye_c: str,
    skin: str,
    hair_c: str,
    outline: str,
    t: float,
    box_head: bool,
    ha: float,
) -> None:
    rx, ry = d.head_rx, d.head_ry
    shift = (0.20 + 0.26 * pose["head_turn"]) * rx
    open_ = clamp(pose["eye_open"] * (1.0 - pose["blink"]), 0.0, 1.4)
    wide_eyes = ft.get("eyes") == "wide"
    visor = ft.get("eyes") == "visor"
    erx = rx * (0.215 if wide_eyes else 0.17)
    ery_full = ry * (0.30 if wide_eyes else 0.25)
    ey = -ry * 0.08
    gaze_x, gaze_y = pose["eye_dx"], pose["eye_dy"]

    if visor:
        vis = sb.rect(
            *H.loc(shift - rx * 0.62, ey - ery_full),
            rx * 1.24,
            ery_full * 2.0,
            "#18222c",
            r=ery_full * 0.9,
            tag="visor",
            material="metal",
        )
        vis.xf = (0.0, 0.0, ha, 1.0, 1.0, *H.loc(shift - rx * 0.62, ey - ery_full))
        for sx in (-1, 1):
            c = H.loc(shift + sx * rx * 0.30 + gaze_x * 8, ey + gaze_y * 5)
            sb.ellipse(
                c[0],
                c[1],
                erx * 0.78,
                max(3.0, ery_full * 0.78 * min(open_, 1.1)),
                eye_c,
                rot=ha,
                material="emissive",
                glow=18,
                tag="eye",
            )
    else:
        for sx in (-1, 1):
            k = 1.0 if sx > 0 else 0.9  # the far eye reads smaller (3/4 view)
            cx, cy = H.loc(shift + sx * rx * 0.31 * (1 if sx > 0 else 1.02), ey)
            ry_e = max(ery_full * k * open_, 0.0)
            if open_ < 0.16:  # closed: a lid line (happy arc when laughing)
                a, b = (
                    H.loc(shift + sx * rx * 0.31 - erx * k, ey + 2),
                    H.loc(shift + sx * rx * 0.31 + erx * k, ey + 2),
                )
                m = H.loc(
                    shift + sx * rx * 0.31,
                    ey + 2 + ery_full * 0.5 * (-1.0 if pose["mouth_smile"] > 0.5 else 0.2),
                )
                sb.path(
                    [("M", *a), ("Q", *m, *b)], None, eye_c, 7, tag="lid", cap="round", shadow=False
                )
            else:
                sb.ellipse(
                    cx,
                    cy,
                    erx * k,
                    ry_e,
                    "#ffffff",
                    rot=ha,
                    tag="eye",
                    material="flat",
                    shadow=False,
                )
                pr = min(erx * k, ery_full * k) * 0.56
                px, py = H.loc(
                    shift + sx * rx * 0.31 + gaze_x * erx * 0.36 + erx * 0.10,
                    ey + gaze_y * ery_full * 0.30,
                )
                sb.ellipse(
                    px,
                    py,
                    pr,
                    min(pr * 1.1, ry_e * 0.92 + 0.5),
                    eye_c,
                    rot=ha,
                    tag="pupil",
                    shadow=False,
                )
                sb.ellipse(
                    px - pr * 0.28,
                    py - pr * 0.36,
                    pr * 0.26,
                    pr * 0.26,
                    "#ffffff",
                    tag="glint",
                    lod=1,
                    shadow=False,
                )
            # brows
            by = ey - ery_full * 1.35 - pose["brow_raise"] * ry * 0.10
            tilt = pose["brow_angle"] * ry * 0.075
            # worried (+): inner end (towards the nose) raised; angry (-): inner end lowered
            if sx > 0:
                a = H.loc(shift + sx * rx * 0.31 - erx * 1.15, by - tilt)
                b = H.loc(shift + sx * rx * 0.31 + erx * 1.15, by + tilt)
            else:
                a = H.loc(shift + sx * rx * 0.31 - erx * 1.15, by + tilt)
                b = H.loc(shift + sx * rx * 0.31 + erx * 1.15, by - tilt)
            sb.line(a, b, 9, darken(hair_c, 0.05), tag="brow", lod=1)
        # nose
        nose = ft.get("nose", "round")
        if nose == "round":
            c = H.loc(shift + rx * 0.10, ry * 0.15)
            sb.ellipse(
                c[0],
                c[1],
                rx * 0.065,
                ry * 0.085,
                darken(skin, 0.13),
                rot=ha,
                tag="nose",
                lod=1,
                shadow=False,
            )
        elif nose == "pointy":
            sb.poly(
                H.locs(
                    [
                        (shift + rx * 0.03, ry * 0.0),
                        (shift + rx * 0.24, ry * 0.21),
                        (shift + rx * 0.0, ry * 0.21),
                    ]
                ),
                darken(skin, 0.14),
                smooth=0.25,
                tag="nose",
                lod=1,
                shadow=False,
            )

    # mouth ---------------------------------------------------------------------------------------------------
    sm, op, wd = pose["mouth_smile"], pose["mouth_open"], pose["mouth_wide"]
    mcx = shift + rx * 0.08
    my = ry * (0.46 if not box_head else 0.50)
    mw = rx * (0.20 + 0.16 * wd)
    corner_dy = -sm * ry * 0.055
    depth = sm * ry * 0.13
    lc, rc = H.loc(mcx - mw, my + corner_dy), H.loc(mcx + mw, my + corner_dy)
    if op < 0.09 and wd < 0.25:
        ctrl = H.loc(mcx, my + corner_dy + depth * 2.0)
        sb.path(
            [("M", *lc), ("Q", *ctrl, *rc)],
            None,
            outline,
            7.5,
            tag="mouth",
            cap="round",
            shadow=False,
        )
    else:
        oh = (0.05 + 0.30 * op) * ry
        top_c = H.loc(mcx, my + corner_dy + depth * 0.9)
        bot_c = H.loc(mcx, my + corner_dy + depth * 0.9 + oh * 2.0 + depth)
        sb.path(
            [("M", *lc), ("Q", *top_c, *rc), ("Q", *bot_c, *lc), ("Z",)],
            "#6e1b2a",
            outline,
            4.5,
            tag="mouth",
            shadow=False,
        )
        if op > 0.35 or wd > 0.5:
            sb.poly(
                H.locs(
                    [
                        (mcx - mw * 0.72, my + corner_dy + depth * 0.7),
                        (mcx + mw * 0.72, my + corner_dy + depth * 0.7),
                        (mcx + mw * 0.62, my + corner_dy + depth * 0.7 + oh * 0.5),
                        (mcx - mw * 0.62, my + corner_dy + depth * 0.7 + oh * 0.5),
                    ]
                ),
                "#ffffff",
                smooth=0.2,
                tag="teeth",
                lod=1,
                shadow=False,
            )
        if op > 0.3:
            c = H.loc(mcx, my + corner_dy + depth + oh * 1.55)
            sb.ellipse(
                c[0],
                c[1],
                mw * 0.42,
                max(3.0, oh * 0.45),
                "#e8707f",
                rot=ha,
                tag="tongue",
                lod=1,
                shadow=False,
            )
    # cheeks / tears / sweat ----------------------------------------------------------------------------------
    if pose["blush"] > 0.02:
        for sx in (-1, 1):
            c = H.loc(shift + sx * rx * 0.55, ry * 0.26)
            sb.ellipse(
                c[0],
                c[1],
                rx * 0.17,
                ry * 0.095,
                "#ff6f91",
                rot=ha,
                alpha=clamp(pose["blush"] * 0.55, 0, 0.6),
                tag="blush",
                lod=1,
                shadow=False,
            )
    if pose["tear"] > 0.05 and not visor:
        for sx in (-1, 1):
            for k in range(2):
                ph = (t * 0.85 + k * 0.5 + (0.17 if sx > 0 else 0.0)) % 1.0
                c = H.loc(shift + sx * rx * (0.34 + 0.05 * k), ey + ery_full * 0.9 + ph * ry * 0.55)
                sb.ellipse(
                    c[0],
                    c[1],
                    rx * 0.032,
                    ry * 0.065,
                    "#8fd3ff",
                    rot=ha,
                    alpha=pose["tear"] * (1 - ph * 0.7),
                    tag="tear",
                    lod=1,
                    shadow=False,
                )
            c = H.loc(shift + sx * rx * 0.31, ey + ery_full * 0.95)
            sb.ellipse(
                c[0],
                c[1],
                erx * 0.9,
                ry * 0.03,
                "#8fd3ff",
                rot=ha,
                alpha=pose["tear"] * 0.7,
                tag="tear",
                lod=2,
                shadow=False,
            )
    if pose["sweat"] > 0.05:
        ph = (t * 0.35) % 1.0
        sb.poly(
            H.locs(
                [
                    (shift + rx * 0.80, -ry * 0.50 + ph * ry * 0.5 - ry * 0.12),
                    (shift + rx * 0.86, -ry * 0.50 + ph * ry * 0.5 + ry * 0.04),
                    (shift + rx * 0.74, -ry * 0.50 + ph * ry * 0.5 + ry * 0.04),
                ]
            ),
            "#9be0ff",
            smooth=0.5,
            alpha=clamp(pose["sweat"], 0, 1) * (1 - ph * 0.6),
            tag="sweat",
            lod=1,
            shadow=False,
        )
    if ft.get("facial") == "mustache":
        sb.poly(
            H.locs(
                [
                    (mcx - mw * 1.25, my - ry * 0.10),
                    (mcx, my - ry * 0.17),
                    (mcx + mw * 1.25, my - ry * 0.10),
                    (mcx + mw * 0.9, my - ry * 0.015),
                    (mcx, my - ry * 0.07),
                    (mcx - mw * 0.9, my - ry * 0.015),
                ]
            ),
            "#d9d9de",
            smooth=0.3,
            tag="mustache",
            lod=1,
            shadow=False,
            material="fabric",
        )
