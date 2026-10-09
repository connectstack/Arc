"""rooftop: a city rooftop at golden hour or night - layered skyline, parapet, string lights and rooftop clutter."""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from pydantic import Field

from reel.core.geometry import Pt, adjust, clamp, darken, lerp, lighten, mix
from reel.core.ir import Anim, Particles
from reel.core.rng import derive_rng
from reel.templates.base import (
    X0,
    X1,
    Y0,
    Y1,
    BgParams,
    BuildContext,
    hill_points,
    register_background,
)

# vertical layout of the set (design px); characters stand with their feet at y = 0.74 * 1920 = 1421
HORIZON = 1130.0  # sky ends here (hidden behind the skyline rows)
CAP_Y = 1122.0  # top of the parapet cap
WALL_Y = 1150.0  # top of the parapet wall face
FLOOR_Y = 1304.0  # where the wall meets the roof floor
K = 0.5523
Cmds = list[tuple[Any, ...]]


class RooftopParams(BgParams):
    skyline: Literal["dense", "sparse", "harbor"] = Field(
        "dense",
        description="dense = tall downtown towers packed together; "
        "sparse = a low-rise town with pitched roofs, hills and a church spire; "
        "harbor = waterfront with a bay, a bridge on the horizon, cranes and boats",
    )
    lights: bool = Field(
        True,
        description="string (festoon) lights strung overhead; they glow at dusk, dawn and night",
    )
    railing: bool = Field(
        True, description="a metal safety railing on top of the parapet wall (false = bare parapet)"
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


def _quad(a: Pt, b: Pt, c: Pt, d: Pt) -> Cmds:
    return [("M", *a), ("L", *b), ("L", *c), ("L", *d), ("Z",)]


def _tri(a: Pt, b: Pt, c: Pt) -> Cmds:
    return [("M", *a), ("L", *b), ("L", *c), ("Z",)]


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


def _calm(x: float) -> float:
    """1 in the zone behind the characters (x ~ 260..820), fading to 0 toward the frame edges."""
    d = max(0.0, 260.0 - x, x - 820.0)
    return clamp(1.0 - d / 170.0, 0.0, 1.0)


class _Roof:
    def __init__(self, ctx: BuildContext, p: RooftopParams) -> None:
        self.ctx = ctx
        self.p = p
        self.tod = ctx.time_of_day
        self.night = self.tod == "night"
        self.lamps = ctx.graph.lamps_on
        self.lit_p = {"day": 0.0, "dawn": 0.30, "dusk": 0.52, "night": 0.62}[self.tod]
        self.mode = p.skyline
        self.haze_c = mix(self.b("sky_top"), self.b("sky_bottom"), 0.55)
        self.sun_pos: Pt = (800.0, 330.0)
        self.smokers: list[Pt] = []

    # ------------------------------------------------------------------------------ small helpers
    def b(self, c: str) -> str:
        return str(self.ctx.scheme.base(c))

    def rng(self, *key: Any) -> np.random.Generator:
        """Structure never depends on time of day or density: same seed, same rooftop."""
        return derive_rng(self.ctx.seed, "rooftop", self.mode, *key)

    def fade(self, c: str, k: float) -> str:
        return mix(self.b(c), self.haze_c, clamp(k, 0.0, 0.97))

    def win_cols(self) -> list[str]:
        w = self.b("window")
        return [w, mix(w, "#ffffff", 0.5), mix(w, self.b("fabric"), 0.38)]

    # -------------------------------------------------------------------------------------- sky
    def sky(self) -> None:
        ctx = self.ctx
        d0 = ctx.density
        ctx.density = min(
            d0, 0.2 if self.night else 0.3
        )  # city skies: only the brightest stars, a few clouds
        ctx.sky(HORIZON, sun=False)
        ctx.density = d0
        x, y, r = {
            "dawn": (790.0, 650.0, 88.0),
            "day": (810.0, 320.0, 80.0),
            "dusk": (820.0, 610.0, 100.0),
            "night": (810.0, 330.0, 66.0),
        }[self.tod]
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
            for dx, dy, rr in ((-16, -10, 13), (14, 16, 9), (18, -20, 6)):
                ctx.ellipse("sky", x + dx, y + dy, rr, rr, mix(col, self.b("sky_bottom"), 0.22), material="emissive", alpha=0.5, shadow=False, tag="moon_crater", lod=2)  # fmt: skip
        else:
            rng = self.rng("birds")
            bc = mix(self.b("sky_top"), "#1c2233", 0.55)
            for i in range(3):
                bx = float(rng.uniform(60, 600))
                by = float(rng.uniform(380, 640))
                s = float(rng.uniform(0.8, 1.3))
                ctx.path("sky", [("M", bx - 16 * s, by), ("Q", bx - 8 * s, by - 12 * s, bx, by), ("Q", bx + 8 * s, by - 12 * s, bx + 16 * s, by)], None, stroke=bc, sw=3.2, shadow=False, tag="bird", lod=2, alpha=0.8, anim=Anim("drift", amp=1.0, freq=0.10 + 0.03 * i, axis=-8.0, phase=float(i * 90)))  # fmt: skip

    def city_glow(self) -> None:
        """Light pollution above the skyline: a warm haze band that makes the towers pop at dusk/night."""
        if not self.lamps:
            return
        ctx = self.ctx
        col = mix(self.b("window"), self.b("sky_bottom"), 0.55 if self.night else 0.35)
        top, bot = 760.0, 1180.0
        a = {"dawn": 0.10, "dusk": 0.12, "night": 0.17}[self.tod]
        n = 18
        a_each = 1.0 - (1.0 - a * 0.9) ** (1.0 / n)
        for i in range(n):
            f = i / n / 2.0
            ctx.rect("far", X0, top + (bot - top) * f * 1.6, X1 - X0, (bot - top) * (1.0 - 1.6 * f) - 0.0, col, material="emissive", alpha=a_each, shadow=False, tag="city_glow", lod=2)  # fmt: skip

    # ------------------------------------------------------------------------------- buildings
    def env(
        self, x: float, hmin: float, hmax: float, valley: float = 280.0, centre: float = 560.0
    ) -> float:
        t = clamp((abs(x - centre) - valley) / 400.0, 0.0, 1.0)
        return lerp(hmin, hmax, t**0.85)

    def windows(
        self, plane: str, rng: np.random.Generator, x: float, w: float, top: float, bot: float, body: str,
        pitch: tuple[float, float], size: tuple[float, float], haze: float, lod: int,
        lit_scale: float = 1.0, calm: float = 0.0, dark_a: float = 0.22, twinkle: bool = False,
        margin: float = 10.0, y_cap: float = 1140.0,
    ) -> None:  # fmt: skip
        """Window grid: a faint dark grid for every window plus lit ones (emissive) after dusk."""
        ctx = self.ctx
        px, py = pitch
        ww, wh = size
        cols_n = int((w - 2 * margin) / px)
        rows_n = int((min(bot, y_cap) - top - 22) / py)
        if cols_n <= 0 or rows_n <= 0:
            return
        ox = x + (w - cols_n * px) / 2 + (px - ww) / 2
        grid: Cmds = []
        lit: list[Cmds] = [[], [], []]
        blink: Cmds = []
        p_lit = self.lit_p * lit_scale * (1.0 - 0.7 * calm)
        for r_ in range(rows_n):
            for c_ in range(cols_n):
                wx, wy = ox + c_ * px, top + 16 + r_ * py
                grid += _rect(wx, wy, ww, wh)
                u = float(rng.random())
                if u < p_lit:
                    if twinkle and float(rng.random()) < 0.18:
                        blink += _rect(wx, wy, ww, wh)
                    else:
                        v = float(rng.random())
                        lit[0 if v < 0.66 else (1 if v < 0.86 else 2)] += _rect(wx, wy, ww, wh)
        ctx.path(
            plane, grid, mix(body, "#000000", 0.28), alpha=dark_a, tag="window", lod=2, shadow=False
        )
        wc = self.win_cols()
        a = clamp(1.0 - 0.35 * haze - 0.45 * calm, 0.3, 1.0)
        for i in range(3):
            if lit[i]:
                ctx.path(
                    plane,
                    lit[i],
                    self.fade_win(wc[i], haze),
                    material="emissive",
                    alpha=a,
                    tag="window_lit",
                    lod=2,
                    shadow=False,
                )
        if blink:
            ctx.path(plane, blink, self.fade_win(wc[0], haze), material="emissive", alpha=a, tag="window_lit", lod=2, shadow=False, anim=Anim("twinkle", amp=0.55, freq=float(rng.uniform(0.08, 0.2)), phase=float(rng.uniform(0, 6))))  # fmt: skip

    def fade_win(self, c: str, haze: float) -> str:
        return mix(c, self.haze_c, haze * 0.45)

    def beacon(self, plane: str, x: float, y: float, key: int) -> None:
        ctx = self.ctx
        ph = float(self.rng("beacon", key).uniform(0, 6))
        ctx.ellipse(plane, x, y, 5.0, 5.0, "#ff4e45", material="emissive", glow=20.0, alpha=0.95 if self.lamps else 0.55, shadow=False, tag="beacon", lod=2, anim=Anim("twinkle", amp=1.0, freq=0.55 + 0.07 * (key % 4), phase=ph))  # fmt: skip

    def towers(
        self, plane: str, key: str, *, base: float, hmin: float, hmax: float, wmin: float, wmax: float,
        haze: float, lod: int, pitch: tuple[float, float], size: tuple[float, float],
        lit_scale: float = 1.0, beacons: int = 0, twinkle: bool = False, valley: float = 280.0,
        elev: float = 3.0, tanks: bool = False, gear: bool = False, x0: float = X0 - 80, x1: float = X1 + 80,
        dark_a: float = 0.22, wmargin: float = 10.0,
    ) -> None:  # fmt: skip
        ctx = self.ctx
        b = self.b
        rng = self.rng(key)
        pal = [
            b("stone"),
            b("stone2"),
            b("fabric"),
            b("wall2"),
            b("brick"),
            b("accent2"),
            b("metal"),
        ]
        x = x0
        idx = 0
        tall: list[tuple[float, float, float]] = []
        while x < x1:
            w = float(rng.uniform(wmin, wmax))
            h = self.env(x + w / 2, hmin, hmax, valley) * float(rng.uniform(0.86, 1.14))
            top = base - h
            c0 = pal[int(rng.integers(len(pal)))]
            col = adjust(mix(c0, self.haze_c, haze), light=float(rng.uniform(0.94, 1.06)))
            kind = float(rng.random())
            calm = _calm(x + w / 2) if lod < 2 else 0.0
            body_lod = (
                0 if (plane == "back" and idx % 3 == 0) else lod
            )  # a few essential towers for line-art styles
            ctx.rect(
                plane,
                x,
                top,
                w,
                h + 120,
                col,
                tag="building",
                lod=body_lod,
                elev=elev,
                material="paper",
            )
            ctx.rect(
                plane,
                x + w * 0.78,
                top,
                w * 0.22,
                h + 120,
                darken(col, 0.1),
                tag="building_shade",
                lod=2,
                alpha=0.45,
                shadow=False,
            )
            roof_y = top
            if kind < 0.20 and w > 64:  # setback crown
                cw = w * float(rng.uniform(0.5, 0.7))
                ch = float(rng.uniform(40, 90))
                ctx.rect(
                    plane,
                    x + (w - cw) / 2,
                    top - ch,
                    cw,
                    ch + 2,
                    adjust(col, light=1.04),
                    tag="building",
                    lod=lod,
                    elev=elev,
                    material="paper",
                )
                roof_y = top - ch
            elif kind < 0.38:  # spire with a beacon
                ax = x + w * float(rng.uniform(0.35, 0.65))
                ah = float(rng.uniform(70, 150)) * (0.6 if plane != "far" else 1.0)
                ctx.line(plane, (ax, top), (ax, top - ah), 4.0, col, tag="antenna", lod=lod)
                roof_y = top - ah
                tall.append((ax, roof_y, h))
            elif kind < 0.50:  # angled modern roof
                ctx.poly(
                    plane,
                    [(x, top + 2), (x + w, top - float(rng.uniform(20, 50))), (x + w, top + 2)],
                    adjust(col, light=1.03),
                    tag="building",
                    lod=lod,
                    elev=elev,
                )
            elif kind < 0.58 and w > 80:  # rounded observation deck
                ctx.ellipse(
                    plane,
                    x + w / 2,
                    top - 14,
                    w * 0.34,
                    16,
                    adjust(col, light=1.03),
                    tag="building",
                    lod=lod,
                    elev=elev,
                )
            if tanks and kind > 0.84 and w > 90:  # wooden water tank on a roof
                tx = x + w * 0.55
                wood = mix(b("wood2"), self.haze_c, haze)
                for lx in (tx - 20, tx + 18):
                    ctx.line(
                        plane,
                        (lx, top),
                        (lx, top - 34),
                        4,
                        darken(col, 0.3),
                        tag="tank",
                        lod=lod,
                        shadow=False,
                    )
                ctx.rect(
                    plane,
                    tx - 28,
                    top - 86,
                    56,
                    54,
                    wood,
                    r=5,
                    tag="water_tank",
                    lod=lod,
                    elev=elev,
                )
                ctx.poly(
                    plane,
                    [(tx - 33, top - 84), (tx + 33, top - 84), (tx, top - 112)],
                    darken(wood, 0.2),
                    tag="water_tank",
                    lod=lod,
                    elev=elev,
                )
            self.windows(
                plane,
                rng,
                x,
                w,
                top,
                base + 40,
                col,
                pitch,
                size,
                haze,
                2 if lod > 0 else 1,
                lit_scale,
                calm,
                twinkle=twinkle,
                margin=wmargin,
                dark_a=dark_a,
            )
            if gear and kind > 0.5:  # AC units on the roof edge
                gx = x + w * float(rng.uniform(0.1, 0.5))
                ctx.rect(
                    plane,
                    gx,
                    top - 20,
                    34,
                    20,
                    mix(b("metal"), self.haze_c, haze),
                    r=3,
                    tag="ac_unit",
                    lod=2,
                    shadow=False,
                )
            x += w - 2
            idx += 1
        if beacons and self.lamps:
            tall.sort(key=lambda t: -t[2])
            for i, (ax, ay, _h) in enumerate(tall[:beacons]):
                self.beacon(plane, ax, ay, i)
        elif beacons:  # by day the beacons are just small red marks
            tall.sort(key=lambda t: -t[2])
            for ax, ay, _h in tall[:beacons]:
                ctx.ellipse(
                    plane, ax, ay, 4.0, 4.0, "#e5483a", alpha=0.7, shadow=False, tag="beacon", lod=2
                )

    def lowrise(
        self, plane: str, key: str, *, base: float, hmin: float, hmax: float, wmin: float, wmax: float, haze: float,
        lod: int, pitch: tuple[float, float], size: tuple[float, float], x0: float = X0 - 80, x1: float = X1 + 80,
        valley: float = 120.0, steeple: bool = False, chimneys: bool = True, elev: float = 3.0, lit_scale: float = 1.0,
    ) -> None:  # fmt: skip
        """A row of small-town buildings: pitched or flat roofs, chimneys and a church spire."""
        ctx = self.ctx
        b = self.b
        rng = self.rng(key)
        pal = [mix(b("brick"), b("wall2"), 0.3), mix(b("wall"), b("wall2"), 0.3), mix(b("accent3"), b("wall"), 0.5), mix(b("accent"), b("wall"), 0.6), mix(b("fabric"), b("wall"), 0.5), mix(b("accent2"), b("wall"), 0.55)]  # fmt: skip
        roofs = [
            mix(b("brick2"), b("accent"), 0.2),
            mix(b("stone2"), b("fabric"), 0.4),
            mix(b("brick"), b("brick2"), 0.5),
        ]
        x = x0
        idx = 0
        steeple_done = not steeple
        while x < x1:
            w = float(rng.uniform(wmin, wmax))
            h = self.env(x + w / 2, hmin, hmax, valley) * float(rng.uniform(0.8, 1.2))
            top = base - h
            col = adjust(
                mix(pal[int(rng.integers(len(pal)))], self.haze_c, haze),
                light=float(rng.uniform(0.96, 1.05)),
            )
            rc = adjust(
                mix(roofs[int(rng.integers(len(roofs)))], self.haze_c, haze),
                light=float(rng.uniform(0.95, 1.05)),
            )
            calm = _calm(x + w / 2) if lod < 2 else 0.0
            body_lod = 0 if (plane == "back" and idx % 3 == 0) else lod
            ctx.rect(
                plane,
                x,
                top,
                w,
                h + 120,
                col,
                tag="building",
                lod=body_lod,
                elev=elev,
                material="paper",
            )
            ctx.rect(
                plane,
                x + w * 0.78,
                top,
                w * 0.22,
                h + 120,
                darken(col, 0.1),
                tag="building_shade",
                lod=2,
                alpha=0.45,
                shadow=False,
            )
            kind = float(rng.random())
            if kind < 0.55:  # gable roof
                rh = w * float(rng.uniform(0.24, 0.38))
                if chimneys and float(rng.random()) < 0.6:
                    cx = x + w * float(rng.uniform(0.62, 0.8))
                    ctx.rect(
                        plane,
                        cx,
                        top - rh * 0.9 - 6,
                        14,
                        rh * 0.9 + 10,
                        darken(rc, 0.12),
                        tag="chimney",
                        lod=lod,
                        elev=elev,
                    )
                    if 0 < cx < 1080 and plane == "back":
                        self.smokers.append((cx + 7, top - rh * 0.9 - 10))
                ctx.poly(
                    plane,
                    [(x - 8, top + 3), (x + w / 2, top - rh), (x + w + 8, top + 3)],
                    rc,
                    tag="roof",
                    lod=body_lod,
                    elev=elev + 1,
                    smooth=0.04,
                )
            elif kind < 0.8:  # hip roof
                rh = w * float(rng.uniform(0.16, 0.26))
                ctx.poly(
                    plane,
                    [
                        (x - 8, top + 3),
                        (x + w * 0.18, top - rh),
                        (x + w * 0.82, top - rh),
                        (x + w + 8, top + 3),
                    ],
                    rc,
                    tag="roof",
                    lod=body_lod,
                    elev=elev + 1,
                    smooth=0.04,
                )
            else:  # flat roof with a cap
                ctx.rect(
                    plane,
                    x - 4,
                    top - 6,
                    w + 8,
                    10,
                    lighten(col, 0.2),
                    tag="cornice",
                    lod=lod,
                    elev=elev,
                )
            if not steeple_done and w > 100 and x > 800 and idx > 2:
                steeple_done = True
                sx = x + w * 0.5
                ctx.rect(
                    plane,
                    sx - 20,
                    top - 150,
                    40,
                    154,
                    adjust(col, light=1.04),
                    tag="tower",
                    lod=lod,
                    elev=elev,
                )
                ctx.poly(
                    plane,
                    [(sx - 26, top - 148), (sx, top - 270), (sx + 26, top - 148)],
                    rc,
                    tag="roof",
                    lod=lod,
                    elev=elev,
                )
                ctx.ellipse(
                    plane,
                    sx,
                    top - 100,
                    11,
                    11,
                    self.fade("trim", haze * 0.5),
                    tag="clock",
                    lod=2,
                    shadow=False,
                )
                ctx.line(plane, (sx, top - 270), (sx, top - 296), 3.0, rc, tag="spire", lod=lod)
            self.windows(
                plane,
                rng,
                x,
                w,
                top,
                base + 40,
                col,
                pitch,
                size,
                haze,
                2 if lod > 0 else 1,
                lit_scale,
                calm,
                margin=12.0,
            )
            x += w + float(rng.uniform(-2, 16))
            idx += 1

    # ------------------------------------------------------------------------- distant layers
    def far_layers(self) -> None:
        m = self.mode
        if m == "dense":
            self.towers("far", "F", base=1190, hmin=300, hmax=860, wmin=60, wmax=150, haze=0.80, lod=2, pitch=(22, 30), size=(11, 16), lit_scale=0.8, beacons=3, valley=260)  # fmt: skip
        elif m == "sparse":
            self.hills("far", "h1", 930.0, 130.0, 0.82)
            self.hills("far", "h2", 1020.0, 100.0, 0.70)
        else:
            self.harbor_far()

    def hills(
        self, plane: str, key: str, base: float, amp: float, haze: float, trees: bool = True
    ) -> None:
        ctx = self.ctx
        r = self.rng(key)
        col = mix(mix(self.b("foliage2"), self.b("stone"), 0.3), self.haze_c, haze)
        pts = hill_points(r, X0, X1, base, amp, step=130.0, bottom=base + 600)
        ctx.poly(plane, pts, col, smooth=0.4, tag="hill", lod=2, elev=2, material="grass")
        if trees:
            tc = mix(mix(self.b("foliage"), self.b("foliage2"), 0.5), self.haze_c, haze + 0.05)
            cmds: Cmds = []
            x = X0
            while x < X1:
                rr = float(r.uniform(18, 40))
                cmds += _circ(x, base - amp * 0.2 - rr * 0.2, rr)
                x += rr * float(r.uniform(1.0, 1.7))
            ctx.path(plane, cmds, tc, tag="treeline", lod=2, elev=2, material="grass")

    def mid_layers(self) -> None:
        m = self.mode
        if m == "dense":
            self.towers("back", "M", base=1215, hmin=240, hmax=700, wmin=84, wmax=190, haze=0.5, lod=1, pitch=(26, 36), size=(13, 19), lit_scale=1.0, beacons=3, twinkle=True, valley=290, tanks=True, gear=True, elev=4.0, dark_a=0.26)  # fmt: skip
        elif m == "sparse":
            self.lowrise("back", "M", base=1215, hmin=210, hmax=470, wmin=90, wmax=170, haze=0.45, lod=1, pitch=(30, 40), size=(15, 21), valley=160, steeple=True)  # fmt: skip
        else:
            self.harbor_mid()

    def near_layers(self) -> None:
        m = self.mode
        if m == "dense":
            self.towers("mid_back", "N", base=1330, hmin=150, hmax=400, wmin=150, wmax=250, haze=0.16, lod=0, pitch=(46, 60), size=(26, 36), lit_scale=1.0, valley=330, gear=True, elev=6.0, dark_a=0.3, x0=-760, x1=1900, wmargin=16.0)  # fmt: skip
        elif m == "sparse":
            self.lowrise("mid_back", "N", base=1330, hmin=170, hmax=380, wmin=160, wmax=240, haze=0.16, lod=0, pitch=(50, 64), size=(28, 38), valley=330, x0=-760, x1=1900, chimneys=True, elev=6.0)  # fmt: skip
        else:
            self.harbor_near()

    # ------------------------------------------------------------------------------------ harbor
    def harbor_far(self) -> None:
        ctx = self.ctx
        b = self.b
        rng = self.rng("hfar")
        # distant headland, then the bay
        self.hills("far", "hh", 905.0, 55.0, 0.84, trees=False)
        sea_top, sea_bot = 930.0, 1210.0
        w_far = mix(b("sky_bottom"), b("water"), 0.3)  # the bay mirrors the glow of the sky
        w_near = mix(darken(b("water"), 0.28), b("sky_top"), 0.3)
        ctx.rect("far", X0, sea_top, X1 - X0, sea_bot - sea_top + 40, w_far, tag="sea", lod=0, material="water", shadow=False, gradient=(w_far, w_near, 90.0))  # fmt: skip
        ctx.rect("far", X0, sea_top - 3, X1 - X0, 6, mix(self.haze_c, "#ffffff", 0.25), tag="horizon", lod=2, alpha=0.5, shadow=False, material="emissive")  # fmt: skip
        # waves
        waves: Cmds = []
        for k in range(26):
            y = sea_top + 14 + (k % 14) * 19 + float(rng.uniform(-3, 3))
            x = float(rng.uniform(X0, X1))
            ln = float(rng.uniform(40, 120)) * (0.6 + (y - sea_top) / 160.0)
            waves += [("M", x, y), ("L", x + ln, y)]
        ctx.path(
            "far",
            waves,
            None,
            stroke=lighten(w_far, 0.3),
            sw=3.0,
            tag="wave",
            lod=2,
            shadow=False,
            alpha=0.4,
        )
        # glitter column under the sun / moon
        gx = self.sun_pos[0]
        col = "#f4f6ff" if self.night else self.b("glow")
        for k in range(9):
            y = sea_top + 10 + k * 17
            half = (16 + k * 6) * float(rng.uniform(0.7, 1.2))
            ctx.rect("far", gx - half + float(rng.uniform(-20, 20)), y, half * 2, 3.5, col, material="emissive", alpha=0.35, shadow=False, tag="glitter", lod=2, anim=Anim("twinkle", amp=0.7, freq=float(rng.uniform(0.12, 0.35)), phase=float(rng.uniform(0, 6))))  # fmt: skip
        # suspension bridge on the horizon
        self.bridge()
        # boats
        for i, (bx, by, s) in enumerate(
            ((300.0, 1080.0, 1.1), (640.0, 1020.0, 0.7), (930.0, 1110.0, 1.3))
        ):
            self.boat("far", bx, by, s, i)

    def bridge(self) -> None:
        ctx = self.ctx
        col = self.fade("stone2", 0.72)
        deck_y = 946.0
        t1, t2 = 330.0, 830.0
        top = 728.0
        steel: Cmds = []
        for tx in (t1, t2):
            steel += [
                ("M", tx - 11, deck_y + 4),
                ("L", tx - 9, top),
                ("M", tx + 11, deck_y + 4),
                ("L", tx + 9, top),
            ]
            for k in range(4):
                yy = top + 22 + k * 50
                steel += [("M", tx - 10, yy), ("L", tx + 10, yy)]
            steel += [("M", tx - 12, top + 4), ("L", tx + 12, top + 4)]
        ctx.path("far", steel, None, stroke=col, sw=5.0, tag="bridge", lod=0, shadow=False)
        ctx.rect("far", X0, deck_y, X1 - X0, 9, col, tag="bridge", lod=0, shadow=False)
        cables: Cmds = []
        hang: Cmds = []

        def cable(x_a: float, y_a: float, x_b: float, y_b: float, sag: float) -> None:
            pts = []
            n = 14
            for i in range(n + 1):
                u = i / n
                x = lerp(x_a, x_b, u)
                y = lerp(y_a, y_b, u) + sag * 4 * u * (1 - u)
                pts.append((x, y))
            cables.extend([("M", *pts[0])] + [("L", *p) for p in pts[1:]])
            for i in range(1, n):
                hang.extend([("M", pts[i][0], pts[i][1]), ("L", pts[i][0], deck_y)])

        cable(X0 - 80, deck_y - 30, t1, top + 4, 20)
        cable(t1, top + 4, t2, top + 4, 150)
        cable(t2, top + 4, X1 + 80, deck_y - 30, 20)
        ctx.path("far", cables, None, stroke=col, sw=3.4, tag="bridge", lod=0, shadow=False)
        ctx.path(
            "far", hang, None, stroke=col, sw=1.8, tag="bridge", lod=2, shadow=False, alpha=0.75
        )
        if self.lamps:
            lights: Cmds = []
            xx = X0
            while xx < X1:
                lights += _circ(xx, deck_y - 2, 3.0)
                xx += 34
            ctx.path(
                "far",
                lights,
                mix(self.b("window"), self.haze_c, 0.2),
                material="emissive",
                alpha=0.8,
                shadow=False,
                tag="bridge_lights",
                lod=2,
            )
            for tx in (t1, t2):
                self.beacon("far", tx, top - 8, int(tx))

    def boat(self, plane: str, x: float, y: float, s: float, key: int) -> None:
        ctx = self.ctx
        hull = self.fade("brick2" if key % 2 == 0 else "fabric", 0.55)
        cab = self.fade("trim", 0.55)
        a = (
            Anim("bob", amp=3.0, freq=0.12 + 0.03 * key, phase=float(key * 2)) if key == 2 else None
        )  # only the nearest boat rocks
        ctx.poly(plane, [(x - 40 * s, y - 14 * s), (x + 44 * s, y - 14 * s), (x + 30 * s, y + 4 * s), (x - 30 * s, y + 4 * s)], hull, tag="boat", lod=2, smooth=0.12, anim=a)  # fmt: skip
        ctx.rect(plane, x - 14 * s, y - 32 * s, 28 * s, 20 * s, cab, tag="boat", lod=2, anim=a)
        ctx.line(
            plane,
            (x + 20 * s, y - 14 * s),
            (x + 20 * s, y - 52 * s),
            3.0 * s,
            hull,
            tag="boat",
            lod=2,
            anim=a,
        )
        if self.lamps:
            ctx.ellipse(plane, x + 20 * s, y - 52 * s, 3.5 * s, 3.5 * s, "#fff1c4", material="emissive", glow=14 * s, shadow=False, tag="boat_light", lod=2, anim=a)  # fmt: skip

    def crane(
        self, plane: str, x: float, base: float, h: float, haze: float, flip: bool = False
    ) -> None:
        ctx = self.ctx
        col = mix(mix(self.b("accent"), self.b("brick2"), 0.3), self.haze_c, haze)
        s = -1.0 if flip else 1.0
        legs: Cmds = []
        for dx in (-26, 26):
            legs += [("M", x + dx, base), ("L", x + dx * 0.6, base - h)]
        for k in range(1, 6):
            f = k / 6
            xa, xb = x - lerp(26, 15.6, f), x + lerp(26, 15.6, f)
            ya = base - h * f
            legs += [("M", xa, ya), ("L", xb, ya)]
            if k < 5:
                legs += [
                    ("M", xa, ya),
                    ("L", lerp(x + 26, x + 15.6, f + 0.17) * 0 + xb - (xb - xa) * 0.0, ya - h / 6),
                ]
        ctx.path(plane, legs, None, stroke=col, sw=4.0, tag="crane", lod=2, shadow=False)
        jib = [
            ("M", x - s * 70, base - h),
            ("L", x + s * 230, base - h),
            ("M", x - s * 70, base - h - 22),
            ("L", x + s * 230, base - h - 22),
        ]
        for k in range(13):
            xx = x - s * 70 + s * 25 * k
            jib += [
                ("M", xx, base - h),
                ("L", xx + s * 12, base - h - 22),
                ("L", xx + s * 25, base - h),
            ]
        ctx.path(plane, jib, None, stroke=col, sw=3.0, tag="crane", lod=2, shadow=False)
        ctx.rect(
            plane,
            x - s * 70 - (22 if not flip else 0),
            base - h - 26,
            22,
            26,
            darken(col, 0.15),
            tag="crane",
            lod=2,
            shadow=False,
        )
        ctx.line(
            plane,
            (x + s * 200, base - h),
            (x + s * 200, base - h + 62),
            2.5,
            col,
            tag="crane",
            lod=2,
        )
        ctx.rect(
            plane,
            x + s * 200 - 11,
            base - h + 62,
            22,
            18,
            darken(col, 0.1),
            tag="crane",
            lod=2,
            shadow=False,
        )
        if self.lamps:
            self.beacon(plane, x, base - h - 30, int(x))

    def harbor_mid(self) -> None:
        ctx = self.ctx
        # waterfront warehouses at both sides, cranes behind them
        self.crane("back", 90.0, 1150.0, 560.0, 0.5)
        self.crane("back", 1010.0, 1150.0, 500.0, 0.5, flip=True)
        self.crane("back", -300.0, 1150.0, 460.0, 0.5)
        self.crane("back", 1330.0, 1150.0, 520.0, 0.5, flip=True)
        rng = self.rng("hmid")
        b = self.b
        x = X0 - 60
        while x < X1 + 60:
            w = float(rng.uniform(110, 200))
            c = abs(x + w / 2 - 560)
            if c < 330:  # keep the bay open in the middle
                x += 40
                continue
            h = float(rng.uniform(120, 300)) * (1.0 + 0.4 * clamp((c - 400) / 500, 0, 1))
            col = adjust(
                mix(mix(b("brick"), b("stone"), float(rng.uniform(0.2, 0.7))), self.haze_c, 0.45),
                light=float(rng.uniform(0.95, 1.05)),
            )
            top = 1215 - h
            ctx.rect(
                "back", x, top, w, h + 120, col, tag="building", lod=1, elev=4, material="paper"
            )
            ctx.rect(
                "back",
                x + w * 0.78,
                top,
                w * 0.22,
                h + 120,
                darken(col, 0.1),
                tag="building_shade",
                lod=2,
                alpha=0.45,
                shadow=False,
            )
            # sawtooth warehouse roof
            teeth = max(2, int(w / 46))
            tw = w / teeth
            saw = []
            for i in range(teeth):
                saw += [
                    ("M", x + i * tw, top),
                    ("L", x + i * tw, top - 26),
                    ("L", x + (i + 1) * tw, top),
                    ("Z",),
                ]
            ctx.path("back", saw, darken(col, 0.12), tag="roof", lod=1, elev=4)
            self.windows(
                "back",
                rng,
                x,
                w,
                top + 6,
                1215.0,
                col,
                (30, 40),
                (15, 20),
                0.45,
                2,
                1.0,
                _calm(x + w / 2),
                margin=10.0,
            )
            x += w + float(rng.uniform(0, 14))

    def harbor_near(self) -> None:
        ctx = self.ctx
        b = self.b
        rng = self.rng("hnear")
        x = -760.0
        while x < 1900.0:
            w = float(rng.uniform(190, 280))
            c = abs(x + w / 2 - 560)
            if c < 340:
                x += 60
                continue
            h = float(rng.uniform(170, 330))
            col = adjust(
                mix(mix(b("brick"), b("stone"), float(rng.uniform(0.15, 0.6))), self.haze_c, 0.14),
                light=float(rng.uniform(0.95, 1.05)),
            )
            top = 1330 - h
            ctx.rect(
                "mid_back", x, top, w, h + 120, col, tag="building", lod=0, elev=6, material="paper"
            )
            ctx.rect(
                "mid_back",
                x + w * 0.8,
                top,
                w * 0.2,
                h + 120,
                darken(col, 0.1),
                tag="building_shade",
                lod=2,
                alpha=0.45,
                shadow=False,
            )
            teeth = max(2, int(w / 60))
            tw = w / teeth
            saw = []
            for i in range(teeth):
                saw += [
                    ("M", x + i * tw, top),
                    ("L", x + i * tw, top - 34),
                    ("L", x + (i + 1) * tw, top),
                    ("Z",),
                ]
            ctx.path("mid_back", saw, darken(col, 0.14), tag="roof", lod=1, elev=6)
            self.windows(
                "mid_back",
                rng,
                x,
                w,
                top + 8,
                1330.0,
                col,
                (50, 64),
                (28, 38),
                0.14,
                1,
                1.0,
                _calm(x + w / 2),
                margin=14.0,
                dark_a=0.3,
            )
            # a bollard/ladder detail
            ctx.rect(
                "mid_back",
                x + w * 0.1,
                top - 24,
                40,
                24,
                mix(b("metal"), self.haze_c, 0.2),
                r=3,
                tag="ac_unit",
                lod=2,
                shadow=False,
            )
            x += w + float(rng.uniform(0, 20))

    # ------------------------------------------------------------------------------- the roof
    def parapet(self) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        wall = mix(mix(b("stone"), b("wall2"), 0.35), b("stone2"), 0.15)
        wall_dark = darken(wall, 0.18)
        ctx.rect(P, X0, WALL_Y, X1 - X0, FLOOR_Y - WALL_Y + 6, wall, tag="parapet", elev=7, material="stone", gradient=(lighten(wall, 0.04), wall_dark, 90.0))  # fmt: skip
        # brick/concrete courses
        courses: Cmds = []
        rng = self.rng("parapet")
        for k, y in enumerate((WALL_Y + 38, WALL_Y + 76, WALL_Y + 114)):
            courses += [("M", X0, y), ("L", X1, y)]
            x = X0 + float(rng.uniform(0, 80)) + (k % 2) * 40
            while x < X1:
                courses += [("M", x, y - 38), ("L", x, y)]
                x += 120
        ctx.path(
            P,
            courses,
            None,
            stroke=darken(wall, 0.2),
            sw=2.5,
            tag="mortar",
            lod=2,
            shadow=False,
            alpha=0.55,
        )
        # cap
        cap = lighten(wall, 0.25)
        ctx.rect(
            P,
            X0,
            CAP_Y,
            X1 - X0,
            WALL_Y - CAP_Y + 4,
            cap,
            tag="parapet_cap",
            elev=9,
            material="stone",
        )
        ctx.rect(
            P,
            X0,
            CAP_Y,
            X1 - X0,
            6,
            lighten(cap, 0.25),
            tag="parapet_cap",
            lod=2,
            shadow=False,
            alpha=0.8,
        )
        ctx.rect(
            P, X0, WALL_Y, X1 - X0, 10, b("shadow"), alpha=0.22, tag="shade", lod=2, shadow=False
        )
        # drain scuppers
        for sx in (-120.0, 470.0, 1190.0):
            ctx.rect(
                P,
                sx,
                FLOOR_Y - 34,
                46,
                26,
                darken(wall, 0.3),
                r=4,
                tag="scupper",
                lod=2,
                shadow=False,
            )
            ctx.rect(
                P,
                sx + 6,
                FLOOR_Y - 28,
                34,
                14,
                darken(wall, 0.5),
                r=3,
                tag="scupper",
                lod=2,
                shadow=False,
            )

    def railing(self) -> None:
        if not self.p.railing:
            return
        ctx = self.ctx
        P = "mid_back"
        steel = mix(darken(mix(self.b("metal"), self.b("fabric"), 0.3), 0.45), self.haze_c, 0.12)
        posts: Cmds = []
        x = X0 + 10
        while x < X1:
            posts += _rect(x, CAP_Y - 126, 7, 130)
            x += 96
        ctx.path(P, posts, steel, tag="railing", lod=1, elev=3)
        ctx.rect(P, X0, CAP_Y - 126, X1 - X0, 9, steel, r=4, tag="railing", elev=3)
        ctx.rect(P, X0, CAP_Y - 84, X1 - X0, 5, steel, tag="railing", lod=1, shadow=False)
        ctx.rect(P, X0, CAP_Y - 42, X1 - X0, 5, steel, tag="railing", lod=1, shadow=False)
        ctx.rect(
            P,
            X0,
            CAP_Y - 126,
            X1 - X0,
            3,
            lighten(steel, 0.35),
            tag="railing_hi",
            lod=2,
            shadow=False,
            alpha=0.7,
        )

    def floor(self) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        rng = self.rng("floor")
        deck = self.mode != "dense"
        if deck:
            near = mix(b("wood"), b("floor"), 0.4)
            far = darken(near, 0.12)
        else:
            near = mix(lighten(b("stone"), 0.18), b("asphalt"), 0.35)
            far = darken(near, 0.14)
        ctx.rect(P, X0, FLOOR_Y, X1 - X0, Y1 - FLOOR_Y + 40, far, tag="floor", material="wood" if deck else "stone", shadow=False, gradient=(far, near, 90.0))  # fmt: skip
        ctx.rect(
            P, X0, FLOOR_Y, X1 - X0, 30, b("shadow"), alpha=0.2, tag="shade", lod=2, shadow=False
        )
        lines: Cmds = []
        if deck:  # boards run along the parapet, widening toward the viewer
            y = FLOOR_Y
            gap = 22.0
            k = 0
            tone: Cmds = []
            while y < Y1 + 40:
                lines += [("M", X0, y), ("L", X1, y)]
                x = X0 + float(rng.uniform(0, 300))
                while x < X1:
                    lines += [("M", x, y), ("L", x, y + gap)]
                    x += float(rng.uniform(220, 420))
                if k % 3 == 1:
                    tone += _rect(X0, y, X1 - X0, gap)
                y += gap
                gap *= 1.09
                k += 1
            ctx.path(P, tone, lighten(near, 0.08), tag="deck", lod=2, shadow=False, alpha=0.35)
        else:  # paver slabs converging toward the middle
            for k in range(-14, 15):
                lines += [("M", 540 + k * 52.0, FLOOR_Y), ("L", 540 + k * 175.0, Y1 + 40)]
            y = FLOOR_Y
            gap = 34.0
            while y < Y1 + 40:
                lines += [("M", X0, y), ("L", X1, y)]
                y += gap
                gap *= 1.2
        ctx.path(
            P,
            lines,
            None,
            stroke=darken(near, 0.22),
            sw=2.6,
            tag="paving",
            lod=2,
            shadow=False,
            alpha=0.5,
        )

    # ------------------------------------------------------------------------------- shadows
    def contact(self, x: float, y: float, rx: float, ry: float = 8.0, a: float = 0.26) -> None:
        self.ctx.ellipse(
            "mid_back", x, y, rx, ry, self.b("shadow"), alpha=a, shadow=False, tag="shadow", lod=2
        )

    def glow(self, plane: str, x: float, y: float, rx: float, ry: float, col: str, alpha: float, steps: int = 10, tag: str = "glow") -> None:  # fmt: skip
        """Soft radial glow: many nested, very faint ellipses (alpha builds up toward the centre)."""
        a_each = 1.0 - (1.0 - clamp(alpha * 1.6, 0.0, 0.9)) ** (1.0 / steps)
        for i in range(steps):
            k = 1.0 - i / steps
            self.ctx.ellipse(
                plane,
                x,
                y,
                rx * k,
                ry * k,
                col,
                material="emissive",
                alpha=a_each,
                shadow=False,
                tag=tag,
                lod=2,
            )

    # ------------------------------------------------------------------------------------ props
    def stair_hut(self, x: float, w: float) -> None:
        """The stairwell penthouse: a plain box with a lit door, a wall lamp, pipes and an antenna mast."""
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        base = FLOOR_Y + 36
        h = 560.0
        top = base - h
        wall = mix(b("wall"), b("stone"), 0.45)
        self.contact(x + w / 2, base + 4, w * 0.6, 12, 0.28)
        ctx.rect(P, x, top, w, h, wall, tag="hut", elev=9, material="paper", gradient=(lighten(wall, 0.05), darken(wall, 0.1), 90.0))  # fmt: skip
        ctx.rect(
            P,
            x + w - 34,
            top,
            34,
            h,
            darken(wall, 0.12),
            tag="hut_shade",
            lod=2,
            shadow=False,
            alpha=0.5,
        )
        ctx.rect(
            P,
            x - 16,
            top - 26,
            w + 32,
            30,
            darken(wall, 0.25),
            tag="hut_roof",
            elev=10,
            material="paper",
        )
        ctx.rect(
            P, x - 16, top - 26, w + 32, 8, lighten(wall, 0.2), tag="hut_roof", lod=2, shadow=False
        )
        courses: Cmds = []
        yy = top + 60
        while yy < base - 30:
            courses += [("M", x, yy), ("L", x + w, yy)]
            yy += 60
        ctx.path(
            P,
            courses,
            None,
            stroke=darken(wall, 0.16),
            sw=2.2,
            tag="stucco",
            lod=2,
            shadow=False,
            alpha=0.5,
        )
        dw, dh = 112.0, 330.0
        dx = x + w * 0.55
        lit = self.lamps
        ctx.rect(
            P,
            dx - 10,
            base - dh - 10,
            dw + 20,
            dh + 10,
            lighten(wall, 0.4),
            tag="door_frame",
            lod=1,
            shadow=False,
        )
        door_c = mix(b("accent2"), darken(wall, 0.5), 0.45)
        ctx.rect(P, dx, base - dh, dw, dh, door_c, tag="door", elev=3, material="metal")
        ctx.rect(P, dx + 18, base - dh + 34, dw - 36, 84, b("window") if lit else mix(b("glass"), b("fabric"), 0.4), material="emissive" if lit else "glass", alpha=0.85 if lit else 1.0, tag="door_glass", lod=1, shadow=False)  # fmt: skip
        ctx.path(P, [("M", dx + dw / 2, base - dh + 34), ("L", dx + dw / 2, base - dh + 118), ("M", dx + 18, base - dh + 76), ("L", dx + dw - 18, base - dh + 76)], None, stroke=door_c, sw=4, tag="door_glass", lod=2, shadow=False)  # fmt: skip
        ctx.rect(
            P,
            dx + 16,
            base - 120,
            dw - 32,
            60,
            darken(door_c, 0.12),
            r=3,
            tag="door_panel",
            lod=2,
            shadow=False,
        )
        ctx.ellipse(
            P,
            dx + dw - 20,
            base - 150,
            7,
            7,
            lighten(b("accent3"), 0.2),
            tag="door_handle",
            lod=2,
            shadow=False,
        )
        ctx.rect(
            P, dx - 34, base - 16, dw + 68, 16, lighten(b("stone"), 0.15), tag="step", lod=1, elev=2
        )
        # wall lamp above the door
        lx = dx + dw / 2
        ly = base - dh - 62
        ctx.rect(P, lx - 6, ly - 4, 12, 22, darken(b("metal"), 0.4), tag="wall_lamp", lod=2)
        ctx.ellipse(P, lx, ly + 16, 17, 20, b("window") if lit else lighten(b("glass"), 0.3), material="emissive" if lit else "glass", glow=50 if lit else 0.0, shadow=False, tag="wall_lamp", lod=1)  # fmt: skip
        if lit:
            a = 1.0 if self.night else 0.6
            self.glow(P, lx, ly + 16, 170, 170, b("window"), 0.32 * a, 10, "lamp_halo")
            ctx.poly(P, [(lx - 18, ly + 34), (lx + 18, ly + 34), (lx + 190, base + 4), (lx - 190, base + 4)], b("window"), material="emissive", alpha=0.06 * a, shadow=False, tag="light_cone", lod=2)  # fmt: skip
        # number plate + drain pipe
        ctx.rect(
            P, x + 34, top + 70, 80, 50, lighten(wall, 0.3), r=5, tag="plate", lod=2, shadow=False
        )
        ctx.path(
            P,
            _rect(x + 50, top + 84, 14, 22) + _rect(x + 74, top + 84, 14, 22),
            darken(wall, 0.4),
            tag="plate",
            lod=2,
            shadow=False,
        )
        pipe = darken(b("metal"), 0.25)
        ctx.rect(P, x + 14, top + 28, 16, h - 40, pipe, r=5, tag="pipe", lod=1, elev=3)
        ctx.rect(P, x + 8, top + 130, 28, 12, darken(pipe, 0.2), tag="pipe", lod=2, shadow=False)
        ctx.rect(P, x + 8, top + 330, 28, 12, darken(pipe, 0.2), tag="pipe", lod=2, shadow=False)
        # antenna mast with a dish and a blinking red light
        mx = x + w * 0.2
        ctx.line(
            P, (mx, top - 24), (mx, top - 230), 7, darken(b("metal"), 0.4), tag="antenna", lod=1
        )
        for yy, ln in ((top - 190, 40), (top - 140, 30), (top - 96, 22)):
            ctx.line(
                P, (mx - ln, yy), (mx + ln, yy), 4, darken(b("metal"), 0.4), tag="antenna", lod=2
            )
        ctx.ellipse(
            P,
            mx + 56,
            top - 52,
            30,
            19,
            lighten(b("metal"), 0.3),
            tag="dish",
            lod=1,
            elev=3,
            rot=-35,
        )
        ctx.line(
            P,
            (mx + 50, top - 46),
            (mx + 62, top - 26),
            4,
            darken(b("metal"), 0.3),
            tag="dish",
            lod=1,
        )
        self.beacon(P, mx, top - 238, 77)

    def water_tank(self, x: float, base: float) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        steel = darken(mix(b("metal"), b("fabric"), 0.3), 0.45)
        wood = mix(b("wood2"), b("wood"), 0.3)
        self.contact(x + 10, base + 4, 120, 12, 0.28)
        legs: Cmds = []
        for dx in (-82, -32, 32, 82):
            legs += [("M", x + dx, base), ("L", x + dx * 0.85, base - 230)]
        legs += [("M", x - 82, base - 60), ("L", x + 32, base - 170), ("M", x + 82, base - 60), ("L", x - 32, base - 170), ("M", x - 78, base - 150), ("L", x + 78, base - 150), ("M", x - 84, base - 60), ("L", x + 84, base - 60)]  # fmt: skip
        ctx.path(P, legs, None, stroke=steel, sw=7.0, tag="tank_frame", lod=1, shadow=True, elev=4)
        ctx.rect(P, x - 100, base - 236, 200, 12, steel, tag="tank_frame", lod=2, elev=4)
        top = base - 236
        ctx.rect(
            P, x - 92, top - 190, 184, 190, wood, r=10, tag="water_tank", elev=8, material="wood"
        )
        ctx.rect(
            P,
            x + 44,
            top - 188,
            46,
            186,
            darken(wood, 0.18),
            r=8,
            tag="tank_shade",
            lod=2,
            shadow=False,
            alpha=0.5,
        )
        for yy in (top - 160, top - 100, top - 40):
            ctx.rect(
                P, x - 94, yy, 188, 8, darken(steel, 0.1), tag="tank_hoop", lod=2, shadow=False
            )
        plank: Cmds = []
        for k in range(-3, 4):
            plank += [("M", x + k * 26, top - 186), ("L", x + k * 26, top - 4)]
        ctx.path(
            P,
            plank,
            None,
            stroke=darken(wood, 0.3),
            sw=2.0,
            tag="tank_plank",
            lod=2,
            shadow=False,
            alpha=0.6,
        )
        ctx.poly(
            P,
            [(x - 104, top - 186), (x + 104, top - 186), (x, top - 262)],
            darken(wood, 0.25),
            tag="tank_roof",
            elev=9,
            smooth=0.04,
            material="wood",
        )
        ctx.line(P, (x - 98, top - 70), (x - 98, top + 205), 5, steel, tag="ladder", lod=2)
        ctx.line(P, (x - 78, top - 70), (x - 78, top + 205), 5, steel, tag="ladder", lod=2)

    def ac_unit(self, x: float, base: float, w: float = 190.0) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        h = w * 0.78
        body = mix(lighten(b("metal"), 0.35), b("stone"), 0.3)
        self.contact(x + w / 2, base + 3, w * 0.62, 9, 0.25)
        ctx.rect(P, x, base - h, w, h, body, r=7, tag="ac_unit", elev=6, material="metal")
        ctx.rect(
            P,
            x + w - 36,
            base - h,
            36,
            h,
            darken(body, 0.14),
            tag="ac_unit",
            lod=2,
            shadow=False,
            alpha=0.6,
        )
        cx, cy = x + w * 0.36, base - h * 0.55
        ctx.ellipse(
            P, cx, cy, w * 0.26, w * 0.26, darken(body, 0.3), tag="ac_fan", lod=1, shadow=False
        )
        ctx.ellipse(
            P, cx, cy, w * 0.2, w * 0.2, darken(body, 0.5), tag="ac_fan", lod=2, shadow=False
        )
        blades: Cmds = []
        for k in range(4):
            a = math.pi * k / 4
            blades += [
                ("M", cx - math.cos(a) * w * 0.2, cy - math.sin(a) * w * 0.2),
                ("L", cx + math.cos(a) * w * 0.2, cy + math.sin(a) * w * 0.2),
            ]
        ctx.path(
            P, blades, None, stroke=darken(body, 0.25), sw=5, tag="ac_fan", lod=2, shadow=False
        )
        louv: Cmds = []
        for k in range(6):
            louv += [
                ("M", x + w * 0.74, base - h * 0.85 + k * h * 0.13),
                ("L", x + w * 0.74 + w * 0.17, base - h * 0.85 + k * h * 0.13),
            ]
        ctx.path(
            P, louv, None, stroke=darken(body, 0.4), sw=4, tag="ac_louver", lod=2, shadow=False
        )
        ctx.rect(
            P, x + 10, base - h - 12, 26, 12, darken(body, 0.4), tag="ac_unit", lod=2, shadow=False
        )

    def vent(self, x: float, base: float, kind: int = 0) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        metal = mix(lighten(b("metal"), 0.2), b("stone"), 0.3)
        self.contact(x, base + 2, 44, 8, 0.25)
        if kind == 0:  # tall stack with a cowl
            ctx.rect(
                P, x - 22, base - 320, 44, 320, metal, r=8, tag="vent", elev=5, material="metal"
            )
            ctx.rect(
                P,
                x - 8,
                base - 320,
                12,
                320,
                lighten(metal, 0.3),
                tag="vent",
                lod=2,
                shadow=False,
                alpha=0.6,
            )
            ctx.poly(P, [(x - 40, base - 316), (x + 40, base - 316), (x + 22, base - 372), (x - 22, base - 372)], darken(metal, 0.12), tag="vent", elev=5, smooth=0.1)  # fmt: skip
            ctx.rect(
                P, x - 30, base - 50, 60, 16, darken(metal, 0.2), tag="vent", lod=2, shadow=False
            )
        else:  # elbow pipe
            ctx.rect(
                P, x - 17, base - 200, 34, 200, metal, r=7, tag="vent", elev=5, material="metal"
            )
            ctx.path(
                P,
                [("M", x, base - 196), ("Q", x, base - 270, x + 62, base - 270)],
                None,
                stroke=metal,
                sw=34,
                tag="vent",
                lod=1,
                elev=5,
            )
            ctx.ellipse(
                P, x + 68, base - 270, 12, 22, darken(metal, 0.4), tag="vent", lod=2, shadow=False
            )

    def potted_tree(self, x: float, base: float, h: float = 400.0) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        rng = self.rng("ptree", int(x))
        pot = mix(b("brick2"), b("wall2"), 0.2)
        self.contact(x, base + 3, 78, 11, 0.26)
        ctx.poly(
            P,
            [(x - 62, base - 110), (x + 62, base - 110), (x + 46, base), (x - 46, base)],
            pot,
            tag="pot",
            elev=6,
            smooth=0.08,
        )
        ctx.rect(P, x - 70, base - 130, 140, 26, lighten(pot, 0.12), r=6, tag="pot", lod=1, elev=6)
        ctx.line(
            P,
            (x, base - 128),
            (x + 8, base - h + 110),
            18,
            darken(b("trunk"), 0.1),
            tag="trunk",
            lod=1,
        )
        g = [b("foliage2"), b("foliage"), b("foliage3")]
        cx, cy = x + 8, base - h + 70
        ph = float(rng.uniform(0, 6))
        for i, (rr, dx, dy) in enumerate(((112, 0, 0), (84, -14, -16), (52, -34, -34))):
            cmds = (
                _circ(cx + dx, cy + dy, rr)
                + _circ(cx + dx + rr * 0.7, cy + dy + rr * 0.3, rr * 0.55)
                + _circ(cx + dx - rr * 0.7, cy + dy + rr * 0.35, rr * 0.5)
            )
            ctx.path(P, cmds, g[i], tag="canopy", lod=0 if i == 0 else 1, elev=6 + i, material="grass", anim=Anim("sway", amp=0.8, freq=0.17, phase=ph, pivot=(x, base - 120)) if i > 0 else None)  # fmt: skip

    def planter(self, x: float, base: float, w: float, key: int) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        rng = self.rng("planter", key)
        box = mix(b("brick"), b("wall2"), 0.25)
        self.contact(x + w / 2, base + 3, w * 0.58, 9, 0.25)
        ctx.rect(P, x, base - 96, w, 96, box, r=6, tag="planter", elev=6, material="wood")
        ctx.rect(
            P, x - 6, base - 110, w + 12, 20, lighten(box, 0.12), r=6, tag="planter", lod=1, elev=6
        )
        ctx.rect(
            P, x + 12, base - 70, w - 24, 8, darken(box, 0.2), tag="planter", lod=2, shadow=False
        )
        g = [b("foliage2"), b("foliage"), b("foliage3")]
        puff: list[Cmds] = [[], [], []]
        n = max(3, int(w / 50))
        for i in range(n):
            cx = x + 24 + i * (w - 48) / max(1, n - 1)
            hh = float(rng.uniform(70, 140))
            puff[i % 3] += _circ(cx, base - 112 - hh * 0.45, float(rng.uniform(32, 50)))
            puff[(i + 1) % 3] += _circ(
                cx + float(rng.uniform(-14, 14)), base - 112 - hh * 0.85, float(rng.uniform(22, 34))
            )
        ph = float(rng.uniform(0, 6))
        for i in range(3):
            ctx.path(P, puff[i], g[i], tag="plant", lod=1, elev=5, material="grass", anim=Anim("sway", amp=1.1, freq=0.2 + 0.03 * i, phase=ph, pivot=(x + w / 2, base - 100)))  # fmt: skip
        fl: Cmds = []
        for _ in range(max(2, int(w / 60))):
            fl += _circ(
                x + float(rng.uniform(14, w - 14)), base - 112 - float(rng.uniform(50, 150)), 9.0
            )
        ctx.path(
            P,
            fl,
            b("accent"),
            tag="flower",
            lod=2,
            shadow=False,
            anim=Anim("sway", amp=1.1, freq=0.2, phase=ph, pivot=(x + w / 2, base - 100)),
        )

    def lounge_chair(self, x: float, base: float) -> None:
        """A sun lounger in profile: a thick bent cushion on a wooden frame, head end to the right."""
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        wood = mix(b("wood"), b("accent3"), 0.12)
        cush = mix(b("accent2"), "#ffffff", 0.1)
        self.contact(x + 110, base + 3, 150, 10, 0.25)
        for lx, ly in ((x + 8, base - 62), (x + 118, base - 62), (x + 188, base - 120)):
            ctx.line(
                P,
                (lx, ly),
                (lx + (6 if lx > x + 150 else 0), base),
                9,
                wood,
                tag="chair",
                lod=1,
                elev=3,
            )
        ctx.path(
            P,
            [("M", x, base - 66), ("L", x + 118, base - 66), ("L", x + 196, base - 176)],
            None,
            stroke=wood,
            sw=12,
            tag="chair",
            lod=1,
            elev=3,
        )
        ctx.path(
            P,
            [("M", x, base - 82), ("L", x + 118, base - 82), ("L", x + 200, base - 194)],
            None,
            stroke=cush,
            sw=34,
            tag="chair",
            elev=6,
            material="fabric",
        )
        ctx.path(
            P,
            [("M", x + 4, base - 92), ("L", x + 112, base - 92), ("L", x + 192, base - 202)],
            None,
            stroke=lighten(cush, 0.35),
            sw=8,
            tag="chair_stripe",
            lod=2,
            shadow=False,
            alpha=0.7,
        )

    def floor_lantern(self, x: float, base: float) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        steel = darken(mix(b("metal"), b("fabric"), 0.3), 0.5)
        self.contact(x, base + 2, 34, 6, 0.22)
        ctx.rect(P, x - 22, base - 14, 44, 14, steel, r=4, tag="lantern", elev=4)
        glass = b("window") if self.lamps else lighten(b("glass"), 0.3)
        ctx.rect(P, x - 18, base - 96, 36, 82, glass, r=6, material="emissive" if self.lamps else "glass", glow=50 if self.lamps else 0.0, shadow=False, tag="lantern", lod=1)  # fmt: skip
        ctx.path(
            P,
            [
                ("M", x - 6, base - 96),
                ("L", x - 6, base - 14),
                ("M", x + 6, base - 96),
                ("L", x + 6, base - 14),
            ],
            None,
            stroke=steel,
            sw=3,
            tag="lantern",
            lod=2,
            shadow=False,
        )
        ctx.poly(
            P,
            [(x - 26, base - 96), (x + 26, base - 96), (x + 12, base - 120), (x - 12, base - 120)],
            steel,
            tag="lantern",
            elev=4,
            smooth=0.1,
        )
        ctx.path(
            P,
            [("M", x - 14, base - 118), ("Q", x, base - 150, x + 14, base - 118)],
            None,
            stroke=steel,
            sw=4,
            tag="lantern",
            lod=2,
            shadow=False,
        )
        if self.lamps:
            a = 1.0 if self.night else 0.6
            self.glow(P, x, base - 56, 130, 110, b("window"), 0.30 * a, 10, "lantern_glow")

    def cat(self, x: float, base: float) -> None:
        """A black cat sitting on the parapet, tail curled, eyes catching the light."""
        ctx = self.ctx
        P = "mid_back"
        fur = darken(mix(self.b("stone2"), self.b("fabric"), 0.3), 0.62)
        ctx.path(P, [("M", x + 22, base - 6), ("Q", x + 66, base - 4, x + 56, base - 52), ("Q", x + 52, base - 70, x + 40, base - 64)], None, stroke=fur, sw=12, tag="cat", lod=1, shadow=False)  # fmt: skip
        ctx.poly(P, [(x - 30, base), (x + 30, base), (x + 24, base - 52), (x + 10, base - 82), (x - 10, base - 82), (x - 24, base - 52)], fur, tag="cat", lod=1, elev=3, smooth=0.3)  # fmt: skip
        ctx.ellipse(P, x, base - 98, 22, 20, fur, tag="cat", lod=1, elev=3)
        ctx.poly(
            P,
            [(x - 20, base - 106), (x - 14, base - 130), (x - 4, base - 112)],
            fur,
            tag="cat",
            lod=1,
            shadow=False,
        )
        ctx.poly(
            P,
            [(x + 20, base - 106), (x + 14, base - 130), (x + 4, base - 112)],
            fur,
            tag="cat",
            lod=1,
            shadow=False,
        )
        eye = "#9dff7a" if self.night else mix("#e6d36a", fur, 0.15)
        ctx.path(P, _circ(x - 8, base - 100, 3.4) + _circ(x + 8, base - 100, 3.4), eye, material="emissive" if self.night else "flat", glow=10 if self.night else 0.0, shadow=False, tag="cat_eyes", lod=2)  # fmt: skip

    def rug(self, cx: float, y0: float, y1: float, wtop: float, wbot: float) -> None:
        """A woven striped rug in perspective on the floor."""
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        floor_c = mix(b("stone"), b("asphalt"), 0.4)
        pal = [
            mix(b("accent"), floor_c, 0.55),
            mix(b("trim"), floor_c, 0.5),
            mix(b("accent2"), floor_c, 0.55),
            mix(b("accent3"), floor_c, 0.55),
        ]
        groups: list[Cmds] = [[], [], [], []]

        def half(y: float) -> float:
            return lerp(wtop, wbot, (y - y0) / (y1 - y0)) / 2

        ys = [y0]
        n = 9
        for i in range(1, n + 1):
            ys.append(lerp(y0, y1, (i / n) ** 1.15))
        ctx.poly(P, [(cx - half(y0) - 14, y0 - 4), (cx + half(y0) + 14, y0 - 4), (cx + half(y1) + 18, y1 + 6), (cx - half(y1) - 18, y1 + 6)], darken(pal[0], 0.2), tag="rug", elev=2, smooth=0.02, material="fabric")  # fmt: skip
        for i in range(n):
            ya, yb = ys[i], ys[i + 1]
            k = 1 if i in (0, n - 1) else (2 if i % 3 == 0 else (3 if i % 3 == 1 else 0))
            groups[k] += _quad(
                (cx - half(ya), ya), (cx + half(ya), ya), (cx + half(yb), yb), (cx - half(yb), yb)
            )
        for k, g in enumerate(groups):
            if g:
                ctx.path(P, g, pal[k], tag="rug", lod=1, shadow=False, material="fabric")
        fringe: Cmds = []
        for yy in [lerp(y0, y1, u / 14) for u in range(14)]:
            fringe += [
                ("M", cx - half(yy) - 14, yy),
                ("L", cx - half(yy) - 30, yy + 3),
                ("M", cx + half(yy) + 14, yy),
                ("L", cx + half(yy) + 30, yy + 3),
            ]
        ctx.path(
            P, fringe, None, stroke=pal[1], sw=3, tag="rug_fringe", lod=2, shadow=False, alpha=0.8
        )

    def skylight(self, x: float, y: float, w: float, h: float) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        steel = darken(mix(b("metal"), b("fabric"), 0.3), 0.4)
        top_w = w * 0.84
        ctx.poly(P, [(x + (w - top_w) / 2, y), (x + (w + top_w) / 2, y), (x + w, y + h), (x, y + h)], steel, tag="skylight", elev=5, smooth=0.02, material="metal")  # fmt: skip
        glass = (
            b("window")
            if self.lamps
            else mix(mix(b("glass"), b("sky_bottom"), 0.4), "#ffffff", 0.1)
        )
        ins = 12.0
        ctx.poly(P, [(x + (w - top_w) / 2 + ins, y + ins), (x + (w + top_w) / 2 - ins, y + ins), (x + w - ins - 6, y + h - ins), (x + ins + 6, y + h - ins)], glass, material="emissive" if self.lamps else "glass", alpha=0.55 if self.lamps else 1.0, tag="skylight_glass", lod=1, shadow=False)  # fmt: skip
        ctx.path(P, [("M", x + w / 2, y + ins), ("L", x + w / 2, y + h - ins), ("M", x + w * 0.25, y + ins), ("L", x + w * 0.2, y + h - ins), ("M", x + w * 0.75, y + ins), ("L", x + w * 0.8, y + h - ins)], None, stroke=steel, sw=5, tag="skylight", lod=2, shadow=False)  # fmt: skip
        if self.lamps:
            a = 1.0 if self.night else 0.5
            ctx.ellipse(
                P,
                x + w / 2,
                y + h * 0.5,
                w * 0.8,
                h * 1.1,
                b("window"),
                material="emissive",
                alpha=0.07 * a,
                shadow=False,
                tag="light_pool",
                lod=2,
            )

    def cables(self) -> None:
        ctx = self.ctx
        P = "mid_back"
        col = darken(self.b("asphalt"), 0.35)
        ctx.path(P, [("M", -200.0, FLOOR_Y + 70), ("Q", 120.0, 1470.0, 340.0, 1620.0), ("Q", 560.0, 1760.0, 900.0, 1790.0), ("Q", 1100.0, 1800.0, 1300.0, 1740.0)], None, stroke=col, sw=5, tag="cable", lod=2, shadow=False, alpha=0.42)  # fmt: skip

    def props(self) -> None:
        d = self.ctx.density
        P = "mid_back"
        _ = P
        # left: the stairwell hut (door, lamp, mast) and a pipe at its foot
        self.stair_hut(-150.0, 350.0)
        # right: the wooden water tower and a lounge corner
        self.water_tank(1000.0, FLOOR_Y + 50)
        self.lounge_chair(858.0, 1410.0)
        self.floor_lantern(836.0, 1412.0)
        self.cat(655.0, CAP_Y + 2)
        if d >= 0.25:
            self.rug(440.0, 1490.0, 1690.0, 400.0, 540.0)
        if d >= 0.4:
            self.skylight(790.0, 1670.0, 230.0, 124.0)
        if d >= 0.5:
            self.cables()
            self.ac_unit(-420.0, FLOOR_Y + 60, 200.0)
        if d >= 0.55:
            self.potted_tree(1260.0, FLOOR_Y + 130, 420.0)
            self.vent(-280.0, FLOOR_Y + 70, 0)
        if d >= 0.7:
            self.planter(1330.0, FLOOR_Y + 70, 260.0, 0)
            self.ac_unit(1500.0, FLOOR_Y + 80, 200.0)
        if d >= 0.8:
            self.vent(-560.0, FLOOR_Y + 60, 1)
            self.planter(-760.0, FLOOR_Y + 70, 230.0, 1)

    def string_lights(self) -> None:
        if not self.p.lights:
            return
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        steel = darken(mix(b("metal"), b("fabric"), 0.3), 0.5)
        pole_x = (-26.0, 1076.0)
        top_y = 470.0
        for px in pole_x:
            ctx.poly(
                P,
                [(px - 9, FLOOR_Y + 90), (px + 9, FLOOR_Y + 90), (px + 6, top_y), (px - 6, top_y)],
                steel,
                tag="pole",
                elev=7,
            )
            ctx.rect(
                P,
                px - 22,
                FLOOR_Y + 80,
                44,
                14,
                darken(steel, 0.1),
                r=4,
                tag="pole_base",
                lod=2,
                elev=5,
            )
            ctx.line(
                P,
                (px, top_y + 6),
                (px + (40 if px < 500 else -40), top_y - 6),
                4,
                steel,
                tag="pole",
                lod=1,
            )
        swags = ((620.0, 150.0, 1), (700.0, 120.0, 2))  # (sag y at the middle, droop) per string
        bulb_groups: list[Cmds] = [[] for _ in range(4)]
        for si, (ymid, sag, _k) in enumerate(swags):
            xa, xb = pole_x[0], pole_x[1]
            ya = top_y + 8 + si * 40
            pts = []
            n = 44
            for i in range(n + 1):
                u = i / n
                x = lerp(xa, xb, u)
                y = lerp(ya, ya + 4, u) + (ymid - ya) * 4 * u * (1 - u) * (1.0 + 0.0 * sag)
                pts.append((x, y))
            wire = [("M", *pts[0])] + [("L", *p) for p in pts[1:]]
            ctx.path(
                P,
                wire,
                None,
                stroke=darken(steel, 0.1),
                sw=2.6,
                tag="string",
                lod=1,
                shadow=False,
                alpha=0.9,
            )
            for i in range(2, n - 1, 2):
                x, y = pts[i]
                bulb_groups[(i // 2 + si) % 4] += _circ(x, y + 12, 7.5)
        on = self.lamps
        warm = b("window")
        cols = [mix(warm, "#ffffff", 0.25), warm, mix(warm, b("accent3"), 0.35), warm]
        for i, g in enumerate(bulb_groups):
            if on:
                ctx.path(P, g, cols[i], material="emissive", glow=34.0, shadow=False, tag="bulb", lod=1, anim=Anim("twinkle", amp=0.30, freq=0.22 + 0.05 * i, phase=float(i * 1.7)))  # fmt: skip
            else:
                ctx.path(P, g, lighten(b("glass"), 0.4), tag="bulb", lod=1, elev=2)
        if on:  # a soft warm haze along the strings and light pools on the deck
            a = 1.0 if self.night else 0.6
            for ymid, _s, _k in swags:
                self.glow(P, 520.0, ymid + 14, 640, 70, warm, 0.10 * a, 8, "string_glow")
            for gx, gy, rx in ((300.0, 1440.0, 260.0), (780.0, 1500.0, 300.0)):
                ctx.ellipse(
                    P,
                    gx,
                    gy,
                    rx,
                    rx * 0.14,
                    warm,
                    material="emissive",
                    alpha=0.10 * a,
                    shadow=False,
                    tag="light_pool",
                    lod=2,
                )

    # ------------------------------------------------------------------------------------ near
    def foreground_plant(self, x: float, y: float, flip: bool, key: int) -> None:
        """Big dark leaves of a potted plant in a bottom corner (near plane framing)."""
        ctx = self.ctx
        b = self.b
        P = "near"
        rng = self.rng("fgplant", key)
        g = [darken(b("foliage2"), 0.3), darken(b("foliage"), 0.25), darken(b("foliage3"), 0.2)]
        groups: list[Cmds] = [[], [], []]
        veins: Cmds = []
        for k in range(8):  # leaves fan up and into the frame from the corner
            ang = math.radians(-85 + k * 10) if flip else math.radians(-95 - k * 10)
            ln = float(rng.uniform(300, 430))
            wd = ln * float(rng.uniform(0.16, 0.22))
            groups[k % 3] += _leaf(x, y, ang, ln, wd)
            veins += [
                ("M", x, y),
                ("L", x + math.cos(ang) * ln * 0.9, y + math.sin(ang) * ln * 0.9),
            ]
        sway = Anim("sway", amp=1.2, freq=0.2, phase=float(key), pivot=(x, y))
        for i in range(3):
            ctx.path(
                P,
                groups[i],
                g[i],
                tag="leaf",
                lod=2,
                elev=12,
                material="grass",
                anim=sway if i else None,
            )
        ctx.path(
            P,
            veins,
            None,
            stroke=lighten(g[2], 0.15),
            sw=3.0,
            tag="leaf_vein",
            lod=2,
            shadow=False,
            alpha=0.5,
            anim=sway,
        )

    def near_plane(self) -> None:
        d = self.ctx.density
        if d >= 0.3:
            self.foreground_plant(1110.0, 2060.0, False, 0)
        if d >= 0.5:
            self.foreground_plant(-40.0, 2060.0, True, 1)

    def particles(self) -> None:
        ctx = self.ctx
        d = ctx.density
        if self.night or self.tod == "dusk":
            ctx.emit("near", Particles("sparkle", (X0, 300.0, X1, 1500.0), int(14 + 20 * d), speed=3.0, size=7.0, color=self.b("window"), alpha=0.8, wind=0.4, seed=ctx.seed + 3, material="emissive"))  # fmt: skip
        elif self.tod in ("day", "dawn"):
            ctx.emit("near", Particles("dust", (X0, 300.0, X1, 1500.0), int(16 + 20 * d), speed=0.8, size=5.0, color=self.b("glow"), alpha=0.6, wind=0.5, seed=ctx.seed + 3, material="emissive"))  # fmt: skip
        if self.mode == "sparse":  # chimney smoke
            for i, (cx, cy) in enumerate(self.smokers[:3]):
                ctx.emit("back", Particles("bubbles", (cx - 14, cy - 240, cx + 14, cy), 5, speed=0.35, size=24.0, color=mix(self.b("stone"), self.haze_c, 0.5), alpha=0.35, wind=0.8, seed=ctx.seed + 9 + i))  # fmt: skip

    # -------------------------------------------------------------------------------- build
    def build(self) -> None:
        self.sky()
        self.city_glow()
        self.far_layers()
        self.mid_layers()
        self.near_layers()
        self.parapet()
        self.railing()
        self.floor()
        self.props()
        self.string_lights()
        self.near_plane()
        self.particles()
        _ = (Y0, Y1)


@register_background(
    "rooftop",
    params_schema=RooftopParams,
    summary="City rooftop with a layered skyline (dense towers, low-rise town or harbor), parapet, water tower, "
    "string lights and rooftop clutter; best at dusk or night",
    slots={
        "hut_door": (0.09, 0.755),
        "parapet": (0.34, 0.74),
        "cat": (0.60, 0.745),
        "lounge": (0.74, 0.76),
        "rug": (0.40, 0.84),
    },
    tags=("outdoor", "city"),
    ground_y=0.74,
    perspective=1.0,
    horizon=0.60,
)
def rooftop(ctx: BuildContext, p: RooftopParams) -> None:
    _Roof(ctx, p).build()
