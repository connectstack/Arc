"""street: a charming city street - layered skyline, shopfronts, lamp posts, sidewalk and road."""

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
HORIZON = 1190.0  # where the far skyline rows end (hidden behind the nearer buildings)
BASE_Y = 1215.0  # buildings meet the sidewalk
CURB_Y = 1500.0  # sidewalk front edge
ROAD_Y = 1552.0  # road surface starts below the curb face
K = 0.5523  # bezier circle constant
Cmds = list[tuple[Any, ...]]


class StreetParams(BgParams):
    weather: Literal["clear", "rain", "snow", "fog"] = Field(
        "clear",
        description="clear = open sky; rain = overcast, wet ground and falling rain; "
        "snow = white ground and falling snow; fog = thick mist that fades the distance",
    )
    district: Literal["downtown", "suburb", "market"] = Field(
        "downtown",
        description="downtown = tall shopfront buildings, cars and a traffic light; "
        "suburb = houses with lawns, hedges and picket fences; "
        "market = old-town square with colourful stalls, bunting and cobblestones",
    )
    shops: bool = Field(
        True,
        description="true = ground floors are shopfronts with awnings and signs (a corner store in "
        "the suburb); false = plain residential doors, stoops and porches",
    )


# ------------------------------------------------------------------------------------ path helpers
def _rect(x: float, y: float, w: float, h: float) -> Cmds:
    return [("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)]


def _arch(x: float, y: float, w: float, h: float) -> Cmds:
    """Window/door with a semicircular head: bbox top-left (x, y), width w, height h."""
    r = w / 2
    k = K * r
    return [
        ("M", x, y + h),
        ("L", x, y + r),
        ("C", x, y + r - k, x + r - k, y, x + r, y),
        ("C", x + r + k, y, x + w, y + r - k, x + w, y + r),
        ("L", x + w, y + h),
        ("Z",),
    ]


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


def _calm(x: float) -> float:
    """1 in the zone behind the characters (x ~ 260..820), fading to 0 toward the frame edges."""
    d = max(0.0, 260.0 - x, x - 820.0)
    return clamp(1.0 - d / 170.0, 0.0, 1.0)


class _Street:
    def __init__(self, ctx: BuildContext, p: StreetParams) -> None:
        self.ctx = ctx
        self.p = p
        self.tod = ctx.time_of_day
        self.night = self.tod == "night"
        self.snow = p.weather == "snow"
        self.rain = p.weather == "rain"
        self.fog = p.weather == "fog"
        gloomy = p.weather != "clear"
        self.lamps = ctx.graph.lamps_on or gloomy  # lamps and shop lights come on in bad weather
        ctx.graph.lamps_on = self.lamps
        self.lit_p = {"day": 0.0, "dawn": 0.30, "dusk": 0.52, "night": 0.58}[self.tod]
        if gloomy and self.tod == "day":
            self.lit_p = 0.24
        self.shops = p.shops
        self.dist = p.district
        self.haze_c = "#000000"  # set once the scheme is final (see `weather_scheme`)

    # ------------------------------------------------------------------------------ small helpers
    def b(self, c: str) -> str:
        return str(self.ctx.scheme.base(c))

    def rng(self, *key: Any) -> np.random.Generator:
        """Structure never depends on time of day, weather or density: same seed, same street."""
        return derive_rng(self.ctx.seed, "street", self.dist, *key)

    @property
    def sun_dir(self) -> float:
        """+1 when light comes from the left (shadows fall right), -1 from the right."""
        return -1.0 if self.ctx.scheme.light_dir[0] > 0 else 1.0

    def snowy(self, c: str, k: float = 0.7) -> str:
        return mix(c, "#f4f7fb", k) if self.snow else c

    # ---------------------------------------------------------------------------------- scheme
    def weather_scheme(self) -> None:
        w = self.p.weather
        sc = self.ctx.scheme
        if w != "clear":
            top, bot = sc.base("sky_top"), sc.base("sky_bottom")
            if w == "rain":
                top = adjust(top, sat=0.42, light=0.78)
                bot = adjust(bot, sat=0.36, light=0.88)
                amb, amt, ex = mix(sc.ambient, "#7f8fa3", 0.5), 0.16, 0.92
            elif w == "snow":
                top = mix(adjust(top, sat=0.35, light=1.08), "#dfe7f0", 0.35)
                bot = mix(adjust(bot, sat=0.3, light=1.03), "#f0f4f8", 0.35)
                amb, amt, ex = mix(sc.ambient, "#cfe0f5", 0.5), 0.12, 0.99
            else:  # fog
                top = mix(adjust(top, sat=0.3, light=1.0), bot, 0.55)
                bot = mix(adjust(bot, sat=0.3), "#6d82b0" if self.night else "#eef2f5", 0.3)
                amb, amt, ex = mix(sc.ambient, bot, 0.55), 0.16, 0.97
            self.ctx.graph.scheme = replace(
                sc.with_roles(sky_top=top, sky_bottom=bot),
                ambient=amb,
                ambient_amt=min(0.85, sc.ambient_amt + amt),
                exposure=sc.exposure * ex,
            )
        self.haze_c = mix(self.b("sky_top"), self.b("sky_bottom"), 0.5)

    # -------------------------------------------------------------------------------------- sky
    def sky(self) -> None:
        ctx = self.ctx
        if self.p.weather == "clear":
            d0 = ctx.density
            ctx.density = min(d0, 0.4)  # city skies: fewer stars and clouds
            ctx.sky(HORIZON, sun=False)
            ctx.density = d0
        else:
            self.overcast()
        self.celestial()
        if self.p.weather == "clear" and not self.night:
            self.birds()

    def overcast(self) -> None:
        """Gradient sky plus layered, bumpy-bellied cloud decks (rain / snow / fog)."""
        ctx = self.ctx
        ctx.rect(
            "sky", X0, Y0, X1 - X0, HORIZON - Y0 + 4, "sky_top", material="emissive",
            shadow=False, tag="sky", gradient=("sky_top", "sky_bottom", 90.0),
        )  # fmt: skip
        rng = self.rng("overcast")
        base = self.haze_c
        specs = {
            "rain": (
                (560.0, darken(base, 0.04), 0.55),
                (400.0, darken(base, 0.12), 0.7),
                (240.0, darken(base, 0.22), 0.9),
            ),
            "snow": (
                (540.0, lighten(base, 0.10), 0.55),
                (380.0, lighten(base, 0.04), 0.65),
                (230.0, darken(base, 0.03), 0.8),
            ),
            "fog": ((520.0, lighten(base, 0.12), 0.35), (330.0, lighten(base, 0.06), 0.4)),
        }[self.p.weather]
        for k, (yb, col, alpha) in enumerate(specs):
            cmds: Cmds = [
                ("M", X0 - 700.0, Y0 - 200.0),
                ("L", X1 + 700.0, Y0 - 200.0),
                ("L", X1 + 700.0, yb),
            ]
            x = X1 + 700.0
            while x > X0 - 700.0:
                wb = float(rng.uniform(150, 320))
                dp = wb * float(rng.uniform(0.28, 0.42))
                cmds.append(("C", x, yb + dp * 1.33, x - wb, yb + dp * 1.33, x - wb, yb))
                x -= wb
                yb += float(rng.uniform(-14, 14))
            cmds.append(("Z",))
            ctx.path(
                "sky", cmds, col, material="emissive", alpha=alpha, shadow=False, tag="cloud", lod=1,
                anim=Anim("drift", amp=1.0, freq=0.015 + 0.006 * k, axis=0.0),
            )  # fmt: skip

    def celestial(self) -> None:
        ctx = self.ctx
        w = self.p.weather
        if w == "rain":
            return
        x, y, r = {
            "dawn": (820.0, 520.0, 86.0),
            "day": (820.0, 300.0, 80.0),
            "dusk": (830.0, 480.0, 100.0),
            "night": (810.0, 340.0, 66.0),
        }[self.tod]
        col = "#f4f6ff" if self.night else self.b("glow")
        faint = 0.35 if w in ("snow", "fog") else 1.0
        for k, a in ((3.2, 0.035), (2.8, 0.045), (2.4, 0.055), (2.0, 0.07), (1.65, 0.09)):
            ctx.ellipse(
                "sky", x, y, r * k, r * k, col, material="emissive", alpha=a * faint,
                shadow=False, tag="sun_halo", lod=1,
            )  # fmt: skip
        ctx.ellipse(
            "sky", x, y, r, r, col, material="emissive", alpha=faint,
            glow=r * 1.6 if faint == 1.0 else 0.0, shadow=False,
            tag="moon" if self.night else "sun",
        )  # fmt: skip
        if self.night and faint == 1.0:
            for dx, dy, rr in ((-16, -10, 13), (14, 16, 9), (18, -20, 6)):
                ctx.ellipse(
                    "sky", x + dx, y + dy, rr, rr, mix(col, self.b("sky_bottom"), 0.22),
                    material="emissive", alpha=0.5, shadow=False, tag="moon_crater", lod=2,
                )  # fmt: skip

    def birds(self) -> None:
        ctx = self.ctx
        rng = self.rng("birds")
        col = mix(self.b("sky_top"), "#1c2233", 0.55)
        for i in range(3):
            x = float(rng.uniform(80, 700))
            y = float(rng.uniform(420, 620))
            s = float(rng.uniform(0.8, 1.3))
            ctx.path(
                "sky",
                [("M", x - 16 * s, y), ("Q", x - 8 * s, y - 12 * s, x, y), ("Q", x + 8 * s, y - 12 * s, x + 16 * s, y)],
                None, stroke=col, sw=3.2, shadow=False, tag="bird", lod=2, alpha=0.8,
                anim=Anim("drift", amp=1.0, freq=0.10 + 0.03 * i, axis=-8.0, phase=float(i * 90)),
            )  # fmt: skip

    # ----------------------------------------------------------------------- distant layers
    def skyline(
        self, plane: str, key: str, *, hmin: float, hmax: float, wmin: float, wmax: float,
        haze: float, lod: int, style: str = "towers", base: float = HORIZON + 50.0,
        lit_scale: float = 1.0, spires: float = 0.2, valley_x: float = 800.0, wscale: float = 1.0,
    ) -> None:  # fmt: skip
        """A row of distant buildings (flat towers, or old-town roofs) fading into the haze."""
        ctx = self.ctx
        rng = self.rng(key)
        if self.fog:
            haze = haze + (1.0 - haze) * 0.78
        b = self.b
        if style == "oldtown":
            pal = [b("brick"), b("wall2"), b("accent3"), b("accent"), b("wall"), b("stone")]
        else:
            pal = [b("stone"), b("stone2"), b("fabric"), b("wall2"), b("brick"), b("accent2")]
        haze_c = self.haze_c
        win_dark = mix(haze_c, "#000000", 0.18)
        roof_c = mix(mix(b("brick"), b("accent"), 0.3), haze_c, haze)
        x = X0 - 90
        while x < X1 + 90:
            w = float(rng.uniform(wmin, wmax))
            t = clamp(abs((x + w / 2) - valley_x) / 760.0, 0.0, 1.0)
            h = lerp(hmin, hmax, 0.12 + 0.88 * t**0.9) * float(rng.uniform(0.86, 1.14))
            top = base - h
            c0 = pal[int(rng.integers(len(pal)))]
            col = adjust(mix(c0, haze_c, haze), light=float(rng.uniform(0.95, 1.05)))
            kind = float(rng.random())
            ctx.rect(
                plane, x, top, w, h + 80, col, tag="building", lod=lod, elev=3, material="paper"
            )
            if style == "oldtown":
                rh = w * float(rng.uniform(0.22, 0.42))
                rc = adjust(roof_c, light=float(rng.uniform(0.95, 1.06)))
                if kind < 0.5:
                    pts = [(x - 5, top + 3), (x + w / 2, top - rh), (x + w + 5, top + 3)]
                else:
                    pts = [
                        (x - 5, top + 3),
                        (x + w * 0.2, top - rh),
                        (x + w * 0.8, top - rh),
                        (x + w + 5, top + 3),
                    ]
                ctx.poly(plane, pts, rc, tag="roof", lod=lod, elev=3, smooth=0.04)
                if kind > 0.86 and w > 60:  # bell tower with a spire
                    tw = w * 0.5
                    tx = x + w * 0.25
                    ctx.rect(
                        plane,
                        tx,
                        top - h * 0.5,
                        tw,
                        h * 0.5 + 4,
                        adjust(col, light=1.03),
                        tag="tower",
                        lod=lod,
                        elev=3,
                    )
                    ctx.poly(
                        plane,
                        [
                            (tx - 4, top - h * 0.5 + 2),
                            (tx + tw / 2, top - h * 0.5 - tw * 1.5),
                            (tx + tw + 4, top - h * 0.5 + 2),
                        ],
                        rc,
                        tag="roof",
                        lod=lod,
                        elev=3,
                    )
            elif kind < 0.18 and w > 70:  # setback crown
                cw = w * float(rng.uniform(0.5, 0.7))
                ch = float(rng.uniform(40, 90))
                ctx.rect(
                    plane,
                    x + (w - cw) / 2,
                    top - ch,
                    cw,
                    ch + 2,
                    adjust(col, light=1.03),
                    tag="building",
                    lod=lod,
                    elev=3,
                    material="paper",
                )
            elif kind < 0.18 + spires:
                ax = x + w * float(rng.uniform(0.3, 0.7))
                ah = float(rng.uniform(60, 130))
                ctx.line(plane, (ax, top), (ax, top - ah), 4.0, col, tag="antenna", lod=lod)
                if self.lit_p > 0 and plane == "far":
                    ctx.ellipse(plane, ax, top - ah, 4.5, 4.5, "#ff5a4d", material="emissive", glow=18, shadow=False, tag="beacon", lod=2, anim=Anim("twinkle", amp=1.0, freq=0.45, phase=float(rng.uniform(0, 6))))  # fmt: skip
            elif kind < 0.40:  # slanted top
                ctx.poly(plane, [(x, top + 2), (x + w * 0.2, top - 34), (x + w * 0.8, top - 34), (x + w, top + 2)], adjust(col, light=1.02), tag="building", lod=lod, elev=3, smooth=0.05)  # fmt: skip
            # windows: faint grid + lit ones
            pitch_x, pitch_y = (22.0, 30.0) if style == "towers" else (26.0, 36.0)
            pitch_x, pitch_y = pitch_x * wscale, pitch_y * wscale
            ww_, wh_ = 11.0 * wscale, 16.0 * wscale
            cols_n = int((w - 16) / pitch_x)
            rows_n = int((h - 30) / pitch_y)
            if cols_n > 0 and rows_n > 0:
                grid: Cmds = []
                lit: Cmds = []
                ox = x + (w - cols_n * pitch_x) / 2 + 3
                for r_ in range(rows_n):
                    for c_ in range(cols_n):
                        wx, wy = ox + c_ * pitch_x, top + 22 + r_ * pitch_y
                        grid += _rect(wx, wy, ww_, wh_)
                        if self.lit_p > 0 and rng.random() < self.lit_p * 0.55 * lit_scale:
                            lit += _rect(wx, wy, ww_, wh_)
                ctx.path(plane, grid, win_dark, alpha=0.20, tag="window", lod=2, shadow=False)
                if lit:
                    wc = mix(b("window"), haze_c, haze * 0.55)
                    ctx.path(
                        plane,
                        lit,
                        wc,
                        material="emissive",
                        alpha=0.8,
                        tag="window_lit",
                        lod=2,
                        shadow=False,
                    )
            x += w - 2

    def hills(
        self, plane: str, key: str, base: float, amp: float, haze: float, trees: bool, lod: int = 2
    ) -> None:
        """Rolling hills with a tree-line (suburb background)."""
        ctx = self.ctx
        rng = self.rng(key)
        if self.fog:
            haze = haze + (1.0 - haze) * 0.75
        col = mix(mix(self.b("foliage2"), self.b("stone"), 0.3), self.haze_c, haze)
        pts = hill_points(rng, X0, X1, base, amp, step=130.0, bottom=base + 600)
        ctx.poly(plane, pts, col, smooth=0.4, tag="hill", lod=lod, elev=3, material="grass")
        if trees:
            tcol = mix(mix(self.b("foliage"), self.b("foliage2"), 0.5), self.haze_c, haze + 0.04)
            tr: Cmds = []
            x = X0 - 40
            while x < X1 + 40:
                r = float(rng.uniform(26, 58))
                u = (x - X0) / (X1 - X0)
                y = (
                    base
                    - amp
                    * (0.6 * math.sin(x * 0.0045 + 1.0) + 0.4 * math.sin(x * 0.011 + 1.7))
                    * 0.0
                    - 6
                )
                tr += _circ(x, y - r * 0.3, r)
                x += r * float(rng.uniform(1.0, 1.6))
                _ = u
            ctx.path(
                plane, tr, tcol, tag="treeline", lod=lod, elev=3, shadow=True, material="grass"
            )

    def trees_back(self) -> None:
        """Suburb: a hazy row of round trees and a few small houses behind the front gardens."""
        ctx = self.ctx
        rng = self.rng("trees_back")
        b = self.b
        haze = 0.84 if self.fog else 0.34
        dark = mix(b("foliage2"), self.haze_c, haze)
        mid = mix(b("foliage"), self.haze_c, haze)
        trunk = mix(b("trunk"), self.haze_c, haze)
        # a few small houses first (behind the trees)
        for x, w, k in ((470.0, 190.0, 0), (-120.0, 200.0, 1), (1180.0, 210.0, 2)):
            wall = mix(self.facade_colors()[k % 5], self.haze_c, haze)
            roof = mix(b("brick2"), self.haze_c, haze + 0.05)
            top = 1214.0 - 130.0
            ctx.rect("back", x, top, w, 134, wall, tag="building", lod=1, elev=4, material="paper")
            ctx.poly(
                "back",
                [
                    (x - 14, top + 4),
                    (x + w * 0.2, top - 62),
                    (x + w * 0.8, top - 62),
                    (x + w + 14, top + 4),
                ],
                roof,
                tag="roof",
                lod=1,
                elev=4,
                smooth=0.04,
            )
            wins: Cmds = []
            for j in range(3):
                wins += _rect(x + 26 + j * (w - 52) / 3 + 8, top + 28, 24, 36)
            ctx.path(
                "back",
                wins,
                mix(b("glass"), self.haze_c, haze * 0.7),
                tag="window",
                lod=2,
                shadow=False,
                alpha=0.9,
            )
        t1: Cmds = []
        t2: Cmds = []
        tr: Cmds = []
        x = X0 - 40
        while x < X1 + 60:
            r = float(rng.uniform(62, 104))
            ty = 1200.0 - r * 1.35
            tr += _rect(x - 8, ty, 16, 1214.0 - ty)
            t1 += _circ(x, ty - r * 0.15, r)
            t2 += _circ(x - r * 0.25, ty - r * 0.45, r * 0.62)
            x += r * float(rng.uniform(1.35, 2.0))
        ctx.path("back", tr, trunk, tag="trunk", lod=1, elev=3)
        ctx.path("back", t1, dark, tag="canopy", lod=1, elev=5, material="grass")
        ctx.path("back", t2, mid, tag="canopy", lod=2, elev=6, material="grass", shadow=False)

    def back_row(self) -> None:
        d = self.dist
        if d == "downtown":
            self.skyline(
                "back",
                "b1",
                hmin=470,
                hmax=820,
                wmin=90,
                wmax=190,
                haze=0.34,
                lod=1,
                lit_scale=1.5,
                spires=0.06,
                wscale=1.35,
            )
        elif d == "market":
            self.skyline(
                "back",
                "b1",
                hmin=330,
                hmax=640,
                wmin=110,
                wmax=190,
                haze=0.34,
                lod=1,
                style="oldtown",
                wscale=1.3,
            )
        else:
            self.trees_back()

    # -------------------------------------------------------------------------- mid building row
    def facade_colors(self) -> list[str]:
        b = self.b
        if self.dist == "market":
            return [
                mix(b("brick"), b("wall2"), 0.28),
                mix(b("accent3"), b("wall"), 0.5),
                mix(b("accent"), b("wall"), 0.62),
                mix(b("accent2"), b("wall"), 0.55),
                mix(b("wall"), b("wall2"), 0.2),
                mix(b("fabric"), b("wall"), 0.5),
                mix(b("brick2"), b("wall2"), 0.4),
            ]
        if self.dist == "suburb":
            return [
                mix(b("accent3"), b("wall"), 0.62),
                mix(b("accent2"), b("wall"), 0.62),
                mix(b("fabric"), b("wall"), 0.58),
                mix(b("wall"), b("wall2"), 0.2),
                mix(b("accent"), b("wall"), 0.72),
            ]
        return [
            mix(b("wall"), b("wall2"), 0.15),
            mix(b("brick"), b("wall2"), 0.22),
            mix(b("stone"), b("fabric2"), 0.45),
            mix(b("accent2"), b("wall"), 0.6),
            mix(b("accent3"), b("wall"), 0.62),
            mix(b("fabric"), b("wall"), 0.5),
            mix(b("accent"), b("wall"), 0.68),
        ]

    def plan_mid(self) -> list[dict[str, Any]]:
        rng = self.rng("plan")
        pal = self.facade_colors()
        order = [int(i) for i in rng.permutation(len(pal))]
        out: list[dict[str, Any]] = []
        if self.dist == "suburb":
            spans = [
                (-760.0, 320.0),
                (-340.0, 330.0),
                (80.0, 330.0),
                (660.0, 340.0),
                (1070.0, 330.0),
                (1470.0, 320.0),
            ]
            kinds = ["hip", "gable", "hip", "gable", "hip", "gable"]
            floors = [2, 1, 2, 2, 1, 2]
            for i, (x0, w) in enumerate(spans):
                out.append(
                    {
                        "x": x0,
                        "w": w,
                        "floors": floors[i],
                        "wall": pal[order[i % len(order)]],
                        "accent": i,
                        "roof": kinds[i],
                    }
                )
            return out
        if self.dist == "market":
            cuts = [-760.0, -450.0, -170.0, 60.0, 410.0, 660.0, 1010.0, 1330.0, 1680.0]
            floors = [3, 4, 3, 3, 2, 2, 3, 3]
            roofs = ["hip", "gable", "flat", "hip", "gable", "hip", "flat", "gable"]
        else:
            cuts = [-760.0, -470.0, -190.0, 60.0, 410.0, 660.0, 1010.0, 1330.0, 1680.0]
            floors = [5, 3, 6, 4, 2, 1, 5, 4]
            roofs = ["flat"] * 8
        for i in range(len(cuts) - 1):
            j = 0.0 if i in (3, 4, 5, 6) else 14.0
            x0 = cuts[i] + float(rng.uniform(-j, j))
            x1 = cuts[i + 1] + float(rng.uniform(-j, j))
            out.append({"x": x0, "w": x1 - x0, "floors": floors[i], "wall": pal[order[i % len(order)]], "accent": i, "roof": roofs[i]})  # fmt: skip
        return out

    def window_group(
        self, rng: Any, plane: str, x: float, w: float, y0: float, y1: float, wall: str, calm: float,
        style: str = "rect", shutter: str | None = None, boxes: bool = False, balcony: bool = False,
    ) -> None:  # fmt: skip
        """A grid of windows between y0 and y1 (frames, glass, lit panes, sills, mullions, ...)."""
        ctx = self.ctx
        b = self.b
        n = max(1, round((y1 - y0) / 150))
        fh = (y1 - y0) / n
        margin = 30.0
        cols = max(1, int((w - 2 * margin) / 100))
        pitch = (w - 2 * margin) / cols
        ww = min(pitch * 0.52, 74.0)
        wh = min(fh * 0.56, 98.0)
        trim = lighten(wall, 0.42)
        glass = mix(mix(b("glass"), b("fabric"), 0.38), wall, 0.45 * calm)
        frames: Cmds = []
        panes: Cmds = []
        lit_a: Cmds = []
        lit_b: Cmds = []
        sills: Cmds = []
        mull: Cmds = []
        shut: Cmds = []
        box: Cmds = []
        bloom_a: Cmds = []
        bloom_b: Cmds = []
        lit_p = self.lit_p * (1.0 - 0.6 * calm)
        for r in range(n):
            wy = y0 + r * fh + fh * 0.22
            for c in range(cols):
                wx = x + margin + c * pitch + (pitch - ww) / 2
                if style == "arch":
                    frames += _arch(wx - 5, wy - 5, ww + 10, wh + 10)
                    panes += _arch(wx, wy, ww, wh)
                else:
                    frames += _rect(wx - 5, wy - 5, ww + 10, wh + 10)
                    panes += _rect(wx, wy, ww, wh)
                sills += _rect(wx - 9, wy + wh + 4, ww + 18, 7)
                mull += [
                    ("M", wx + ww / 2, wy),
                    ("L", wx + ww / 2, wy + wh),
                    ("M", wx, wy + wh * 0.42),
                    ("L", wx + ww, wy + wh * 0.42),
                ]
                if shutter:
                    sw_ = ww * 0.40
                    shut += _rect(wx - sw_ - 8, wy - 4, sw_, wh + 8) + _rect(
                        wx + ww + 8, wy - 4, sw_, wh + 8
                    )
                if boxes and r == 0:
                    box += _rect(wx - 8, wy + wh + 12, ww + 16, 15)
                    for j in range(4):
                        (bloom_a if j % 2 == 0 else bloom_b).extend(
                            _circ(wx - 2 + (j + 0.5) * (ww + 4) / 4, wy + wh + 10, 7.5)
                        )
                if rng.random() < lit_p:
                    tgt = lit_a if rng.random() < 0.7 else lit_b
                    tgt += _arch(wx, wy, ww, wh) if style == "arch" else _rect(wx, wy, ww, wh)
        if shut:
            ctx.path(plane, shut, shutter, tag="shutter", lod=1, shadow=False, material="wood")
        ctx.path(plane, frames, trim, tag="window_frame", lod=0, shadow=False)
        ctx.path(plane, panes, glass, material="glass", tag="window", lod=1, shadow=False)
        a_lit = 1.0 - 0.4 * calm
        if lit_a:
            ctx.path(
                plane,
                lit_a,
                b("window"),
                material="emissive",
                alpha=a_lit,
                tag="window_lit",
                lod=1,
                shadow=False,
            )
        if lit_b:
            ctx.path(plane, lit_b, mix(b("window"), "#ffffff", 0.45), material="emissive", alpha=a_lit, tag="window_lit", lod=1, shadow=False)  # fmt: skip
        ctx.path(plane, sills, trim, tag="sill", lod=1, shadow=False)
        ctx.path(
            plane,
            mull,
            None,
            stroke=trim,
            sw=3.0,
            tag="window_bars",
            lod=2,
            shadow=False,
            alpha=0.85,
        )
        if box:
            ctx.path(
                plane,
                box,
                mix(b("wood"), wall, 0.2),
                tag="flower_box",
                lod=1,
                elev=2,
                material="wood",
            )
            ctx.path(
                plane,
                bloom_a,
                mix(b("accent"), wall, 0.1 * calm),
                tag="flower",
                lod=2,
                shadow=False,
            )
            ctx.path(
                plane,
                bloom_b,
                mix(b("accent3"), wall, 0.1 * calm),
                tag="flower",
                lod=2,
                shadow=False,
            )
        if balcony and n >= 1 and cols >= 2:
            by = y0 + fh * 0.22 + wh + 16
            bx0, bx1 = x + margin - 8, x + w - margin + 8
            ctx.rect(
                plane,
                bx0 - 6,
                by,
                bx1 - bx0 + 12,
                12,
                darken(wall, 0.3),
                tag="balcony",
                lod=1,
                elev=4,
            )
            bars: Cmds = []
            xx = bx0
            while xx <= bx1:
                bars += [("M", xx, by - 44), ("L", xx, by)]
                xx += 15
            bars += [("M", bx0, by - 44), ("L", bx1, by - 44)]
            ctx.path(
                plane,
                bars,
                None,
                stroke=darken(wall, 0.45),
                sw=3.0,
                tag="balcony_rail",
                lod=1,
                shadow=False,
            )
            pots: Cmds = []
            for k in range(cols):
                pots += _circ(bx0 + 30 + k * ((bx1 - bx0 - 60) / max(1, cols - 1)), by - 56, 15)
            ctx.path(plane, pots, b("foliage"), tag="plant", lod=2, shadow=False, material="grass")

    def roof(self, kind: str, x: float, w: float, top: float, wall: str, key: int) -> float:
        """Pitched roof over the eave line ``top``; returns the ridge height above ``top``."""
        ctx = self.ctx
        P = "mid_back"
        b = self.b
        rng = self.rng("roof", key)
        tile = mix(mix(b("brick"), b("accent"), 0.25 + 0.2 * float(rng.random())), b("brick2"), 0.2)
        if self.dist == "suburb":
            tile = (
                mix(mix(b("stone2"), b("fabric"), 0.45), "#1b2030", 0.2)
                if key % 2 == 0
                else mix(b("brick2"), b("brick"), 0.4)
            )
        if kind == "hip":
            rh = 96.0 + w * 0.13
            pts = [
                (x - 20, top + 8),
                (x + w * 0.13, top - rh),
                (x + w * 0.87, top - rh),
                (x + w + 20, top + 8),
            ]
            if key % 3 == 0:  # chimney
                cx = x + w * 0.70
                ctx.rect(
                    P,
                    cx,
                    top - rh - 34,
                    38,
                    rh + 40,
                    darken(b("brick"), 0.05),
                    tag="chimney",
                    elev=4,
                )
                ctx.rect(
                    P,
                    cx - 5,
                    top - rh - 44,
                    48,
                    14,
                    darken(b("brick"), 0.2),
                    tag="chimney",
                    lod=1,
                    elev=4,
                )
            ctx.poly(P, pts, tile, tag="roof", elev=7, smooth=0.03, material="paper")
            ctx.poly(P, [(x + w * 0.5, top - rh), (x + w * 0.87, top - rh), (x + w + 20, top + 8), (x + w * 0.5, top + 8)], darken(tile, 0.1), tag="roof_shade", lod=2, alpha=0.5, shadow=False)  # fmt: skip
            tl: Cmds = []
            for i in range(1, 5):
                yy = top - rh + rh * i / 5
                f = i / 5
                xl = lerp(x + w * 0.13, x - 20, f)
                xr = lerp(x + w * 0.87, x + w + 20, f)
                tl += [("M", xl, yy), ("L", xr, yy)]
            ctx.path(
                P,
                tl,
                None,
                stroke=darken(tile, 0.2),
                sw=3.0,
                tag="roof_tiles",
                lod=2,
                shadow=False,
                alpha=0.7,
            )
            ctx.rect(P, x - 20, top + 6, w + 40, 9, lighten(tile, 0.22), tag="eave", lod=1, elev=3)
            if self.snow:
                ctx.poly(P, [(x + w * 0.11, top - rh - 6), (x + w * 0.89, top - rh - 6), (x + w * 0.9, top - rh + 22), (x + w * 0.1, top - rh + 22)], "#ffffff", smooth=0.3, tag="snow", lod=1, shadow=False)  # fmt: skip
            return rh
        # front gable: wall triangle, fascia boards along the slopes, round attic window
        rh = w * 0.30 + 28.0
        ctx.poly(
            P,
            [(x, top + 2), (x + w / 2, top - rh), (x + w, top + 2)],
            lighten(wall, 0.05),
            tag="gable",
            elev=6,
        )
        ctx.path(P, [("M", x - 24, top + 14), ("L", x + w / 2, top - rh - 8), ("L", x + w + 24, top + 14)], None, stroke=tile, sw=26, tag="roof", elev=7, material="paper", cap="butt")  # fmt: skip
        ctx.path(P, [("M", x - 24, top + 14), ("L", x + w / 2, top - rh - 8), ("L", x + w + 24, top + 14)], None, stroke=lighten(tile, 0.18), sw=6, tag="eave", lod=2, shadow=False)  # fmt: skip
        ay = top - rh * 0.36
        ctx.ellipse(
            P, x + w / 2, ay, 26, 26, lighten(wall, 0.4), tag="window_frame", lod=1, shadow=False
        )
        ctx.ellipse(P, x + w / 2, ay, 20, 20, b("window") if self.lit_p > 0.2 else mix(b("glass"), b("fabric"), 0.4), material="emissive" if self.lit_p > 0.2 else "glass", tag="window", lod=1, shadow=False)  # fmt: skip
        if self.snow:
            ctx.path(P, [("M", x - 20, top + 6), ("L", x + w / 2, top - rh - 18), ("L", x + w + 20, top + 6)], None, stroke="#ffffff", sw=12, tag="snow", lod=1, shadow=False)  # fmt: skip
        return rh

    def facade(self, rng: Any, bd: dict[str, Any], idx: int) -> None:
        ctx = self.ctx
        P = "mid_back"
        x, w = bd["x"], bd["w"]
        wall = bd["wall"]
        if self.fog:
            wall = mix(wall, self.haze_c, 0.2)
            bd["wall"] = wall
        calm = _calm(x + w / 2)
        shop = self.shops
        gh = 300.0 if shop else 236.0
        y_g = BASE_Y - gh
        fh = 150.0 + float(rng.uniform(-6, 6))
        top = y_g - 50 - bd["floors"] * fh
        bd["top"] = top
        roof = bd.get("roof", "flat")
        market = self.dist == "market"
        if roof != "flat":
            self.roof(roof, x, w, top + 10, wall, idx)
        ctx.rect(
            P, x, top, w, BASE_Y - top, wall, tag="building", elev=8, material="paper",
            gradient=(lighten(wall, 0.05), darken(wall, 0.07), 90.0),
        )  # fmt: skip
        ctx.rect(
            P,
            x + w - 22,
            top,
            22,
            BASE_Y - top,
            darken(wall, 0.10),
            tag="building_shade",
            lod=2,
            alpha=0.5,
            shadow=False,
        )
        trim = lighten(wall, 0.42)
        if roof == "flat":
            ctx.rect(P, x - 8, top, w + 16, 22, trim, tag="cornice", lod=1, elev=4)
            ctx.rect(
                P,
                x - 3,
                top + 22,
                w + 6,
                12,
                darken(trim, 0.14),
                tag="cornice",
                lod=2,
                shadow=False,
            )
            ctx.rect(
                P,
                x,
                top + 34,
                w,
                10,
                self.b("shadow"),
                alpha=0.14,
                tag="shade",
                lod=2,
                shadow=False,
            )
            if self.snow:
                ctx.rect(
                    P, x - 8, top - 10, w + 16, 14, "#ffffff", r=7, tag="snow", lod=1, shadow=False
                )
            if self.dist == "downtown" and idx in (0, 2, 6):
                self.roof_gear(x, w, top, idx)
        courses = _rect(x, y_g - 9, w, 18)
        n = max(1, round((y_g - (top + 50)) / fh))
        for k in range(1, n):
            courses += _rect(x, top + 50 + k * fh - 4, w, 6)
        ctx.path(P, courses, trim, tag="string_course", lod=2, shadow=False, alpha=0.9)
        style = "arch" if (idx % 3 == 1 or market) else "rect"
        shutter = None
        if market:
            shutter = mix(
                self.b(["accent2", "fabric", "accent", "accent2"][idx % 4]), wall, 0.25 + 0.3 * calm
            )
        self.window_group(
            rng, P, x, w, top + 50, y_g - 14, wall, calm, style, shutter=shutter,
            boxes=market or (self.dist == "downtown" and idx in (3, 7)),
            balcony=self.dist == "downtown" and idx in (1, 4) and n >= 2,
        )  # fmt: skip
        if shop:
            (self.arcade if market else self.shopfront)(rng, bd, y_g, calm)
        else:
            self.stoop(rng, bd, y_g, calm)

    def arcade(self, rng: Any, bd: dict[str, Any], y_g: float, calm: float) -> None:
        """Old-town ground floor: a row of arches over warm shop interiors, with a hanging sign."""
        ctx = self.ctx
        P = "mid_back"
        b = self.b
        x, w = bd["x"], bd["w"]
        wall = bd["wall"]
        trim = lighten(wall, 0.5)
        pier = 30.0
        n = max(2, round((w - pier) / 150))
        ow = (w - (n + 1) * pier) / n
        door_x = (
            clamp(302.0, x + 90, x + w - 90)
            if bd["accent"] == 3
            else x + w * float(rng.uniform(0.35, 0.65))
        )
        bd["door_x"] = door_x
        ctx.rect(P, x, y_g - 4, w, 26, trim, tag="cornice", lod=1, elev=3)
        ctx.rect(P, x, BASE_Y - 30, w, 30, darken(wall, 0.22), tag="plinth", lod=1)
        top_o = y_g + 50
        frames: Cmds = []
        holes: Cmds = []
        doors = []
        for i in range(n):
            ox = x + pier + i * (ow + pier)
            frames += _arch(ox - 7, top_o - 7, ow + 14, BASE_Y - top_o + 7)
            holes += _arch(ox, top_o, ow, BASE_Y - top_o)
            if abs(ox + ow / 2 - door_x) < (ow + pier) / 2:
                doors.append((ox, ow))
        ctx.path(P, frames, trim, tag="arch", lod=0, elev=2, shadow=True)
        warm = b("window")
        if self.lamps:
            ctx.path(P, holes, warm, material="emissive", alpha=1.0 - 0.15 * calm, tag="shop_window", lod=1, shadow=False, gradient=(mix(warm, "#ffffff", 0.3), mix(warm, b("accent3"), 0.4), 90.0))  # fmt: skip
            ink = mix(b("accent3"), darken(b("accent"), 0.2), 0.5)
        else:
            dim = mix(darken(wall, 0.52), b("accent3"), 0.12)
            ctx.path(
                P,
                holes,
                dim,
                tag="shop_window",
                lod=1,
                shadow=False,
                gradient=(darken(dim, 0.1), lighten(dim, 0.1), 90.0),
            )
            ink = mix(b("accent3"), dim, 0.45)
        goods: Cmds = []
        for i in range(n):
            ox = x + pier + i * (ow + pier)
            px_ = ox + 10
            while px_ < ox + ow - 24:
                iw = float(rng.uniform(14, 26))
                ih = float(rng.uniform(18, 46))
                goods += _rect(px_, BASE_Y - 70 - ih, iw, ih)
                px_ += iw + float(rng.uniform(8, 18))
        ctx.path(P, goods, ink, material="emissive" if self.lamps else "flat", alpha=0.85 if self.lamps else 0.7, tag="shop_goods", lod=2, shadow=False)  # fmt: skip
        ctx.rect(
            P,
            x + pier,
            BASE_Y - 70,
            w - 2 * pier,
            8,
            darken(ink, 0.3),
            tag="shelf",
            lod=2,
            shadow=False,
            alpha=0.8,
        )
        for ox, ow_ in doors:  # the door leaf stands in its arch
            dw_ = min(ow_ * 0.72, 80.0)
            dx_ = ox + (ow_ - dw_) / 2
            dcol = mix(
                b(["accent", "accent2", "accent3", "fabric"][bd["accent"] % 4]),
                darken(wall, 0.35),
                0.5,
            )
            ctx.path(
                P, _arch(dx_, BASE_Y - 196, dw_, 196), dcol, tag="door", elev=2, material="wood"
            )
            ctx.path(P, _arch(dx_ + 12, BASE_Y - 180, dw_ - 24, 76), warm if self.lamps else mix(b("glass"), b("fabric"), 0.4), material="emissive" if self.lamps else "glass", tag="door_glass", lod=1, shadow=False)  # fmt: skip
            ctx.ellipse(
                P,
                dx_ + dw_ - 12,
                BASE_Y - 92,
                4.5,
                4.5,
                lighten(b("accent3"), 0.2),
                tag="door_handle",
                lod=2,
                shadow=False,
            )
        # hanging blade sign on the first pier
        sx = x + pier / 2 + 2
        acc = mix(b(["accent", "accent2", "accent3", "fabric"][bd["accent"] % 4]), wall, 0.2 * calm)
        sy = y_g + 70
        ctx.line(
            P,
            (sx, sy - 10),
            (sx + 44, sy - 10),
            4,
            darken(b("metal"), 0.4),
            tag="sign_bracket",
            lod=1,
            shadow=False,
        )
        sign_anim = Anim(
            "sway", amp=1.8, freq=0.22, phase=float(bd["accent"]), pivot=(sx + 22, sy - 10)
        )
        ctx.rect(
            P,
            sx + 2,
            sy - 8,
            40,
            54,
            darken(acc, 0.35),
            r=5,
            tag="sign",
            elev=4,
            material="wood",
            anim=sign_anim,
        )
        ctx.ellipse(P, sx + 22, sy + 14, 11, 11, warm if self.lamps else lighten(acc, 0.5), material="emissive" if self.lamps else "flat", tag="sign_icon", lod=1, shadow=False, anim=sign_anim)  # fmt: skip

    def roof_gear(self, x: float, w: float, top: float, key: int) -> None:
        """A few rooftop odds and ends: AC unit, antenna, water tank."""
        ctx = self.ctx
        P = "mid_back"
        b = self.b
        metal = mix(b("metal"), b("stone"), 0.3)
        if key == 0:  # AC unit + antenna
            ctx.rect(
                P,
                x + w * 0.55,
                top - 38,
                70,
                38,
                metal,
                r=4,
                tag="ac_unit",
                elev=3,
                material="metal",
            )
            ctx.ellipse(
                P,
                x + w * 0.55 + 35,
                top - 19,
                13,
                13,
                darken(metal, 0.3),
                tag="ac_unit",
                lod=2,
                shadow=False,
            )
            ctx.line(
                P,
                (x + w * 0.2, top),
                (x + w * 0.2, top - 90),
                4,
                darken(metal, 0.35),
                tag="antenna",
                lod=1,
            )
            ctx.line(
                P,
                (x + w * 0.2 - 22, top - 66),
                (x + w * 0.2 + 22, top - 66),
                3,
                darken(metal, 0.35),
                tag="antenna",
                lod=2,
            )
        elif key == 2:  # water tank on stilts
            tx = x + w * 0.62
            wood = mix(b("wood2"), b("wall2"), 0.15)
            for lx in (tx - 26, tx + 22):
                ctx.line(P, (lx, top), (lx, top - 44), 5, darken(metal, 0.4), tag="tank", lod=1)
            ctx.rect(
                P, tx - 34, top - 108, 68, 66, wood, r=6, tag="water_tank", elev=4, material="wood"
            )
            ctx.poly(
                P,
                [(tx - 40, top - 106), (tx + 40, top - 106), (tx, top - 142)],
                darken(wood, 0.2),
                tag="water_tank",
                elev=4,
            )
            ctx.path(P, [("M", tx - 34, top - 88), ("L", tx + 34, top - 88), ("M", tx - 34, top - 64), ("L", tx + 34, top - 64)], None, stroke=darken(wood, 0.35), sw=3.0, tag="water_tank", lod=2, shadow=False)  # fmt: skip
        else:  # satellite dishes
            ctx.ellipse(
                P,
                x + w * 0.3,
                top - 24,
                22,
                15,
                lighten(metal, 0.25),
                tag="dish",
                lod=1,
                elev=3,
                rot=-30,
            )
            ctx.line(
                P,
                (x + w * 0.3, top - 22),
                (x + w * 0.3 + 6, top),
                4,
                darken(metal, 0.3),
                tag="dish",
                lod=1,
            )

    def shopfront(self, rng: Any, bd: dict[str, Any], y_g: float, calm: float) -> None:
        ctx = self.ctx
        P = "mid_back"
        x, w = bd["x"], bd["w"]
        wall = bd["wall"]
        b = self.b
        acc = b(["accent", "accent2", "accent3", "fabric"][bd["accent"] % 4])
        acc = mix(acc, wall, 0.30 * calm)
        cream = lighten(wall, 0.55)
        door_w = 78.0
        door_x = x + w * float(rng.uniform(0.36, 0.64))
        if bd["accent"] == 3:
            door_x = clamp(302.0, x + 90, x + w - 90)
        bd["door_x"] = door_x
        ctx.rect(P, x, BASE_Y - 34, w, 34, darken(wall, 0.22), tag="plinth", lod=1)
        # fascia sign with abstract lettering and a badge
        fy, fhh = y_g + 8, 54.0
        board = mix(darken(acc, 0.55), wall, 0.1 * calm)
        ctx.rect(P, x + 10, fy, w - 20, fhh, board, r=6, tag="sign", elev=3, material="wood")
        bars: Cmds = []
        cx = x + 62
        lim = x + w - 36
        nb = 3 if w < 300 else 4
        raw = [float(rng.uniform(26, 64)) for _ in range(nb)]
        k_fit = min(1.0, (lim - cx - 14 * (nb - 1)) / sum(raw))
        for lw in raw:
            bars += _rect(cx, fy + 15, lw * k_fit, fhh - 30)
            cx += lw * k_fit + 14
        ink = lighten(acc, 0.55)
        if self.lamps:
            sc = mix(b("window"), ink, 0.4)
            a = 0.95 - 0.35 * calm
            ctx.path(
                P, bars, sc, material="emissive", alpha=a, tag="sign_text", lod=1, shadow=False
            )
            ctx.ellipse(
                P,
                x + 36,
                fy + fhh / 2,
                15,
                15,
                sc,
                material="emissive",
                alpha=a,
                tag="sign_icon",
                lod=1,
                shadow=False,
            )
        else:
            ctx.path(P, bars, ink, tag="sign_text", lod=1, shadow=False)
            ctx.ellipse(P, x + 36, fy + fhh / 2, 15, 15, ink, tag="sign_icon", lod=1, shadow=False)
        # awning with a scalloped, striped valance
        ay = fy + fhh + 6
        a_h, sc_h = 46.0, 18.0
        stripes = max(5, int((w - 28) / 36))
        sw_top = (w - 36) / stripes
        sw_bot = (w - 20) / stripes
        sa: Cmds = []
        sb: Cmds = []
        for i in range(stripes):
            xt0, xt1 = x + 18 + i * sw_top, x + 18 + (i + 1) * sw_top
            xb0, xb1 = x + 10 + i * sw_bot, x + 10 + (i + 1) * sw_bot
            yb = ay + a_h
            cmd: Cmds = [
                ("M", xt0, ay),
                ("L", xt1, ay),
                ("L", xb1, yb),
                ("C", xb1, yb + sc_h * 1.33, xb0, yb + sc_h * 1.33, xb0, yb),
                ("Z",),
            ]
            (sa if i % 2 == 0 else sb).extend(cmd)
        ctx.rect(
            P,
            x + 10,
            ay + a_h + 22,
            w - 20,
            26,
            b("shadow"),
            alpha=0.20,
            tag="shade",
            lod=2,
            shadow=False,
        )
        ctx.path(P, sa, mix(acc, "#ffffff", 0.05), material="fabric", tag="awning", elev=5, lod=0)
        ctx.path(P, sb, lighten(wall, 0.6) if bd["accent"] % 2 == 0 else lighten(acc, 0.55), material="fabric", tag="awning", elev=5, lod=0)  # fmt: skip
        if self.snow:
            ctx.rect(P, x + 16, ay - 8, w - 32, 12, "#ffffff", r=6, tag="snow", lod=1, shadow=False)
        # display window(s) and door
        wy0 = ay + a_h + sc_h + 8
        wy1 = BASE_Y - 38
        segs = []
        if door_x - door_w / 2 - 14 - (x + 16) > 100:
            segs.append((x + 16, door_x - door_w / 2 - 14))
        if (x + w - 16) - (door_x + door_w / 2 + 14) > 100:
            segs.append((door_x + door_w / 2 + 14, x + w - 16))
        for sx0, sx1 in segs:
            self.display(rng, P, sx0, wy0, sx1 - sx0, wy1 - wy0, wall, calm)
        dy0 = y_g + 96
        ctx.path(
            P,
            _arch(door_x - door_w / 2 - 6, dy0 - 6, door_w + 12, BASE_Y - dy0 + 6),
            cream,
            tag="door_frame",
            lod=1,
            shadow=False,
        )
        dcol = mix(acc, darken(wall, 0.35), 0.5)
        ctx.path(
            P,
            _arch(door_x - door_w / 2, dy0, door_w, BASE_Y - dy0),
            dcol,
            tag="door",
            elev=2,
            material="wood",
        )
        dglass = b("window") if self.lamps else mix(b("glass"), b("fabric"), 0.4)
        ctx.path(P, _arch(door_x - door_w / 2 + 14, dy0 + 14, door_w - 28, 76), dglass, material="emissive" if self.lamps else "glass", alpha=0.9 if self.lamps else 1.0, tag="door_glass", lod=1, shadow=False)  # fmt: skip
        ctx.ellipse(
            P,
            door_x + door_w / 2 - 14,
            BASE_Y - 92,
            4.5,
            4.5,
            lighten(b("accent3"), 0.2),
            tag="door_handle",
            lod=2,
            shadow=False,
        )

    def display(
        self, rng: Any, P: str, x: float, y: float, w: float, h: float, wall: str, calm: float
    ) -> None:
        ctx = self.ctx
        b = self.b
        ctx.rect(
            P,
            x - 6,
            y - 6,
            w + 12,
            h + 12,
            lighten(wall, 0.5),
            r=4,
            tag="shop_frame",
            lod=1,
            shadow=False,
        )
        if self.lamps:
            ctx.rect(P, x, y, w, h, b("window"), material="emissive", alpha=1.0 - 0.15 * calm, tag="shop_window", lod=1, shadow=False, gradient=(mix(b("window"), "#ffffff", 0.3), mix(b("window"), b("accent3"), 0.35), 90.0))  # fmt: skip
            item_col = mix(b("accent3"), darken(b("accent"), 0.2), 0.5)
        else:
            glass = mix(mix(b("glass"), b("fabric"), 0.3), wall, 0.35 * calm)
            ctx.rect(P, x, y, w, h, glass, material="glass", tag="shop_window", lod=1, shadow=False, gradient=(lighten(glass, 0.18), darken(glass, 0.08), 90.0))  # fmt: skip
            item_col = mix(darken(b("accent"), 0.15), glass, 0.35)
        if self.lamps and self.tod != "day" and w > 130:  # neon "open" sign
            nc = (
                mix(b("accent"), "#ff4f9a", 0.55)
                if int(x) % 2 == 0
                else mix(b("accent2"), "#3de0ff", 0.5)
            )
            nw, nh = min(70.0, w * 0.5), 30.0
            ctx.rect(P, x + w / 2 - nw / 2, y + 12, nw, nh, None, r=14, stroke=nc, sw=4.5, material="emissive", glow=26, shadow=False, tag="neon", lod=1)  # fmt: skip
            ctx.path(
                P,
                _rect(x + w / 2 - nw * 0.3, y + 12 + nh / 2 - 2, nw * 0.6, 4),
                nc,
                material="emissive",
                shadow=False,
                tag="neon",
                lod=2,
            )
        shelf_y = y + h * 0.62
        items: Cmds = []
        px = x + 16
        while px < x + w - 24:
            iw = float(rng.uniform(14, 26))
            ih = float(rng.uniform(16, 40))
            items += _rect(px, shelf_y - ih, iw, ih)
            px += iw + float(rng.uniform(8, 20))
        ctx.path(P, items, item_col, material="emissive" if self.lamps else "flat", alpha=0.85 if self.lamps else 0.7, tag="shop_goods", lod=2, shadow=False)  # fmt: skip
        ctx.rect(
            P,
            x,
            shelf_y,
            w,
            6,
            mix(item_col, "#000000", 0.25),
            tag="shelf",
            lod=2,
            shadow=False,
            alpha=0.8,
        )
        if not self.lamps:  # sky glint
            ctx.path(P, _quad((x + w * 0.12, y + h), (x + w * 0.32, y + h), (x + w * 0.52, y), (x + w * 0.32, y)), "#ffffff", alpha=0.20, material="glass", tag="glint", lod=2, shadow=False)  # fmt: skip
        else:  # warm spill on the pavement
            ctx.poly(P, [(x, y + h), (x + w, y + h), (x + w + 60, y + h + 130), (x - 60, y + h + 130)], b("window"), material="emissive", alpha=0.10, tag="light_spill", lod=2, shadow=False)  # fmt: skip

    def stoop(self, rng: Any, bd: dict[str, Any], y_g: float, calm: float) -> None:
        ctx = self.ctx
        P = "mid_back"
        x, w = bd["x"], bd["w"]
        wall = bd["wall"]
        b = self.b
        door_w = 80.0
        door_x = x + w * float(rng.uniform(0.35, 0.65))
        if bd["accent"] == 3:
            door_x = clamp(302.0, x + 90, x + w - 90)
        bd["door_x"] = door_x
        ctx.rect(P, x, BASE_Y - 40, w, 40, darken(wall, 0.2), tag="plinth", lod=1)
        dcol = mix(
            b(["accent", "accent2", "fabric", "accent3"][bd["accent"] % 4]), darken(wall, 0.3), 0.35
        )
        ctx.path(
            P,
            _arch(door_x - door_w / 2 - 8, y_g + 30, door_w + 16, BASE_Y - y_g - 50),
            lighten(wall, 0.5),
            tag="door_frame",
            lod=1,
            shadow=False,
        )
        ctx.path(
            P,
            _arch(door_x - door_w / 2, y_g + 38, door_w, BASE_Y - y_g - 58),
            dcol,
            tag="door",
            elev=2,
            material="wood",
        )
        ctx.rect(
            P,
            door_x - door_w / 2 - 26,
            BASE_Y - 20,
            door_w + 52,
            20,
            lighten(b("stone"), 0.2),
            tag="step",
            lod=1,
            elev=2,
        )
        ctx.rect(
            P,
            door_x - door_w / 2 - 14,
            BASE_Y - 38,
            door_w + 28,
            18,
            lighten(b("stone"), 0.28),
            tag="step",
            lod=1,
            elev=2,
        )
        lx = door_x + door_w / 2 + 34
        ctx.rect(P, lx - 6, y_g + 50, 12, 22, darken(b("metal"), 0.4), tag="wall_lamp", lod=2)
        lc = b("window") if self.lamps else lighten(b("glass"), 0.3)
        ctx.ellipse(P, lx, y_g + 80, 10, 13, lc, material="emissive" if self.lamps else "glass", glow=34 if self.lamps else 0.0, shadow=False, tag="wall_lamp", lod=1)  # fmt: skip
        for sx0, sx1 in (
            (x + 24, door_x - door_w / 2 - 34),
            (door_x + door_w / 2 + 60, x + w - 24),
        ):
            if sx1 - sx0 > 70:
                gw = min(sx1 - sx0, 80.0)
                gx = (sx0 + sx1) / 2 - gw / 2
                ctx.rect(
                    P,
                    gx - 5,
                    y_g + 44,
                    gw + 10,
                    110,
                    lighten(wall, 0.42),
                    tag="window_frame",
                    lod=1,
                    shadow=False,
                )
                lit = self.lamps and rng.random() < 0.7
                col = b("window") if lit else mix(b("glass"), b("fabric"), 0.4)
                ctx.rect(P, gx, y_g + 49, gw, 100, col, material="emissive" if lit else "glass", alpha=1.0 - 0.3 * calm, tag="window", lod=1, shadow=False)  # fmt: skip

    # ------------------------------------------------------------------------------- suburban house
    def house(self, rng: Any, bd: dict[str, Any], idx: int) -> None:
        """A pastel family home: hip or gabled roof, shuttered windows, porch and front door."""
        ctx = self.ctx
        P = "mid_back"
        b = self.b
        x, w = bd["x"], bd["w"]
        wall = bd["wall"]
        if self.fog:
            wall = mix(wall, self.haze_c, 0.2)
        two = bd["floors"] == 2
        bh = 404.0 if two else 262.0
        top = BASE_Y - bh
        bd["top"] = top
        calm = _calm(x + w / 2)
        trim = lighten(wall, 0.5)
        door_x = x + w * (0.62 if idx % 2 == 0 else 0.38)
        if idx == 2:
            door_x = 302.0
        bd["door_x"] = door_x
        self.roof(bd["roof"], x, w, top + 4, wall, idx)
        ctx.rect(P, x, top, w, bh, wall, tag="building", elev=8, material="paper", gradient=(lighten(wall, 0.05), darken(wall, 0.07), 90.0))  # fmt: skip
        ctx.rect(
            P,
            x + w - 22,
            top,
            22,
            bh,
            darken(wall, 0.10),
            tag="building_shade",
            lod=2,
            alpha=0.5,
            shadow=False,
        )
        # clapboard lines
        lines: Cmds = []
        yy = top + 24
        while yy < BASE_Y - 30:
            lines += [("M", x, yy), ("L", x + w, yy)]
            yy += 26
        ctx.path(
            P,
            lines,
            None,
            stroke=darken(wall, 0.10),
            sw=2.0,
            tag="siding",
            lod=2,
            shadow=False,
            alpha=0.55,
        )
        ctx.rect(
            P,
            x - 4,
            BASE_Y - 26,
            w + 8,
            26,
            darken(b("stone"), 0.05),
            tag="foundation",
            lod=1,
            elev=2,
        )
        shutter = mix(
            b(["accent2", "fabric", "accent", "accent2"][idx % 4]), wall, 0.2 + 0.25 * calm
        )
        win_y = [top + 34, BASE_Y - 26 - 150] if two else [BASE_Y - 26 - 150]
        ww, wh = 64.0, 92.0
        frames: Cmds = []
        panes: Cmds = []
        mull: Cmds = []
        shut: Cmds = []
        lit_a: Cmds = []
        box: Cmds = []
        bloom: Cmds = []
        for r, wy in enumerate(win_y):
            for wx in (
                x + w * 0.17 - ww / 2,
                x + w * 0.5 - ww / 2 if r == 0 and two else None,
                x + w * 0.83 - ww / 2,
            ):
                if wx is None:
                    continue
                if abs((wx + ww / 2) - door_x) < 70 and r == len(win_y) - 1:
                    continue  # the door lives here
                frames += _rect(wx - 5, wy - 5, ww + 10, wh + 10)
                panes += _rect(wx, wy, ww, wh)
                mull += [
                    ("M", wx + ww / 2, wy),
                    ("L", wx + ww / 2, wy + wh),
                    ("M", wx, wy + wh * 0.45),
                    ("L", wx + ww, wy + wh * 0.45),
                ]
                sh = ww * 0.36
                shut += _rect(wx - sh - 9, wy - 3, sh, wh + 6) + _rect(
                    wx + ww + 9, wy - 3, sh, wh + 6
                )
                if r == len(win_y) - 1:
                    box += _rect(wx - 8, wy + wh + 8, ww + 16, 14)
                    for j in range(4):
                        bloom += _circ(wx - 2 + (j + 0.5) * (ww + 4) / 4, wy + wh + 7, 7)
                if rng.random() < self.lit_p * (1.0 - 0.5 * calm):
                    lit_a += _rect(wx, wy, ww, wh)
        ctx.path(P, shut, shutter, tag="shutter", lod=1, shadow=False, material="wood")
        ctx.path(P, frames, trim, tag="window_frame", lod=0, shadow=False)
        ctx.path(
            P,
            panes,
            mix(mix(b("glass"), b("fabric"), 0.38), wall, 0.4 * calm),
            material="glass",
            tag="window",
            lod=1,
            shadow=False,
        )
        if lit_a:
            ctx.path(
                P,
                lit_a,
                b("window"),
                material="emissive",
                alpha=1.0 - 0.35 * calm,
                tag="window_lit",
                lod=1,
                shadow=False,
            )
        ctx.path(
            P, mull, None, stroke=trim, sw=3.0, tag="window_bars", lod=2, shadow=False, alpha=0.85
        )
        ctx.path(
            P, box, mix(b("wood"), wall, 0.25), tag="flower_box", lod=1, elev=2, material="wood"
        )
        ctx.path(P, bloom, mix(b("accent"), wall, 0.15), tag="flower", lod=2, shadow=False)
        # porch with a little roof, posts, steps, door and light
        dw = 74.0
        dtop = BASE_Y - 26 - 186
        for px_ in (door_x - 52, door_x + 46):
            ctx.rect(P, px_, dtop - 30, 8, 30 + 186, trim, tag="porch_post", lod=1, elev=4)
        ctx.poly(P, [(door_x - 70, dtop - 28), (door_x + 70, dtop - 28), (door_x + 54, dtop - 58), (door_x - 54, dtop - 58)], mix(b("brick2"), b("brick"), 0.4) if idx % 2 == 0 else mix(b("stone2"), b("fabric"), 0.4), tag="porch_roof", elev=6, smooth=0.06, material="paper")  # fmt: skip
        dcol = mix(b(["accent", "accent3", "fabric", "accent2"][idx % 4]), darken(wall, 0.3), 0.3)
        ctx.rect(
            P,
            door_x - dw / 2 - 6,
            dtop - 6,
            dw + 12,
            186 + 6,
            trim,
            r=3,
            tag="door_frame",
            lod=1,
            shadow=False,
        )
        ctx.rect(P, door_x - dw / 2, dtop, dw, 186, dcol, r=3, tag="door", elev=2, material="wood")
        ctx.rect(P, door_x - dw / 2 + 12, dtop + 16, dw - 24, 54, b("window") if self.lamps and self.lit_p > 0.25 else mix(b("glass"), b("fabric"), 0.4), material="emissive" if self.lamps and self.lit_p > 0.25 else "glass", tag="door_glass", lod=1, shadow=False)  # fmt: skip
        ctx.ellipse(
            P,
            door_x + dw / 2 - 12,
            dtop + 100,
            4.5,
            4.5,
            lighten(b("accent3"), 0.2),
            tag="door_handle",
            lod=2,
            shadow=False,
        )
        ctx.rect(
            P,
            door_x - dw / 2 - 20,
            BASE_Y - 22,
            dw + 40,
            22,
            lighten(b("stone"), 0.2),
            tag="step",
            lod=1,
            elev=2,
        )
        lx = door_x + 62
        ctx.ellipse(P, lx, dtop + 30, 9, 12, b("window") if self.lamps else lighten(b("glass"), 0.3), material="emissive" if self.lamps else "glass", glow=30 if self.lamps else 0.0, shadow=False, tag="porch_light", lod=1)  # fmt: skip

    def lawn(self) -> None:
        """Front gardens: grass band, flower beds, hedges, picket fence and garden paths."""
        ctx = self.ctx
        P = "mid_back"
        b = self.b
        rng = self.rng("lawn")
        grass = mix(b("foliage3"), b("foliage"), 0.38)
        if self.snow:
            grass = mix(grass, "#f4f7fb", 0.8)
        y0, y1 = BASE_Y, 1336.0
        ctx.rect(P, X0, y0, X1 - X0, y1 - y0, grass, tag="lawn", material="grass", shadow=False, gradient=(darken(grass, 0.1), lighten(grass, 0.04), 90.0))  # fmt: skip
        stripes: Cmds = []
        x = X0
        while x < X1:
            stripes += _rect(x, y0, 90, y1 - y0)
            x += 190
        ctx.path(P, stripes, lighten(grass, 0.1), tag="lawn", lod=2, shadow=False, alpha=0.25)
        ctx.rect(P, X0, y0, X1 - X0, 24, b("shadow"), alpha=0.14, tag="shade", lod=2, shadow=False)
        # garden paths from the doors down to the pavement (a gate gap in the fence)
        path_c = mix(b("sand"), b("stone"), 0.3)
        if self.snow:
            path_c = mix(path_c, "#f4f7fb", 0.7)
        for door_x in self.door_xs:
            ctx.poly(P, [(door_x - 34, y0 + 4), (door_x + 34, y0 + 4), (door_x + 58, y1 + 6), (door_x - 58, y1 + 6)], path_c, tag="garden_path", lod=1, shadow=False)  # fmt: skip
        # hedges and flower beds along the house fronts
        for x0_ in self.house_spans:
            xa, xb = x0_
            bush: Cmds = []
            bush2: Cmds = []
            xx = xa - 10
            while xx < xb + 10:
                if any(abs(xx - dx) < 70 for dx in self.door_xs):
                    xx += 40
                    continue
                r = float(rng.uniform(20, 34))
                (bush if rng.random() < 0.6 else bush2).extend(_circ(xx, y0 + 2 - r * 0.2, r))
                xx += r * 1.3
            ctx.path(P, bush, self.snowy(b("foliage2"), 0.35), tag="bush", elev=4, material="grass")
            ctx.path(P, bush2, self.snowy(b("foliage"), 0.35), tag="bush", elev=4, material="grass")
            blooms: Cmds = []
            for _ in range(7):
                bx = float(rng.uniform(xa, xb))
                if any(abs(bx - dx) < 70 for dx in self.door_xs):
                    continue
                blooms += _circ(bx, y0 - float(rng.uniform(4, 26)), 5.5)
            if not self.snow:
                ctx.path(P, blooms, b("accent3"), tag="flower", lod=2, shadow=False)
        # picket fence
        fence_y = y1 + 4
        pick: Cmds = []
        rails: Cmds = []
        x = X0 - 10
        while x < X1:
            if not any(abs(x + 8 - dx) < 52 for dx in self.door_xs):
                pick += [
                    ("M", x, fence_y),
                    ("L", x, fence_y - 56),
                    ("L", x + 8, fence_y - 68),
                    ("L", x + 16, fence_y - 56),
                    ("L", x + 16, fence_y),
                    ("Z",),
                ]
            x += 26
        rails += _rect(X0, fence_y - 46, X1 - X0, 7) + _rect(X0, fence_y - 18, X1 - X0, 7)
        fc = self.snowy(mix(lighten(b("trim"), 0.1), grass, 0.12), 0.55)
        ctx.path(P, rails, darken(fc, 0.06), tag="fence", lod=1, shadow=False)
        ctx.path(P, pick, fc, tag="fence", elev=3, material="wood")
        ctx.rect(
            P, X0, fence_y, X1 - X0, 8, b("shadow"), alpha=0.18, tag="shade", lod=2, shadow=False
        )

    # ---------------------------------------------------------------------------------- market
    def stall(self, x: float, base: float, w: float, key: int) -> None:
        """A market stall: wooden counter, striped canopy on two poles, crates of produce."""
        ctx = self.ctx
        P = "mid_back"
        b = self.b
        rng = self.rng("stall", key)
        wood = mix(b("wood"), b("accent3"), 0.12)
        cols = (["accent", "accent2", "accent3"][key % 3], ["accent2", "fabric", "accent"][key % 3])
        ca, cb = mix(b(cols[0]), "#ffffff", 0.04), lighten(b("trim"), 0.15)
        self.contact(P, x + w / 2, base + 2, w * 0.58, 9, 0.22)
        for px_ in (x + 8, x + w - 20):
            ctx.rect(
                P,
                px_,
                base - 330,
                12,
                330,
                darken(wood, 0.2),
                tag="stall_pole",
                elev=4,
                material="wood",
            )
        # canopy (striped, scalloped)
        cy = base - 340
        n = 8
        sw_ = (w + 40) / n
        sa: Cmds = []
        sb_: Cmds = []
        for i in range(n):
            xa, xb = x - 20 + i * sw_, x - 20 + (i + 1) * sw_
            cm: Cmds = [
                ("M", xa + 6, cy - 46),
                ("L", xb - 6, cy - 46),
                ("L", xb, cy),
                ("C", xb, cy + 24, xa, cy + 24, xa, cy),
                ("Z",),
            ]
            (sa if i % 2 == 0 else sb_).extend(cm)
        ctx.path(P, sa, ca, material="fabric", tag="awning", elev=6, lod=0)
        ctx.path(P, sb_, cb, material="fabric", tag="awning", elev=6, lod=0)
        if self.snow:
            ctx.rect(
                P, x - 14, cy - 56, w + 28, 14, "#ffffff", r=7, tag="snow", lod=1, shadow=False
            )
        ctx.rect(
            P,
            x - 20,
            cy + 22,
            w + 40,
            24,
            b("shadow"),
            alpha=0.16,
            tag="shade",
            lod=2,
            shadow=False,
        )
        # counter
        ctx.rect(P, x, base - 92, w, 92, wood, tag="stall_counter", elev=4, material="wood")
        ctx.rect(
            P,
            x - 6,
            base - 100,
            w + 12,
            14,
            lighten(wood, 0.18),
            r=3,
            tag="stall_counter",
            lod=1,
            elev=4,
            material="wood",
        )
        ctx.rect(
            P,
            x + 10,
            base - 78,
            w - 20,
            56,
            darken(wood, 0.14),
            r=3,
            tag="stall_front",
            lod=2,
            shadow=False,
        )
        # produce: crates with heaps of fruit
        goods = [
            (b("accent"), "#ffffff"),
            (b("accent3"), "#ffffff"),
            (b("foliage3"), "#ffffff"),
            (mix(b("accent"), b("accent3"), 0.5), "#ffffff"),
        ]
        nbox = max(2, int(w // 80))
        gp: list[Cmds] = [[] for _ in goods]
        crate: Cmds = []
        for i in range(nbox):
            bx = x + 12 + i * ((w - 24) / nbox)
            bw = (w - 24) / nbox - 8
            crate += _rect(bx, base - 126, bw, 26)
            gi = int(rng.integers(len(goods)))
            for j in range(int(bw // 22)):
                gp[gi] += _circ(bx + 12 + j * 22, base - 134, 11.5)
                if j > 0:
                    gp[gi] += _circ(bx + 22 + (j - 1) * 22, base - 150, 11)
        ctx.path(P, crate, darken(wood, 0.25), tag="crate", lod=1, elev=3, material="wood")
        for i, g in enumerate(gp):
            if g:
                ctx.path(P, g, goods[i][0], tag="produce", lod=2, elev=2, shadow=False)
        # lantern + price flags
        lx = x + w - 6
        ctx.line(P, (lx, cy + 30), (lx, cy + 60), 3, darken(wood, 0.3), tag="lantern", lod=1)
        ctx.ellipse(P, lx, cy + 76, 12, 15, b("window") if self.lamps else lighten(b("accent"), 0.3), material="emissive" if self.lamps else "flat", glow=40 if self.lamps else 0.0, shadow=False, tag="lantern", lod=1)  # fmt: skip

    def barrel(self, x: float, base: float, key: int = 0) -> None:
        ctx = self.ctx
        P = "mid_back"
        wood = mix(self.b("wood"), self.b("wood2"), 0.4)
        self.contact(P, x, base + 1, 44, 7)
        ctx.poly(P, [(x - 34, base), (x + 34, base), (x + 40, base - 40), (x + 34, base - 80), (x - 34, base - 80), (x - 40, base - 40)], wood, tag="barrel", elev=3, smooth=0.12, material="wood")  # fmt: skip
        for yy in (base - 16, base - 64):
            ctx.rect(
                P,
                x - 38,
                yy,
                76,
                7,
                darken(self.b("metal"), 0.45),
                tag="barrel",
                lod=2,
                shadow=False,
            )
        ctx.ellipse(P, x, base - 80, 34, 8, darken(wood, 0.25), tag="barrel", lod=1, shadow=False)

    def bunting(self, y0: float, sag: float, key: int) -> None:
        """A string of triangular flags (and bulbs after dark) across the top of the frame."""
        ctx = self.ctx
        P = "near"
        b = self.b
        rng = self.rng("bunting", key)
        xa, xb = -300.0, 1380.0
        string_c = darken(b("wood2"), 0.2)

        def yat(xx: float) -> float:
            u = (xx - xa) / (xb - xa)
            return y0 + sag * 4 * u * (1 - u)

        pts: Cmds = [("M", xa, yat(xa))]
        for i in range(1, 15):
            xx = xa + (xb - xa) * i / 14
            pts.append(("L", xx, yat(xx)))
        ctx.path(
            P,
            pts,
            None,
            stroke=string_c,
            sw=2.6,
            tag="bunting_string",
            lod=2,
            shadow=False,
            alpha=0.9,
        )
        palette = [b("accent"), b("accent3"), b("accent2"), b("fabric"), lighten(b("trim"), 0.2)]
        groups: list[Cmds] = [[] for _ in palette]
        xx = xa + 40
        i = 0
        while xx < xb - 30:
            y = yat(xx)
            y2 = yat(xx + 40)
            groups[i % len(palette)] += [
                ("M", xx, y),
                ("L", xx + 40, y2),
                ("L", xx + 20, max(y, y2) + 52 + float(rng.uniform(-4, 4))),
                ("Z",),
            ]
            xx += 56
            i += 1
        for k, g in enumerate(groups):
            ctx.path(P, g, palette[k], material="fabric", tag="bunting", lod=1, elev=6, shadow=True,
                     anim=Anim("sway", amp=1.4, freq=0.2 + 0.03 * k, phase=float(k), pivot=(540.0, y0)))  # fmt: skip
        if self.lamps:  # bulbs between the flags
            bulbs: Cmds = []
            xx = xa + 68
            while xx < xb:
                bulbs += _circ(xx, yat(xx) + 8, 7)
                xx += 56
            ctx.path(P, bulbs, b("window"), material="emissive", glow=22, shadow=False, tag="bunting_bulb", lod=1,
                     anim=Anim("twinkle", amp=0.35, freq=0.3, phase=float(key)))  # fmt: skip

    # --------------------------------------------------------------------------------- ground
    def ground(self) -> None:
        ctx = self.ctx
        b = self.b
        snow = self.snow
        rng = self.rng("pave")
        P = "mid_back"
        if self.dist == "market":
            self.plaza(rng)
            return
        side = mix(lighten(b("stone"), 0.34), b("sand"), 0.34)
        if self.dist == "suburb":
            side = mix(lighten(b("stone"), 0.4), b("sand"), 0.2)
        side_dark = darken(side, 0.10)
        if snow:
            side = mix(side, "#f4f7fb", 0.78)
            side_dark = mix(side_dark, "#dfe7f1", 0.7)
        g = ctx.ground_band(
            BASE_Y, CURB_Y + 16, side_dark, gradient_to=side, material="stone", shadow=False
        )
        g.tag = "sidewalk"
        ctx.rect(
            P, X0, BASE_Y, X1 - X0, 26, b("shadow"), alpha=0.16, tag="shade", lod=2, shadow=False
        )
        # paving: joints and alternating lighter slabs
        rows = ((BASE_Y + 62, BASE_Y + 128), (BASE_Y + 128, BASE_Y + 206), (BASE_Y + 206, CURB_Y))
        joints: Cmds = []
        tiles: Cmds = []
        for k, (ya, yb) in enumerate(rows):
            joints += [("M", X0, ya), ("L", X1, ya)]
            step = 130 + 22 * k
            xx = X0 + float(rng.uniform(0, 120)) + (k % 2) * 60
            j = 0
            while xx < X1:
                joints += [("M", xx, ya), ("L", xx, yb)]
                if j % 2 == 0:
                    tiles += _rect(xx, ya, step, yb - ya)
                xx += step
                j += 1
        ctx.path(P, tiles, lighten(side, 0.2), tag="paving", lod=2, shadow=False, alpha=0.30)
        ctx.path(P, joints, None, stroke=darken(side, 0.10 if not snow else 0.05), sw=2.5, tag="paving", lod=2, shadow=False, alpha=0.7)  # fmt: skip
        # curb: bright top edge + shaded face
        curb_top = lighten(b("stone"), 0.5)
        curb_face = darken(b("stone2"), 0.08)
        if snow:
            curb_top, curb_face = "#ffffff", mix(curb_face, "#cfd9e6", 0.5)
        ctx.rect(P, X0, CURB_Y - 4, X1 - X0, 16, curb_top, tag="curb", elev=3, shadow=False)
        ctx.rect(
            P,
            X0,
            CURB_Y + 12,
            X1 - X0,
            ROAD_Y - CURB_Y - 12,
            curb_face,
            tag="curb",
            elev=1,
            shadow=False,
        )
        # road
        road = b("asphalt")
        road_far = lighten(road, 0.04)
        if snow:
            road, road_far = mix(road, "#9aa4b0", 0.45), mix(road, "#b6bfca", 0.45)
        ctx.rect(P, X0, ROAD_Y, X1 - X0, Y1 - ROAD_Y + 10, road_far, tag="road", shadow=False, material="stone", gradient=(road_far, darken(road, 0.12), 90.0))  # fmt: skip
        ctx.rect(
            P,
            X0,
            ROAD_Y,
            X1 - X0,
            26,
            darken(road, 0.3),
            alpha=0.5,
            tag="gutter",
            lod=2,
            shadow=False,
        )
        line_c = b("line") if not snow else mix(b("line"), "#ffffff", 0.4)
        dashes: Cmds = []
        xx = X0 + 30.0
        while xx < X1:
            dashes += _rect(xx, 1742, 130, 12)
            xx += 250
        ctx.path(
            P, dashes, line_c, tag="road_mark", lod=1, shadow=False, alpha=0.55 if not snow else 0.4
        )
        tracks: Cmds = _rect(X0, 1636, X1 - X0, 54) + _rect(X0, 1820, X1 - X0, 70)
        ctx.path(P, tracks, darken(road, 0.3), tag="road_wear", lod=2, shadow=False, alpha=0.18)
        for mx_ in (210.0, 930.0):
            ctx.ellipse(
                P,
                mx_,
                1850 if mx_ < 500 else 1790,
                44,
                12,
                darken(road, 0.32),
                tag="manhole",
                lod=2,
                shadow=False,
                alpha=0.8,
            )
            ctx.ellipse(
                P,
                mx_,
                1848 if mx_ < 500 else 1788,
                36,
                9,
                lighten(road, 0.08),
                tag="manhole",
                lod=2,
                shadow=False,
                alpha=0.7,
            )
        zebra: Cmds = []
        for k in range(-3, 4):
            cx = 540 + k * 74
            zebra += _quad(
                (cx - 17, ROAD_Y + 18),
                (cx + 17, ROAD_Y + 18),
                (cx + 25, ROAD_Y + 170),
                (cx - 25, ROAD_Y + 170),
            )
        ctx.path(
            P,
            zebra,
            lighten(b("trim"), 0.2),
            tag="crosswalk",
            lod=1,
            shadow=False,
            alpha=0.62 if not snow else 0.4,
        )

    def plaza(self, rng: Any) -> None:
        """Market square: warm cobblestones filling the whole foreground (no kerb or road)."""
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        base = mix(mix(b("sand"), b("stone"), 0.5), b("wall2"), 0.2)
        if self.snow:
            base = mix(base, "#f4f7fb", 0.75)
        far = darken(base, 0.08)
        near = darken(base, 0.16)
        g = ctx.ground_band(BASE_Y, Y1 + 10, far, gradient_to=near, material="stone", shadow=False)
        g.tag = "plaza"
        ctx.rect(
            P, X0, BASE_Y, X1 - X0, 26, b("shadow"), alpha=0.16, tag="shade", lod=2, shadow=False
        )
        joints: Cmds = []
        slabs: Cmds = []
        y = BASE_Y + 24
        row = 0
        while y < Y1:
            hh = 22.0 * (1.0 + 0.05 * row)
            ww = 44.0 * (1.0 + 0.045 * row)
            joints += [("M", X0, y), ("L", X1, y)]
            xx = X0 + float(rng.uniform(0, ww)) - (row % 2) * ww * 0.5
            while xx < X1:
                joints += [("M", xx, y), ("L", xx, y + hh)]
                if rng.random() < 0.34:
                    slabs += _rect(xx + 2, y + 2, ww - 4, hh - 4)
                xx += ww
            y += hh
            row += 1
        ctx.path(P, slabs, lighten(base, 0.16), tag="cobbles", lod=2, shadow=False, alpha=0.38)
        ctx.path(
            P,
            joints,
            None,
            stroke=darken(base, 0.22),
            sw=2.6,
            tag="cobbles",
            lod=2,
            shadow=False,
            alpha=0.55,
        )

    # ----------------------------------------------------------------------------- glows & lights
    def glow(
        self, plane: str, x: float, y: float, rx: float, ry: float, col: str, alpha: float,
        steps: int = 9, flicker: bool = False, tag: str = "glow",
    ) -> None:  # fmt: skip
        """Soft radial glow: many nested, very faint ellipses (alpha builds up toward the centre)."""
        ctx = self.ctx
        rng = self.rng("flicker", int(x), int(y))
        a_each = 1.0 - (1.0 - clamp(alpha * 1.6, 0.0, 0.9)) ** (1.0 / steps)
        for i in range(steps):
            k = 1.0 - i / steps
            an = None
            if flicker and i == steps // 2:
                an = Anim(
                    "flicker",
                    amp=0.30,
                    freq=float(rng.uniform(0.25, 0.5)),
                    phase=float(rng.uniform(0, 6)),
                )
            ctx.ellipse(
                plane, x, y, rx * k, ry * k, col, material="emissive", alpha=a_each,
                shadow=False, tag=tag, lod=2, anim=an,
            )  # fmt: skip

    def cast(self, plane: str, x: float, y: float, h: float, wdt: float = 10.0) -> None:
        """Long soft shadow of a vertical object on the ground (day/dawn/dusk only)."""
        if self.night or self.p.weather in ("rain", "fog"):
            return
        k = {"day": 0.42, "dawn": 1.0, "dusk": 1.0}[self.tod] * h
        d = self.sun_dir
        self.ctx.poly(
            plane, [(x - wdt / 2, y), (x + wdt / 2, y), (x + d * k + wdt * 0.3, y + k * 0.16), (x + d * k - wdt * 0.3, y + k * 0.16)],
            self.b("shadow"), alpha=0.16, shadow=False, tag="cast_shadow", lod=2,
        )  # fmt: skip

    def contact(
        self, plane: str, x: float, y: float, rx: float, ry: float = 8.0, a: float = 0.26
    ) -> None:
        self.ctx.ellipse(
            plane, x, y, rx, ry, self.b("shadow"), alpha=a, shadow=False, tag="shadow", lod=2
        )

    def puff(self, rng: Any, cx: float, cy: float, rx: float, ry: float, n: int = 7) -> Cmds:
        """A puffy canopy: a ring of overlapping circles (non-zero fill merges them)."""
        r0 = min(rx, ry)
        cmds = _circ(cx, cy, r0 * 0.78)
        for i in range(n):
            a = 2 * math.pi * (i + float(rng.uniform(-0.2, 0.2))) / n
            cmds += _circ(
                cx + rx * 0.64 * math.cos(a),
                cy + ry * 0.64 * math.sin(a),
                r0 * float(rng.uniform(0.40, 0.55)),
            )
        return cmds

    def blob(
        self, rng: Any, cx: float, cy: float, rx: float, ry: float, n: int = 10, wob: float = 0.13
    ) -> list[Pt]:
        pts: list[Pt] = []
        for i in range(n):
            a = 2 * math.pi * i / n
            r = 1.0 + float(rng.uniform(-wob, wob))
            pts.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
        return pts

    # -------------------------------------------------------------------------------------- props
    def lamp(
        self, x: float, base: float, h: float, plane: str = "mid_back", scale: float = 1.0
    ) -> None:
        ctx = self.ctx
        b = self.b
        steel = darken(mix(b("metal"), b("fabric"), 0.3), 0.55)
        hi = lighten(steel, 0.18)
        s = scale
        top = base - h
        self.contact(plane, x, base + 2, 34 * s, 8 * s)
        self.cast(plane, x, base, h * 0.8)
        ctx.poly(plane, [(x - 17 * s, base), (x + 17 * s, base), (x + 11 * s, base - 34 * s), (x - 11 * s, base - 34 * s)], steel, tag="lamp", elev=4, smooth=0.12)  # fmt: skip
        ctx.poly(plane, [(x - 8 * s, base - 30 * s), (x + 8 * s, base - 30 * s), (x + 5.5 * s, top), (x - 5.5 * s, top)], steel, tag="lamp", elev=4)  # fmt: skip
        ctx.rect(
            plane,
            x - 2.5 * s,
            base - h * 0.9,
            3 * s,
            h * 0.86,
            hi,
            tag="lamp_hi",
            lod=2,
            shadow=False,
            alpha=0.5,
        )
        for yy in (base - 70 * s, top + 40 * s):  # collars
            ctx.rect(plane, x - 12 * s, yy, 24 * s, 9 * s, steel, r=3, tag="lamp", lod=1, elev=4)
        ctx.rect(plane, x - 20 * s, top - 6 * s, 40 * s, 10 * s, steel, r=3, tag="lamp", elev=4)
        on = self.lamps
        glass = b("window") if on else lighten(mix(b("glass"), b("sky_bottom"), 0.3), 0.3)
        ctx.poly(plane, [(x - 17 * s, top - 6 * s), (x + 17 * s, top - 6 * s), (x + 21 * s, top - 58 * s), (x - 21 * s, top - 58 * s)], glass, material="emissive" if on else "glass", glow=46 * s if on else 0.0, shadow=False, tag="lamp_glass", lod=0)  # fmt: skip
        ctx.path(plane, [("M", x - 6 * s, top - 6 * s), ("L", x - 7 * s, top - 58 * s), ("M", x + 6 * s, top - 6 * s), ("L", x + 7 * s, top - 58 * s)], None, stroke=steel, sw=3.0 * s, tag="lamp", lod=2, shadow=False)  # fmt: skip
        ctx.poly(plane, [(x - 27 * s, top - 58 * s), (x + 27 * s, top - 58 * s), (x + 9 * s, top - 84 * s), (x - 9 * s, top - 84 * s)], steel, tag="lamp", elev=4, smooth=0.1)  # fmt: skip
        ctx.ellipse(plane, x, top - 90 * s, 7 * s, 7 * s, steel, tag="lamp", lod=1, elev=4)
        if self.snow:
            ctx.rect(
                plane,
                x - 24 * s,
                top - 90 * s,
                48 * s,
                10 * s,
                "#ffffff",
                r=5 * s,
                tag="snow",
                lod=1,
                shadow=False,
            )
        elif self.ctx.density >= 0.3 and self.dist != "market":  # hanging flower basket
            side = 1.0 if x < 540 else -1.0
            by = top + 120 * s
            bx = x + side * 40 * s
            ctx.path(
                plane,
                [("M", x, by - 22 * s), ("L", bx, by - 22 * s), ("L", bx, by)],
                None,
                stroke=steel,
                sw=4.0 * s,
                tag="lamp",
                lod=2,
                shadow=False,
            )
            ctx.poly(
                plane,
                [
                    (bx - 24 * s, by),
                    (bx + 24 * s, by),
                    (bx + 14 * s, by + 22 * s),
                    (bx - 14 * s, by + 22 * s),
                ],
                darken(b("wood"), 0.1),
                tag="basket",
                lod=1,
                elev=3,
                smooth=0.2,
            )
            ctx.path(
                plane,
                _circ(bx - 12 * s, by - 3 * s, 11 * s)
                + _circ(bx + 12 * s, by - 3 * s, 11 * s)
                + _circ(bx, by - 11 * s, 12 * s),
                b("foliage"),
                tag="plant",
                lod=2,
                shadow=False,
            )
            ctx.path(
                plane,
                _circ(bx - 8 * s, by - 10 * s, 5 * s)
                + _circ(bx + 14 * s, by - 6 * s, 5 * s)
                + _circ(bx + 2 * s, by - 16 * s, 5 * s),
                b("accent"),
                tag="flower",
                lod=2,
                shadow=False,
            )
        if on:
            a = {"night": 1.0, "dusk": 0.6, "dawn": 0.6, "day": 0.4}[self.tod]
            self.glow(
                plane,
                x,
                top - 30 * s,
                130 * s,
                130 * s,
                b("window"),
                0.34 * a,
                9,
                flicker=True,
                tag="lamp_halo",
            )
            ctx.poly(plane, [(x - 14 * s, top - 4 * s), (x + 14 * s, top - 4 * s), (x + 150 * s, base), (x - 150 * s, base)], b("window"), material="emissive", alpha=0.06 * a, shadow=False, tag="light_cone", lod=2)  # fmt: skip
            ctx.ellipse(plane, x, base + 4, 170 * s, 20 * s, b("window"), material="emissive", alpha=0.15 * a, shadow=False, tag="light_pool", lod=2)  # fmt: skip

    def bench(self, x: float, base: float, w: float = 214.0) -> None:
        ctx = self.ctx
        P = "mid_back"
        b = self.b
        wood = mix(b("wood"), b("accent3"), 0.18)
        metal = darken(mix(b("metal"), b("fabric"), 0.3), 0.5)
        self.contact(P, x + w / 2, base + 2, w * 0.58, 9, 0.24)
        self.cast(P, x + w / 2, base, 100, w * 0.8)
        for lx in (x + 16, x + w - 30):
            ctx.rect(P, lx, base - 74, 14, 74, metal, r=3, tag="bench", elev=3)
            ctx.rect(P, lx - 6, base - 6, 26, 6, metal, r=2, tag="bench", lod=1, elev=3)
        ctx.rect(P, x, base - 84, w, 16, wood, r=4, tag="bench", elev=4, material="wood")
        ctx.rect(P, x + 4, base - 152, w - 8, 18, wood, r=4, tag="bench", elev=4, material="wood")
        ctx.rect(P, x + 4, base - 126, w - 8, 18, wood, r=4, tag="bench", elev=4, material="wood")
        for bx in (x + 22, x + w - 36):
            ctx.rect(P, bx, base - 152, 12, 80, metal, tag="bench", lod=1, elev=3)
        if self.snow:
            ctx.rect(
                P, x + 2, base - 160, w - 4, 11, "#ffffff", r=5, tag="snow", lod=1, shadow=False
            )
            ctx.rect(
                P, x - 2, base - 92, w + 4, 10, "#ffffff", r=5, tag="snow", lod=1, shadow=False
            )

    def hydrant(self, x: float, base: float) -> None:
        ctx = self.ctx
        P = "mid_back"
        red = self.b("accent")
        self.contact(P, x, base + 1, 30, 6)
        self.cast(P, x, base, 54, 24)
        ctx.rect(P, x - 20, base - 9, 40, 9, darken(red, 0.25), r=3, tag="hydrant", elev=2)
        ctx.rect(P, x - 15, base - 60, 30, 54, red, r=7, tag="hydrant", elev=3)
        ctx.rect(P, x - 29, base - 40, 58, 14, darken(red, 0.12), r=5, tag="hydrant", elev=3)
        ctx.ellipse(P, x, base - 61, 17, 12, lighten(red, 0.08), tag="hydrant", elev=3)
        ctx.rect(P, x - 5, base - 76, 10, 9, darken(red, 0.2), r=3, tag="hydrant", lod=1, elev=3)
        for sx in (-30, 30):
            ctx.ellipse(
                P,
                x + sx,
                base - 33,
                5,
                8,
                lighten(self.b("metal"), 0.2),
                tag="hydrant",
                lod=1,
                elev=2,
            )
        if self.snow:
            ctx.ellipse(P, x, base - 70, 18, 8, "#ffffff", tag="snow", lod=1, shadow=False)

    def bin(self, x: float, base: float) -> None:
        ctx = self.ctx
        P = "mid_back"
        col = darken(mix(self.b("accent2"), self.b("metal"), 0.4), 0.2)
        self.contact(P, x, base + 1, 40, 7)
        self.cast(P, x, base, 80, 40)
        ctx.poly(
            P,
            [(x - 30, base), (x + 30, base), (x + 34, base - 82), (x - 34, base - 82)],
            col,
            tag="bin",
            elev=3,
            smooth=0.06,
            material="metal",
        )
        ctx.rect(
            P,
            x - 38,
            base - 96,
            76,
            16,
            darken(col, 0.18),
            r=7,
            tag="bin",
            elev=3,
            material="metal",
        )
        ctx.path(P, [("M", x - 18, base - 70), ("L", x - 20, base - 8), ("M", x, base - 70), ("L", x, base - 8), ("M", x + 18, base - 70), ("L", x + 20, base - 8)], None, stroke=darken(col, 0.3), sw=3.0, tag="bin", lod=2, shadow=False)  # fmt: skip
        if self.snow:
            ctx.rect(P, x - 40, base - 104, 80, 12, "#ffffff", r=6, tag="snow", lod=1, shadow=False)

    def mailbox(self, x: float, base: float) -> None:
        ctx = self.ctx
        P = "mid_back"
        blue = mix(self.b("fabric"), "#1d4f9a", 0.4)
        self.contact(P, x, base + 1, 28, 6)
        self.cast(P, x, base, 110, 30)
        ctx.rect(P, x - 6, base - 50, 12, 50, darken(self.b("metal"), 0.4), tag="mailbox", elev=2)
        ctx.poly(
            P,
            [(x - 26, base - 52), (x + 26, base - 52), (x + 26, base - 108), (x - 26, base - 108)],
            blue,
            tag="mailbox",
            elev=3,
            smooth=0.28,
        )
        ctx.rect(P, x - 18, base - 86, 36, 6, darken(blue, 0.4), r=3, tag="mailbox", lod=1)
        ctx.rect(
            P, x - 26, base - 70, 52, 8, lighten(blue, 0.18), tag="mailbox", lod=2, shadow=False
        )

    def parking_meter(self, x: float, base: float) -> None:
        ctx = self.ctx
        P = "mid_back"
        steel = darken(mix(self.b("metal"), self.b("fabric"), 0.3), 0.4)
        self.contact(P, x, base + 1, 18, 5)
        ctx.rect(P, x - 3, base - 90, 6, 90, steel, tag="meter", elev=2)
        ctx.rect(
            P,
            x - 15,
            base - 130,
            30,
            44,
            mix(steel, self.b("accent2"), 0.4),
            r=10,
            tag="meter",
            elev=3,
        )
        ctx.ellipse(
            P, x, base - 112, 8, 8, lighten(self.b("glass"), 0.3), tag="meter", lod=1, shadow=False
        )

    def street_tree(
        self, x: float, base: float, h: float = 520.0, key: int = 0, size: float = 1.0
    ) -> None:
        ctx = self.ctx
        b = self.b
        P = "mid_back"
        rng = self.rng("tree", key)
        trunk_c = darken(b("trunk"), 0.12)
        self.contact(P, x, base + 3, 90, 12, 0.24)
        self.cast(P, x, base, h * 0.5, 30)
        ctx.ellipse(
            P, x, base - 2, 44, 10, darken(b("stone2"), 0.3), tag="tree_pit", lod=2, shadow=False
        )

        def sway(ph: float) -> Anim:
            return Anim("sway", amp=0.55, freq=0.18, phase=ph, pivot=(x, base))

        trunk_h = h * 0.5
        ctx.poly(P, [(x - 19, base), (x + 19, base), (x + 12, base - trunk_h), (x - 12, base - trunk_h)], trunk_c, tag="trunk", elev=3, smooth=0.05, material="wood", anim=sway(0.0))  # fmt: skip
        ctx.line(
            P,
            (x, base - trunk_h * 0.7),
            (x - 46, base - trunk_h - 20),
            9,
            trunk_c,
            tag="trunk",
            lod=1,
            anim=sway(0.0),
        )
        ctx.line(
            P,
            (x, base - trunk_h * 0.8),
            (x + 50, base - trunk_h - 30),
            8,
            trunk_c,
            tag="trunk",
            lod=1,
            anim=sway(0.0),
        )
        cy = base - h + 150
        dark, mid, light = b("foliage2"), b("foliage"), b("foliage3")
        if self.snow:
            dark, mid, light = (
                mix(dark, "#cbd8e6", 0.45),
                mix(mid, "#dbe5ef", 0.5),
                mix(light, "#f2f6fb", 0.6),
            )
        ph = float(rng.uniform(0, 6))
        z = size
        ctx.path(
            P,
            self.puff(rng, x, cy + 20 * z, 176 * z, 150 * z, 8),
            dark,
            tag="canopy",
            elev=6,
            material="grass",
            anim=sway(ph),
        )
        ctx.path(
            P,
            self.puff(rng, x - 22 * z, cy - 8 * z, 140 * z, 120 * z, 7),
            mid,
            tag="canopy",
            lod=1,
            elev=8,
            material="grass",
            anim=sway(ph),
        )
        ctx.path(
            P,
            self.puff(rng, x - 46 * z, cy - 38 * z, 84 * z, 72 * z, 6),
            light,
            tag="canopy",
            lod=1,
            elev=10,
            material="grass",
            anim=sway(ph),
        )

    def car(self, x: float, y: float, col: str, w: float = 372.0, flip: bool = False) -> None:
        """Parked sedan seen from the side; ``y`` is the road line under the wheels."""
        ctx = self.ctx
        P = "mid_back"
        b = self.b

        def X(u: float) -> float:
            return x + (w - u * w if flip else u * w)

        def poly(pts: list[tuple[float, float]], fill: str, **kw: Any) -> None:
            ctx.poly(P, [(X(u), y - v) for u, v in pts], fill, **kw)

        self.contact(P, x + w / 2, y + 4, w * 0.56, 12, 0.30)
        dark = darken(col, 0.18)
        glass = mix(b("glass"), b("fabric"), 0.55)
        ctx.rect(P, min(X(0), X(1)), y - 76, w, 54, col, r=22, tag="car", elev=4, material="metal")
        poly(
            [(0.17, 70), (0.29, 124), (0.64, 128), (0.80, 70)],
            col,
            tag="car",
            elev=4,
            smooth=0.12,
            material="metal",
        )
        poly(
            [(0.215, 72), (0.305, 116), (0.455, 118), (0.455, 72)],
            glass,
            tag="car_window",
            lod=1,
            shadow=False,
            material="glass",
        )
        poly(
            [(0.485, 72), (0.485, 118), (0.615, 116), (0.725, 72)],
            glass,
            tag="car_window",
            lod=1,
            shadow=False,
            material="glass",
        )
        ctx.rect(
            P,
            min(X(0.03), X(0.97)),
            y - 44,
            w * 0.94,
            5,
            dark,
            tag="car_trim",
            lod=2,
            shadow=False,
            alpha=0.7,
        )
        ctx.rect(
            P,
            min(X(0.005), X(0.995)),
            y - 48,
            w * 0.99,
            14,
            darken(col, 0.3),
            r=6,
            tag="car_bumper",
            lod=1,
            shadow=False,
        )
        hx = X(0.965) - 9
        tx = X(0.03) - 7
        ctx.rect(P, hx, y - 62, 18, 12, "#fff3c4", r=5, material="emissive" if self.lamps else "flat", glow=24 if self.lamps else 0.0, shadow=False, tag="headlight", lod=1)  # fmt: skip
        ctx.rect(
            P,
            tx,
            y - 62,
            14,
            12,
            "#e5483a",
            r=5,
            material="emissive" if self.lamps else "flat",
            shadow=False,
            tag="taillight",
            lod=1,
        )
        for u in (0.215, 0.79):
            wx = X(u)
            ctx.ellipse(P, wx, y - 24, 36, 36, "#20232b", tag="wheel", elev=3)
            ctx.ellipse(
                P, wx, y - 24, 17, 17, lighten(b("metal"), 0.25), tag="wheel", lod=1, shadow=False
            )
            ctx.ellipse(
                P, wx, y - 24, 6, 6, darken(b("metal"), 0.3), tag="wheel", lod=2, shadow=False
            )
        if self.snow:
            poly(
                [(0.25, 124), (0.31, 128), (0.62, 132), (0.66, 124)],
                "#ffffff",
                tag="snow",
                lod=1,
                shadow=False,
                smooth=0.3,
            )

    def potted(self, x: float, base: float, s: float = 1.0, key: int = 0) -> None:
        ctx = self.ctx
        P = "mid_back"
        rng = self.rng("pot", key)
        pot = mix(self.b("brick"), self.b("wall2"), 0.2)
        ctx.poly(P, [(x - 22 * s, base - 46 * s), (x + 22 * s, base - 46 * s), (x + 16 * s, base), (x - 16 * s, base)], pot, tag="planter", elev=2, smooth=0.08)  # fmt: skip
        ctx.rect(
            P,
            x - 25 * s,
            base - 54 * s,
            50 * s,
            12 * s,
            lighten(pot, 0.1),
            r=4 * s,
            tag="planter",
            lod=1,
            elev=2,
        )
        leaf = [self.b("foliage"), self.b("foliage2"), self.b("foliage3")]
        for i in range(4):
            dx = float(rng.uniform(-18, 18)) * s
            ctx.ellipse(P, x + dx, base - (88 + i * 6) * s, float(rng.uniform(15, 24)) * s, float(rng.uniform(22, 34)) * s, leaf[i % 3], tag="plant", lod=1, elev=2, rot=float(rng.uniform(-25, 25)))  # fmt: skip
        fl = [self.b("accent"), self.b("accent3"), "#ffffff"]
        for i in range(3):
            ctx.ellipse(
                P,
                x + float(rng.uniform(-18, 18)) * s,
                base - float(rng.uniform(88, 112)) * s,
                6 * s,
                6 * s,
                fl[i % 3],
                tag="flower",
                lod=2,
                shadow=False,
            )

    def sidewalk_props(self) -> None:
        d = self.ctx.density
        if self.dist == "market":
            self.market_props()
            return
        # lamp posts along the curb
        for x in (68.0, 1030.0, -610.0, 1590.0):
            self.lamp(x, CURB_Y - 8, 560.0)
        self.bench(
            878.0, 1322.0 if self.dist == "downtown" else 1352.0
        )  # always: it is a character slot
        if d >= 0.2:
            self.hydrant(150.0, CURB_Y - 10)
        if d >= 0.3:
            self.bin(962.0, CURB_Y - 10) if self.dist == "suburb" else self.bin(-60.0, CURB_Y - 10)
        if d >= 0.4 and self.dist == "downtown":
            self.street_tree(1128.0, 1300.0, 720.0, key=1, size=1.1)
            self.street_tree(-250.0, 1300.0, 600.0, key=0)
        if d >= 0.45:
            self.mailbox(
                -190.0 if self.dist == "downtown" else 1205.0,
                1330.0 if self.dist == "downtown" else 1352.0,
            )
            self.parking_meter(1090.0, CURB_Y - 8)
        if d >= 0.55 and self.dist == "downtown":
            self.potted(204.0, 1292.0, 1.0, 0)
            self.potted(1010.0, 1292.0, 0.9, 1)
        if d >= 0.35:
            self.car(800.0, 1722.0, self.b("accent2"), 372.0)
        if d >= 0.7:
            self.car(-250.0, 1722.0, self.b("accent3"), 372.0, flip=True)
        if d >= 0.8 and self.dist == "downtown":
            self.potted(-90.0, 1292.0, 1.1, 2)
            self.hydrant(1250.0, CURB_Y - 10)

    def market_props(self) -> None:
        d = self.ctx.density
        P = "mid_back"
        self.stall(-120.0, 1352.0, 250.0, 0)
        self.bench(878.0, 1322.0)  # always: it is a character slot
        self.stall(1090.0, 1352.0, 250.0, 1)
        if d >= 0.3:
            self.barrel(176.0, 1330.0)
        if d >= 0.45:
            self.barrel(1000.0, 1352.0, 1)
        if d >= 0.55:
            self.potted(-200.0, 1360.0, 1.1, 3)
            self.potted(1400.0, 1360.0, 1.0, 4)
        if d >= 0.65:
            self.stall(-640.0, 1352.0, 250.0, 2)
            self.stall(1420.0, 1352.0, 250.0, 3)
        _ = P

    # --------------------------------------------------------------------------- weather & near
    def mist(self, plane: str, y0: float, y1: float, alpha: float, steps: int = 20) -> None:
        """A soft band of mist: nested translucent strips, so alpha builds up toward the middle."""
        ctx = self.ctx
        col = mix(self.b("sky_bottom"), "#ffffff", 0.15)
        a_each = 1.0 - (1.0 - clamp(alpha * 0.8, 0.0, 0.92)) ** (
            1.0 / steps
        )  # centre alpha ~ alpha * 0.8
        for i in range(steps):
            f = i / steps / 2.0  # inset fraction of the band height on each side
            ctx.rect(plane, X0, y0 + (y1 - y0) * f, X1 - X0, (y1 - y0) * (1.0 - 2.0 * f), col, material="emissive", alpha=a_each, shadow=False, tag="mist", lod=2)  # fmt: skip

    def puddle(self, x: float, y: float, rx: float) -> None:
        ctx = self.ctx
        sky = mix(self.b("sky_bottom"), "#ffffff", 0.2)
        ctx.ellipse(
            "mid_back",
            x,
            y,
            rx,
            rx * 0.11,
            sky,
            material="emissive",
            alpha=0.42,
            shadow=False,
            tag="puddle",
            lod=2,
        )
        ctx.ellipse("mid_back", x - rx * 0.2, y - rx * 0.012, rx * 0.55, rx * 0.04, "#ffffff", material="emissive", alpha=0.35, shadow=False, tag="puddle", lod=2)  # fmt: skip

    def weather_fx(self) -> None:
        ctx = self.ctx
        d = ctx.density
        P = "mid_back"
        market = self.dist == "market"
        if self.rain:
            sheen = mix(self.b("sky_bottom"), "#ffffff", 0.2)
            ctx.rect(P, X0, BASE_Y + 30, X1 - X0, (Y1 - BASE_Y - 30) if market else CURB_Y - BASE_Y - 20, sheen, material="emissive", alpha=0.10, shadow=False, tag="wet", lod=2)  # fmt: skip
            if not market:
                ctx.rect(
                    P,
                    X0,
                    ROAD_Y,
                    X1 - X0,
                    200,
                    sheen,
                    material="emissive",
                    alpha=0.10,
                    shadow=False,
                    tag="wet",
                    lod=2,
                )
            spots = (
                (-120, 1290, 150),
                (470, 1352, 120),
                (960, 1330, 130),
                (180, 1630, 170),
                (880, 1830, 190),
                (-300, 1810, 160),
            )
            for i, (x, y, rx) in enumerate(spots):
                if i < 3 + int(3 * d):
                    self.puddle(x, y, rx)
            if self.lamps and not market:  # lamp light stretched down the wet road
                for lx in (68.0, 1030.0, -610.0, 1590.0):
                    for k, (ry, a) in enumerate(((60, 0.20), (95, 0.14), (130, 0.09))):
                        ctx.ellipse(P, lx, ROAD_Y + 70 + k * 40, 16 - 3 * k, ry, self.b("window"), material="emissive", alpha=a, shadow=False, tag="reflection", lod=2)  # fmt: skip
            self.rain_particles()
        elif self.snow:
            rng = self.rng("snowlumps")
            lumps: Cmds = []
            sx = X0
            while sx < X1:
                w = float(rng.uniform(70, 160))
                h = float(rng.uniform(10, 26))
                lumps += [
                    ("M", sx, BASE_Y + 6),
                    ("C", sx + w * 0.1, BASE_Y - h, sx + w * 0.9, BASE_Y - h, sx + w, BASE_Y + 6),
                    ("Z",),
                ]
                sx += w * float(rng.uniform(0.6, 1.0))
            ctx.path(P, lumps, "#ffffff", tag="snow", lod=1, shadow=False)
            if not market:
                curb: Cmds = []
                sx = X0
                while sx < X1:
                    w = float(rng.uniform(80, 200))
                    h = float(rng.uniform(10, 22))
                    curb += [
                        ("M", sx, CURB_Y + 14),
                        (
                            "C",
                            sx + w * 0.15,
                            CURB_Y + 14 - h,
                            sx + w * 0.85,
                            CURB_Y + 14 - h,
                            sx + w,
                            CURB_Y + 14,
                        ),
                        ("Z",),
                    ]
                    sx += w * float(rng.uniform(0.7, 1.05))
                ctx.path(P, curb, "#ffffff", tag="snow", lod=1, shadow=False)
            self.snow_particles()
        elif self.fog:
            self.mist("far", 420, 980, 0.6)
            self.mist("back", 640, 1180, 0.55)
            self.mist(P, 1040, 1330, 0.5)
            self.mist(P, 1380, 1700, 0.28)

    def rain_particles(self) -> None:
        ctx = self.ctx
        col = mix(self.b("sky_bottom"), "#ffffff", 0.45)
        n = int(120 + 150 * ctx.density)
        ctx.emit(
            "back",
            Particles(
                "rain",
                (X0, Y0, X1, Y1),
                n,
                speed=0.9,
                size=9.0,
                color=col,
                alpha=0.38,
                wind=0.9,
                seed=ctx.seed,
            ),
        )
        ctx.emit(
            "near",
            Particles(
                "rain",
                (X0, Y0, X1, Y1),
                int(n * 0.7),
                speed=1.15,
                size=14.0,
                color=col,
                alpha=0.5,
                wind=0.9,
                seed=ctx.seed + 1,
            ),
        )

    def snow_particles(self) -> None:
        ctx = self.ctx
        n = int(130 + 170 * ctx.density)
        ctx.emit(
            "back",
            Particles(
                "snow",
                (X0, Y0, X1, Y1),
                n,
                speed=0.8,
                size=7.0,
                color="#ffffff",
                alpha=0.7,
                wind=0.4,
                seed=ctx.seed,
            ),
        )
        ctx.emit(
            "near",
            Particles(
                "snow",
                (X0, Y0, X1, Y1),
                int(n * 0.45),
                speed=1.0,
                size=15.0,
                color="#ffffff",
                alpha=0.85,
                wind=0.5,
                seed=ctx.seed + 1,
            ),
        )

    def signal(self, x: float, top: float) -> None:
        """Traffic-light mast at the right-hand edge of the frame (near plane)."""
        ctx = self.ctx
        P = "near"
        steel = darken(mix(self.b("metal"), self.b("fabric"), 0.3), 0.6)
        ctx.poly(
            P,
            [(x - 10, Y1), (x + 10, Y1), (x + 7, top), (x - 7, top)],
            steel,
            tag="signal",
            elev=10,
        )
        ctx.line(
            P, (x, top + 10), (x - 150, top - 14), 11, steel, tag="signal", elev=10, shadow=True
        )
        hx = x - 150
        ctx.line(P, (hx, top - 14), (hx, top + 4), 6, steel, tag="signal", lod=1)
        ctx.rect(
            P, hx - 28, top + 4, 56, 136, mix(steel, "#000000", 0.2), r=10, tag="signal", elev=10
        )
        ctx.rect(P, hx - 36, top + 12, 8, 120, steel, r=3, tag="signal", lod=1, elev=10)
        lit = 0 if self.night or self.tod == "dusk" else 2  # red at night, green by day
        for i, c in enumerate(("#ef4538", "#ffb41f", "#3cc86a")):
            on = i == lit
            col = c if on else darken(c, 0.62)
            ctx.ellipse(P, hx, top + 34 + i * 40, 15, 15, col, material="emissive" if on else "flat", glow=34 if on else 0.0, shadow=False, tag="signal_light", lod=1)  # fmt: skip
        if self.snow:
            ctx.rect(P, hx - 30, top - 2, 60, 9, "#ffffff", r=4, tag="snow", lod=1, shadow=False)

    def leaves(self, rng: Any, pts: list[Pt], n: int, size: float, spread: float) -> Cmds:
        """``n`` pointed leaves scattered along a polyline (as path commands)."""
        cmds: Cmds = []
        for i in range(n):
            u = (i + float(rng.uniform(0.0, 1.0))) / n
            seg = min(len(pts) - 2, int(u * (len(pts) - 1)))
            f = u * (len(pts) - 1) - seg
            bx = lerp(pts[seg][0], pts[seg + 1][0], f) + float(rng.uniform(-spread, spread))
            by = lerp(pts[seg][1], pts[seg + 1][1], f) + float(rng.uniform(-spread, spread))
            ang = float(rng.uniform(0, 2 * math.pi))
            ln_ = size * float(rng.uniform(0.75, 1.25))
            wd = ln_ * float(rng.uniform(0.42, 0.6))
            ca, sa = math.cos(ang), math.sin(ang)
            tx, ty = bx + ln_ * ca, by + ln_ * sa
            mx, my = bx + ln_ * 0.5 * ca, by + ln_ * 0.5 * sa
            cmds += [
                ("M", bx, by),
                ("Q", mx - sa * wd, my + ca * wd, tx, ty),
                ("Q", mx + sa * wd, my - ca * wd, bx, by),
                ("Z",),
            ]
        return cmds

    def branch(self) -> None:
        """Leafy branch hanging into the top-left corner (near plane)."""
        ctx = self.ctx
        P = "near"
        rng = self.rng("branch")
        b = self.b
        cols = [darken(b("foliage2"), 0.28), darken(b("foliage"), 0.2), darken(b("foliage3"), 0.1)]
        if self.snow:
            cols = [mix(c, w, 0.5) for c, w in zip(cols, ("#9fb1c4", "#bccbdb", "#e3ecf5"))]
        bark = darken(b("trunk"), 0.3)
        main = [(-140.0, -30.0), (20.0, 20.0), (150.0, 80.0), (250.0, 170.0)]
        ctx.path(P, [("M", *main[0]), ("Q", main[1][0], main[1][1], main[2][0], main[2][1]), ("Q", 210.0, 110.0, *main[3])], None, stroke=bark, sw=13, tag="branch", lod=1, shadow=False, elev=14)  # fmt: skip
        ctx.path(
            P,
            [("M", 20.0, 20.0), ("Q", 60.0, -10.0, 130.0, -40.0)],
            None,
            stroke=bark,
            sw=8,
            tag="branch",
            lod=1,
            shadow=False,
        )
        dense = [*main, (130.0, -40.0), (60.0, 0.0)]
        ph = float(rng.uniform(0, 6))
        for k, (n, size) in enumerate(((20, 120.0), (18, 104.0), (12, 84.0))):
            cmds = self.leaves(rng, dense, n, size, 46.0)
            ctx.path(P, cmds, cols[k], tag="leaf", elev=12 + 3 * k, lod=1, material="grass", anim=Anim("sway", amp=1.0, freq=0.16, phase=ph + k * 0.4, pivot=(-140.0, -40.0)))  # fmt: skip

    def wires(self) -> None:
        ctx = self.ctx
        P = "near"
        col = darken(mix(self.b("metal"), self.b("fabric"), 0.3), 0.6)
        rng = self.rng("wires")
        for y0, sag in ((118.0, 34.0), (160.0, 24.0)):
            pts: Cmds = [("M", -300.0, y0 - 8)]
            n = 8
            for i in range(1, n + 1):
                u = i / n
                pts.append(("L", -300.0 + 1700.0 * u, y0 - 8 + sag * 4 * u * (1 - u) + 14 * u))
            ctx.path(P, pts, None, stroke=col, sw=2.6, shadow=False, tag="wire", lod=2, alpha=0.8)

        def wire_y(xx: float) -> float:
            u = (xx + 300.0) / 1700.0
            return 118.0 - 8 + 34.0 * 4 * u * (1 - u) + 14 * u

        for xx in (690.0, 742.0, 936.0):  # three pigeons on the upper wire
            y = wire_y(xx)
            s = float(rng.uniform(0.9, 1.1))
            bc = mix(self.b("stone2"), "#1b2030", 0.35)
            ctx.ellipse(P, xx, y - 15 * s, 15 * s, 11 * s, bc, tag="bird", lod=2, rot=-10)
            ctx.ellipse(P, xx + 14 * s, y - 24 * s, 7.5 * s, 7 * s, bc, tag="bird", lod=2)
            ctx.poly(
                P,
                [(xx - 12 * s, y - 14 * s), (xx - 30 * s, y - 6 * s), (xx - 12 * s, y - 8 * s)],
                bc,
                tag="bird",
                lod=2,
            )
            ctx.poly(
                P,
                [(xx + 20 * s, y - 25 * s), (xx + 27 * s, y - 23 * s), (xx + 20 * s, y - 21 * s)],
                "#e0a53c",
                tag="bird",
                lod=2,
                shadow=False,
            )

    def near_plane(self) -> None:
        d = self.ctx.density
        if self.dist == "market":
            self.bunting(250.0, 56.0, 0)
            if d >= 0.4:
                self.bunting(400.0, 46.0, 1)
            return
        if d >= 0.3 and self.dist == "downtown":
            self.signal(1064.0, 470.0)
        if d >= 0.25:
            self.branch()
        if d >= 0.4:
            self.wires()

    # -------------------------------------------------------------------------------- build
    def build(self) -> None:
        self.weather_scheme()
        self.sky()
        d = self.dist
        if d == "downtown":
            self.skyline(
                "far", "f1", hmin=650, hmax=1040, wmin=60, wmax=150, haze=0.80, lod=2, spires=0.2
            )
            self.skyline(
                "far",
                "f2",
                hmin=560,
                hmax=900,
                wmin=80,
                wmax=170,
                haze=0.66,
                lod=2,
                spires=0.1,
                lit_scale=1.2,
            )
        elif d == "market":
            self.skyline(
                "far",
                "f1",
                hmin=520,
                hmax=860,
                wmin=60,
                wmax=130,
                haze=0.80,
                lod=2,
                style="oldtown",
            )
            self.skyline(
                "far",
                "f2",
                hmin=470,
                hmax=760,
                wmin=80,
                wmax=150,
                haze=0.64,
                lod=2,
                style="oldtown",
            )
        else:
            self.hills("far", "h1", 930.0, 150.0, 0.78, trees=False)
            self.hills("far", "h2", 1010.0, 110.0, 0.62, trees=True)
        self.back_row()
        rng = self.rng("mid")
        plan = self.plan_mid()
        self.door_xs: list[float] = []
        self.house_spans: list[tuple[float, float]] = []
        for i, bd in enumerate(plan):
            if d == "suburb" and not (self.shops and i == 3):
                self.house(rng, bd, i)
                self.door_xs.append(bd["door_x"])
                self.house_spans.append((bd["x"], bd["x"] + bd["w"]))
            else:
                if d == "suburb":  # the corner store
                    bd["floors"], bd["roof"] = 1, "flat"
                self.facade(rng, bd, i)
        self.ground()
        if d == "suburb":
            self.lawn()
        self.sidewalk_props()
        self.weather_fx()
        self.near_plane()


@register_background(
    "street",
    params_schema=StreetParams,
    summary="Charming city street: layered skyline, shopfront buildings, lamp posts, sidewalk and road; "
    "downtown / suburb / market districts in clear, rain, snow or fog",
    slots={
        "shopfront": (0.30, 0.74),
        "lamppost": (0.17, 0.745),
        "bench": (0.76, 0.76),
        "curb": (0.42, 0.775),
        "crosswalk": (0.50, 0.835),
    },
    tags=("outdoor", "city"),
    ground_y=0.74,
    perspective=1.0,
    horizon=0.62,
)
def street(ctx: BuildContext, p: StreetParams) -> None:
    _Street(ctx, p).build()
