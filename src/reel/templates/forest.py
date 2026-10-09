"""forest: layered woodland - misty hills, rows of trees, a winding path and a sunlit clearing."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Literal

import numpy as np
from pydantic import Field

from reel.core.geometry import Pt, adjust, clamp, darken, lerp, lighten, mix
from reel.core.ir import Anim, Particles
from reel.core.rng import derive_rng
from reel.templates.base import (
    ROLE_SETS,
    X0,
    X1,
    Y0,
    Y1,
    BgParams,
    BuildContext,
    hill_points,
    register_background,
)

# extra colour roles (tinted by mood like every other role)
ROLE_SETS["forest"] = {
    "pine": "#2f6b57",
    "pine2": "#23514a",
    "bark": "#5d4333",
    "birch": "#e8e5d8",
    "blossom": "#f3a6c8",
    "blossom2": "#ffd3e4",
    "fall_red": "#c9442b",
    "fall_orange": "#e98a2b",
    "fall_gold": "#efc13f",
    "fall_olive": "#98993c",
    "snow": "#f4f8ff",
    "snow_shadow": "#a9bbdc",
    "moss": "#6f9a4e",
    "cap_red": "#d9483b",
    "cap_brown": "#b9804f",
    "bloom_pink": "#f06ca0",
    "bloom_yellow": "#ffd23f",
    "bloom_blue": "#7a96ff",
}

HORIZON = 1070.0  # sky meets the far hills
GROUND_Y = 1290.0  # back edge of the playable ground
K = 0.5523
Cmds = list[tuple[Any, ...]]


class ForestParams(BgParams):
    season: Literal["spring", "summer", "autumn", "winter"] = Field(
        "summer",
        description="spring = fresh greens, pink blossom, petals; summer = lush green and wildflowers; "
        "autumn = orange/red/gold leaves, mushrooms and falling leaves; "
        "winter = bare trees, snow-capped pines, snowy ground and falling snow",
    )
    trees: Literal["broadleaf", "pine", "mixed"] = Field(
        "mixed",
        description="broadleaf = round leafy trees (and birches); pine = tall evergreen conifers; "
        "mixed = both. In winter the broadleaf trees are bare.",
    )
    fog: float = Field(
        0.25, ge=0.0, le=1.0,
        description="mist between the tree rows: 0 = crystal clear, 0.3 = soft haze, 1 = thick fog",
    )  # fmt: skip
    path: bool = Field(
        True,
        description="true = a winding dirt path leads into the woods; false = open grassy clearing",
    )


# ------------------------------------------------------------------------------------ path helpers
def _rect(x: float, y: float, w: float, h: float) -> Cmds:
    return [("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)]


def _circ(cx: float, cy: float, r: float) -> Cmds:
    k = K * r
    return [
        ("M", cx + r, cy),
        ("C", cx + r, cy + k, cx + k, cy + r, cx, cy + r),
        ("C", cx - k, cy + r, cx - r, cy + k, cx - r, cy),
        ("C", cx - r, cy - k, cx - k, cy - r, cx, cy - r),
        ("C", cx + k, cy - r, cx + r, cy - k, cx + r, cy),
        ("Z",),
    ]


def _dome(cx: float, cy: float, rx: float, ry: float) -> Cmds:
    """Upper half of an ellipse (mushroom cap), flat side down at ``cy``."""
    return [
        ("M", cx - rx, cy),
        ("C", cx - rx, cy - ry * 1.33, cx + rx, cy - ry * 1.33, cx + rx, cy),
        ("Z",),
    ]


def _leaf(bx: float, by: float, ang: float, ln: float, wd: float) -> Cmds:
    ca, sa = math.cos(ang), math.sin(ang)
    tx, ty = bx + ln * ca, by + ln * sa
    mx, my = bx + ln * 0.5 * ca, by + ln * 0.5 * sa
    return [
        ("M", bx, by),
        ("Q", mx - sa * wd, my + ca * wd, tx, ty),
        ("Q", mx + sa * wd, my - ca * wd, bx, by),
        ("Z",),
    ]


class _Forest:
    def __init__(self, ctx: BuildContext, p: ForestParams) -> None:
        self.ctx = ctx
        self.p = p
        self.tod = ctx.time_of_day
        self.night = self.tod == "night"
        self.season = p.season
        self.winter = p.season == "winter"
        self.fog = p.fog
        self.haze_c = "#000000"
        self.pal: dict[str, Any] = {}

    # ------------------------------------------------------------------------------ small helpers
    def b(self, c: str) -> str:
        return str(self.ctx.scheme.base(c))

    def rng(self, *key: Any) -> np.random.Generator:
        """Structure never depends on time of day, season or density: same seed, same woods."""
        return derive_rng(self.ctx.seed, "forest", *key)

    def fade(self, c: str, k: float) -> str:
        """Colour pushed toward the horizon haze (atmospheric perspective)."""
        return mix(self.b(c), self.haze_c, clamp(k, 0.0, 0.97))

    def hz(self, base: float) -> float:
        """Haze for a layer: the fog parameter pushes everything further into the mist."""
        return clamp(base + self.fog * (0.97 - base) * 0.45, 0.0, 0.96)

    # ---------------------------------------------------------------------------------- scheme
    def season_scheme(self) -> None:
        sc = self.ctx.scheme
        top, bot = sc.base("sky_top"), sc.base("sky_bottom")
        roles: dict[str, str] = {}
        extra: dict[str, Any] = {}
        if self.season == "winter":
            if self.night:
                roles = {"sky_top": adjust(top, sat=0.85), "sky_bottom": adjust(bot, sat=0.8)}
            else:
                roles = {
                    "sky_top": mix(adjust(top, sat=0.55), "#d3deec", 0.3),
                    "sky_bottom": mix(adjust(bot, sat=0.5), "#f1f5fa", 0.3),
                }
            extra = {
                "ambient": mix(sc.ambient, "#cfe0f5", 0.5),
                "ambient_amt": min(0.85, sc.ambient_amt + 0.10),
            }
        elif self.season == "autumn":
            extra = {
                "ambient": mix(sc.ambient, "#ffc58a", 0.35),
                "ambient_amt": min(0.85, sc.ambient_amt + 0.05),
            }
        elif self.season == "spring":
            extra = {"exposure": sc.exposure * 1.02}
        s2 = sc.with_roles(**roles) if roles else sc
        self.ctx.graph.scheme = replace(s2, **extra) if extra else s2
        self.haze_c = mix(self.b("sky_top"), self.b("sky_bottom"), 0.55)

    def make_palette(self) -> None:
        b = self.b
        s = self.season
        pal: dict[str, Any] = {}
        green = (b("foliage2"), b("foliage"), b("foliage3"))
        if s == "spring":
            lite = mix(b("foliage3"), "#d6f2a0", 0.4)
            pal["leaf"] = [
                (
                    mix(b("foliage"), b("foliage3"), 0.4),
                    mix(b("foliage3"), "#cdeea0", 0.35),
                    lighten(lite, 0.25),
                )
            ]
            pal["leaf_alt"] = [(darken(b("blossom"), 0.1), b("blossom"), b("blossom2"))]
            pal["grass"] = (
                mix(b("foliage"), b("foliage3"), 0.35),
                mix(b("foliage3"), "#d6f2a0", 0.3),
                lighten(b("foliage3"), 0.3),
            )
        elif s == "autumn":
            red = (
                darken(b("fall_red"), 0.15),
                b("fall_red"),
                mix(b("fall_red"), b("fall_orange"), 0.5),
            )
            org = (darken(b("fall_orange"), 0.18), b("fall_orange"), b("fall_gold"))
            gold = (darken(b("fall_gold"), 0.22), b("fall_gold"), lighten(b("fall_gold"), 0.3))
            oliv = (darken(b("fall_olive"), 0.2), b("fall_olive"), lighten(b("fall_olive"), 0.22))
            pal["leaf"] = [org, gold, red, oliv]
            pal["leaf_alt"] = [org]
            pal["grass"] = (
                mix(b("soil"), b("foliage2"), 0.45),
                mix(b("foliage"), b("fall_gold"), 0.42),
                mix(b("fall_gold"), b("foliage3"), 0.4),
            )
        elif s == "winter":
            pal["leaf"] = [green]
            pal["leaf_alt"] = [green]
            pal["grass"] = (
                mix(b("snow"), b("snow_shadow"), 0.35),
                mix(b("snow"), b("snow_shadow"), 0.12),
                b("snow"),
            )
        else:
            pal["leaf"] = [green]
            pal["leaf_alt"] = [
                (mix(b("moss"), b("foliage2"), 0.3), b("moss"), mix(b("moss"), b("foliage3"), 0.5))
            ]
            pal["grass"] = (
                mix(b("foliage2"), b("foliage"), 0.35),
                b("foliage"),
                mix(b("foliage3"), b("foliage"), 0.25),
            )
        pal["pine"] = (b("pine2"), b("pine"), lighten(b("pine"), 0.14))
        pal["bark"] = b("bark")
        pal["trunk"] = mix(b("trunk"), b("bark"), 0.5)
        if s == "winter":
            pal["path"] = (
                mix(b("snow_shadow"), b("snow"), 0.35),
                mix(b("snow"), b("snow_shadow"), 0.2),
                b("snow"),
            )
        else:
            pal["path"] = (
                darken(mix(b("soil"), b("sand"), 0.35), 0.1),
                mix(b("soil"), b("sand"), 0.5),
                mix(b("sand"), b("soil"), 0.3),
            )
        self.pal = pal

    # -------------------------------------------------------------------------------------- sky
    def sky(self) -> None:
        ctx = self.ctx
        d0 = ctx.density
        ctx.density = min(
            d0, 0.2 if self.night else 0.5
        )  # most of the sky is hidden by trees anyway
        ctx.sky(HORIZON, sun=False)
        ctx.density = d0
        x, y, r = {
            "dawn": (690.0, 610.0, 88.0),
            "day": (760.0, 330.0, 80.0),
            "dusk": (780.0, 590.0, 100.0),
            "night": (700.0, 330.0, 64.0),
        }[self.ctx.time_of_day]
        self.sun_pos = (x, y)
        col = "#f4f6ff" if self.night else self.b("glow")
        for k, a in ((3.2, 0.035), (2.8, 0.045), (2.4, 0.055), (2.0, 0.07), (1.65, 0.09)):
            ctx.ellipse(
                "sky",
                x,
                y,
                r * k,
                r * k,
                col,
                material="emissive",
                alpha=a,
                shadow=False,
                tag="sun_halo",
                lod=1,
            )
        ctx.ellipse(
            "sky",
            x,
            y,
            r,
            r,
            col,
            material="emissive",
            glow=r * 1.6,
            shadow=False,
            tag="moon" if self.night else "sun",
        )
        if self.night:
            for dx, dy, rr in ((-15, -10, 12), (13, 15, 8), (17, -19, 6)):
                ctx.ellipse("sky", x + dx, y + dy, rr, rr, mix(col, self.b("sky_bottom"), 0.22), material="emissive", alpha=0.5, shadow=False, tag="moon_crater", lod=2)  # fmt: skip

    # ------------------------------------------------------------------------------------ hills
    def far_hills(self) -> None:
        ctx = self.ctx
        kind = self.p.trees
        layers = ((1010.0, 130.0, 0.84), (1088.0, 100.0, 0.72), (1158.0, 74.0, 0.60))
        tint = {
            "spring": self.b("blossom"),
            "autumn": self.b("fall_orange"),
            "summer": self.b("foliage3"),
            "winter": self.b("snow"),
        }[self.season]
        tint_k = {"spring": 0.14, "autumn": 0.28, "summer": 0.0, "winter": 0.5}[self.season]
        for i, (base, amp, haze) in enumerate(layers):
            r = self.rng("hill", i)
            hz = self.hz(haze)
            col = self.fade(mix(mix(self.b("foliage2"), self.b("stone"), 0.3), tint, tint_k), hz)
            ph = float(r.uniform(0, 6.28))
            f1, f2 = float(r.uniform(0.0028, 0.0042)), float(r.uniform(0.007, 0.011))
            pts: list[Pt] = []
            x = X0 - 120
            n = 0
            while x < X1 + 120:
                y = base - amp * (0.6 * math.sin(x * f1 + ph) + 0.4 * math.sin(x * f2 + ph * 1.7))
                piney = kind == "pine" or (kind == "mixed" and math.sin(x * 0.004 + ph * 2) > 0.1)
                if piney:  # jagged conifer line
                    pts.append((x, y - (26.0 + 12 * float(r.random()) if n % 2 == 0 else 2.0)))
                    x += 17.0
                else:  # rounded broadleaf bumps
                    pts.append((x, y - 6 - 18 * abs(math.sin(x * 0.045 + i))))
                    x += 24.0
                n += 1
            pts = [(X0 - 140, base + 700), *pts, (X1 + 140, base + 700)]
            ctx.poly(
                "far",
                pts,
                col,
                smooth=0.25 if kind != "pine" else 0.05,
                tag="hill",
                lod=2 if i < 2 else 1,
                elev=2 + i,
                material="grass",
            )

    # ------------------------------------------------------------------------------------ trees
    def kind_for(self, r: np.random.Generator, row: int) -> str:
        t = self.p.trees
        u = float(r.random())
        if t == "pine":
            return "pine"
        if t == "broadleaf" or u < 0.58:
            if self.winter:
                return "bare"
            return "birch" if (row > 0 and float(r.random()) < 0.16) else "round"
        return "pine"

    def leaf_pal(self, r: np.random.Generator, kind: str) -> tuple[str, str, str]:
        lp = self.pal["leaf"]
        if self.season == "spring" and float(r.random()) < 0.36:
            return self.pal["leaf_alt"][0]
        if self.season == "summer" and float(r.random()) < 0.22:
            return self.pal["leaf_alt"][0]
        pick = lp[int(r.integers(len(lp)))]
        if kind == "birch":
            return (
                mix(pick[0], self.b("fall_gold"), 0.12),
                mix(pick[1], self.b("foliage3"), 0.25),
                lighten(pick[2], 0.12),
            )
        return pick

    def trunk(self, plane: str, x: float, base: float, h: float, w0: float, w1: float, col: str, lod: int, elev: float, hz: float, anim: Anim | None = None, bark: bool = False, birch: bool = False) -> None:  # fmt: skip
        ctx = self.ctx
        pts = [(x - w0 * 0.78, base + 6), (x - w0 * 0.5, base - h * 0.05), (x - w1 / 2, base - h), (x + w1 / 2, base - h), (x + w0 * 0.5, base - h * 0.05), (x + w0 * 0.78, base + 6)]  # fmt: skip
        ctx.poly(
            plane,
            pts,
            col,
            tag="trunk",
            lod=lod,
            elev=elev,
            smooth=0.06,
            material="wood",
            anim=anim,
        )
        sh = darken(col, 0.18)
        ctx.poly(plane, [(x + w0 * 0.1, base + 4), (x + w0 * 0.5, base - h * 0.05), (x + w1 / 2, base - h), (x + w1 * 0.08, base - h)], sh, tag="trunk_shade", lod=2, alpha=0.55, shadow=False, anim=anim)  # fmt: skip
        if birch:
            marks: Cmds = []
            rr = self.rng("birch", int(x), int(base))
            yy = base - h * 0.08
            while yy > base - h * 0.95:
                wdt = float(rr.uniform(0.35, 0.8)) * w0
                marks += _rect(
                    x - wdt * 0.5 + float(rr.uniform(-3, 3)), yy, wdt, max(3.0, w0 * 0.07)
                )
                yy -= float(rr.uniform(h * 0.07, h * 0.15))
            ctx.path(
                plane,
                marks,
                mix(darken(col, 0.7), self.haze_c, hz),
                tag="birch_mark",
                lod=2,
                shadow=False,
                anim=anim,
            )
        elif bark and w0 > 40:
            lines: Cmds = []
            rr = self.rng("bark", int(x), int(base))
            for _ in range(7):
                lx = x + float(rr.uniform(-0.4, 0.4)) * w0
                ly = base - float(rr.uniform(0.05, 0.7)) * h
                lines += [
                    ("M", lx, ly),
                    ("L", lx + float(rr.uniform(-4, 4)), ly - float(rr.uniform(40, 120))),
                ]
            ctx.path(
                plane,
                lines,
                None,
                stroke=darken(col, 0.3),
                sw=3.0,
                tag="bark",
                lod=2,
                shadow=False,
                alpha=0.7,
                anim=anim,
            )

    def puff(
        self, r: np.random.Generator, cx: float, cy: float, rx: float, ry: float, n: int = 8
    ) -> Cmds:
        r0 = min(rx, ry)
        cmds = _circ(cx, cy, r0 * 0.8)
        for i in range(n):
            a = 2 * math.pi * (i + float(r.uniform(-0.22, 0.22))) / n
            cmds += _circ(
                cx + rx * 0.64 * math.cos(a),
                cy + ry * 0.62 * math.sin(a),
                r0 * float(r.uniform(0.40, 0.56)),
            )
        return cmds

    def blob(
        self, r: np.random.Generator, cx: float, cy: float, rx: float, ry: float, n: int = 11
    ) -> list[Pt]:
        pts: list[Pt] = []
        for i in range(n):
            a = 2 * math.pi * i / n
            k = 0.86 + float(r.uniform(-0.05, 0.05))
            pts.append((cx + rx * k * math.cos(a), cy + ry * k * math.sin(a)))
        return pts

    def round_tree(self, plane: str, x: float, base: float, h: float, hz: float, key: tuple[Any, ...], lod: int, elev: float, sway: bool, canopy: tuple[float, float, float, float] = (0.0, 0.70, 0.32, 0.27), birch: bool = False, detail: int = 1) -> None:  # fmt: skip
        ctx = self.ctx
        r = self.rng("tree", *key)
        dark, mid, lite = (
            self.fade(c, hz) for c in self.leaf_pal(r, "birch" if birch else "round")
        )
        tcol = self.fade("birch", hz * 0.6) if birch else self.fade(self.pal["trunk"], hz)
        th = h * (0.62 if h > 800 else 0.52)
        w0 = h * (0.045 if birch else 0.07)
        w1 = w0 * 0.62
        self.trunk(plane, x, base, th, w0, w1, tcol, lod, elev, hz, bark=detail > 1, birch=birch)
        cxo, cyf, rxf, ryf = canopy
        cx, cy = x + cxo + float(r.uniform(-0.04, 0.04)) * h, base - h * cyf
        rx, ry = h * rxf * float(r.uniform(0.92, 1.08)), h * ryf
        # branches into the canopy
        for sgn in (-1, 1):
            ctx.line(
                plane,
                (x, base - th * 0.8),
                (x + sgn * rx * 0.42, base - th - ry * 0.35),
                w1 * 0.55,
                tcol,
                tag="branch",
                lod=1 if lod == 0 else lod,
                shadow=False,
            )
        ph = float(r.uniform(0, 6))

        def an(amp: float) -> Anim | None:
            return (
                Anim(
                    "sway", amp=amp, freq=0.15 + 0.03 * float(r.random()), phase=ph, pivot=(x, base)
                )
                if sway
                else None
            )

        ctx.poly(
            plane,
            self.blob(r, cx, cy, rx * 0.92, ry * 0.9),
            dark,
            curve=True,
            tag="canopy",
            lod=lod,
            elev=elev,
            material="grass",
        )
        ctx.path(
            plane,
            self.puff(r, cx, cy + ry * 0.06, rx, ry, 8),
            dark,
            tag="canopy",
            lod=max(lod, 1),
            elev=elev,
            material="grass",
        )
        ctx.path(
            plane,
            self.puff(r, cx - rx * 0.1, cy - ry * 0.12, rx * 0.8, ry * 0.78, 7),
            mid,
            tag="canopy",
            lod=max(lod, 1),
            elev=elev + 2,
            material="grass",
            anim=an(0.55),
        )
        ctx.path(
            plane,
            self.puff(r, cx - rx * 0.28, cy - ry * 0.34, rx * 0.5, ry * 0.46, 6),
            lite,
            tag="canopy",
            lod=max(lod, 1),
            elev=elev + 3,
            material="grass",
            shadow=False,
            anim=an(0.7),
        )
        if detail > 1:  # dappled light on the canopy
            dots: Cmds = []
            for _ in range(10):
                a = float(r.uniform(0, 6.28))
                dd = float(r.uniform(0.15, 0.85))
                dots += _circ(
                    cx - rx * 0.2 + rx * dd * math.cos(a) * 0.9,
                    cy - ry * 0.25 + ry * dd * math.sin(a) * 0.8,
                    float(r.uniform(7, 16)),
                )
            ctx.path(
                plane,
                dots,
                lighten(lite, 0.2),
                tag="leaf_dapple",
                lod=2,
                shadow=False,
                alpha=0.7,
                anim=an(0.7),
            )
        # ground shadow
        ctx.ellipse(
            plane,
            x,
            base + 2,
            w0 * 1.9,
            w0 * 0.34,
            self.b("shadow"),
            alpha=0.18 * (1.0 - hz),
            shadow=False,
            tag="shadow",
            lod=2,
        )

    def pine(self, plane: str, x: float, base: float, h: float, hz: float, key: tuple[Any, ...], lod: int, elev: float, sway: bool, slim: float = 1.0, detail: int = 1) -> None:  # fmt: skip
        ctx = self.ctx
        r = self.rng("pine", *key)
        dark, mid, lite = (self.fade(c, hz) for c in self.pal["pine"])
        tcol = self.fade(self.pal["trunk"], hz)
        w0 = h * 0.045
        ctx.poly(plane, [(x - w0 * 0.7, base + 6), (x - w0 * 0.4, base - h * 0.2), (x + w0 * 0.4, base - h * 0.2), (x + w0 * 0.7, base + 6)], tcol, tag="trunk", lod=lod, elev=elev, material="wood")  # fmt: skip
        n = 5 if h > 520 else 4
        top = base - h
        hw_bot = h * 0.19 * slim
        hw_top = hw_bot * 0.34
        y_start = base - h * 0.12
        right: list[Pt] = []
        left: list[Pt] = []
        tiers: list[
            tuple[float, float, float, float]
        ] = []  # (half width, y top, y bottom, left jitter)
        prev_yb = top
        for i in range(n):
            f = (i + 1) / n
            hw = lerp(hw_top, hw_bot, f**0.95) * float(r.uniform(0.94, 1.06))
            yb = lerp(top + h * 0.1, y_start, f)
            hl = hw * float(r.uniform(0.94, 1.06))
            right.append((x + hw, yb))
            left.append((x - hl, yb))
            tiers.append((hw, prev_yb if i == 0 else prev_yb - h * 0.075, yb, hl))
            prev_yb = yb
            if i < n - 1:
                right.append((x + hw * 0.5, yb - h * 0.035))
                left.append((x - hw * 0.5, yb - h * 0.035))
        pts = [(x, top), *right, *reversed(left)]
        a_p = None  # conifers stand stiff: only leafy canopies sway (keeps the animated-shape count low)
        _ = sway
        ctx.poly(
            plane, pts, dark, tag="pine", lod=lod, elev=elev, smooth=0.1, material="grass", anim=a_p
        )
        # lit left side
        half = [(x, top), *left, (x, left[-1][1])]
        ctx.poly(
            plane,
            half,
            mid,
            tag="pine_light",
            lod=max(lod, 1),
            elev=elev + 1,
            smooth=0.1,
            material="grass",
            shadow=False,
            alpha=0.9,
            anim=a_p,
        )
        # light tips on each tier
        tips: Cmds = []
        for _hw, ty, yb, hl in tiers:
            tips += [
                ("M", x - hl, yb),
                ("L", x - hl * 0.55, yb - (yb - ty) * 0.36),
                ("L", x - hl * 0.2, yb),
                ("Z",),
            ]
        ctx.path(plane, tips, lite, tag="pine_tip", lod=2, shadow=False, alpha=0.55, anim=a_p)
        if self.winter:  # snow settles on every tier
            caps: Cmds = []
            for hw, ty, yb, hl in tiers:
                th = yb - ty
                caps += [
                    ("M", x, ty - 2),
                    ("L", x + hw * 0.62, ty + th * 0.52),
                    ("Q", x + hw * 0.42, ty + th * 0.40, x + hw * 0.24, ty + th * 0.50),
                    ("Q", x + hw * 0.08, ty + th * 0.38, x - hl * 0.12, ty + th * 0.50),
                    ("Q", x - hl * 0.3, ty + th * 0.40, x - hl * 0.62, ty + th * 0.52),
                    ("Z",),
                ]
            ctx.path(
                plane, caps, self.fade("snow", hz * 0.6), tag="snow", lod=2, shadow=False, anim=a_p
            )
        ctx.ellipse(
            plane,
            x,
            base + 2,
            w0 * 2.2,
            w0 * 0.4,
            self.b("shadow"),
            alpha=0.18 * (1.0 - hz),
            shadow=False,
            tag="shadow",
            lod=2,
        )
        _ = detail

    def bare_tree(self, plane: str, x: float, base: float, h: float, hz: float, key: tuple[Any, ...], lod: int, elev: float, sway: bool, detail: int = 1) -> None:  # fmt: skip
        ctx = self.ctx
        r = self.rng("bare", *key)
        tcol = self.fade(self.pal["bark"], hz)
        th = h * 0.45
        w0 = h * 0.06
        self.trunk(plane, x, base, th, w0, w0 * 0.5, tcol, lod, elev, hz, bark=detail > 1)
        classes: list[Cmds] = [[], [], []]

        def grow(x0: float, y0: float, ang: float, ln: float, depth: int) -> None:
            x1, y1 = x0 + ln * math.sin(ang), y0 - ln * math.cos(ang)
            classes[min(2, 3 - depth)] += [("M", x0, y0), ("L", x1, y1)]
            if depth > 0:
                for da in (-0.55, 0.5):
                    grow(
                        x1,
                        y1,
                        ang + da + float(r.uniform(-0.18, 0.18)),
                        ln * float(r.uniform(0.62, 0.74)),
                        depth - 1,
                    )

        top_y = base - th
        for ang in (-0.42, 0.02, 0.4):
            grow(x, top_y + th * 0.08, ang + float(r.uniform(-0.12, 0.12)), h * 0.26, 3)
        a_b = None
        _ = sway
        for i, wd in enumerate((w0 * 0.34, w0 * 0.2, w0 * 0.12)):
            ctx.path(
                plane,
                classes[i],
                None,
                stroke=tcol,
                sw=max(2.0, wd),
                tag="branch",
                lod=0 if i == 0 else 1,
                shadow=False,
                anim=a_b,
            )
        ctx.ellipse(
            plane,
            x,
            base + 2,
            w0 * 1.7,
            w0 * 0.32,
            self.b("shadow"),
            alpha=0.18 * (1.0 - hz),
            shadow=False,
            tag="shadow",
            lod=2,
        )

    def tree(self, plane: str, x: float, base: float, h: float, hz: float, row: int, idx: int, lod: int, elev: float, sway: bool, kind: str, detail: int = 1, slim: float = 1.0, canopy: tuple[float, float, float, float] = (0.0, 0.70, 0.32, 0.27)) -> None:  # fmt: skip
        key = (row, idx)
        if kind == "pine":
            self.pine(plane, x, base, h, hz, key, lod, elev, sway, slim=slim, detail=detail)
        elif kind == "bare":
            self.bare_tree(plane, x, base, h, hz, key, lod, elev, sway, detail=detail)
        else:
            self.round_tree(
                plane,
                x,
                base,
                h,
                hz,
                key,
                lod,
                elev,
                sway,
                canopy=canopy,
                birch=kind == "birch",
                detail=detail,
            )

    def gap(self, x: float, w: float) -> bool:
        """The woods open up where the path leads and where the light comes in."""
        return abs(x - 560.0) < w

    def row1(self) -> None:
        r = self.rng("row1")
        hz = self.hz(0.58)
        x = X0 - 60.0
        idx = 0
        while x < X1 + 60:
            step = float(r.uniform(58, 104))
            base = 1208.0 + float(r.uniform(-10, 8))
            h = float(r.uniform(430, 610))
            kind = self.kind_for(r, 1)
            u = float(r.random())
            if not self.gap(x, 70.0) or u < 0.1:
                self.tree(
                    "back",
                    x,
                    base,
                    h,
                    hz + 0.04 * float(r.random()),
                    1,
                    idx,
                    1,
                    3.0,
                    False,
                    kind,
                    canopy=(0.0, 0.68, 0.30, 0.26),
                )
            x += step
            idx += 1

    def undergrowth(self, plane: str, y: float, hz: float, key: str, x0: float = X0, x1: float = X1, r_lo: float = 30.0, r_hi: float = 56.0) -> None:  # fmt: skip
        ctx = self.ctx
        r = self.rng("under", key)
        pal = self.pal["leaf"][0] if not self.winter else self.pal["pine"]
        a: Cmds = []
        bb: Cmds = []
        x = x0
        while x < x1:
            rr = float(r.uniform(r_lo, r_hi))
            (a if r.random() < 0.55 else bb).extend(
                _circ(x, y - rr * 0.35 + float(r.uniform(-8, 8)), rr)
            )
            x += rr * float(r.uniform(1.0, 1.7))
        ctx.path(plane, a, self.fade(pal[0], hz), tag="bush", lod=1, elev=3, material="grass")
        ctx.path(
            plane,
            bb,
            self.fade(pal[1], hz),
            tag="bush",
            lod=2,
            elev=3,
            material="grass",
            shadow=False,
        )

    def back_mound(self) -> None:
        ctx = self.ctx
        r = self.rng("mound")
        g = self.pal["grass"]
        pts = hill_points(r, X0, X1, 1224.0, 14.0, step=150.0, bottom=1340.0)
        ctx.poly(
            "back",
            pts,
            self.fade(g[0], self.hz(0.5)),
            smooth=0.4,
            tag="ground",
            lod=1,
            elev=2,
            material="grass",
        )

    def row2(self) -> None:
        r = self.rng("row2")
        hz = self.hz(0.30)
        x = X0 - 80.0
        idx = 0
        while x < X1 + 80:
            step = float(r.uniform(150, 250))
            base = 1262.0 + float(r.uniform(-6, 10))
            h = float(r.uniform(620, 860))
            kind = self.kind_for(r, 2)
            side = x < 250 or x > 840
            centre_ok = ((310 < x < 400 and idx % 2 == 0) or 700 < x < 780) and not self.gap(
                x, 130.0
            )
            if side:
                self.tree(
                    "back",
                    x,
                    base,
                    h,
                    hz,
                    2,
                    idx,
                    0,
                    5.0,
                    True,
                    kind,
                    canopy=(0.0, 0.72, 0.30, 0.25),
                )
            elif centre_ok:  # only a few small, hazy trees stand in the middle
                self.tree(
                    "back",
                    x,
                    base,
                    h * 0.62,
                    hz + 0.18,
                    2,
                    idx,
                    0,
                    5.0,
                    True,
                    kind,
                    canopy=(0.0, 0.72, 0.30, 0.25),
                )
            x += step
            idx += 1

    def row3(self) -> None:
        """Big framing trees at the edges of the clearing (the middle stays open)."""
        r = self.rng("row3")
        left_x = [-40.0 + float(r.uniform(-25, 25)), -330.0, -640.0]
        right_x = [1095.0 + float(r.uniform(-25, 25)), 1400.0, 1700.0]
        for i, x in enumerate(left_x + right_x):
            base = 1332.0 + float(r.uniform(-8, 8))
            h = float(r.uniform(1020, 1260)) if i not in (1, 4) else float(r.uniform(760, 920))
            kind = self.kind_for(r, 3)
            if i in (0, 3) and self.p.trees == "mixed":
                kind = "round" if not self.winter else "pine"
            slim = 0.55
            canopy = (-60.0 if x < 540 else 60.0, 0.80, 0.30, 0.19)
            self.tree(
                "mid_back",
                x,
                base,
                h,
                0.06,
                3,
                i,
                0,
                8.0,
                True,
                kind,
                detail=2,
                slim=slim,
                canopy=canopy,
            )
            if x > -300 and x < 1400:
                self.undergrowth("mid_back", base + 6, 0.06, f"r3{i}", x - 150, x + 150, 26.0, 44.0)

    # ---------------------------------------------------------------------------------- ground
    def ground(self) -> None:
        ctx = self.ctx
        r = self.rng("ground")
        g = self.pal["grass"]
        far, mid = g[0], g[1]
        pts = hill_points(r, X0, X1, GROUND_Y, 20.0, step=190.0, bottom=Y1 + 60)
        top_c = self.fade(far, self.hz(0.22))
        gr = ctx.poly("mid_back", pts, top_c, smooth=0.3, tag="ground", elev=4, shadow=False, material="grass", gradient=(top_c, darken(mid, 0.05), 90.0))  # fmt: skip
        _ = gr
        ctx.rect("mid_back", X0, 1560.0, X1 - X0, Y1 - 1560.0 + 60, darken(mid, 0.12), tag="ground", lod=1, shadow=False, alpha=0.35, gradient=(darken(mid, 0.06), darken(far, 0.22), 90.0))  # fmt: skip
        # soft light and dark patches of grass
        patches: list[Cmds] = [[], []]
        for i in range(18):
            px = float(r.uniform(X0, X1))
            py = float(r.uniform(GROUND_Y + 40, 1900))
            rx = float(r.uniform(90, 220)) * (0.5 + (py - GROUND_Y) / 700.0)
            ry = rx * 0.16
            k = i % 2
            patches[k] += [("M", px - rx, py), ("C", px - rx, py - ry * 1.33, px + rx, py - ry * 1.33, px + rx, py), ("C", px + rx, py + ry * 1.33, px - rx, py + ry * 1.33, px - rx, py), ("Z",)]  # fmt: skip
        ctx.path(
            "mid_back",
            patches[0],
            lighten(mid, 0.12),
            tag="grass_patch",
            lod=2,
            shadow=False,
            alpha=0.30,
        )
        ctx.path(
            "mid_back",
            patches[1],
            darken(mid, 0.14),
            tag="grass_patch",
            lod=2,
            shadow=False,
            alpha=0.26,
        )
        # shaded strip under the trees at the back
        ctx.rect(
            "mid_back",
            X0,
            GROUND_Y - 10,
            X1 - X0,
            54,
            self.b("shadow"),
            alpha=0.10,
            tag="shade",
            lod=2,
            shadow=False,
        )

    def path(self) -> None:
        if not self.p.path:
            return
        ctx = self.ctx
        r = self.rng("path")
        pe, pm, pl = self.pal["path"]
        left: list[Pt] = []
        right: list[Pt] = []
        y = GROUND_Y - 4
        while y < Y1 + 80:
            cx = 565.0 + 62.0 * math.sin((y - GROUND_Y) / 330.0 + 0.45)
            hw = 58.0 + (y - GROUND_Y) * 0.66
            left.append((cx - hw + float(r.uniform(-5, 5)), y))
            right.append((cx + hw + float(r.uniform(-5, 5)), y))
            y += 46.0
        poly = left + right[::-1]
        far = self.fade(pm, self.hz(0.28))
        # darker rim, then the path itself, then wheel ruts
        ctx.poly("mid_back", [(x + (-8 if i < len(left) else 8), y) for i, (x, y) in enumerate(poly)], darken(pe, 0.06), curve=True, tag="path_edge", lod=1, shadow=False, elev=1)  # fmt: skip
        ctx.poly("mid_back", poly, far, curve=True, tag="path", lod=0, elev=2, shadow=False, material="stone", gradient=(far, pl if not self.winter else pm, 90.0))  # fmt: skip
        ruts: Cmds = []
        for off in (-0.3, 0.3):
            ruts.append(
                ("M", left[0][0] * (0.5 - off / 2) + right[0][0] * (0.5 + off / 2), left[0][1])
            )
            for (lx, ly), (rx, _ry) in zip(left[1:], right[1:]):
                ruts.append(("L", lx * (0.5 - off / 2) + rx * (0.5 + off / 2), ly))
        ctx.path(
            "mid_back",
            ruts,
            None,
            stroke=darken(pe, 0.04),
            sw=8.0,
            tag="path_rut",
            lod=2,
            shadow=False,
            alpha=0.26,
        )
        # pebbles along the path
        peb: Cmds = []
        for _ in range(18):
            yy = float(r.uniform(GROUND_Y + 60, 1950))
            i = min(len(left) - 1, int((yy - GROUND_Y) / 46.0))
            lx, rx = left[i][0], right[i][0]
            px = float(r.uniform(lx + 20, rx - 20))
            rr = float(r.uniform(4, 10)) * (0.6 + (yy - GROUND_Y) / 600.0)
            peb += _circ(px, yy, rr)
        ctx.path(
            "mid_back",
            peb,
            darken(pe, 0.1) if not self.winter else self.b("snow_shadow"),
            tag="pebble",
            lod=2,
            shadow=False,
            alpha=0.55,
        )
        self.path_left, self.path_right = left, right

    def on_path(self, x: float, y: float, margin: float = 0.0) -> bool:
        if not self.p.path:
            return False
        i = clamp((y - GROUND_Y + 4) / 46.0, 0, len(self.path_left) - 2)
        i0 = int(i)
        lx = lerp(self.path_left[i0][0], self.path_left[i0 + 1][0], i - i0)
        rx = lerp(self.path_right[i0][0], self.path_right[i0 + 1][0], i - i0)
        return lx - margin < x < rx + margin

    def tufts(self) -> None:
        ctx = self.ctx
        r = self.rng("tufts")
        g = self.pal["grass"]
        d = ctx.density
        n = int(40 + 90 * d)
        cols = (darken(g[1], 0.16), g[1], g[2])
        groups: list[Cmds] = [[], [], []]
        for _ in range(n):
            yy = float(r.uniform(GROUND_Y + 12, 1950))
            xx = float(r.uniform(X0, X1))
            if self.on_path(xx, yy, 6.0):
                continue
            s = 0.55 + (yy - GROUND_Y) / 520.0
            k = int(r.integers(3))
            for j in range(int(r.integers(3, 6))):
                dx = (j - 2) * 7 * s + float(r.uniform(-3, 3))
                hh = float(r.uniform(22, 44)) * s
                groups[k] += [
                    ("M", xx + dx, yy),
                    (
                        "Q",
                        xx + dx + float(r.uniform(-8, 8)) * s,
                        yy - hh * 0.6,
                        xx + dx + float(r.uniform(-14, 14)) * s,
                        yy - hh,
                    ),
                ]
        for k in range(3):
            ctx.path(
                "mid_back",
                groups[k],
                None,
                stroke=cols[k] if not self.winter else self.fade(cols[k], 0.0),
                sw=4.0,
                tag="grass_tuft",
                lod=2,
                shadow=False,
                alpha=0.9,
            )
        # a few swaying clusters at the edge of the frame, in front of the characters' feet line
        for i, xx in enumerate((60.0, 190.0, 905.0, 1040.0, -180.0, 1230.0)):
            yy = 1520.0 + 40.0 * (i % 3)
            blades: Cmds = []
            for j in range(6):
                dx = (j - 2.5) * 11
                hh = float(r.uniform(60, 100))
                blades += [
                    ("M", xx + dx, yy),
                    (
                        "Q",
                        xx + dx + float(r.uniform(-14, 14)),
                        yy - hh * 0.55,
                        xx + dx * 1.5 + float(r.uniform(-22, 22)),
                        yy - hh,
                    ),
                ]
            ctx.path("mid_front", blades, None, stroke=cols[1 + (i % 2)], sw=5.0, tag="grass_tuft", lod=0, shadow=False, anim=Anim("sway", amp=2.4, freq=0.28 + 0.03 * i, phase=float(i), pivot=(xx, yy)))  # fmt: skip

    def flowers(self) -> None:
        if self.winter:
            return
        ctx = self.ctx
        r = self.rng("flowers")
        d = ctx.density
        s = self.season
        n = int((10 + 46 * d) * {"spring": 1.4, "summer": 1.0, "autumn": 0.35}[s])
        cols = {
            "spring": [
                self.b("bloom_pink"),
                self.b("blossom2"),
                self.b("bloom_yellow"),
                "#ffffff",
                self.b("bloom_blue"),
            ],
            "summer": [
                self.b("bloom_yellow"),
                "#ffffff",
                self.b("bloom_pink"),
                self.b("bloom_blue"),
                self.b("accent"),
            ],
            "autumn": [self.b("fall_orange"), self.b("fall_gold"), self.b("bloom_pink")],
        }[s]
        groups: list[Cmds] = [[] for _ in cols]
        stems: Cmds = []
        heads: Cmds = []
        g = self.pal["grass"]
        for _ in range(n):
            yy = float(r.uniform(GROUND_Y + 50, 1950))
            xx = float(r.uniform(X0, X1))
            if 170 < xx < 910 and yy < 1560:
                continue  # keep the area behind the characters calm
            if self.on_path(xx, yy, 6.0):
                continue
            sc = 0.55 + (yy - GROUND_Y) / 600.0
            hh = float(r.uniform(26, 46)) * sc
            stems += [
                ("M", xx, yy),
                (
                    "Q",
                    xx + float(r.uniform(-5, 5)),
                    yy - hh * 0.5,
                    xx + float(r.uniform(-4, 4)),
                    yy - hh,
                ),
            ]
            rr = float(r.uniform(6, 10)) * sc
            groups[int(r.integers(len(cols)))].extend(_circ(xx, yy - hh, rr))
            heads += _circ(xx, yy - hh, rr * 0.38)
        ctx.path(
            "mid_back",
            stems,
            None,
            stroke=darken(g[1], 0.2),
            sw=2.6,
            tag="flower_stem",
            lod=2,
            shadow=False,
        )
        for i, grp in enumerate(groups):
            if grp:
                ctx.path("mid_back", grp, cols[i], tag="flower", lod=2, shadow=False)
        ctx.path(
            "mid_back",
            heads,
            self.b("bloom_yellow") if s != "autumn" else self.b("fall_red"),
            tag="flower",
            lod=2,
            shadow=False,
            alpha=0.9,
        )

    def mushrooms(self) -> None:
        if self.winter:
            return
        ctx = self.ctx
        r = self.rng("mush")
        d = ctx.density
        n = int((2 + 8 * d) * (2.0 if self.season == "autumn" else 1.0))
        caps: list[Cmds] = [[], []]
        stems: Cmds = []
        spots: Cmds = []
        glow: Cmds = []
        for i in range(n):
            xx = float(r.uniform(X0 + 100, X1 - 100))
            yy = float(r.uniform(GROUND_Y + 120, 1850))
            if 190 < xx < 890 and yy < 1580:
                xx = xx + (260.0 if xx > 540 else -260.0) * 1.4
            if self.on_path(xx, yy, 10.0):
                continue
            sc = (0.7 + (yy - GROUND_Y) / 520.0) * float(r.uniform(0.8, 1.25))
            for j in range(int(r.integers(1, 4))):
                mx = xx + j * 34 * sc - 30 * sc
                my = yy + float(r.uniform(-8, 8)) * sc
                hh = float(r.uniform(20, 34)) * sc * (1.0 - 0.18 * j)
                rx = float(r.uniform(16, 24)) * sc * (1.0 - 0.18 * j)
                stems += _rect(mx - rx * 0.22, my - hh, rx * 0.44, hh)
                if self.night and i % 3 == 0:
                    glow += _dome(mx, my - hh + 4, rx, rx * 0.8)
                else:
                    caps[(i + j) % 2] += _dome(mx, my - hh + 4, rx, rx * 0.8)
                    if (i + j) % 2 == 0:
                        spots += _circ(mx - rx * 0.35, my - hh - rx * 0.28, rx * 0.16) + _circ(
                            mx + rx * 0.3, my - hh - rx * 0.4, rx * 0.13
                        )
        ctx.path(
            "mid_back",
            stems,
            lighten(self.b("birch"), 0.1),
            tag="mushroom",
            lod=2,
            shadow=False,
            elev=1,
        )
        ctx.path("mid_back", caps[0], self.b("cap_red"), tag="mushroom", lod=1, elev=2)
        ctx.path("mid_back", caps[1], self.b("cap_brown"), tag="mushroom", lod=1, elev=2)
        ctx.path("mid_back", spots, "#ffffff", tag="mushroom_spot", lod=2, shadow=False, alpha=0.9)
        if glow:
            ctx.path(
                "mid_back",
                glow,
                "#7dffd6",
                material="emissive",
                glow=24,
                shadow=False,
                tag="glow_mushroom",
                lod=1,
            )

    def rocks(self) -> None:
        ctx = self.ctx
        r = self.rng("rocks")
        d = ctx.density
        n = int(2 + 6 * d)
        stone = self.b("stone")
        spots_x = [905.0, 120.0, 1010.0, -90.0, 760.0, 330.0, 1220.0, -260.0]
        for i in range(n):
            xx = spots_x[i % len(spots_x)] + float(r.uniform(-20, 20))
            yy = (
                1380.0 + float(r.uniform(0, 380))
                if 230 < xx < 880
                else 1330.0 + float(r.uniform(0, 330))
            )
            if 230 < xx < 880:
                yy = max(yy, 1560.0)
            s = float(r.uniform(0.7, 1.4)) * (0.7 + (yy - GROUND_Y) / 700.0)
            w, h = 80 * s, 52 * s
            pts = [(xx - w * 0.5, yy), (xx - w * 0.48, yy - h * 0.55), (xx - w * 0.2, yy - h), (xx + w * 0.22, yy - h * 0.92), (xx + w * 0.5, yy - h * 0.45), (xx + w * 0.5, yy)]  # fmt: skip
            col = mix(stone, self.pal["grass"][0], 0.12)
            ctx.ellipse(
                "mid_back",
                xx + w * 0.05,
                yy + 2,
                w * 0.62,
                h * 0.14,
                self.b("shadow"),
                alpha=0.22,
                shadow=False,
                tag="shadow",
                lod=2,
            )
            ctx.poly("mid_back", pts, col, smooth=0.3, tag="rock", elev=4, material="stone")
            ctx.poly("mid_back", [(xx - w * 0.38, yy - h * 0.45), (xx - w * 0.18, yy - h * 0.86), (xx + w * 0.1, yy - h * 0.86), (xx - w * 0.06, yy - h * 0.5)], lighten(col, 0.2), smooth=0.3, tag="rock_light", lod=2, shadow=False, alpha=0.8)  # fmt: skip
            if self.winter:
                ctx.poly("mid_back", [(xx - w * 0.46, yy - h * 0.62), (xx - w * 0.2, yy - h * 1.02), (xx + w * 0.24, yy - h * 0.96), (xx + w * 0.48, yy - h * 0.55), (xx, yy - h * 0.7)], self.b("snow"), smooth=0.35, tag="snow", lod=1, shadow=False)  # fmt: skip
            elif i % 2 == 0:
                ctx.ellipse(
                    "mid_back",
                    xx - w * 0.22,
                    yy - h * 0.2,
                    w * 0.2,
                    h * 0.14,
                    self.b("moss"),
                    tag="moss",
                    lod=2,
                    shadow=False,
                    alpha=0.85,
                )

    def log_and_stump(self) -> None:
        ctx = self.ctx
        P = "mid_back"
        b = self.b
        wood = self.pal["trunk"]
        # fallen log on the right (the "log" slot stands just in front of its left end)
        lx0, ly = 872.0, 1452.0
        ln, rad = 270.0, 36.0
        ctx.ellipse(
            P,
            lx0 + ln / 2,
            ly + rad * 0.72,
            ln * 0.58,
            14,
            b("shadow"),
            alpha=0.25,
            shadow=False,
            tag="shadow",
            lod=2,
        )
        ctx.rect(
            P, lx0, ly - rad, ln, rad * 2, wood, r=rad * 0.5, tag="log", elev=5, material="wood"
        )
        ctx.rect(
            P,
            lx0 + 6,
            ly + rad * 0.2,
            ln - 12,
            rad * 0.8,
            darken(wood, 0.18),
            r=rad * 0.35,
            tag="log_shade",
            lod=2,
            shadow=False,
            alpha=0.6,
        )
        ctx.ellipse(P, lx0, ly, rad * 0.55, rad, lighten(wood, 0.28), tag="log_end", lod=1, elev=5)
        for k in (0.75, 0.5, 0.25):
            ctx.ellipse(
                P,
                lx0,
                ly,
                rad * 0.55 * k,
                rad * k,
                darken(wood, 0.08),
                tag="log_ring",
                lod=2,
                shadow=False,
                alpha=0.6,
            )
        ctx.path(P, [("M", lx0 + 40, ly - rad * 0.4), ("L", lx0 + 90, ly - rad * 0.45), ("M", lx0 + 130, ly + rad * 0.1), ("L", lx0 + 200, ly + rad * 0.05), ("M", lx0 + 170, ly - rad * 0.6), ("L", lx0 + 230, ly - rad * 0.58)], None, stroke=darken(wood, 0.28), sw=3.0, tag="bark", lod=2, shadow=False)  # fmt: skip
        if not self.winter:
            ctx.ellipse(
                P,
                lx0 + 140,
                ly - rad * 0.95,
                56,
                11,
                b("moss"),
                tag="moss",
                lod=2,
                shadow=False,
                alpha=0.9,
            )
            if self.ctx.density >= 0.3:
                caps = _dome(lx0 + 196, ly - rad * 0.95, 14, 11) + _dome(
                    lx0 + 222, ly - rad * 0.9, 10, 8
                )
                ctx.path(
                    P,
                    _rect(lx0 + 193, ly - rad * 0.98, 6, 10)
                    + _rect(lx0 + 219, ly - rad * 0.92, 5, 8),
                    lighten(b("birch"), 0.1),
                    tag="mushroom",
                    lod=2,
                    shadow=False,
                )
                ctx.path(P, caps, b("cap_red"), tag="mushroom", lod=2, elev=1)
        else:
            ctx.rect(
                P,
                lx0 + 10,
                ly - rad - 9,
                ln - 24,
                15,
                b("snow"),
                r=7,
                tag="snow",
                lod=1,
                shadow=False,
            )
        # stump on the left
        sx, sy = 138.0, 1440.0
        ctx.ellipse(
            P, sx, sy + 8, 66, 12, b("shadow"), alpha=0.25, shadow=False, tag="shadow", lod=2
        )
        ctx.poly(
            P,
            [(sx - 52, sy + 10), (sx - 38, sy - 66), (sx + 38, sy - 66), (sx + 52, sy + 10)],
            wood,
            smooth=0.08,
            tag="stump",
            elev=5,
            material="wood",
        )
        ctx.poly(
            P,
            [(sx + 6, sy + 10), (sx + 20, sy - 66), (sx + 38, sy - 66), (sx + 52, sy + 10)],
            darken(wood, 0.18),
            tag="stump_shade",
            lod=2,
            shadow=False,
            alpha=0.5,
        )
        ctx.ellipse(P, sx, sy - 66, 38, 12, lighten(wood, 0.3), tag="stump_top", lod=1, elev=5)
        for k in (0.7, 0.4):
            ctx.ellipse(
                P,
                sx,
                sy - 66,
                38 * k,
                12 * k,
                darken(wood, 0.06),
                tag="stump_ring",
                lod=2,
                shadow=False,
                alpha=0.6,
            )
        if self.winter:
            ctx.ellipse(P, sx, sy - 70, 34, 10, b("snow"), tag="snow", lod=1, shadow=False)
        elif self.ctx.density >= 0.3:
            ctx.path(
                P,
                _dome(sx + 70, sy + 6, 13, 10) + _dome(sx + 92, sy + 8, 9, 7),
                b("cap_brown"),
                tag="mushroom",
                lod=2,
                elev=1,
            )
            ctx.path(
                P,
                _rect(sx + 67, sy - 3, 6, 9) + _rect(sx + 89, sy, 5, 8),
                lighten(b("birch"), 0.1),
                tag="mushroom",
                lod=2,
                shadow=False,
            )

    def fallen_leaves(self) -> None:
        if self.season != "autumn":
            return
        ctx = self.ctx
        r = self.rng("fallen")
        cols = [
            self.b("fall_red"),
            self.b("fall_orange"),
            self.b("fall_gold"),
            self.b("fall_olive"),
        ]
        groups: list[Cmds] = [[] for _ in cols]
        for _ in range(int(60 + 90 * ctx.density)):
            xx = float(r.uniform(X0, X1))
            yy = float(r.uniform(GROUND_Y + 20, 1950))
            s = 0.6 + (yy - GROUND_Y) / 600.0
            groups[int(r.integers(4))].extend(
                _leaf(xx, yy, float(r.uniform(0, 6.28)), 20 * s, 7 * s)
            )
        for i, gp in enumerate(groups):
            ctx.path("mid_back", gp, cols[i], tag="fallen_leaf", lod=2, shadow=False, alpha=0.9)

    def snow_drifts(self) -> None:
        if not self.winter:
            return
        ctx = self.ctx
        r = self.rng("drifts")
        sh = self.b("snow_shadow")
        lumps: Cmds = []
        for _ in range(10):
            xx = float(r.uniform(X0, X1))
            yy = float(r.uniform(GROUND_Y + 80, 1900))
            w = float(r.uniform(120, 300)) * (0.6 + (yy - GROUND_Y) / 800.0)
            lumps += [
                ("M", xx - w, yy),
                ("C", xx - w * 0.5, yy - w * 0.16, xx + w * 0.5, yy - w * 0.2, xx + w, yy),
                ("Z",),
            ]
        ctx.path("mid_back", lumps, sh, tag="snow_drift", lod=2, shadow=False, alpha=0.3)

    # ---------------------------------------------------------------------------- light & mist
    def shafts(self) -> None:
        ctx = self.ctx
        sx, sy = self.sun_pos
        d = ctx.density
        col = {"dawn": "#ffd2a0", "day": "#fff6cf", "dusk": "#ffc17a", "night": "#a9bfff"}[self.tod]
        n = (3 + int(3 * d)) if not self.night else 2 + int(2 * d)
        a0 = {"dawn": 0.10, "day": 0.085, "dusk": 0.11, "night": 0.05}[self.tod] * (
            1.0 - 0.5 * self.fog * 0.0
        )
        r = self.rng("shafts")
        for i in range(n):
            ang = math.radians(90.0 + (i - (n - 1) / 2) * 13.0 + float(r.uniform(-4, 4)))
            ln = 1500.0
            w0 = float(r.uniform(14, 30))
            w1 = float(r.uniform(150, 260))
            ca, sa = math.cos(ang), math.sin(ang)
            nx, ny = -sa, ca
            p0 = (sx + nx * w0 * 0.5, sy + ny * w0 * 0.5)
            p1 = (sx - nx * w0 * 0.5, sy - ny * w0 * 0.5)
            p2 = (sx + ca * ln - nx * w1 * 0.5, sy + sa * ln - ny * w1 * 0.5)
            p3 = (sx + ca * ln + nx * w1 * 0.5, sy + sa * ln + ny * w1 * 0.5)
            ctx.poly("back", [p0, p1, p2, p3], col, material="emissive", alpha=a0 * float(r.uniform(0.7, 1.2)), shadow=False, tag="light_shaft", lod=2,
                     anim=Anim("twinkle", amp=0.35, freq=0.06 + 0.03 * float(r.random()), phase=float(r.uniform(0, 6))))  # fmt: skip
        if not self.night:  # pools of light on the ground
            for k, (px, py, rx) in enumerate(
                ((330.0, 1470.0, 210.0), (770.0, 1560.0, 250.0), (560.0, 1360.0, 150.0))
            ):
                if k < 2 + int(d * 1.4):
                    ctx.ellipse(
                        "mid_back",
                        px,
                        py,
                        rx,
                        rx * 0.13,
                        col,
                        material="emissive",
                        alpha=a0 * 0.9,
                        shadow=False,
                        tag="light_pool",
                        lod=2,
                    )

    def mist(self, plane: str, y0: float, y1: float, alpha: float, steps: int = 20) -> None:
        """A soft band of mist: nested translucent strips, so alpha builds up toward the middle."""
        ctx = self.ctx
        col = mix(self.b("sky_bottom"), "#ffffff", 0.18)
        a_each = 1.0 - (1.0 - clamp(alpha * 0.8, 0.0, 0.92)) ** (
            1.0 / steps
        )  # centre alpha ~ alpha * 0.8
        for i in range(steps):
            f = i / steps / 2.0  # inset fraction of the band height on each side
            ctx.rect(plane, X0, y0 + (y1 - y0) * f, X1 - X0, (y1 - y0) * (1.0 - 2.0 * f), col, material="emissive", alpha=a_each, shadow=False, tag="mist", lod=2)  # fmt: skip

    # ------------------------------------------------------------------------------------ near
    def near_plane(self) -> None:
        ctx = self.ctx
        P = "near"
        r = self.rng("near")
        d = ctx.density
        left_trunk = float(r.random()) < 0.5
        trunk_c = darken(self.pal["bark"], 0.26)
        # a dark framing trunk on one side
        tx = -8.0 if left_trunk else 1092.0
        w = 104.0
        pts = [(tx - w / 2 - 10, Y1), (tx - w / 2 + 6, 1500), (tx - w * 0.42, 800), (tx - w * 0.38, 200), (tx - w * 0.36, Y0), (tx + w * 0.36, Y0), (tx + w * 0.4, 200), (tx + w * 0.44, 800), (tx + w / 2 - 6, 1500), (tx + w / 2 + 10, Y1)]  # fmt: skip
        ctx.poly(P, pts, trunk_c, smooth=0.04, tag="trunk", elev=14, material="wood")
        ctx.poly(P, [(tx + w * 0.05, Y1), (tx + w * 0.44, 800), (tx + w * 0.36, Y0), (tx + w * 0.1, Y0), (tx + w * 0.1, 800)], darken(trunk_c, 0.35), tag="trunk_shade", lod=2, shadow=False, alpha=0.5)  # fmt: skip
        bark: Cmds = []
        for _ in range(16):
            yy = float(r.uniform(-200, 2100))
            xx = tx + float(r.uniform(-0.3, 0.3)) * w
            bark += [
                ("M", xx, yy),
                ("L", xx + float(r.uniform(-5, 5)), yy + float(r.uniform(60, 170))),
            ]
        ctx.path(
            P,
            bark,
            None,
            stroke=darken(trunk_c, 0.35),
            sw=4.0,
            tag="bark",
            lod=2,
            shadow=False,
            alpha=0.7,
        )
        # leaves overhanging the top corners
        pal = self.pal["leaf"][0] if not self.winter else self.pal["pine"]
        cols = [darken(pal[0], 0.34), darken(pal[1], 0.26), darken(pal[2], 0.18)]
        if self.winter:
            cols = [darken(c, 0.2) for c in self.pal["pine"]]
        for side in (0, 1):
            ox = -60.0 if side == 0 else 1140.0
            sgn = 1.0 if side == 0 else -1.0
            if self.season == "autumn" and side == 1:
                cols_s = [
                    darken(self.b("fall_orange"), 0.3),
                    darken(self.b("fall_red"), 0.22),
                    darken(self.b("fall_gold"), 0.15),
                ]
            else:
                cols_s = cols
            main = [
                (ox, 10.0),
                (ox + sgn * 150, 40.0),
                (ox + sgn * 300, 130.0),
                (ox + sgn * 380, 250.0),
            ]
            ctx.path(P, [("M", *main[0]), ("Q", main[1][0], main[1][1], main[2][0], main[2][1]), ("Q", main[2][0] + sgn * 40, main[2][1] + 60, *main[3])], None, stroke=trunk_c, sw=12, tag="branch", lod=1, shadow=False, elev=14)  # fmt: skip
            ph = float(r.uniform(0, 6))
            for k, (cnt, size) in enumerate(((16, 130.0), (14, 110.0), (10, 90.0))):
                cmds: Cmds = []
                for _ in range(cnt):
                    u = float(r.random())
                    seg = min(2, int(u * 3))
                    f = u * 3 - seg
                    bx = lerp(main[seg][0], main[seg + 1][0], f) + float(r.uniform(-50, 50))
                    by = lerp(main[seg][1], main[seg + 1][1], f) + float(r.uniform(-40, 60))
                    if self.winter:
                        cmds += _leaf(
                            bx, by, float(r.uniform(0.9, 2.2)), size * 0.9, size * 0.13
                        )  # needles-ish
                    else:
                        cmds += _leaf(
                            bx,
                            by,
                            float(r.uniform(0, 6.28)),
                            size * float(r.uniform(0.75, 1.2)),
                            size * float(r.uniform(0.2, 0.3)),
                        )
                ctx.path(P, cmds, cols_s[k], tag="leaf", lod=1, elev=12 + 3 * k, material="grass", anim=Anim("sway", amp=1.0, freq=0.15, phase=ph + k * 0.4 + side, pivot=(ox, -40.0)))  # fmt: skip
        # ferns in the bottom corners
        if d >= 0.2:
            for fx, sg in ((-40.0, 1.0), (1110.0, -1.0)):
                self.fern(fx, 1960.0, sg)

    def fern(self, x: float, y: float, sg: float) -> None:
        """A fern: arching fronds with pointed leaflets (near plane, bottom corner)."""
        ctx = self.ctx
        r = self.rng("fern", int(x))
        g = self.pal["grass"]
        base = darken(g[1], 0.34) if not self.winter else darken(self.b("pine"), 0.22)
        cols = (base, lighten(base, 0.12))
        stems: Cmds = []
        groups: list[Cmds] = [[], []]
        for k in range(6):
            a0 = math.radians(-78 + k * 27) if sg > 0 else math.radians(258 - k * 27)
            ln = float(r.uniform(300, 430))
            pts = []
            for t_ in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
                pts.append(
                    (
                        x + math.cos(a0) * ln * t_ + sg * 50 * t_ * t_,
                        y + math.sin(a0) * ln * t_ + 150 * t_ * t_,
                    )
                )
            stems += [("M", *pts[0])] + [("L", *p) for p in pts[1:]]
            for i in range(1, 6):
                px, py = pts[i]
                dxn, dyn = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
                ll = math.hypot(dxn, dyn) or 1.0
                ang = math.atan2(dyn, dxn)
                size = 78.0 * (1.15 - i * 0.16)
                for s_ in (-1, 1):
                    groups[(k + i) % 2] += _leaf(px, py, ang + s_ * 1.05, size, size * 0.17)
                _ = ll
        sway = Anim("sway", amp=1.4, freq=0.22, phase=float(x), pivot=(x, y))
        ctx.path(
            "near", stems, None, stroke=base, sw=6.0, tag="fern", lod=2, shadow=False, anim=sway
        )
        for i, gp in enumerate(groups):
            ctx.path("near", gp, cols[i], tag="fern", lod=2, elev=12, shadow=True, anim=sway)

    # ------------------------------------------------------------------------------ particles
    def particles(self) -> None:
        ctx = self.ctx
        d = ctx.density
        s = self.season
        seed = ctx.seed
        if s == "winter":
            n = int(120 + 160 * d)
            ctx.emit(
                "back",
                Particles(
                    "snow",
                    (X0, Y0, X1, Y1),
                    n,
                    speed=0.7,
                    size=7.0,
                    color="#ffffff",
                    alpha=0.7,
                    wind=0.3,
                    seed=seed,
                ),
            )
            ctx.emit(
                "near",
                Particles(
                    "snow",
                    (X0, Y0, X1, Y1),
                    int(n * 0.4),
                    speed=0.9,
                    size=15.0,
                    color="#ffffff",
                    alpha=0.85,
                    wind=0.4,
                    seed=seed + 1,
                ),
            )
        elif s == "autumn":
            for i, c in enumerate((self.b("fall_red"), self.b("fall_orange"), self.b("fall_gold"))):
                ctx.emit(
                    "near",
                    Particles(
                        "leaves",
                        (X0, Y0, X1, Y1),
                        int(6 + 10 * d),
                        speed=0.8,
                        size=17.0,
                        color=c,
                        alpha=0.95,
                        wind=1.0,
                        seed=seed + 10 + i,
                    ),
                )
                ctx.emit(
                    "back",
                    Particles(
                        "leaves",
                        (X0, Y0, X1, Y1),
                        int(6 + 9 * d),
                        speed=0.6,
                        size=11.0,
                        color=mix(c, self.haze_c, 0.3),
                        alpha=0.8,
                        wind=1.0,
                        seed=seed + 20 + i,
                    ),
                )
        elif s == "spring":
            ctx.emit(
                "near",
                Particles(
                    "leaves",
                    (X0, Y0, X1, Y1),
                    int(14 + 24 * d),
                    speed=0.6,
                    size=9.0,
                    color=self.b("blossom"),
                    alpha=0.9,
                    wind=0.8,
                    seed=seed + 5,
                ),
            )
            ctx.emit(
                "back",
                Particles(
                    "leaves",
                    (X0, Y0, X1, Y1),
                    int(10 + 16 * d),
                    speed=0.5,
                    size=7.0,
                    color=self.b("blossom2"),
                    alpha=0.8,
                    wind=0.8,
                    seed=seed + 6,
                ),
            )
        if self.night or self.tod == "dusk":
            n = int((26 + 40 * d) * (1.0 if self.night else 0.5))
            if s != "winter":
                ctx.emit(
                    "mid_front",
                    Particles(
                        "sparkle",
                        (X0, 520.0, X1, 1750.0),
                        n,
                        speed=-4.0,
                        size=11.0,
                        color="#e4ff8a",
                        alpha=0.95,
                        wind=0.6,
                        seed=seed + 30,
                        material="emissive",
                    ),
                )
        elif s != "winter":
            ctx.emit(
                "mid_front",
                Particles(
                    "dust",
                    (X0, 300.0, X1, 1500.0),
                    int(24 + 30 * d),
                    speed=0.8,
                    size=5.0,
                    color=self.b("glow"),
                    alpha=0.7,
                    wind=0.5,
                    seed=seed + 31,
                    material="emissive",
                ),
            )

    # -------------------------------------------------------------------------------- build
    def build(self) -> None:
        self.season_scheme()
        self.make_palette()
        self.sky()
        self.far_hills()
        if self.fog > 0.05:
            self.mist("far", 700, 1130, 0.7 * self.fog + 0.1)
        self.row1()
        self.back_mound()
        self.undergrowth("back", 1226.0, self.hz(0.46), "u1")
        if self.fog > 0.05:
            self.mist("back", 760, 1280, 0.9 * self.fog + 0.05)
        self.row2()
        self.shafts()
        self.ground()
        self.path()
        if not self.p.path:
            self.path_left = [(-10.0, GROUND_Y)]
            self.path_right = [(-5.0, GROUND_Y)]
        self.tufts()
        self.fallen_leaves()
        self.snow_drifts()
        self.flowers()
        self.row3()
        self.rocks()
        self.mushrooms()
        self.log_and_stump()
        if self.fog > 0.05:
            self.mist("mid_back", 1180, 1400, 0.6 * self.fog)
        self.near_plane()
        self.particles()


@register_background(
    "forest",
    params_schema=ForestParams,
    summary="Layered woodland: misty hills, rows of trees, a winding path with grass, rocks, mushrooms and "
    "flowers; spring / summer / autumn / winter, with light shafts and fireflies",
    slots={
        "path_left": (0.28, 0.76),
        "path_right": (0.72, 0.76),
        "clearing": (0.50, 0.80),
        "log": (0.80, 0.755),
        "stump": (0.13, 0.755),
    },
    tags=("outdoor", "nature"),
    ground_y=0.74,
    perspective=1.0,
    horizon=0.56,
)
def forest(ctx: BuildContext, p: ForestParams) -> None:
    _Forest(ctx, p).build()
