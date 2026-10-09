"""room: a cosy interior set - bedroom, living room, office or kitchen - with a window onto the sky,
lamps and screens that light up at dawn/dusk/night, rugs, shelves, plants and a door."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field

from reel.core.geometry import Pt, darken, lighten, mix
from reel.core.ir import Anim, Line, Particles
from reel.templates.base import (
    BASE_ROLES,
    X0,
    X1,
    Y0,
    Y1,
    BgParams,
    BuildContext,
    register_background,
)
from reel.templates.palette import SKY, make_scheme

FLOOR_Y = 1150.0  # where the back wall meets the floor (design px)
VP = (540.0, 560.0)  # vanishing point of the floorboards / tiles
BASE_Y = 1230.0  # where floor-standing furniture touches the floor

# window frame (x, y, w, h): upper right, above the main piece of furniture
WIN = (644.0, 176.0, 340.0, 480.0)

# Role overrides per room.  They go through `make_scheme`, so time of day and mood still tint them.
PALETTES: dict[str, dict[str, str]] = {
    "living": {
        "wall": "#c7d8d3",
        "wall2": "#b6cac5",
        "trim": "#f8f4ea",
        "floor": "#bd8c58",
        "floor2": "#a57545",
        "wood": "#9d6b44",
        "wood2": "#6f452b",
        "accent": "#d9603b",
        "accent2": "#2f7f86",
        "accent3": "#f0b43c",
        "fabric": "#c4694d",
        "fabric2": "#efe3cf",
        "curtain": "#efe0bf",
        "door": "#4a8a8f",
        "rug": "#dcc8a6",
        "rug2": "#c67553",
        "shade": "#f6ead2",
        "pot": "#d9603b",
    },
    "bedroom": {
        "wall": "#d9d2ec",
        "wall2": "#cac1e2",
        "trim": "#fbf8ff",
        "floor": "#c8a070",
        "floor2": "#b48b5b",
        "wood": "#a9794f",
        "wood2": "#7d5636",
        "accent": "#ee8f8f",
        "accent2": "#5fb3a6",
        "accent3": "#f6c85f",
        "fabric": "#79bdb2",
        "fabric2": "#fff3ea",
        "curtain": "#cfe6e0",
        "door": "#fbf6ee",
        "rug": "#e8c9c0",
        "rug2": "#d58282",
        "shade": "#fff0d8",
        "pot": "#5fb3a6",
    },
    "office": {
        "wall": "#d3dfe9",
        "wall2": "#c2d1de",
        "trim": "#f5f8fb",
        "floor": "#8d97a3",
        "floor2": "#7d8793",
        "wood": "#c8975f",
        "wood2": "#8d6339",
        "accent": "#e4572e",
        "accent2": "#2a9d8f",
        "accent3": "#f3a712",
        "fabric": "#41546d",
        "fabric2": "#dfe7ef",
        "curtain": "#e7eef5",
        "door": "#dfe7ef",
        "rug": "#b6c0cb",
        "rug2": "#2a9d8f",
        "shade": "#e8eef4",
        "pot": "#f3a712",
    },
    "kitchen": {
        "wall": "#cde6db",
        "wall2": "#bcd9cc",
        "trim": "#fffaf0",
        "floor": "#d08e68",
        "floor2": "#f3e6d2",
        "wood": "#b98757",
        "wood2": "#855c37",
        "accent": "#e4572e",
        "accent2": "#2a9d8f",
        "accent3": "#f3b73c",
        "fabric": "#f6ebd4",
        "fabric2": "#fff8ea",
        "curtain": "#fff3d6",
        "door": "#f3c54b",
        "rug": "#f2e2c8",
        "rug2": "#4aa598",
        "shade": "#fff0cf",
        "pot": "#2a9d8f",
    },
}


class RoomParams(BgParams):
    room: Literal["bedroom", "living", "office", "kitchen"] = Field(
        "living",
        description="which room: bedroom (bed, nightstand lamp, fairy lights), living (sofa, floor lamp, "
        "gallery wall), office (desk, monitor, pinboard, blinds) or kitchen (counter, fridge, hanging lamp)",
    )
    view: Literal["sky", "city", "garden"] = Field(
        "city",
        description="what shows through the window: plain sky with clouds/sun/moon, a city skyline, "
        "or a garden with hills and trees",
    )
    lights: Literal["auto", "on", "off"] = Field(
        "auto",
        description="lamps and screens: auto = switched on at dawn/dusk/night, on = always lit "
        "(e.g. a cosy day scene), off = dark (lights-out moment)",
    )


# ----------------------------------------------------------------------------------- tiny helpers
def _rgba(hex6: str, a: float) -> str:
    return f"{hex6[:7]}{round(max(0.0, min(1.0, a)) * 255):02x}"


def _rot(pts: list[Pt], cx: float, cy: float, deg: float) -> list[Pt]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


class _Room:
    """Drawing kit + the shared pieces of every room variant."""

    def __init__(self, ctx: BuildContext, p: RoomParams) -> None:
        self.c = ctx
        self.p = p
        self.rng = ctx.rng
        self.d = ctx.density
        self.tod = ctx.time_of_day
        self.lit = {"auto": ctx.graph.lamps_on, "on": True, "off": False}[p.lights]
        ctx.graph.lamps_on = self.lit
        self.sky = SKY[ctx.time_of_day]
        # warm electric light colour (emissive; derived from the scheme's accent so mood tints it a bit)
        self.lamp = mix(self.col("accent3"), "#fff4d2", 0.62)
        # the glow around a lamp leans orange: pure yellow turns green over a blue or mint wall
        self.halo_col = mix(self.lamp, "#ff9d58", 0.4)
        self.light_sx = -self.c.scheme.light_dir[
            0
        ]  # which way window light slants (+1 = to the right)
        self.win = WIN
        self.lk = (
            0.5 if ctx.time_of_day == "day" else 1.0
        )  # electric light reads weaker in daylight

    # -- colours -------------------------------------------------------------------------------------
    def col(self, c: str) -> str:
        return str(self.c.scheme.base(c))

    def dk(self, c: str, a: float) -> str:
        return darken(self.col(c), a)

    # -- generic drawing helpers -------------------------------------------------------------------------
    def block(
        self,
        plane: str,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        r: float = 0.0,
        *,
        tag: str,
        mat: str = "flat",
        elev: float = 0.0,
        lod: int = 0,
        k: float = 6.0,
        shade: float = 0.15,
    ) -> None:
        """Rounded block with a darker lower/right rim (cheap cel shading, light from upper left)."""
        base = self.col(fill)
        c = self.c
        c.rect(plane, x, y, w, h, darken(base, shade), r, tag=tag, material=mat, elev=elev, lod=lod)
        c.rect(plane, x, y, w - k, h - k, base, r, tag=tag, material=mat, shadow=False, lod=lod + 1)

    def disc(
        self,
        plane: str,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        fill: str,
        *,
        tag: str,
        mat: str = "flat",
        elev: float = 0.0,
        lod: int = 0,
        k: float = 4.0,
        shade: float = 0.15,
    ) -> None:
        base = self.col(fill)
        c = self.c
        c.ellipse(
            plane, cx, cy, rx, ry, darken(base, shade), tag=tag, material=mat, elev=elev, lod=lod
        )
        c.ellipse(
            plane, cx - k * 0.5, cy - k * 0.5, rx - k * 0.5, ry - k * 0.5, base,
            tag=tag, material=mat, shadow=False, lod=lod + 1,
        )  # fmt: skip

    def wall_shadow(
        self, x: float, y: float, w: float, h: float, r: float = 4.0, dx: float = 6.0, dy: float = 9.0, a: float = 0.15
    ) -> None:  # fmt: skip
        """Faint drop shadow of something hung on the wall (flat styles; paper derives its own from elev)."""
        self.c.rect(
            "back",
            x + dx,
            y + dy,
            w,
            h,
            "shadow",
            r,
            alpha=a,
            tag="wall_shadow",
            lod=2,
            shadow=False,
        )

    def floor_shadow(
        self, cx: float, y: float, rx: float, ry: float | None = None, a: float = 0.26
    ) -> None:
        ry = ry if ry is not None else max(8.0, rx * 0.05)
        c = self.c
        c.ellipse(
            "mid_back",
            cx,
            y,
            rx * 1.05,
            ry * 1.5,
            "shadow",
            alpha=a * 0.45,
            shadow=False,
            tag="shadow",
            lod=2,
        )
        c.ellipse(
            "mid_back",
            cx,
            y,
            rx * 0.93,
            ry,
            "shadow",
            alpha=a * 0.8,
            shadow=False,
            tag="shadow",
            lod=2,
        )

    def fade_rect(
        self,
        plane: str,
        x: float,
        y: float,
        w: float,
        h: float,
        color: str,
        a0: float,
        a1: float,
        angle: float = 90.0,
        tag: str = "light",
    ) -> None:
        c0, c1 = _rgba(self.col(color), a0), _rgba(self.col(color), a1)
        self.c.rect(
            plane, x, y, w, h, c0, material="emissive", shadow=False, tag=tag, lod=2,
            gradient=(c0, c1, angle),
        )  # fmt: skip

    def fade_poly(
        self,
        plane: str,
        pts: list[Pt],
        color: str,
        a0: float,
        a1: float,
        angle: float = 90.0,
        tag: str = "light",
    ) -> None:
        c0, c1 = _rgba(self.col(color), a0), _rgba(self.col(color), a1)
        self.c.poly(
            plane,
            pts,
            c0,
            material="emissive",
            shadow=False,
            tag=tag,
            lod=2,
            gradient=(c0, c1, angle),
        )

    def halo(
        self,
        plane: str,
        cx: float,
        cy: float,
        r: float,
        color: str | None = None,
        a: float = 0.30,
        flick: float = 0.0,
        phase: float = 0.0,
        sky: bool = False,
    ) -> None:
        """Soft round glow (peak alpha ~1.7 x ``a`` at the centre).

        Equal-alpha feathered discs whose alpha is an exact multiple of 1/255, radii following a smooth
        falloff: no visible banding.  Optionally a gently flickering core.
        """
        col = color or self.halo_col
        c = self.c
        if not sky:
            a *= self.lk
        peak = a * 1.7
        n = max(8, min(40, round(peak * 255 / 3)))
        al = max(1, round(peak * 255 / n)) / 255
        prev = 0.0
        for j in range(1, n + 1):
            rj = r * math.sqrt(1.0 - math.sqrt((n - j) / n))
            c.ellipse(
                plane,
                cx,
                cy,
                rj,
                rj,
                col,
                alpha=al,
                material="emissive",
                shadow=False,
                tag="glow",
                lod=2,
                glow=max(3.0, (rj - prev) * 1.3),
            )
            prev = rj
        if flick > 0:
            c.ellipse(
                plane, cx, cy, r * 0.4, r * 0.4, col, alpha=a * 0.34, material="emissive", shadow=False,
                tag="glow", lod=2, glow=r * 0.5,
                anim=Anim("flicker", amp=flick, freq=0.23 + 0.05 * (phase % 3), phase=phase),
            )  # fmt: skip

    # -- scenery pieces ------------------------------------------------------------------------------
    def wall(self, pattern: str = "none", dado: float = 0.0) -> None:
        c = self.c
        c.rect(
            "back", X0, Y0, X1 - X0, Y1 - Y0, "wall", tag="wall", material="paper", shadow=False,
            gradient=("wall", "wall2", 90.0),
        )  # fmt: skip
        if pattern == "stripes":
            x = -240.0
            while x < 1340:
                c.rect(
                    "back",
                    x,
                    Y0,
                    38,
                    FLOOR_Y - Y0,
                    "white",
                    alpha=0.11,
                    tag="wallpaper",
                    lod=2,
                    shadow=False,
                )
                x += 96
        elif pattern == "dots":
            cmds: list[tuple[Any, ...]] = []
            for row, y in enumerate(range(-120, int(FLOOR_Y) - 40, 84)):
                for xx in range(-260 + (row % 2) * 42, 1340, 84):
                    cmds += [
                        ("M", xx, y - 6),
                        ("L", xx + 6, y),
                        ("L", xx, y + 6),
                        ("L", xx - 6, y),
                        ("Z",),
                    ]
            c.path("back", cmds, "white", alpha=0.22, tag="wallpaper", lod=2, shadow=False)
        if dado > 0:
            top = FLOOR_Y - dado
            c.rect("back", X0, top, X1 - X0, dado + 60, "wall2", tag="dado", elev=2, lod=1)
            c.rect("back", X0, top - 14, X1 - X0, 16, "trim", tag="dado_rail", elev=3, lod=1)
            for xx in range(-420, 1560, 190):  # raised panels
                c.rect(
                    "back",
                    xx,
                    top + 28,
                    150,
                    dado - 64,
                    "wall",
                    6,
                    alpha=0.5,
                    tag="dado_panel",
                    lod=2,
                    shadow=False,
                )
        # soft shade under the ceiling, baseboard, and ambient occlusion where wall meets floor
        self.fade_rect("back", X0, Y0, X1 - X0, 560, "shadow", 0.16, 0.0, tag="ceiling_shade")
        self.fade_rect(
            "back", X0, FLOOR_Y - 170, X1 - X0, 170, "shadow", 0.0, 0.16, tag="wall_shadow"
        )
        c.rect(
            "back",
            X0,
            FLOOR_Y - 46,
            X1 - X0,
            110,
            "trim",
            tag="baseboard",
            material="wood",
            elev=3,
            lod=1,
        )
        c.rect(
            "back",
            X0,
            FLOOR_Y - 46,
            X1 - X0,
            8,
            "white",
            alpha=0.5,
            tag="baseboard",
            lod=2,
            shadow=False,
        )

    def floor(self, kind: str) -> None:
        """Wood planks / tiles in simple one-point perspective, running to the bottom of the stage."""
        c = self.c
        c.rect(
            "mid_back", X0, FLOOR_Y, X1 - X0, Y1 - FLOOR_Y, "floor", tag="floor", material="wood",
            gradient=("floor", "floor2", 90.0), shadow=False,
        )  # fmt: skip
        vx, vy = VP

        def x_at(i: float, y: float) -> float:  # board line i (spacing at the wall) at height y
            return vx + i * (y - vy) / (FLOOR_Y - vy)

        if kind == "wood":
            sp = 66.0
            n = 17
            base = self.col("floor")
            cmds: list[tuple[Any, ...]] = []
            for i in range(-n, n):
                tone = float(self.rng.uniform(-0.05, 0.05))
                col = lighten(base, -tone) if tone < 0 else darken(base, tone)
                if i % 2:
                    col = lighten(col, 0.03)
                c.poly(
                    "mid_back",
                    [(x_at(i * sp, FLOOR_Y), FLOOR_Y), (x_at((i + 1) * sp, FLOOR_Y), FLOOR_Y),
                     (x_at((i + 1) * sp, Y1), Y1), (x_at(i * sp, Y1), Y1)],
                    col, alpha=0.55, tag="floor_boards", lod=2, shadow=False,
                    gradient=(col, darken(col, 0.10), 90.0),
                )  # fmt: skip
                y = FLOOR_Y + float(self.rng.uniform(30, 90))  # staggered butt joints
                while y < Y1:
                    cmds += [("M", x_at(i * sp, y), y), ("L", x_at((i + 1) * sp, y), y)]
                    y += (y - vy) * float(self.rng.uniform(0.55, 1.15))
            for i in range(-n, n + 1):
                cmds += [("M", x_at(i * sp, FLOOR_Y), FLOOR_Y), ("L", x_at(i * sp, Y1), Y1)]
            c.path(
                "mid_back",
                cmds,
                None,
                stroke="shadow",
                sw=2.2,
                alpha=0.22,
                tag="floor_lines",
                lod=2,
                shadow=False,
            )
        elif kind == "tiles":  # checker, big squares
            sp = 118.0
            n = 9
            rows = [FLOOR_Y]
            while rows[-1] < Y1:
                rows.append(rows[-1] + (rows[-1] - vy) * 0.30)
            light = self.col("floor2")
            for j in range(len(rows) - 1):
                for i in range(-n, n):
                    if (i + j) % 2 == 0:
                        continue
                    y0, y1 = rows[j], rows[j + 1]
                    c.poly(
                        "mid_back",
                        [(x_at(i * sp, y0), y0), (x_at((i + 1) * sp, y0), y0),
                         (x_at((i + 1) * sp, y1), y1), (x_at(i * sp, y1), y1)],
                        light, alpha=0.7, tag="floor_tiles", lod=2, shadow=False,
                    )  # fmt: skip
        else:  # carpet tiles: faint squares
            sp = 130.0
            n = 9
            rows = [FLOOR_Y]
            while rows[-1] < Y1:
                rows.append(rows[-1] + (rows[-1] - vy) * 0.34)
            cmds = []
            for y in rows:
                cmds += [("M", X0, y), ("L", X1, y)]
            for i in range(-n, n + 1):
                cmds += [("M", x_at(i * sp, FLOOR_Y), FLOOR_Y), ("L", x_at(i * sp, Y1), Y1)]
            c.path(
                "mid_back",
                cmds,
                None,
                stroke="shadow",
                sw=2.0,
                alpha=0.14,
                tag="floor_lines",
                lod=2,
                shadow=False,
            )

    def window(self, covering: str = "curtains", bars: tuple[int, int] = (1, 1)) -> None:
        """Window on the back wall: frame, sky view (sun/moon, stars, clouds, skyline), bars, sill."""
        c = self.c
        x, y, w, h = self.win
        t = 22.0
        px0, py0, px1, py1 = x + t, y + t, x + w - t, y + h - t
        pw, ph = px1 - px0, py1 - py0
        c.rect("back", x, y, w, h, "trim", 10, tag="window", material="wood", elev=6)
        # a soft halo of sky light around the opening
        self.halo(
            "back",
            x + w / 2,
            y + h / 2,
            w * 0.95,
            self.col("glow"),
            a=0.10 if self.tod != "night" else 0.05,
            sky=True,
        )
        c.rect(
            "back", px0, py0, pw, ph, "sky_top", tag="window_glass", material="emissive", shadow=False, lod=1,
            gradient=("sky_top", "sky_bottom", 90.0),
        )  # fmt: skip
        sk = self.sky
        night = self.tod == "night"
        # sun / moon
        pos = {
            "dawn": (0.30, 0.74, 0.12),
            "day": (0.70, 0.27, 0.11),
            "dusk": (0.70, 0.72, 0.13),
            "night": (0.34, 0.26, 0.085),
        }[self.tod]
        sx, sy, sr = px0 + pos[0] * pw, py0 + pos[1] * ph, pos[2] * pw
        sc = "#f4f6ff" if night else sk.glow
        c.ellipse(
            "back",
            sx,
            sy,
            sr * 2.3,
            sr * 2.3,
            sc,
            alpha=0.16,
            material="emissive",
            shadow=False,
            tag="sun_halo",
            lod=2,
        )
        c.ellipse(
            "back",
            sx,
            sy,
            sr,
            sr,
            sc,
            material="emissive",
            shadow=False,
            glow=sr * 1.2,
            tag="moon" if night else "sun",
            lod=1,
        )
        if night:
            c.ellipse(
                "back",
                sx + sr * 0.3,
                sy - sr * 0.15,
                sr * 0.22,
                sr * 0.22,
                "#d4dcf2",
                material="emissive",
                shadow=False,
                tag="moon",
                lod=2,
            )
            c.ellipse(
                "back",
                sx - sr * 0.35,
                sy + sr * 0.25,
                sr * 0.15,
                sr * 0.15,
                "#d4dcf2",
                material="emissive",
                shadow=False,
                tag="moon",
                lod=2,
            )
            for i in range(14):
                stx = float(self.rng.uniform(px0 + 12, px1 - 12))
                sty = float(self.rng.uniform(py0 + 10, py0 + ph * 0.6))
                rr = float(self.rng.uniform(1.6, 3.4))
                s = c.ellipse(
                    "back",
                    stx,
                    sty,
                    rr,
                    rr,
                    "#ffffff",
                    material="emissive",
                    shadow=False,
                    tag="star",
                    lod=2,
                )
                if i % 2:
                    s.anim = Anim(
                        "twinkle",
                        amp=0.7,
                        freq=float(self.rng.uniform(0.2, 0.6)),
                        phase=float(self.rng.uniform(0, 6.28)),
                    )
        else:
            cc = "#ffffff" if self.tod == "day" else mix("#ffffff", sk.glow, 0.5)
            for i in range(3):
                cx = px0 + pw * (0.22 + 0.28 * i + float(self.rng.uniform(-0.05, 0.05)))
                cy = py0 + ph * (0.14 + 0.2 * ((i * 2) % 3) / 2 + float(self.rng.uniform(0, 0.06)))
                sz = float(self.rng.uniform(0.42, 0.62)) * pw / 296
                for dx, dy, rx, ry in (
                    (0, 0, 60, 24),
                    (-40, 8, 40, 18),
                    (44, 9, 44, 19),
                    (-10, -14, 38, 20),
                ):
                    c.ellipse(
                        "back", cx + dx * sz, cy + dy * sz, rx * sz, ry * sz, cc, alpha=0.9, material="emissive",
                        shadow=False, tag="cloud", lod=2,
                    )  # fmt: skip
        self.view(px0, py0, px1, py1)
        # glass sheen
        c.poly(
            "back", [(px0, py0 + ph * 0.55), (px0 + pw * 0.34, py0), (px0 + pw * 0.52, py0), (px0, py0 + ph * 0.8)],
            "#ffffff", alpha=0.13, material="emissive", shadow=False, tag="glass_sheen", lod=2,
        )  # fmt: skip
        c.poly(
            "back", [(px1 - pw * 0.5, py1), (px1, py1 - ph * 0.45), (px1, py1 - ph * 0.3), (px1 - pw * 0.3, py1)],
            "#ffffff", alpha=0.10, material="emissive", shadow=False, tag="glass_sheen", lod=2,
        )  # fmt: skip
        # bars
        for i in range(bars[0]):
            bx = px0 + pw * (i + 1) / (bars[0] + 1)
            c.rect("back", bx - 6, py0, 12, ph, "trim", tag="window_bar", material="wood", lod=0)
        for j in range(bars[1]):
            by = py0 + ph * (j + 1) / (bars[1] + 1) * (0.52 / 0.5 if bars[1] == 1 else 1.0)
            c.rect("back", px0, by - 6, pw, 12, "trim", tag="window_bar", material="wood", lod=0)
        c.rect(
            "back", px0, py0, pw, 12, "shadow", alpha=0.18, tag="window_shadow", lod=2, shadow=False
        )
        # sill
        c.rect(
            "back", x - 24, y + h - 8, w + 48, 30, "trim", 8, tag="sill", material="wood", elev=7
        )
        c.rect(
            "back",
            x - 24,
            y + h + 12,
            w + 48,
            6,
            "shadow",
            alpha=0.18,
            tag="sill",
            lod=2,
            shadow=False,
        )
        if covering == "curtains":
            self.curtains(x, y, w, h)
        elif covering == "blinds":
            self.blinds(px0, py0, pw, ph)
        elif covering == "valance":
            self.valance(x, y, w)

    def view(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """What is seen through the glass (kept inside the pane: no clipping needed)."""
        c = self.c
        w, h = x1 - x0, y1 - y0
        night = self.tod == "night"
        far = mix(self.col("sky_bottom"), self.col("stone"), 0.40 if not night else 0.18)
        near = mix(self.col("sky_bottom"), self.col("stone2"), 0.70 if not night else 0.32)
        if night:
            far = darken(far, 0.25)
            near = darken(near, 0.45)
        if self.p.view == "city":
            for layer, (colr, hmin, hmax, wmin, wmax) in enumerate(
                ((far, 70, 190, 34, 62), (near, 40, 130, 40, 74))
            ):
                xx = x0 - 10 + layer * 18
                while xx < x1:
                    bw = float(self.rng.uniform(wmin, wmax))
                    bh = float(self.rng.uniform(hmin, hmax)) * (h / 440)
                    bw = min(bw, x1 - xx)
                    c.rect(
                        "back",
                        xx,
                        y1 - bh,
                        bw,
                        bh,
                        colr,
                        tag="skyline",
                        material="emissive",
                        shadow=False,
                        lod=1,
                    )
                    if night:  # a few lit windows
                        for _ in range(int(bw * bh / 1500) + 1):
                            wx = xx + float(self.rng.uniform(4, max(5, bw - 9)))
                            wy = y1 - bh + float(self.rng.uniform(6, max(7, bh - 10)))
                            c.rect(
                                "back",
                                wx,
                                wy,
                                5,
                                7,
                                "window",
                                material="emissive",
                                shadow=False,
                                tag="city_light",
                                lod=2,
                                alpha=0.9,
                            )
                    xx += bw + float(self.rng.uniform(0, 5))
        elif self.p.view == "garden":
            hill_far = mix(far, self.col("foliage3"), 0.35)
            hill_near = mix(near, self.col("foliage2"), 0.5)
            for colr, base, amp in ((hill_far, 0.70, 0.05), (hill_near, 0.82, 0.06)):
                pts = [(x0, y1), (x0, y0 + h * base)]
                for k in range(1, 7):
                    pts.append(
                        (x0 + w * k / 6, y0 + h * (base - amp * math.sin(k * 1.9 + base * 9)))
                    )
                pts.append((x1, y1))
                c.poly(
                    "back",
                    pts,
                    colr,
                    curve=False,
                    smooth=0.3,
                    tag="hills",
                    material="emissive",
                    shadow=False,
                    lod=1,
                )
            for tx, th in ((0.22, 0.20), (0.82, 0.25), (0.6, 0.15)):
                cx, by = x0 + tx * w, y1 - h * 0.08
                c.rect(
                    "back",
                    cx - 3,
                    by - th * h * 0.5,
                    6,
                    th * h * 0.5,
                    darken(hill_near, 0.2),
                    tag="tree",
                    material="emissive",
                    shadow=False,
                    lod=1,
                )
                c.ellipse(
                    "back",
                    cx,
                    by - th * h * 0.62,
                    th * w * 0.32,
                    th * h * 0.42,
                    hill_near,
                    tag="tree",
                    material="emissive",
                    shadow=False,
                    lod=1,
                )
        else:  # plain sky: a distant ridge
            ridge = [(x0, y1), (x0, y1 - h * 0.1)]
            for k in range(1, 8):
                ridge.append((x0 + w * k / 7, y1 - h * (0.1 + 0.05 * math.sin(k * 2.3))))
            ridge.append((x1, y1))
            c.poly(
                "back",
                ridge,
                far,
                curve=False,
                smooth=0.3,
                tag="hills",
                material="emissive",
                shadow=False,
                lod=1,
            )

    def curtains(self, x: float, y: float, w: float, h: float) -> None:
        c = self.c
        rod_y = y - 38
        c.rect(
            "back",
            x - 50,
            rod_y,
            w + 100,
            9,
            "wood2",
            4,
            tag="curtain_rod",
            material="wood",
            elev=4,
            lod=1,
        )
        for ex in (x - 56, x + w + 56):
            c.ellipse("back", ex, rod_y + 4.5, 11, 11, "wood2", tag="curtain_rod", elev=4, lod=1)
        for side in (-1, 1):
            if side < 0:
                ox, ix = x - 38, x + 38  # outer, inner edge x at the top
            else:
                ox, ix = x + w + 38, x + w - 38
            top, bot, tie = rod_y + 4, y + h + 70, y + h * 0.68
            sgn = 1 if side < 0 else -1
            pts: list[Pt] = [
                (ox, top), (ix, top), (ix - sgn * 4, y + h * 0.3), (ix - sgn * 24, tie),
                (ix + sgn * 6, y + h * 0.88), (ix + sgn * 10, bot), (ox - sgn * 6, bot),
                (ox - sgn * 4, y + h * 0.88), (ox + sgn * 10, tie), (ox + sgn * 2, y + h * 0.3),
            ]  # fmt: skip
            phase = 0.0 if side < 0 else 1.7
            pivot = ((ox + ix) / 2, top)

            def a(amp: float = 0.9, ph: float = phase, pv: Pt = pivot) -> Anim:
                return Anim("sway", amp=amp, freq=0.16, phase=ph, pivot=pv)

            c.poly(
                "back",
                pts,
                "curtain",
                smooth=0.12,
                tag="curtain",
                material="fabric",
                elev=8,
                lod=0,
                anim=a(),
            )
            for k, f in enumerate((0.28, 0.55, 0.8)):
                fx_top = ox + (ix - ox) * f
                fx_bot = (ox - sgn * 6) + ((ix + sgn * 10) - (ox - sgn * 6)) * f
                c.add(
                    "back", Line((fx_top, top + 6), (fx_bot, bot - 6), 6), None,
                    stroke="shadow" if k % 2 == 0 else "white", sw=7 if k % 2 == 0 else 5,
                    alpha=0.16, shadow=False, tag="curtain_fold", lod=2, anim=a(),
                )  # fmt: skip
            tx = (ox + ix) / 2 - sgn * 8
            c.rect(
                "back",
                tx - 34,
                tie - 8,
                68,
                16,
                "accent3",
                8,
                tag="tieback",
                lod=2,
                anim=a(),
                elev=2,
            )

    def valance(self, x: float, y: float, w: float) -> None:
        """Short scalloped café valance over the window."""
        c = self.c
        c.rect(
            "back",
            x - 16,
            y - 4,
            w + 32,
            8,
            "wood2",
            4,
            tag="curtain_rod",
            material="wood",
            elev=4,
            lod=1,
        )
        n = 5
        sw = (w + 20) / n
        pts: list[Pt] = [(x - 10, y), (x + w + 10, y)]
        for i in range(n - 1, -1, -1):
            cx = x - 10 + sw * (i + 0.5)
            for k in range(9):
                a = math.radians(180 * k / 8)
                pts.append((cx + (sw / 2 - 1) * math.cos(a), y + 50 + 30 * math.sin(a)))
        c.poly("back", pts, "curtain", tag="curtain", material="fabric", elev=6, lod=0)
        for i in range(n):
            cx = x - 10 + sw * (i + 0.5)
            c.ellipse(
                "back",
                cx,
                y + 36,
                sw * 0.3,
                14,
                "accent",
                alpha=0.35,
                tag="curtain_fold",
                lod=2,
                shadow=False,
            )

    def blinds(self, px0: float, py0: float, pw: float, ph: float) -> None:
        c = self.c
        n = 9
        slat = ph * 0.52 / n
        c.rect(
            "back",
            px0,
            py0,
            pw,
            n * slat,
            mix(self.col("curtain"), self.col("shadow"), 0.45),
            tag="blind_shade",
            lod=1,
            shadow=False,
        )
        for i in range(n):
            c.rect(
                "back",
                px0,
                py0 + i * slat,
                pw,
                slat - 4,
                "curtain",
                3,
                tag="blind",
                material="metal",
                lod=1,
                elev=2,
            )
        c.rect("back", px0 - 6, py0 - 10, pw + 12, 16, "metal", 5, tag="blind_head", elev=4, lod=1)
        cx = px0 + pw - 18
        c.line(
            "back", (cx, py0 + n * slat), (cx, py0 + ph * 0.7), 3, "metal", tag="blind_cord", lod=2
        )
        c.ellipse("back", cx, py0 + ph * 0.7 + 6, 7, 9, "accent3", tag="blind_cord", lod=2)

    def window_light(self, strength: float = 1.0) -> None:
        """A parallelogram of light thrown on the floor in front of the window (behind furniture)."""
        sx = self.light_sx * 150
        x, _y, w, _h = self.win
        a = (0.30 if self.tod != "night" else 0.14) * strength
        self.fade_poly(
            "mid_back",
            [(x + 20, FLOOR_Y + 20), (x + w - 20, FLOOR_Y + 20), (x + w + sx + 90, FLOOR_Y + 620), (x + sx - 30, FLOOR_Y + 620)],
            "glow", a, 0.0, 90.0, tag="window_light",
        )  # fmt: skip

    def door(
        self, x: float = -96.0, w: float = 272.0, top: float = 372.0, glass: bool = False
    ) -> None:
        c = self.c
        bot = FLOOR_Y + 90
        c.rect(
            "back",
            x - 24,
            top - 24,
            w + 48,
            bot - top + 24,
            "trim",
            6,
            tag="door_frame",
            material="wood",
            elev=4,
            lod=0,
        )
        c.rect("back", x, top, w, bot - top, "door", 4, tag="door", material="wood", elev=2, lod=0)
        shade = self.dk("door", 0.12)
        for py, ph in ((top + 44, 250), (top + 330, 340)):
            c.rect("back", x + 34, py, w - 68, ph, shade, 8, tag="door_panel", lod=1, shadow=False)
            if glass and py == top + 44:
                c.rect(
                    "back",
                    x + 48,
                    py + 14,
                    w - 96,
                    ph - 28,
                    "glass",
                    6,
                    tag="door_glass",
                    lod=1,
                    shadow=False,
                    alpha=0.85,
                )
                c.poly(
                    "back",
                    [
                        (x + 70, py + ph - 20),
                        (x + 130, py + 14),
                        (x + 152, py + 14),
                        (x + 92, py + ph - 20),
                    ],
                    "white",
                    alpha=0.35,
                    tag="door_glass",
                    lod=2,
                    shadow=False,
                )
            else:
                c.rect(
                    "back",
                    x + 46,
                    py + 12,
                    w - 92,
                    ph - 24,
                    "door",
                    6,
                    tag="door_panel",
                    lod=2,
                    shadow=False,
                )
        hx, hy = x + w - 46, top + 330  # handle
        c.rect("back", hx - 12, hy - 36, 24, 74, "metal", 10, tag="door_handle", elev=3, lod=1)
        c.ellipse("back", hx, hy, 15, 15, "metal", tag="door_handle", elev=4, lod=0)
        c.ellipse(
            "back", hx - 4, hy - 4, 6, 6, "white", alpha=0.5, tag="door_handle", lod=2, shadow=False
        )
        self.fade_rect(
            "back", x + w + 24, top - 24, 50, bot - top, "shadow", 0.16, 0.0, 0.0, tag="door_shadow"
        )

    # -- props --------------------------------------------------------------------------------------------
    def leaf(
        self, plane: str, bx: float, by: float, length: float, width: float, ang: float, fill: str,
        anim: Anim | None = None,
    ) -> None:  # fmt: skip
        """A leaf growing from (bx, by) towards angle ``ang`` (degrees, 0 = up, + = clockwise)."""
        a = math.radians(ang)
        ux, uy = math.sin(a), -math.cos(a)
        px, py = -uy, ux

        def pt(f: float, g: float) -> Pt:
            return (bx + ux * length * f + px * width * g, by + uy * length * f + py * width * g)

        pts = [pt(0, 0), pt(0.25, -0.5), pt(0.62, -0.46), pt(1.0, 0), pt(0.62, 0.46), pt(0.25, 0.5)]
        self.c.poly(plane, pts, fill, curve=True, tag="leaf", material="grass", lod=0, anim=anim)

    def plant(
        self,
        x: float,
        base_y: float,
        s: float = 1.0,
        *,
        plane: str = "mid_back",
        n: int = 7,
        pot: str = "pot",
        spiky: bool = False,
    ) -> None:
        c = self.c
        ph, wt, wb = 78 * s, 84 * s, 58 * s
        sy = base_y - ph - 6 * s
        for i in range(n):
            f = (i - (n - 1) / 2) / max(1, (n - 1) / 2)
            if spiky:
                ang = f * 24 + float(self.rng.uniform(-5, 5))
                ln = (210 + 120 * (1 - abs(f))) * s * float(self.rng.uniform(0.85, 1.1))
                wd = 40 * s
            else:
                ang = f * 62 + float(self.rng.uniform(-8, 8))
                ln = (150 + 90 * (1 - abs(f)) ** 0.8) * s * float(self.rng.uniform(0.85, 1.1))
                wd = 54 * s
            tone = ("foliage", "foliage2", "foliage3")[(i * 2 + int(self.rng.integers(0, 2))) % 3]
            self.leaf(
                plane, x + f * 8 * s, sy, ln, wd, ang, tone,
                Anim("sway", amp=float(self.rng.uniform(1.2, 2.4)), freq=float(self.rng.uniform(0.16, 0.28)),
                     phase=float(self.rng.uniform(0, 6.28)), pivot=(x + f * 8 * s, sy)),
            )  # fmt: skip
        c.poly(
            plane, [(x - wt / 2, base_y - ph), (x + wt / 2, base_y - ph), (x + wb / 2, base_y), (x - wb / 2, base_y)],
            pot, smooth=0.1, tag="pot", material="wood", elev=5, lod=0,
        )  # fmt: skip
        c.rect(
            plane,
            x - wt / 2 - 5 * s,
            base_y - ph - 10 * s,
            wt + 10 * s,
            20 * s,
            lighten(self.col(pot), 0.12),
            6 * s,
            tag="pot_rim",
            elev=5,
            lod=1,
        )
        c.ellipse(
            plane,
            x - wt * 0.12,
            base_y - ph * 0.5,
            wt * 0.18,
            ph * 0.28,
            "white",
            alpha=0.14,
            tag="pot_shine",
            lod=2,
            shadow=False,
        )

    def floor_lamp(
        self, x: float, base_y: float, height: float = 560.0, plane: str = "mid_back"
    ) -> None:
        c = self.c
        top = base_y - height
        self.floor_shadow(x, base_y + 4, 54, 10)
        c.ellipse(plane, x, base_y - 6, 40, 11, "metal", tag="lamp_base", elev=3, lod=1)
        c.rect(plane, x - 5, top, 10, height - 6, "metal", 4, tag="lamp_pole", elev=2, lod=0)
        sh = [(x - 30, top - 74), (x + 30, top - 74), (x + 52, top + 8), (x - 52, top + 8)]
        if self.lit:
            self.halo(plane, x, top - 30, 190, self.halo_col, 0.22, flick=0.18, phase=x * 0.01)
            self.fade_poly(
                "back",
                [
                    (x - 50, top + 10),
                    (x + 50, top + 10),
                    (x + 250, top + 520),
                    (x - 250, top + 520),
                ],
                self.halo_col,
                0.20 * self.lk,
                0.0,
                tag="lamp_cone",
            )
            self.fade_poly(
                "back",
                [
                    (x - 28, top - 76),
                    (x + 28, top - 76),
                    (x + 120, top - 330),
                    (x - 120, top - 330),
                ],
                self.halo_col,
                0.12 * self.lk,
                0.0,
                270.0,
                tag="lamp_cone",
            )
            c.poly(plane, sh, self.lamp, smooth=0.06, tag="lampshade", material="emissive", shadow=False, glow=70, lod=0,
                   gradient=(lighten(self.lamp, 0.35), self.lamp, 90.0))  # fmt: skip
        else:
            c.poly(
                plane, sh, "shade", smooth=0.06, tag="lampshade", material="fabric", elev=4, lod=0
            )
            c.poly(
                plane,
                [(x - 26, top - 70), (x - 8, top - 70), (x - 16, top + 6), (x - 44, top + 6)],
                "white",
                alpha=0.25,
                tag="lampshade",
                lod=2,
                shadow=False,
            )

    def table_lamp(self, x: float, base_y: float, s: float = 1.0, plane: str = "mid_back") -> None:
        c = self.c
        c.ellipse(plane, x, base_y - 8 * s, 24 * s, 8 * s, "wood2", tag="lamp_base", elev=3, lod=1)
        c.poly(
            plane,
            [
                (x - 15 * s, base_y - 80 * s),
                (x + 15 * s, base_y - 80 * s),
                (x + 22 * s, base_y - 8 * s),
                (x - 22 * s, base_y - 8 * s),
            ],
            "accent2",
            smooth=0.2,
            tag="lamp_stem",
            material="metal",
            elev=3,
            lod=0,
        )
        sh = [
            (x - 28 * s, base_y - 150 * s),
            (x + 28 * s, base_y - 150 * s),
            (x + 46 * s, base_y - 78 * s),
            (x - 46 * s, base_y - 78 * s),
        ]
        if self.lit:
            self.halo(
                plane, x, base_y - 110 * s, 170 * s, self.halo_col, 0.26, flick=0.2, phase=x * 0.013
            )
            self.fade_poly(
                "back",
                [
                    (x - 44 * s, base_y - 76 * s),
                    (x + 44 * s, base_y - 76 * s),
                    (x + 210 * s, base_y + 160 * s),
                    (x - 210 * s, base_y + 160 * s),
                ],
                self.halo_col,
                0.18 * self.lk,
                0.0,
                tag="lamp_cone",
            )
            c.poly(plane, sh, self.lamp, smooth=0.06, tag="lampshade", material="emissive", shadow=False, glow=44 * s, lod=0,
                   gradient=(lighten(self.lamp, 0.35), self.lamp, 90.0))  # fmt: skip
        else:
            c.poly(
                plane, sh, "shade", smooth=0.06, tag="lampshade", material="fabric", elev=4, lod=0
            )

    def pendant(self, x: float, drop: float = 300.0, w: float = 120.0, kind: str = "dome") -> None:
        c = self.c
        c.line("back", (x, Y0), (x, drop - 6), 4, "ink", tag="cord", lod=0)
        top = drop
        if kind == "dome":
            pts: list[Pt] = [
                (x - w * 0.16, top - 24),
                (x + w * 0.16, top - 24),
                (x + w * 0.5, top + 38),
                (x - w * 0.5, top + 38),
            ]
            c.poly(
                "back", pts, "accent", smooth=0.28, tag="pendant", material="metal", elev=6, lod=0
            )
            c.rect(
                "back",
                x - w * 0.5,
                top + 34,
                w,
                8,
                lighten(self.col("accent"), 0.2),
                4,
                tag="pendant",
                elev=6,
                lod=1,
            )
        elif kind == "globe":  # paper lantern
            fillc = self.lamp if self.lit else "shade"
            cy0 = top + 40
            c.ellipse(
                "back",
                x,
                cy0,
                w * 0.42,
                w * 0.5,
                fillc,
                tag="pendant",
                material="emissive" if self.lit else "paper",
                shadow=not self.lit,
                elev=6,
                lod=0,
            )
            rib = "accent3" if self.lit else "wall2"
            for k in (0.3, 0.64):  # paper ribs
                ry_ = w * 0.5 * math.sqrt(1 - k * k)
                for sg in (-1, 1):
                    x_ = x + sg * w * 0.42 * k
                    c.path(
                        "back",
                        [
                            ("M", x_, cy0 - ry_),
                            ("Q", x + sg * w * 0.42 * k * 1.45, cy0, x_, cy0 + ry_),
                        ],
                        None,
                        stroke=rib,
                        sw=3,
                        alpha=0.55,
                        tag="lantern_rib",
                        lod=2,
                        shadow=False,
                    )
            c.rect("back", x - 14, top - 12, 28, 12, "ink", 3, tag="pendant", lod=1)
        elif kind == "bar":  # slim linear light on two cords
            c.line("back", (x - w * 0.42, Y0), (x - w * 0.42, top), 3, "ink", tag="cord", lod=0)
            c.rect(
                "back",
                x - w * 0.5,
                top,
                w,
                22,
                "metal",
                8,
                tag="pendant",
                material="metal",
                elev=6,
                lod=0,
            )
            c.rect(
                "back",
                x - w * 0.46,
                top + 14,
                w * 0.92,
                8,
                self.lamp if self.lit else "white",
                4,
                tag="pendant",
                material="emissive",
                shadow=False,
                lod=1,
                alpha=1.0 if self.lit else 0.6,
            )
        if self.lit:
            cy = top + (50 if kind != "bar" else 30)
            self.halo(
                "back",
                x,
                cy,
                w * (1.25 if kind == "bar" else 2.4),
                self.halo_col,
                0.26,
                flick=0.16,
                phase=x * 0.007,
            )
            self.fade_poly(
                "back",
                [
                    (x - w * 0.42, cy - 8),
                    (x + w * 0.42, cy - 8),
                    (x + w * 1.4, cy + 600),
                    (x - w * 1.4, cy + 600),
                ],
                self.halo_col,
                0.16 * self.lk,
                0.0,
                tag="lamp_cone",
            )
            if kind == "dome":
                c.ellipse(
                    "back",
                    x,
                    top + 40,
                    w * 0.36,
                    10,
                    self.lamp,
                    material="emissive",
                    shadow=False,
                    glow=30,
                    tag="bulb",
                    lod=1,
                )

    def frame(
        self, x: float, y: float, w: float, h: float, art: str, *, border: str = "wood2"
    ) -> None:
        c = self.c
        self.wall_shadow(x, y, w, h)
        c.rect("back", x, y, w, h, border, 4, tag="frame", material="wood", elev=4, lod=0)
        m = 9.0
        c.rect(
            "back",
            x + m,
            y + m,
            w - 2 * m,
            h - 2 * m,
            "paper",
            2,
            tag="frame_mat",
            lod=1,
            shadow=False,
        )
        ax, ay, aw, ah = x + m + 8, y + m + 8, w - 2 * m - 16, h - 2 * m - 16
        if art == "sun":
            c.rect(
                "back",
                ax,
                ay,
                aw,
                ah,
                "accent3",
                tag="art",
                lod=2,
                shadow=False,
                gradient=("accent3", "accent", 90.0),
            )
            c.ellipse(
                "back",
                ax + aw * 0.5,
                ay + ah * 0.62,
                aw * 0.22,
                aw * 0.22,
                "paper",
                tag="art",
                lod=2,
                shadow=False,
                alpha=0.9,
            )
            c.rect(
                "back", ax, ay + ah * 0.68, aw, ah * 0.32, "accent2", tag="art", lod=2, shadow=False
            )
        elif art == "mountains":
            c.rect("back", ax, ay, aw, ah, "glass", tag="art", lod=2, shadow=False)
            c.poly(
                "back",
                [(ax, ay + ah), (ax + aw * 0.35, ay + ah * 0.3), (ax + aw * 0.62, ay + ah)],
                "accent2",
                tag="art",
                lod=2,
                shadow=False,
            )
            c.poly(
                "back",
                [(ax + aw * 0.3, ay + ah), (ax + aw * 0.7, ay + ah * 0.4), (ax + aw, ay + ah)],
                "foliage2",
                tag="art",
                lod=2,
                shadow=False,
            )
        elif art == "abstract":
            c.rect("back", ax, ay, aw, ah, "fabric2", tag="art", lod=2, shadow=False)
            c.ellipse(
                "back",
                ax + aw * 0.35,
                ay + ah * 0.4,
                aw * 0.26,
                aw * 0.26,
                "accent",
                tag="art",
                lod=2,
                shadow=False,
            )
            c.rect(
                "back",
                ax + aw * 0.45,
                ay + ah * 0.35,
                aw * 0.4,
                ah * 0.5,
                "accent2",
                4,
                tag="art",
                lod=2,
                shadow=False,
                alpha=0.9,
            )
            c.ellipse(
                "back",
                ax + aw * 0.7,
                ay + ah * 0.25,
                aw * 0.12,
                aw * 0.12,
                "accent3",
                tag="art",
                lod=2,
                shadow=False,
            )
        else:  # leaf
            c.rect("back", ax, ay, aw, ah, "fabric2", tag="art", lod=2, shadow=False)
            for i, ang in enumerate((-35, 0, 35)):
                self.leaf(
                    "back",
                    ax + aw / 2,
                    ay + ah * 0.92,
                    ah * 0.7,
                    aw * 0.4,
                    ang,
                    ("foliage", "foliage2", "foliage3")[i],
                )

    def books(
        self, x: float, base_y: float, n: int, hmax: float = 100.0, plane: str = "back"
    ) -> float:
        c = self.c
        cols = ("accent", "accent2", "accent3", "fabric", "wood2", "paper", "ink")
        xx = x
        for _ in range(n):
            bw = float(self.rng.uniform(16, 30))
            bh = float(self.rng.uniform(0.62, 1.0)) * hmax
            col = cols[int(self.rng.integers(0, len(cols)))]
            c.rect(plane, xx, base_y - bh, bw, bh, col, 2, tag="book", elev=2, lod=1)
            c.rect(
                plane,
                xx + 4,
                base_y - bh + 10,
                bw - 8,
                4,
                "white",
                alpha=0.35,
                tag="book",
                lod=2,
                shadow=False,
            )
            xx += bw + 2
        return xx

    def shelf(self, x: float, y: float, w: float) -> None:
        c = self.c
        self.wall_shadow(x, y + 4, w, 14, 3, 4, 10, 0.13)
        c.rect("back", x, y, w, 14, "wood", 3, tag="shelf", material="wood", elev=5, lod=0)
        for bx in (x + 18, x + w - 30):
            c.poly(
                "back",
                [(bx, y + 14), (bx + 12, y + 14), (bx + 12, y + 44)],
                "wood2",
                tag="shelf_bracket",
                elev=3,
                lod=1,
            )

    def rug_persp(self, y0: float, y1: float, w0: float, w1: float, cx: float = 540.0) -> None:
        """Rectangular rug in perspective with border and a centre diamond."""
        c = self.c

        def quad(inset: float) -> list[Pt]:
            ya, yb = y0 + inset * 0.6, y1 - inset
            wa = w0 + (w1 - w0) * (ya - y0) / (y1 - y0) - 2 * inset
            wb = w0 + (w1 - w0) * (yb - y0) / (y1 - y0) - 2 * inset
            return [(cx - wa / 2, ya), (cx + wa / 2, ya), (cx + wb / 2, yb), (cx - wb / 2, yb)]

        c.poly(
            "mid_back", quad(0), "rug2", smooth=0.03, tag="rug", material="fabric", elev=2, lod=1
        )
        c.poly(
            "mid_back",
            quad(18),
            "rug",
            smooth=0.03,
            tag="rug",
            material="fabric",
            lod=2,
            shadow=False,
        )
        c.poly("mid_back", quad(36), "rug2", smooth=0.03, alpha=0.5, tag="rug", lod=2, shadow=False)
        c.poly("mid_back", quad(52), "rug", smooth=0.03, tag="rug", lod=2, shadow=False)
        cy = (y0 + y1) / 2
        wm = (w0 + w1) / 2
        c.poly(
            "mid_back",
            [(cx, cy - 60), (cx + wm * 0.26, cy), (cx, cy + 60), (cx - wm * 0.26, cy)],
            "rug2",
            smooth=0.12,
            alpha=0.8,
            tag="rug_motif",
            lod=2,
            shadow=False,
        )
        c.poly(
            "mid_back",
            [(cx, cy - 30), (cx + wm * 0.12, cy), (cx, cy + 30), (cx - wm * 0.12, cy)],
            "rug",
            smooth=0.12,
            tag="rug_motif",
            lod=2,
            shadow=False,
        )

    def cabinet(
        self, x: float, y: float, w: float, h: float, fill: str, *, doors: int = 1, drawers: int = 0,
        plane: str = "mid_back", elev: float = 8.0, r: float = 8.0, tag: str = "cabinet",
    ) -> None:  # fmt: skip
        base = self.col(fill)
        c = self.c
        self.block(plane, x, y, w, h, fill, r, tag=tag, mat="wood", elev=elev, k=6)
        if drawers:
            dh = (h - 16) / drawers
            for i in range(1, drawers):
                c.line(
                    plane,
                    (x + 4, y + 8 + i * dh - 4),
                    (x + w - 10, y + 8 + i * dh - 4),
                    3,
                    darken(base, 0.35),
                    tag=tag + "_drawer",
                    lod=0,
                )
            for i in range(drawers):
                dy = y + 8 + i * dh
                c.rect(
                    plane,
                    x + 9,
                    dy,
                    w - 24,
                    dh - 8,
                    lighten(base, 0.06),
                    5,
                    tag=tag + "_drawer",
                    lod=2,
                    shadow=False,
                )
                c.rect(
                    plane,
                    x + w / 2 - 26,
                    dy + dh / 2 - 8,
                    40,
                    8,
                    "metal",
                    4,
                    tag="handle",
                    lod=2,
                    shadow=False,
                )
        else:
            dw = (w - 6) / doors
            for i in range(1, doors):
                c.line(
                    plane,
                    (x + i * dw, y + 6),
                    (x + i * dw, y + h - 12),
                    3,
                    darken(base, 0.35),
                    tag=tag + "_door",
                    lod=0,
                )
            for i in range(doors):
                dx = x + i * dw
                c.rect(
                    plane,
                    dx + 9,
                    y + 9,
                    dw - 18,
                    h - 24,
                    lighten(base, 0.06),
                    5,
                    tag=tag + "_door",
                    lod=2,
                    shadow=False,
                )
                hx = dx + dw - 22 if i % 2 == 0 else dx + 22
                c.rect(
                    plane, hx - 3, y + h * 0.3, 6, 44, "metal", 3, tag="handle", lod=2, shadow=False
                )

    def clock(self, x: float, y: float, rad: float) -> None:
        c = self.c
        c.ellipse(
            "back",
            x + 6,
            y + 9,
            rad,
            rad,
            "shadow",
            alpha=0.15,
            tag="wall_shadow",
            lod=2,
            shadow=False,
        )
        self.disc("back", x, y, rad, rad, "trim", tag="clock", mat="metal", elev=5, lod=0, k=5)
        c.ellipse("back", x, y, rad - 9, rad - 9, "paper", tag="clock_face", lod=1, shadow=False)
        for k in range(12):
            a = math.radians(k * 30)
            r0, r1 = rad - 13, rad - (21 if k % 3 == 0 else 17)
            c.line(
                "back",
                (x + r0 * math.sin(a), y - r0 * math.cos(a)),
                (x + r1 * math.sin(a), y - r1 * math.cos(a)),
                3 if k % 3 == 0 else 2,
                "ink",
                tag="clock_tick",
                lod=2,
            )
        for ang, ln, wd in ((-50, 0.5, 5), (70, 0.72, 4)):
            a = math.radians(ang)
            c.line(
                "back",
                (x, y),
                (x + rad * ln * math.sin(a), y - rad * ln * math.cos(a)),
                wd,
                "ink",
                tag="clock_hand",
                lod=0,
            )
        c.line(
            "back",
            (x, y + 8),
            (x, y - rad * 0.78),
            2,
            "accent",
            tag="clock_hand",
            lod=2,
            anim=Anim("spin", amp=1.0, freq=1 / 60, pivot=(x, y)),
        )
        c.ellipse("back", x, y, 4.5, 4.5, "ink", tag="clock_hand", lod=2)

    def fairy_lights(self, p0: Pt, ctrl: Pt, p1: Pt, n: int = 8) -> None:
        c = self.c
        c.path(
            "back",
            [("M", p0[0], p0[1]), ("Q", ctrl[0], ctrl[1], p1[0], p1[1])],
            None,
            stroke="ink",
            sw=3,
            alpha=0.55,
            tag="fairy_wire",
            lod=1,
            shadow=False,
        )
        for i in range(n):
            t = (i + 1) / (n + 1)
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ctrl[0] + t * t * p1[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ctrl[1] + t * t * p1[1] + 12
            if self.lit:
                col = self.lamp if i % 3 else lighten(self.col("accent"), 0.35)
                s = c.ellipse(
                    "back",
                    x,
                    y,
                    7,
                    9,
                    col,
                    material="emissive",
                    shadow=False,
                    glow=22,
                    tag="fairy_light",
                    lod=1,
                )
                if i % 2:
                    s.anim = Anim("twinkle", amp=0.45, freq=0.25 + 0.07 * (i % 3), phase=i * 1.3)
                self.halo("back", x, y, 46, col, 0.16)
            else:
                c.ellipse("back", x, y, 7, 9, "paper", tag="fairy_light", elev=2, lod=1)

    def teddy(self, x: float, y: float, s: float = 1.0) -> None:
        """A small teddy bear sitting with its feet at (x, y)."""
        c = self.c
        fur = mix(self.col("wood"), "#ffffff", 0.22)
        dark = darken(fur, 0.16)
        pl = "mid_back"
        for sx in (-1, 1):
            c.ellipse(pl, x + sx * 22 * s, y - 8 * s, 15 * s, 11 * s, fur, tag="toy", elev=3, lod=1)
            c.ellipse(
                pl,
                x + sx * 34 * s,
                y - 40 * s,
                10 * s,
                20 * s,
                fur,
                rot=sx * -25,
                tag="toy",
                elev=3,
                lod=1,
            )
        c.ellipse(pl, x, y - 34 * s, 30 * s, 34 * s, fur, tag="toy", elev=4, lod=1)
        c.ellipse(
            pl, x, y - 30 * s, 17 * s, 21 * s, lighten(fur, 0.3), tag="toy", lod=2, shadow=False
        )
        for sx in (-1, 1):
            c.ellipse(
                pl, x + sx * 22 * s, y - 80 * s, 10 * s, 10 * s, fur, tag="toy", elev=3, lod=1
            )
            c.ellipse(
                pl, x + sx * 22 * s, y - 80 * s, 5 * s, 5 * s, dark, tag="toy", lod=2, shadow=False
            )
        c.ellipse(pl, x, y - 66 * s, 25 * s, 23 * s, fur, tag="toy", elev=4, lod=1)
        c.ellipse(
            pl, x, y - 58 * s, 11 * s, 8 * s, lighten(fur, 0.32), tag="toy", lod=2, shadow=False
        )
        c.ellipse(pl, x, y - 61 * s, 4 * s, 3 * s, "ink", tag="toy", lod=2, shadow=False)
        for sx in (-1, 1):
            c.ellipse(
                pl,
                x + sx * 9 * s,
                y - 70 * s,
                2.8 * s,
                3.2 * s,
                "ink",
                tag="toy",
                lod=2,
                shadow=False,
            )

    def screen(self, x: float, y: float, w: float, h: float) -> None:
        """A monitor whose screen is lit (dim by day, glowing at dusk/night) unless lights are 'off'."""
        c = self.c
        on = self.p.lights != "off"
        if on and self.tod != "day":
            self.halo("mid_back", x + w / 2, y + h / 2, w * 1.1, self.col("glass"), 0.20)
        c.rect("mid_back", x, y, w, h, "ink", 10, tag="monitor", material="metal", elev=8, lod=0)
        m = 9.0
        if on:
            bg = mix(self.col("glass"), "#ffffff", 0.45 if self.tod == "day" else 0.0)
            c.rect(
                "mid_back",
                x + m,
                y + m,
                w - 2 * m,
                h - 2 * m,
                bg,
                4,
                material="emissive",
                shadow=False,
                glow=34 if self.tod != "day" else 0,
                tag="screen",
                lod=1,
            )
            sx0, sy0, sw_, sh_ = x + m + 12, y + m + 12, w - 2 * m - 24, h - 2 * m - 24
            c.rect(
                "mid_back",
                sx0,
                sy0,
                sw_ * 0.55,
                8,
                "accent2",
                3,
                tag="screen_ui",
                material="emissive",
                shadow=False,
                lod=2,
                alpha=0.9,
            )
            for i, hh in enumerate((0.35, 0.6, 0.45, 0.8, 0.55)):
                bw_ = sw_ * 0.1
                c.rect(
                    "mid_back",
                    sx0 + i * (bw_ + 6),
                    sy0 + sh_ - sh_ * hh * 0.7,
                    bw_,
                    sh_ * hh * 0.7,
                    "accent3" if i % 2 else "accent2",
                    2,
                    tag="screen_ui",
                    material="emissive",
                    shadow=False,
                    lod=2,
                    alpha=0.95,
                )
            c.path(
                "mid_back",
                [
                    ("M", sx0 + sw_ * 0.62, sy0 + sh_ * 0.75),
                    ("L", sx0 + sw_ * 0.72, sy0 + sh_ * 0.45),
                    ("L", sx0 + sw_ * 0.82, sy0 + sh_ * 0.6),
                    ("L", sx0 + sw_, sy0 + sh_ * 0.2),
                ],
                None,
                stroke="accent",
                sw=4,
                tag="screen_ui",
                material="emissive",
                shadow=False,
                lod=2,
            )
        else:
            c.rect(
                "mid_back",
                x + m,
                y + m,
                w - 2 * m,
                h - 2 * m,
                "ink",
                4,
                tag="screen",
                lod=1,
                shadow=False,
            )
            c.poly(
                "mid_back",
                [
                    (x + m, y + h * 0.55),
                    (x + m + w * 0.4, y + m),
                    (x + m + w * 0.62, y + m),
                    (x + m, y + h * 0.85),
                ],
                "white",
                alpha=0.07,
                tag="screen",
                lod=2,
                shadow=False,
            )

    def mug(self, x: float, y: float, fill: str = "accent", steam: bool = False) -> None:
        c = self.c
        self.block(
            "mid_back", x - 16, y - 30, 32, 30, fill, 7, tag="mug", mat="metal", elev=3, k=5, lod=1
        )
        c.path(
            "mid_back",
            [("M", x + 15, y - 24), ("C", x + 31, y - 26, x + 31, y - 6, x + 15, y - 8)],
            None,
            stroke=self.dk(fill, 0.15),
            sw=5,
            tag="mug",
            lod=2,
            shadow=False,
        )
        if steam:
            for i, dx in enumerate((-7, 2, 10)):
                c.path(
                    "mid_back",
                    [
                        ("M", x + dx, y - 36),
                        ("C", x + dx - 8, y - 50, x + dx + 8, y - 60, x + dx, y - 76),
                    ],
                    None,
                    stroke="white",
                    sw=4,
                    alpha=0.5,
                    tag="steam",
                    lod=2,
                    shadow=False,
                    anim=Anim("twinkle", amp=0.8, freq=0.3 + 0.05 * i, phase=i * 2.1),
                )

    def rug_round(self, cx: float, cy: float, rx: float, ry: float) -> None:
        c = self.c
        c.ellipse("mid_back", cx, cy, rx, ry, "rug2", tag="rug", material="fabric", elev=2, lod=1)
        c.ellipse(
            "mid_back",
            cx,
            cy,
            rx - 16,
            ry - 5,
            "rug",
            tag="rug",
            material="fabric",
            lod=2,
            shadow=False,
        )
        c.ellipse(
            "mid_back", cx, cy, rx - 34, ry - 11, "rug2", alpha=0.5, tag="rug", lod=2, shadow=False
        )
        c.ellipse("mid_back", cx, cy, rx - 52, ry - 16, "rug", tag="rug", lod=2, shadow=False)
        c.ellipse(
            "mid_back",
            cx,
            cy,
            rx * 0.3,
            ry * 0.3,
            "rug2",
            alpha=0.6,
            tag="rug_motif",
            lod=2,
            shadow=False,
        )

    def cat(self, x: float, y: float, s: float = 1.0) -> None:
        """A grey cat curled up with its belly on (x, y), head to the left."""
        c = self.c
        pl = "mid_back"
        fur = mix(self.col("stone2"), "#ffffff", 0.12)
        dark = darken(fur, 0.22)
        c.path(
            pl,
            [
                ("M", x + 38 * s, y - 2 * s),
                ("C", x + 72 * s, y - 4 * s, x + 78 * s, y + 30 * s, x + 56 * s, y + 46 * s),
            ],
            None,
            stroke=fur,
            sw=13 * s,
            tag="cat",
            lod=1,
            shadow=False,
        )
        c.ellipse(pl, x, y - 24 * s, 46 * s, 25 * s, fur, tag="cat", elev=3, lod=1)
        c.ellipse(
            pl,
            x + 22 * s,
            y - 28 * s,
            26 * s,
            21 * s,
            dark,
            alpha=0.28,
            tag="cat",
            lod=2,
            shadow=False,
        )
        for k in range(3):
            c.path(
                pl,
                [
                    ("M", x - 4 * s + k * 15 * s, y - 46 * s),
                    ("Q", x + 2 * s + k * 15 * s, y - 36 * s, x - 2 * s + k * 15 * s, y - 28 * s),
                ],
                None,
                stroke=dark,
                sw=4 * s,
                alpha=0.5,
                tag="cat",
                lod=2,
                shadow=False,
            )
        c.ellipse(pl, x - 44 * s, y - 24 * s, 22 * s, 19 * s, fur, tag="cat", elev=3, lod=1)
        for sx0 in (-60, -42):
            c.poly(
                pl,
                [
                    (x + sx0 * s, y - 36 * s),
                    (x + (sx0 + 7) * s, y - 58 * s),
                    (x + (sx0 + 20) * s, y - 38 * s),
                ],
                fur,
                smooth=0.1,
                tag="cat",
                lod=2,
                shadow=False,
            )
            c.poly(
                pl,
                [
                    (x + (sx0 + 5) * s, y - 38 * s),
                    (x + (sx0 + 8) * s, y - 51 * s),
                    (x + (sx0 + 15) * s, y - 39 * s),
                ],
                "accent",
                alpha=0.45,
                tag="cat",
                lod=2,
                shadow=False,
            )
        for ex in (-52, -36):
            c.path(
                pl,
                [
                    ("M", x + (ex - 5) * s, y - 26 * s),
                    ("Q", x + ex * s, y - 21 * s, x + (ex + 5) * s, y - 26 * s),
                ],
                None,
                stroke="ink",
                sw=2.6 * s,
                alpha=0.8,
                tag="cat",
                lod=2,
                shadow=False,
            )
        c.ellipse(
            pl, x - 44 * s, y - 19 * s, 3 * s, 2.4 * s, "accent", tag="cat", lod=2, shadow=False
        )

    def dust(self, area: tuple[float, float, float, float], count: int = 18) -> None:
        if self.tod == "night" and not self.lit:
            return
        col = self.col("glow") if self.tod != "night" else self.lamp
        self.c.emit(
            "mid_front",
            Particles(
                "dust",
                area,
                int(count * (0.5 + self.d)),
                speed=1.0,
                size=7,
                color=col,
                alpha=0.38,
                seed=self.c.seed + 3,
                material="emissive",
            ),
        )


# ----------------------------------------------------------------------------------- the variants
def _living(r: _Room) -> None:
    c = r.c
    d = r.d
    r.wall("stripes", dado=0.0)
    r.floor("wood")
    r.rug_persp(1296, 1660, 560, 900, 540)
    r.window_light()
    r.door()
    # gallery wall
    r.frame(190, 204, 190, 142, "sun")
    r.frame(396, 188, 88, 118, "leaf")
    if d > 0.3:
        r.frame(396, 326, 88, 70, "abstract", border="wood")
    r.pendant(542, drop=330, w=104)
    r.plant(1032, 1000, 0.9, n=6, pot="pot")  # behind the sofa on the right
    # sofa (main piece, cropped by the right edge)
    r.floor_shadow(900, BASE_Y + 6, 430, 22)
    for lx in (540, 600, 1140, 1230):
        c.rect("mid_back", lx, BASE_Y - 26, 20, 28, "wood2", 4, tag="sofa_leg", elev=2, lod=1)
    r.block(
        "mid_back", 520, 982, 760, 152, "fabric", 40, tag="sofa", mat="fabric", elev=10, k=8
    )  # backrest
    base_c = r.col("fabric")
    for bx in (592, 762, 932):  # back cushions
        c.rect(
            "mid_back",
            bx,
            996,
            160,
            96,
            lighten(base_c, 0.05),
            26,
            tag="sofa_cushion",
            lod=2,
            shadow=False,
        )
        c.rect(
            "mid_back",
            bx + 10,
            1002,
            140,
            8,
            "white",
            4,
            alpha=0.18,
            tag="sofa_cushion",
            lod=2,
            shadow=False,
        )
    r.block(
        "mid_back", 580, 1072, 330, 130, "fabric", 26, tag="sofa", mat="fabric", elev=8, k=7
    )  # seat 1
    r.block(
        "mid_back", 902, 1072, 330, 130, "fabric", 26, tag="sofa", mat="fabric", elev=8, k=7
    )  # seat 2
    r.block(
        "mid_back",
        560,
        1180,
        680,
        40,
        "fabric",
        14,
        tag="sofa",
        mat="fabric",
        elev=6,
        k=6,
        shade=0.22,
    )
    r.block(
        "mid_back",
        498,
        1032,
        96,
        178,
        "fabric",
        40,
        tag="sofa",
        mat="fabric",
        elev=12,
        k=8,
        shade=0.2,
    )  # left arm
    r.block(
        "mid_back",
        1200,
        1032,
        96,
        178,
        "fabric",
        40,
        tag="sofa",
        mat="fabric",
        elev=12,
        k=8,
        shade=0.2,
    )  # right arm
    for px, py, sz, ang, colr in (
        (644, 1046, 92, -10, "accent3"),
        (700, 1058, 74, 9, "accent2"),
    ):  # pillows
        pts = _rot(
            [
                (px - sz / 2, py - sz / 2),
                (px + sz / 2, py - sz / 2),
                (px + sz / 2, py + sz / 2),
                (px - sz / 2, py + sz / 2),
            ],
            px,
            py,
            ang,
        )
        c.poly("mid_back", pts, colr, smooth=0.22, tag="cushion", material="fabric", elev=5, lod=1)
    if d > 0.45:
        r.cat(548, 1032, 0.95)  # curled up on the sofa arm
    r.floor_lamp(462, 1232, 520)
    r.dust((500, 200, 1060, 1300))
    r.plant(
        -30, 2010, 1.5, plane="near", n=6, pot="accent2"
    )  # near-plane foliage framing the left edge


def _bedroom(r: _Room) -> None:
    c, d = r.c, r.d
    r.wall("dots")
    r.floor("wood")
    r.rug_round(480, 1500, 440, 130)
    r.window_light()
    r.door()
    r.fairy_lights((190, 176), (335, 300), (480, 176), 8)
    r.frame(214, 322, 128, 164, "mountains", border="trim")
    r.frame(366, 306, 104, 104, "sun", border="wood")
    r.pendant(548, drop=300, w=104, kind="globe")
    if d > 0.4:  # a little wall shelf above the nightstand
        r.shelf(352, 600, 176)
        r.books(362, 600, 5, 70)
        c.rect("back", 468, 560, 40, 40, "accent3", 6, tag="pot", elev=3, lod=1)
        for i, ang in enumerate((-30, 0, 30)):
            r.leaf("back", 488, 562, 54, 22, ang, ("foliage", "foliage2", "foliage3")[i])
    # bed with its foot to the left and the head cropped by the right edge
    r.floor_shadow(900, BASE_Y + 8, 430, 24)
    for lx in (514, 1262):
        c.rect("mid_back", lx, BASE_Y - 22, 20, 24, "wood2", 4, tag="bed_leg", elev=2, lod=1)
    r.block(
        "mid_back", 1236, 880, 70, 336, "wood", 30, tag="bed_headboard", mat="wood", elev=11, k=8
    )
    r.block("mid_back", 500, 1120, 806, 96, "wood", 14, tag="bed", mat="wood", elev=10, k=6)
    r.block(
        "mid_back",
        538,
        1034,
        730,
        112,
        "fabric2",
        28,
        tag="mattress",
        mat="fabric",
        elev=7,
        k=6,
        shade=0.1,
    )
    r.block("mid_back", 600, 1016, 668, 152, "fabric", 30, tag="blanket", mat="fabric", elev=9, k=8)
    c.rect(
        "mid_back",
        600,
        1016,
        64,
        152,
        "white",
        28,
        alpha=0.2,
        tag="blanket_cuff",
        lod=2,
        shadow=False,
    )
    for yy in (1062, 1100):
        c.rect(
            "mid_back",
            676,
            yy,
            580,
            10,
            "white",
            5,
            alpha=0.24,
            tag="blanket_stripe",
            lod=2,
            shadow=False,
        )
    r.block(
        "mid_back", 498, 994, 58, 224, "wood", 24, tag="bed_footboard", mat="wood", elev=12, k=7
    )
    for px, py, ww, hh, ang, colr in (
        (1030, 1000, 150, 90, -5, "fabric2"),
        (1150, 990, 140, 84, 9, "accent"),
    ):
        pts = _rot(
            [
                (px - ww / 2, py - hh / 2),
                (px + ww / 2, py - hh / 2),
                (px + ww / 2, py + hh / 2),
                (px - ww / 2, py + hh / 2),
            ],
            px,
            py,
            ang,
        )
        c.poly("mid_back", pts, colr, smooth=0.3, tag="pillow", material="fabric", elev=6, lod=1)
    r.teddy(632, 1022, 1.0)
    # nightstand with the bedside lamp
    r.floor_shadow(446, BASE_Y + 6, 74, 10)
    r.cabinet(392, 1094, 108, 134, "wood", drawers=2, tag="nightstand")
    r.table_lamp(446, 1094, 0.95)
    r.dust((500, 200, 1060, 1300))
    r.plant(-20, 2010, 1.4, plane="near", n=5, pot="accent", spiky=True)


def _office(r: _Room) -> None:
    c = r.c
    r.wall("none", dado=0.0)
    r.floor("carpet")
    r.window_light()
    r.door(glass=True)
    r.rug_persp(1296, 1660, 560, 900, 540)
    # pinboard with notes and a chart
    px0, py0, pw0, ph0 = 214, 232, 236, 190
    c.rect("back", px0, py0, pw0, ph0, "trim", 6, tag="pinboard", material="wood", elev=5, lod=0)
    c.rect(
        "back",
        px0 + 10,
        py0 + 10,
        pw0 - 20,
        ph0 - 20,
        mix(r.col("wood"), r.col("paper"), 0.35),
        3,
        tag="pinboard_cork",
        lod=1,
        shadow=False,
    )
    c.rect("back", px0 + 24, py0 + 26, 92, 66, "paper", 2, tag="note", elev=2, lod=1)
    for i, hh in enumerate((18, 34, 26, 44)):
        c.rect(
            "back",
            px0 + 34 + i * 20,
            py0 + 82 - hh,
            12,
            hh,
            ("accent", "accent2", "accent3", "accent")[i],
            2,
            tag="chart",
            lod=2,
            shadow=False,
        )
    for nx, ny, nc, ang in (
        (140, 34, "accent3", -6),
        (176, 36, "accent", 7),
        (130, 104, "accent2", 5),
        (60, 118, "accent3", -4),
        (166, 112, "paper", 8),
    ):
        cx_, cy_ = px0 + nx + 24, py0 + ny + 24
        pts = _rot(
            [
                (cx_ - 24, cy_ - 24),
                (cx_ + 24, cy_ - 24),
                (cx_ + 24, cy_ + 24),
                (cx_ - 24, cy_ + 24),
            ],
            cx_,
            cy_,
            ang,
        )
        c.poly("back", pts, nc, tag="note", elev=2, lod=1)
        c.ellipse(
            "back",
            cx_,
            cy_ - 20,
            4.5,
            4.5,
            "accent" if nc != "accent" else "ink",
            tag="pin",
            lod=2,
            shadow=False,
        )
    r.clock(512, 292, 46)
    r.pendant(398, drop=104, w=300, kind="bar")
    # water cooler between the door and the desk
    cx = 450
    r.floor_shadow(cx, BASE_Y + 6, 66, 10)
    r.block(
        "mid_back",
        cx - 44,
        1040,
        88,
        190,
        "fabric2",
        14,
        tag="water_cooler",
        mat="metal",
        elev=8,
        k=6,
    )
    c.rect(
        "mid_back",
        cx - 30,
        1078,
        60,
        42,
        "ink",
        8,
        tag="cooler_dispenser",
        alpha=0.75,
        lod=2,
        shadow=False,
    )
    c.ellipse("mid_back", cx - 12, 1099, 6, 6, "accent2", tag="cooler_tap", lod=2, shadow=False)
    c.ellipse("mid_back", cx + 12, 1099, 6, 6, "accent", tag="cooler_tap", lod=2, shadow=False)
    c.rect("mid_back", cx - 36, 1128, 72, 10, "metal", 4, tag="cooler_tray", lod=2, shadow=False)
    c.rect(
        "mid_back",
        cx - 34,
        920,
        68,
        126,
        mix(r.col("glass"), "#ffffff", 0.2),
        28,
        alpha=0.85,
        tag="water_jug",
        material="glass",
        elev=6,
        lod=0,
    )
    c.rect(
        "mid_back",
        cx - 30,
        962,
        60,
        80,
        "glass",
        20,
        alpha=0.9,
        tag="water_jug",
        lod=2,
        shadow=False,
    )
    for i, (bx, by) in enumerate(((cx - 8, 1004), (cx + 8, 982), (cx, 1022))):
        c.ellipse(
            "mid_back",
            bx,
            by,
            4,
            4,
            "white",
            alpha=0.8,
            tag="bubble",
            lod=2,
            shadow=False,
            anim=Anim("bob", amp=6, freq=0.3, phase=i * 2.0),
        )
    c.rect("mid_back", cx - 14, 904, 28, 22, "metal", 6, tag="water_jug_neck", lod=2, shadow=False)
    # desk (cropped by the right edge) with monitor, lamp and a mug
    r.floor_shadow(900, BASE_Y + 8, 400, 22)
    r.cabinet(878, 1028, 196, 200, "wood", drawers=3, tag="desk_drawers", elev=9)
    r.block("mid_back", 538, 1030, 40, 198, "wood2", 6, tag="desk_leg", mat="wood", elev=7, k=5)
    r.block("mid_back", 1222, 1030, 40, 198, "wood2", 6, tag="desk_leg", mat="wood", elev=7, k=5)
    c.rect(
        "mid_back",
        590,
        1040,
        280,
        120,
        "wood2",
        4,
        alpha=0.8,
        tag="desk_panel",
        lod=2,
        shadow=False,
    )
    r.block(
        "mid_back", 520, 996, 740, 34, "wood", 8, tag="desk", mat="wood", elev=10, k=6, shade=0.18
    )
    r.screen(884, 790, 212, 146)
    c.rect("mid_back", 976, 934, 24, 58, "metal", 4, tag="monitor_stand", elev=4, lod=1)
    c.ellipse("mid_back", 988, 992, 52, 9, "metal", tag="monitor_base", elev=4, lod=1)
    c.rect("mid_back", 904, 982, 110, 12, "ink", 5, tag="keyboard", elev=3, lod=1)
    # articulated desk lamp
    lx = 580
    c.ellipse("mid_back", lx, 992, 30, 8, "ink", tag="desk_lamp", elev=3, lod=1)
    c.line("mid_back", (lx, 990), (lx + 26, 906), 7, "ink", tag="desk_lamp", lod=0)
    c.line("mid_back", (lx + 26, 906), (lx - 6, 846), 6, "ink", tag="desk_lamp", lod=0)
    head: list[Pt] = [(lx - 40, 832), (lx + 16, 826), (lx + 30, 868), (lx - 24, 874)]
    if r.lit:
        c.poly(
            "mid_back",
            head,
            "accent3",
            smooth=0.2,
            tag="desk_lamp",
            material="metal",
            elev=4,
            lod=0,
        )
        r.fade_poly(
            "mid_back",
            [(lx - 30.0, 872.0), (lx + 22.0, 866.0), (lx + 130.0, 998.0), (lx - 130.0, 998.0)],
            r.halo_col,
            0.26 * r.lk,
            0.0,
            tag="lamp_cone",
        )
        c.ellipse(
            "mid_back",
            lx - 4,
            872,
            22,
            6,
            r.lamp,
            material="emissive",
            shadow=False,
            glow=26,
            tag="bulb",
            lod=2,
        )
    else:
        c.poly(
            "mid_back",
            head,
            "accent3",
            smooth=0.2,
            tag="desk_lamp",
            material="metal",
            elev=4,
            lod=0,
        )
    r.mug(660, 996, "accent2", steam=True)
    c.rect("mid_back", 690, 970, 70, 8, "paper", 2, tag="papers", elev=2, lod=1)
    c.rect("mid_back", 694, 960, 70, 8, "white", 2, tag="papers", elev=2, lod=1)
    r.dust((500, 200, 1060, 1300))
    r.plant(1118, 2000, 1.15, plane="near", n=5, pot="accent3", spiky=True)


def _kitchen(r: _Room) -> None:
    c, d = r.c, r.d
    r.win = (556.0, 312.0, 290.0, 330.0)
    r.wall("none")
    # tiled backsplash behind the counter (right half)
    bx0, by0 = 470.0, 700.0
    c.rect(
        "back",
        bx0,
        by0,
        X1 - bx0,
        FLOOR_Y - by0 + 40,
        "trim",
        tag="backsplash",
        material="paper",
        alpha=0.55,
        lod=1,
        shadow=False,
    )
    cmds: list[tuple[Any, ...]] = []
    for yy in range(int(by0), int(FLOOR_Y) + 40, 56):
        cmds += [("M", bx0, yy), ("L", X1, yy)]
    for xx in range(int(bx0), int(X1), 56):
        cmds += [("M", xx, by0), ("L", xx, FLOOR_Y + 40)]
    c.path("back", cmds, None, stroke="wall2", sw=2.5, alpha=0.7, tag="tiles", lod=2, shadow=False)
    c.rect(
        "back",
        bx0 - 8,
        by0 - 12,
        X1 - bx0 + 8,
        14,
        "accent2",
        3,
        tag="backsplash_trim",
        elev=3,
        lod=2,
    )
    r.floor("tiles")
    r.rug_persp(1300, 1540, 500, 660, 690)
    r.window_light()
    r.door()
    r.pendant(412, drop=360, w=112)
    # shelf with jars and plates above the window
    r.shelf(560, 220, 290)
    for jx, jc, jh in ((580, "accent3", 56), (630, "accent", 46), (680, "accent2", 62)):
        c.rect("back", jx, 220 - jh, 36, jh, jc, 8, tag="jar", elev=3, lod=1)
        c.rect("back", jx - 3, 220 - jh - 8, 42, 10, "wood2", 4, tag="jar_lid", elev=3, lod=2)
    for i, px in enumerate((776, 812)):
        c.ellipse("back", px, 220 - 30, 28, 28, "trim", tag="plate", elev=3, lod=1)
        c.ellipse(
            "back",
            px,
            220 - 30,
            18,
            18,
            "accent2" if i == 0 else "accent",
            alpha=0.75,
            tag="plate",
            lod=2,
            shadow=False,
        )
    # counter (left of the fridge)
    r.floor_shadow(680, BASE_Y + 8, 250, 18)
    r.cabinet(478, 1022, 408, 206, "fabric", doors=3, tag="counter_cabinet", elev=9)
    c.rect("mid_back", 490, 1204, 384, 24, "ink", 4, alpha=0.5, tag="toe_kick", lod=2, shadow=False)
    r.block(
        "mid_back",
        466,
        990,
        432,
        36,
        "wood",
        8,
        tag="counter",
        mat="wood",
        elev=10,
        k=6,
        shade=0.18,
    )
    # sink + faucet
    c.rect("mid_back", 636, 984, 126, 10, "metal", 4, tag="sink", lod=1, elev=2)
    c.rect("mid_back", 692, 924, 12, 62, "metal", 5, tag="faucet", elev=3, lod=1)
    c.path(
        "mid_back",
        [("M", 698, 930), ("C", 698, 902, 746, 902, 746, 928)],
        None,
        stroke=r.col("metal"),
        sw=11,
        tag="faucet",
        lod=1,
        elev=3,
    )
    # things on the counter (kept clear of the middle where a character's head sits)
    r.disc("mid_back", 524, 978, 40, 14, "accent2", tag="bowl", mat="metal", elev=3, lod=1, k=3)
    for fx, fy, fc in ((510, 952, "accent"), (534, 948, "accent3"), (526, 962, "foliage3")):
        c.ellipse("mid_back", fx, fy, 15, 15, fc, tag="fruit", elev=2, lod=1)
    r.block(
        "mid_back", 578, 920, 46, 70, "accent3", 14, tag="kettle", mat="metal", elev=4, k=5, lod=1
    )
    c.rect("mid_back", 584, 906, 34, 16, "ink", 6, tag="kettle", lod=2, shadow=False)
    for i, dx in enumerate((-6, 4, 14)):
        c.path(
            "mid_back",
            [("M", 594 + dx, 900), ("C", 586 + dx, 884, 602 + dx, 872, 594 + dx, 854)],
            None,
            stroke="white",
            sw=4,
            alpha=0.5,
            tag="steam",
            lod=2,
            shadow=False,
            anim=Anim("twinkle", amp=0.8, freq=0.3 + 0.05 * i, phase=i * 2.1),
        )
    # fridge at the right edge
    fx0 = 906
    r.floor_shadow(1010, BASE_Y + 8, 150, 16)
    r.block("mid_back", fx0, 640, 232, 590, "accent2", 22, tag="fridge", mat="metal", elev=12, k=8)
    c.rect(
        "mid_back",
        fx0 + 8,
        812,
        210,
        5,
        "ink",
        2,
        alpha=0.5,
        tag="fridge_seam",
        lod=2,
        shadow=False,
    )
    c.rect("mid_back", fx0 + 20, 690, 10, 90, "metal", 5, tag="handle", lod=2, elev=2)
    c.rect("mid_back", fx0 + 20, 840, 10, 150, "metal", 5, tag="handle", lod=2, elev=2)
    for mx, my, mc, ang in (
        (964, 880, "accent3", -8),
        (1010, 920, "accent", 6),
        (990, 700, "paper", 5),
    ):
        pts = _rot(
            [(mx - 22, my - 26), (mx + 22, my - 26), (mx + 22, my + 26), (mx - 22, my + 26)],
            mx,
            my,
            ang,
        )
        c.poly("mid_back", pts, mc, tag="magnet", elev=2, lod=2)
    c.ellipse("mid_back", 1002, 876, 6, 6, "accent", tag="magnet", lod=2, shadow=False)
    if d > 0.35:
        r.plant(1010, 640, 0.6, n=5, pot="accent3")
    r.dust((500, 200, 1060, 1300))
    r.plant(1112, 2000, 1.2, plane="near", n=5, pot="accent3")


# ----------------------------------------------------------------------------------- registration
@register_background(
    "room",
    params_schema=RoomParams,
    summary="Interior set (bedroom, living room, office or kitchen) with a window onto the sky, "
    "lit lamps/screens at dusk and night, rugs, shelves, plants and a door. Slots: 'door' (left "
    "edge), 'window' (right); 'sofa'/'bed'/'desk'/'counter' all stand in front of the room's main "
    "piece of furniture on the right",
    slots={
        "door": (0.13, 0.74),
        "window": (0.78, 0.74),
        "sofa": (0.68, 0.74),
        "bed": (0.66, 0.74),
        "desk": (0.70, 0.74),
        "counter": (0.66, 0.74),
    },
    tags=("indoor", "interior", "home"),
    ground_y=0.74,
    perspective=0.8,
    horizon=0.6,
)
def room(ctx: BuildContext, p: RoomParams) -> None:
    """Interior room set"""
    roles = {**BASE_ROLES, **PALETTES[p.room]}
    ctx.graph.scheme = make_scheme(ctx.time_of_day, ctx.mood, roles)
    r = _Room(ctx, p)
    {"living": _living, "bedroom": _bedroom, "office": _office, "kitchen": _kitchen}[p.room](r)
    r.window({"office": "blinds", "kitchen": "valance"}.get(p.room, "curtains"))
