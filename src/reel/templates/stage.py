"""stage: a studio / theatre set for titles and punchlines - curtains or a backdrop, a polished floor
with spotlight beams and pools of light that gently pulse, an optional podium, and sparkles/confetti."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field

from reel.core.geometry import Pt, darken, lighten, mix
from reel.core.ir import Anim, Particles
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
from reel.templates.palette import make_scheme

FLOOR_Y = 1190.0  # where the back wall meets the stage floor
VP = (540.0, 640.0)  # vanishing point of the floor boards
FEET_Y = 1421.0  # characters' feet line (ground_y 0.74 x 1920)

PALETTES: dict[str, dict[str, str]] = {
    "curtain": {
        "wall": "#3a1020", "wall2": "#240a14",
        "curtain": "#a02236", "curtain2": "#5c1120", "curtain3": "#c13a50",
        "gold": "#e8b94e", "gold2": "#b8892c",
        "floor": "#2a1a14", "floor2": "#654130", "wood": "#7a5038", "wood2": "#472c1f",
        "metal": "#566070",
    },
    "spotlight": {
        "wall": "#161c38", "wall2": "#2a3260",
        "led": "#35d6e6", "led2": "#ff4f9c", "gold": "#e8b94e", "gold2": "#b8892c",
        "floor": "#10142a", "floor2": "#2b3358", "wood": "#2f3658", "wood2": "#1b2040",
        "metal": "#4d566c",
    },
    "city_lights": {
        "wall": "#141a33", "wall2": "#2a3260",
        "gold": "#e8b94e", "gold2": "#b8892c", "bokeh1": "#ffcf6e", "bokeh2": "#ff7aa8", "bokeh3": "#6ad6ff",
        "floor": "#12162a", "floor2": "#2d3558", "wood": "#2f3658", "wood2": "#1b2040",
        "metal": "#4d566c",
    },
    "confetti": {
        "wall": "#f6c6dc", "wall2": "#fde4cf",
        "gold": "#f2b630", "gold2": "#c98f1c", "pink": "#ff7eb6",
        "floor": "#b98458", "floor2": "#d9aa7a", "wood": "#a9764c", "wood2": "#7a5233",
        "metal": "#8c95a3",
    },
}  # fmt: skip


class StageParams(BgParams):
    backdrop: Literal["curtain", "spotlight", "city_lights", "confetti"] = Field(
        "curtain",
        description="what hangs behind the performers: curtain (red velvet theatre curtains with a "
        "valance), spotlight (dark TV studio with LED pylons and a light rig), city_lights (night-club "
        "skyline with bokeh lights) or confetti (party: bunting, balloons, streamers, falling confetti)",
    )
    beams: int = Field(
        2,
        ge=0,
        le=4,
        description="number of spotlight beams / pools of light on the floor (0 = evenly lit; 2 lights "
        "the 'left' and 'right' positions, 1 the centre)",
    )
    podium: bool = Field(
        True,
        description="a lectern with a microphone in front of the centre position: a character at "
        "'center'/'podium' appears behind it (legs hidden); false = open stage",
    )
    sparkles: bool = Field(
        True,
        description="glitter drifting in the spotlight beams; falling confetti is added when the mood is "
        "'playful' or the backdrop is 'confetti'",
    )


def _rgba(hex6: str, a: float) -> str:
    return f"{hex6[:7]}{round(max(0.0, min(1.0, a)) * 255):02x}"


def _rot(pts: list[Pt], cx: float, cy: float, deg: float) -> list[Pt]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


class _Stage:
    def __init__(self, ctx: BuildContext, p: StageParams) -> None:
        self.c = ctx
        self.p = p
        self.rng = ctx.rng
        self.d = ctx.density
        self.tod = ctx.time_of_day
        # how strong the stage lighting reads (house lights on by day, dramatic in the dark)
        self.k = {"dawn": 0.9, "day": 0.8, "dusk": 1.0, "night": 1.35}[ctx.time_of_day] * {
            "dramatic": 1.2,
            "gloomy": 0.8,
            "playful": 1.1,
        }.get(ctx.mood, 1.0)
        self.beam = mix(self.col("glow"), "#ffffff", 0.3)
        self.lit = ctx.graph.lamps_on

    def col(self, c: str) -> str:
        return str(self.c.scheme.base(c))

    # -- generic helpers -------------------------------------------------------------------------------
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
            plane,
            x,
            y,
            w,
            h,
            c0,
            material="emissive",
            shadow=False,
            tag=tag,
            lod=2,
            gradient=(c0, c1, angle),
        )

    def fade_poly(
        self,
        plane: str,
        pts: list[Pt],
        color: str,
        a0: float,
        a1: float,
        angle: float = 90.0,
        tag: str = "light",
        glow: float = 0.0,
        anim: Anim | None = None,
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
            glow=glow,
            anim=anim,
        )

    def halo(
        self,
        plane: str,
        cx: float,
        cy: float,
        r: float,
        color: str,
        a: float,
        ry: float | None = None,
        flick: float = 0.0,
        phase: float = 0.0,
    ) -> None:
        """Soft round glow with peak alpha ``a`` at the centre.

        Built from equal-alpha feathered discs whose alpha is an exact multiple of 1/255, with radii
        following a smooth falloff - so there is no visible banding even for very large halos.
        """
        c = self.c
        n = max(8, min(40, round(a * 255 / 3)))
        al = max(1, round(a * 255 / n)) / 255
        sy = (ry / r) if ry else 1.0
        prev = 0.0
        for j in range(1, n + 1):
            rj = r * math.sqrt(1.0 - math.sqrt((n - j) / n))
            c.ellipse(
                plane,
                cx,
                cy,
                rj,
                rj * sy,
                color,
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
                plane,
                cx,
                cy,
                r * 0.4,
                r * 0.4 * sy,
                color,
                alpha=a * 0.3,
                material="emissive",
                shadow=False,
                tag="glow",
                lod=2,
                glow=r * 0.5,
                anim=Anim("flicker", amp=flick, freq=0.2 + 0.04 * (phase % 3), phase=phase),
            )

    # -- floor ---------------------------------------------------------------------------------------------
    def floor(self, kind: str) -> None:
        c = self.c
        c.rect(
            "mid_back",
            X0,
            FLOOR_Y,
            X1 - X0,
            Y1 - FLOOR_Y,
            "floor",
            tag="floor",
            material="wood",
            shadow=False,
            gradient=("floor", "floor2", 90.0),
        )
        vx, vy = VP

        def x_at(i: float, y: float) -> float:
            return vx + i * (y - vy) / (FLOOR_Y - vy)

        if kind == "wood":
            sp, n = 70.0, 16
            base = self.col("floor2")
            cmds: list[tuple[Any, ...]] = []
            for i in range(-n, n):
                tone = float(self.rng.uniform(0.0, 0.14))
                col = darken(base, tone)
                c.poly(
                    "mid_back",
                    [
                        (x_at(i * sp, FLOOR_Y), FLOOR_Y),
                        (x_at((i + 1) * sp, FLOOR_Y), FLOOR_Y),
                        (x_at((i + 1) * sp, Y1), Y1),
                        (x_at(i * sp, Y1), Y1),
                    ],
                    col,
                    alpha=0.42,
                    tag="floor_boards",
                    lod=2,
                    shadow=False,
                )
                y = FLOOR_Y + float(self.rng.uniform(30, 90))
                while y < Y1:
                    cmds += [("M", x_at(i * sp, y), y), ("L", x_at((i + 1) * sp, y), y)]
                    y += (y - vy) * float(self.rng.uniform(0.55, 1.2))
            for i in range(-n, n + 1):
                cmds += [("M", x_at(i * sp, FLOOR_Y), FLOOR_Y), ("L", x_at(i * sp, Y1), Y1)]
            c.path(
                "mid_back",
                cmds,
                None,
                stroke="black",
                sw=2.2,
                alpha=0.30,
                tag="floor_lines",
                lod=2,
                shadow=False,
            )
        else:  # glossy dark tiles with a faint light grid
            sp, n = 150.0, 10
            rows = [FLOOR_Y]
            while rows[-1] < Y1:
                rows.append(rows[-1] + (rows[-1] - vy) * 0.32)
            cmds = []
            for y in rows:
                cmds += [("M", X0, y), ("L", X1, y)]
            for i in range(-n, n + 1):
                cmds += [("M", x_at(i * sp, FLOOR_Y), FLOOR_Y), ("L", x_at(i * sp, Y1), Y1)]
            c.path(
                "mid_back",
                cmds,
                None,
                stroke="white",
                sw=2.0,
                alpha=0.10,
                tag="floor_lines",
                lod=2,
                shadow=False,
            )
        # polished sheen: a broad soft highlight and shade where the wall meets the floor
        self.fade_rect("mid_back", X0, FLOOR_Y, X1 - X0, 130, "black", 0.5, 0.0, tag="floor_shade")
        c.poly(
            "mid_back",
            [(300, FLOOR_Y + 40), (540, FLOOR_Y + 40), (780, Y1), (200, Y1)],
            "#ffffff",
            alpha=0.04,
            material="emissive",
            shadow=False,
            tag="floor_gloss",
            lod=2,
        )

    # -- spotlights ----------------------------------------------------------------------------------------
    def pool_xs(self) -> tuple[list[float], float]:
        n = self.p.beams
        xs = {
            0: [],
            1: [540.0],
            2: [290.0, 790.0],
            3: [210.0, 540.0, 870.0],
            4: [150.0, 390.0, 690.0, 930.0],
        }[n]
        rx = {0: 0.0, 1: 300.0, 2: 260.0, 3: 230.0, 4: 190.0}[n]
        return xs, rx

    def lights(self, apex: dict[int, Pt] | None = None) -> None:
        """Soft beams from the rig (or from above the frame) down to pools of light under the performers."""
        xs, rx = self.pool_xs()
        k = self.k
        for i, xp in enumerate(xs):
            ax, ay = (apex or {}).get(i, (540 + (xp - 540) * 0.5, -80.0))
            hw = 20.0 if apex and i in apex else 28.0
            pts: list[Pt] = [
                (ax - hw, ay),
                (ax + hw, ay),
                (xp + rx * 0.92, FEET_Y - 26),
                (xp - rx * 0.92, FEET_Y - 26),
            ]
            self.fade_poly(
                "mid_back",
                pts,
                "glow",
                0.15 * k,
                0.04 * k,
                90.0,
                tag="beam",
                glow=26,
                anim=Anim("flicker", amp=0.16, freq=0.16 + 0.03 * i, phase=i * 1.9),
            )
            ry = rx * 0.30
            cy = FEET_Y - 34  # a tall pool: covers feet anywhere between y = 0.68 and 0.76
            self.halo("mid_back", xp, cy, rx, self.beam, 0.34 * k, ry=ry)
            self.c.ellipse(
                "mid_back",
                xp,
                cy,
                rx * 0.5,
                ry * 0.5,
                self.beam,
                alpha=0.08 * k,
                material="emissive",
                shadow=False,
                tag="pool",
                lod=2,
                glow=rx * 0.2,
                anim=Anim("pulse", amp=0.05, freq=0.14, phase=i * 2.3, pivot=(xp, cy)),
            )
            if self.p.sparkles:
                self.c.emit(
                    "mid_back",
                    Particles(
                        "sparkle",
                        (xp - rx * 0.75, 240, xp + rx * 0.75, FEET_Y - 60),
                        int(6 + 10 * self.d),
                        size=11,
                        color=self.beam,
                        alpha=0.9,
                        seed=self.c.seed + 17 * (i + 1),
                        material="emissive",
                    ),
                )

    # -- podium --------------------------------------------------------------------------------------------
    def podium(self) -> None:
        c = self.c
        cx, base, top = 540.0, 1458.0, 1160.0
        c.ellipse(
            "mid_back",
            cx,
            base + 4,
            160,
            20,
            "shadow",
            alpha=0.22,
            shadow=False,
            tag="shadow",
            lod=2,
        )
        c.ellipse(
            "mid_back",
            cx,
            base + 4,
            140,
            14,
            "shadow",
            alpha=0.3,
            shadow=False,
            tag="shadow",
            lod=2,
        )
        pl = "mid_front"
        body = [(cx - 112, top + 24), (cx + 112, top + 24), (cx + 126, base), (cx - 126, base)]
        c.poly(pl, body, "wood2", smooth=0.03, tag="podium", material="wood", elev=10, lod=0)
        panel = [
            (cx - 94, top + 56),
            (cx + 94, top + 56),
            (cx + 102, base - 30),
            (cx - 102, base - 30),
        ]
        c.poly(
            pl, panel, "wood", smooth=0.03, tag="podium_panel", material="wood", lod=1, shadow=False
        )
        c.poly(
            pl,
            [(cx - 94, top + 56), (cx - 70, top + 56), (cx - 80, base - 30), (cx - 102, base - 30)],
            "white",
            alpha=0.08,
            tag="podium_panel",
            lod=2,
            shadow=False,
        )
        for yy in (top + 62, base - 38):
            c.rect(
                pl,
                cx - 98 + (yy - top) * 0.03,
                yy,
                196,
                5,
                "gold",
                2,
                tag="podium_trim",
                lod=2,
                shadow=False,
            )
        # emblem: gold disc with a star
        ey = top + 142
        c.ellipse(pl, cx, ey, 46, 46, "gold2", tag="podium_emblem", elev=3, lod=0)
        c.ellipse(pl, cx - 2, ey - 2, 42, 42, "gold", tag="podium_emblem", lod=2, shadow=False)
        star: list[Pt] = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = 28 if i % 2 == 0 else 12
            star.append((cx + rr * math.cos(a), ey + rr * math.sin(a) + 2))
        c.poly(pl, star, "gold2", tag="podium_emblem", lod=2, shadow=False)
        # sloped top
        c.poly(
            pl,
            [(cx - 132, top - 4), (cx + 132, top - 4), (cx + 114, top + 28), (cx - 114, top + 28)],
            "wood",
            smooth=0.04,
            tag="podium_top",
            material="wood",
            elev=12,
            lod=0,
        )
        c.poly(
            pl,
            [(cx - 132, top - 4), (cx + 132, top - 4), (cx + 128, top + 3), (cx - 128, top + 3)],
            "white",
            alpha=0.28,
            tag="podium_top",
            lod=2,
            shadow=False,
        )
        # microphone on a gooseneck, off to the side so it never covers a speaker's mouth
        mx = cx + 98
        c.rect(pl, mx - 7, top - 12, 14, 12, "metal", 3, tag="mic", elev=3, lod=1)
        stem: list[tuple[Any, ...]] = [
            ("M", mx, top - 10),
            ("C", mx + 2, top - 80, mx - 30, top - 112, mx - 28, top - 160),
        ]
        c.path(pl, stem, None, stroke="metal", sw=8, tag="mic_stem", lod=0, shadow=False)
        c.ellipse(pl, mx - 28, top - 186, 16, 30, "ink", rot=-8, tag="mic", elev=3, lod=0)
        c.ellipse(
            pl,
            mx - 28,
            top - 194,
            12,
            20,
            "metal",
            rot=-8,
            alpha=0.8,
            tag="mic",
            lod=2,
            shadow=False,
        )
        for yy in (-196, -188, -180):
            c.line(
                pl,
                (mx - 38, top + yy),
                (mx - 18, top + yy - 2),
                2,
                "black",
                alpha=0.45,
                tag="mic",
                lod=2,
            )
        c.ellipse(
            pl,
            mx - 12,
            top - 160,
            4,
            4,
            "accent",
            material="emissive",
            shadow=False,
            glow=14,
            tag="mic_led",
            lod=2,
        )
        # a glass of water
        gx = cx - 70
        c.poly(
            pl,
            [(gx - 13, top - 40), (gx + 13, top - 40), (gx + 10, top - 4), (gx - 10, top - 4)],
            "glass",
            alpha=0.7,
            tag="glass",
            material="glass",
            elev=3,
            lod=1,
        )
        c.poly(
            pl,
            [(gx - 12, top - 22), (gx + 12, top - 22), (gx + 10, top - 4), (gx - 10, top - 4)],
            "water",
            alpha=0.7,
            tag="glass",
            lod=2,
            shadow=False,
        )

    # -- hanging stage light ---------------------------------------------------------------------------------
    def fixture(self, x: float, y: float, ang: float, s: float = 1.0) -> Pt:
        """A stage light hanging from the top edge, aimed by ``ang`` degrees; returns its lens position."""
        c = self.c
        pl = "mid_front"
        c.rect(pl, x - 5, Y0, 10, y - Y0, "metal", 2, tag="fixture_cable", lod=0)
        body = _rot(
            [
                (x - 46 * s, y - 38 * s),
                (x + 46 * s, y - 38 * s),
                (x + 46 * s, y + 38 * s),
                (x - 46 * s, y + 38 * s),
            ],
            x,
            y,
            ang,
        )
        c.poly(
            pl,
            [
                (x - 62 * s, y - 40 * s),
                (x - 52 * s, y - 40 * s),
                (x - 52 * s, y + 10 * s),
                (x + 52 * s, y + 10 * s),
                (x + 52 * s, y - 40 * s),
                (x + 62 * s, y - 40 * s),
                (x + 62 * s, y + 18 * s),
                (x - 62 * s, y + 18 * s),
            ],
            "metal",
            tag="fixture_yoke",
            elev=6,
            lod=1,
        )
        c.poly(pl, body, "ink", smooth=0.12, tag="fixture", material="metal", elev=8, lod=0)
        lx, ly = _rot([(x, y + 40 * s)], x, y, ang)[0]
        c.ellipse(
            pl,
            lx,
            ly,
            42 * s,
            12 * s,
            self.beam,
            rot=ang,
            material="emissive",
            shadow=False,
            glow=44 * s,
            tag="fixture_lens",
            lod=1,
        )
        self.halo(pl, lx, ly + 10 * s, 150 * s, self.beam, 0.50 * self.k)
        return (lx, ly)

    # -- backdrops -------------------------------------------------------------------------------------------
    def backdrop_curtain(self) -> None:
        c = self.c
        h = Y1 - Y0
        c.rect(
            "back",
            X0,
            Y0,
            X1 - X0,
            h,
            "curtain",
            tag="curtain",
            material="fabric",
            shadow=False,
            gradient=("curtain", "curtain2", 90.0),
        )
        for i, x in enumerate(
            range(-570, 1640, 90)
        ):  # velvet pleats: dark valley -> lit crest -> dark valley
            amp = 0.85 if i % 2 else 1.0
            c.rect(
                "back",
                x,
                Y0,
                45,
                h,
                "curtain2",
                tag="curtain_fold",
                material="fabric",
                lod=2,
                shadow=False,
                alpha=0.55 * amp,
                gradient=("curtain2", "curtain3", 0.0),
            )
            c.rect(
                "back",
                x + 45,
                Y0,
                45,
                h,
                "curtain3",
                tag="curtain_fold",
                material="fabric",
                lod=2,
                shadow=False,
                alpha=0.55 * amp,
                gradient=("curtain3", "curtain2", 0.0),
            )
        self.fade_rect("back", X0, Y0, X1 - X0, h, "black", 0.0, 0.45, tag="curtain_shade")
        self.halo("back", 540, 760, 760, self.beam, 0.22 * self.k)
        c.rect("back", X0, FLOOR_Y - 20, X1 - X0, 24, "gold2", tag="curtain_hem", lod=1, elev=2)
        c.rect("back", X0, FLOOR_Y - 20, X1 - X0, 6, "gold", tag="curtain_hem", lod=2, shadow=False)

    def drapes(self) -> None:
        """Side curtains tied back with gold cords (they sway a little)."""
        c = self.c
        for side in (-1, 1):

            def X(x: float, side: int = side) -> float:
                return x if side < 0 else 1080 - x

            inner: list[Pt] = [
                (X(250), -60),
                (X(246), 160),
                (X(214), 340),
                (X(150), 520),
                (X(78), 640),
                (X(120), 760),
                (X(188), 940),
                (X(236), 1120),
                (X(262), 1330),
            ]
            pts: list[Pt] = [(X(-150), -60), *inner, (X(-150), 1330)]
            piv = (X(60), -60)
            ph = 0.4 if side < 0 else 2.2

            def an(amp: float = 0.5, ph: float = ph, pv: Pt = piv) -> Anim:
                return Anim("sway", amp=amp, freq=0.1, phase=ph, pivot=pv)

            c.poly(
                "mid_back",
                pts,
                "curtain",
                curve=False,
                smooth=0.04,
                tag="drape",
                material="fabric",
                elev=12,
                lod=0,
                anim=an(),
            )
            # folds following the shape
            for f, alpha, role in (
                (0.2, 0.30, "curtain2"),
                (0.42, 0.22, "curtain3"),
                (0.64, 0.32, "curtain2"),
                (0.84, 0.18, "curtain3"),
            ):
                fold: list[tuple[Any, ...]] = [
                    (
                        "M",
                        X(
                            -150
                            + (inner[0][0] if side < 0 else 1080 - inner[0][0]) * 0
                            + (250 + 150) * f * (1 if side < 0 else -1)
                        ),
                        -50,
                    )
                ]
                for ix, iy in inner[1:]:
                    ox = X(-150)
                    fx = ox + (ix - ox) * f
                    fold.append(("L", fx, iy))
                c.path(
                    "mid_back",
                    fold,
                    None,
                    stroke=role,
                    sw=14,
                    alpha=alpha,
                    tag="drape_fold",
                    lod=2,
                    shadow=False,
                    anim=an(),
                )
            # the gold tie-back cord with a tassel, wrapped around the pinch of the drape
            ty = 640.0
            bx = min(X(-30), X(94))
            c.rect(
                "mid_back",
                bx,
                ty - 13,
                124,
                26,
                "gold",
                12,
                tag="tieback",
                elev=4,
                lod=1,
                anim=an(),
            )
            c.rect(
                "mid_back",
                bx,
                ty - 13,
                124,
                8,
                "white",
                4,
                alpha=0.3,
                tag="tieback",
                lod=2,
                shadow=False,
                anim=an(),
            )
            tsx = X(88)
            c.poly(
                "mid_back",
                [
                    (tsx - 13, ty + 12),
                    (tsx + 13, ty + 12),
                    (tsx + 20, ty + 108),
                    (tsx - 20, ty + 108),
                ],
                "gold2",
                smooth=0.2,
                tag="tassel",
                elev=3,
                lod=2,
                anim=an(),
            )
            c.ellipse(
                "mid_back", tsx, ty + 24, 14, 14, "gold", tag="tassel", elev=3, lod=2, anim=an()
            )

    def valance(self) -> None:
        """Scalloped pelmet across the top with gold fringe (same plane as the drapes: it frames the stage)."""
        c = self.c
        n = 15
        sw = (X1 - X0) / n
        base_y = 150.0
        pts: list[Pt] = [(X0, Y0), (X1, Y0)]
        for i in range(n - 1, -1, -1):
            cx = X0 + sw * (i + 0.5)
            for k in range(9):
                a = math.radians(180 * k / 8)
                pts.append((cx + (sw / 2) * math.cos(a), base_y + 70 * math.sin(a)))
        c.poly("mid_front", pts, "curtain", tag="valance", material="fabric", elev=14, lod=0)
        fringe: list[tuple[Any, ...]] = []
        edge: list[tuple[Any, ...]] = []
        for i in range(n):
            cx = X0 + sw * (i + 0.5)
            hi = [
                (
                    cx + (sw / 2) * math.cos(math.radians(180 * k / 16)),
                    base_y + 70 * math.sin(math.radians(180 * k / 16)),
                )
                for k in range(17)
            ]
            c.poly(
                "mid_front",
                [
                    (cx - sw / 2 + 6, base_y + 4),
                    (cx + sw / 2 - 6, base_y + 4),
                    (cx + sw * 0.28, base_y + 58),
                    (cx - sw * 0.28, base_y + 58),
                ],
                "curtain3",
                smooth=0.3,
                alpha=0.5,
                tag="valance_swag",
                lod=2,
                shadow=False,
            )
            edge += [("M", hi[0][0], hi[0][1])] + [("L", px, py) for px, py in hi[1:]]
            for k in range(1, 16):
                px, py = hi[k]
                fringe += [("M", px, py), ("L", px + 2, py + 22 + (k % 3) * 3)]
            c.ellipse(
                "mid_front",
                cx - sw / 2,
                base_y,
                13,
                13,
                "gold",
                tag="valance_tassel",
                elev=3,
                lod=2,
            )
        c.path(
            "mid_front", edge, None, stroke="gold", sw=9, tag="valance_trim", lod=1, shadow=False
        )
        c.path(
            "mid_front",
            fringe,
            None,
            stroke="gold2",
            sw=3.5,
            tag="valance_fringe",
            lod=2,
            shadow=False,
        )

    def backdrop_spotlight(self) -> None:
        c = self.c
        c.rect(
            "sky",
            X0,
            Y0,
            X1 - X0,
            Y1 - Y0,
            "wall",
            tag="wall",
            shadow=False,
            gradient=("wall", "wall2", 90.0),
        )
        self.halo("back", 540, 880, 780, self.col("led"), 0.34 * self.k, ry=900)
        # faint vertical LED lines on the back wall
        for x in range(-540, 1620, 180):
            self.fade_rect("back", x, Y0, 4, FLOOR_Y - Y0, "led", 0.10, 0.28, tag="led_line")
        # pylons at both edges with a glowing strip
        for px, role in ((-30.0, "led"), (1020.0, "led2")):
            c.rect(
                "back",
                px,
                Y0,
                90,
                FLOOR_Y - Y0 + 60,
                "wall2",
                tag="pylon",
                material="metal",
                elev=8,
                lod=1,
                shadow=False,
                gradient=(
                    darken(self.col("wall2"), 0.3),
                    self.col("wall2"),
                    0.0 if px < 0 else 180.0,
                ),
            )
            sx = px + 36 if px < 0 else px + 28
            c.rect(
                "back",
                sx,
                Y0,
                12,
                FLOOR_Y - Y0,
                role,
                tag="led_strip",
                material="emissive",
                shadow=False,
                glow=34,
                lod=1,
                anim=Anim("twinkle", amp=0.18, freq=0.12, phase=0.0 if px < 0 else 2.0),
            )
            c.rect(
                "back",
                sx + 4,
                Y0,
                4,
                FLOOR_Y - Y0,
                "white",
                tag="led_strip",
                material="emissive",
                shadow=False,
                alpha=0.7,
                lod=2,
            )
        # neon edge where wall meets floor
        c.rect(
            "back",
            X0,
            FLOOR_Y - 6,
            X1 - X0,
            6,
            "led",
            tag="led_edge",
            material="emissive",
            shadow=False,
            glow=18,
            alpha=0.9,
            lod=1,
        )
        self.fade_rect(
            "back", X0, FLOOR_Y - 220, X1 - X0, 220, "led", 0.0, 0.20, tag="led_edge_glow"
        )

    def led_reflections(self) -> None:
        """Glossy floor: the LED pylons reflected as soft streaks (drawn after the floor)."""
        for px, role in ((-30.0, "led"), (1020.0, "led2")):
            sx = px + 36 if px < 0 else px + 28
            self.fade_rect(
                "mid_back", sx - 6, FLOOR_Y, 24, 520, role, 0.30, 0.0, tag="led_reflection"
            )
            self.fade_rect(
                "mid_back", sx - 30, FLOOR_Y, 72, 360, role, 0.10, 0.0, tag="led_reflection"
            )

    def truss(self) -> dict[int, Pt]:
        """Lighting rig along the top edge with a fixture over each beam; returns the beam apexes."""
        c = self.c
        pl = "mid_front"
        c.rect(pl, X0, 8, X1 - X0, 16, "metal", 4, tag="truss", material="metal", elev=6, lod=0)
        c.rect(pl, X0, 70, X1 - X0, 16, "metal", 4, tag="truss", material="metal", elev=6, lod=0)
        zz: list[tuple[Any, ...]] = []
        for x in range(int(X0), int(X1), 60):
            zz += [("M", x, 16), ("L", x + 30, 78), ("L", x + 60, 16)]
        c.path(pl, zz, None, stroke="metal", sw=5, tag="truss", lod=1, shadow=False)
        xs, _ = self.pool_xs()
        apex: dict[int, Pt] = {}
        for i, xp in enumerate(xs):
            xa = 540 + (xp - 540) * 0.5
            c.rect(
                pl, xa - 34, 86, 68, 56, "ink", 10, tag="fixture", material="metal", elev=6, lod=0
            )
            c.ellipse(
                pl,
                xa,
                144,
                40,
                10,
                self.beam,
                material="emissive",
                shadow=False,
                glow=34,
                tag="fixture_lens",
                lod=1,
            )
            self.halo(pl, xa, 150, 130, self.beam, 0.50 * self.k)
            apex[i] = (xa, 146.0)
        return apex

    def backdrop_city(self) -> None:
        c = self.c
        sk_bot = self.col("sky_bottom")
        c.rect(
            "sky",
            X0,
            Y0,
            X1 - X0,
            FLOOR_Y + 40 - Y0,
            "sky_top",
            tag="sky",
            material="emissive",
            shadow=False,
            gradient=("sky_top", "sky_bottom", 90.0),
        )
        c.rect(
            "sky",
            X0,
            FLOOR_Y + 39,
            X1 - X0,
            Y1 - FLOOR_Y - 39,
            "sky_bottom",
            tag="sky",
            material="emissive",
            shadow=False,
        )
        night = self.tod == "night"
        cx, cy, r = {
            "dawn": (260, 960, 120),
            "day": (780, 520, 100),
            "dusk": (800, 980, 130),
            "night": (280, 470, 72),
        }[self.tod]
        sc = "#f4f6ff" if night else self.col("glow")
        self.halo("sky", cx, cy, r * 3.4, sc, 0.30)
        c.ellipse(
            "sky",
            cx,
            cy,
            r,
            r,
            sc,
            material="emissive",
            shadow=False,
            glow=r * 1.6,
            tag="moon" if night else "sun",
            lod=1,
        )
        if night:
            for i in range(34):
                s = c.ellipse(
                    "sky",
                    float(self.rng.uniform(X0, X1)),
                    float(self.rng.uniform(Y0, 760)),
                    (rr := float(self.rng.uniform(1.8, 4.2))),
                    rr,
                    "#ffffff",
                    material="emissive",
                    shadow=False,
                    tag="star",
                    lod=2,
                )
                if i % 3 == 0:
                    s.anim = Anim(
                        "twinkle",
                        amp=0.7,
                        freq=float(self.rng.uniform(0.2, 0.6)),
                        phase=float(self.rng.uniform(0, 6.28)),
                    )
        # two skyline layers with lit windows
        for layer, (plane, hmin, hmax, wmin, wmax, tint) in enumerate(
            (("far", 90, 270, 56, 110, 0.40), ("back", 50, 175, 70, 140, 0.72))
        ):
            colr = mix(sk_bot, self.col("stone2"), tint)
            colr = (
                darken(colr, 0.35 if night else (0.1 if self.tod == "day" else 0.22))
                if layer
                else darken(colr, 0.15 if night else 0.0)
            )
            win: list[tuple[Any, ...]] = []
            x = X0 - 20.0 + layer * 40
            while x < X1:
                bw = float(self.rng.uniform(wmin, wmax))
                bh = float(self.rng.uniform(hmin, hmax))
                top = FLOOR_Y - bh + (30 if layer else 0)
                c.rect(
                    plane,
                    x,
                    top,
                    bw,
                    FLOOR_Y + 240 - top,
                    colr,
                    tag="skyline",
                    material="emissive",
                    shadow=False,
                    lod=0 if layer else 1,
                )
                if self.lit or night:
                    cols, rows = max(1, int((bw - 14) / 22)), int((bh - 20) / 30)
                    for ci in range(cols):
                        for ri in range(rows):
                            if float(self.rng.random()) < (0.42 if night else 0.26):
                                wx, wy = x + 10 + ci * 22, top + 14 + ri * 30
                                win += [
                                    ("M", wx, wy),
                                    ("L", wx + 10, wy),
                                    ("L", wx + 10, wy + 15),
                                    ("L", wx, wy + 15),
                                    ("Z",),
                                ]
                x += bw + float(self.rng.uniform(0, 8))
            if win:
                c.path(
                    plane,
                    win,
                    "window",
                    material="emissive",
                    alpha=0.7 if layer else 0.5,
                    tag="city_light",
                    lod=2,
                    shadow=False,
                )
        # bokeh lights: mostly above the performers' heads and out at the sides
        for i in range(14):
            if i < 9:
                bx, by = float(self.rng.uniform(-300, 1380)), float(self.rng.uniform(80, 640))
            else:
                bx = (
                    float(self.rng.uniform(-300, 90))
                    if i % 2
                    else float(self.rng.uniform(990, 1380))
                )
                by = float(self.rng.uniform(640, 1100))
            rr = float(self.rng.uniform(34, 96))
            role = ("bokeh1", "bokeh2", "bokeh3")[i % 3]
            kk = {"dawn": 0.6, "day": 0.28, "dusk": 0.9, "night": 1.0}[self.tod]
            c.ellipse(
                "back",
                bx,
                by,
                rr,
                rr,
                role,
                alpha=0.20 * kk,
                material="emissive",
                shadow=False,
                glow=rr * 0.5,
                tag="bokeh",
                lod=2,
                anim=Anim(
                    "twinkle",
                    amp=0.5,
                    freq=float(self.rng.uniform(0.08, 0.2)),
                    phase=float(self.rng.uniform(0, 6.28)),
                ),
            )
        self.fade_rect(
            "back", X0, FLOOR_Y - 260, X1 - X0, 260, "black", 0.0, 0.35, tag="city_shade"
        )

    def backdrop_confetti(self) -> None:
        c = self.c
        c.rect(
            "sky",
            X0,
            Y0,
            X1 - X0,
            Y1 - Y0,
            "wall",
            tag="wall",
            shadow=False,
            gradient=("wall", "wall2", 90.0),
        )
        # big translucent party circles
        cols = ("accent", "accent2", "accent3", "fabric", "pink")
        for i in range(10 + int(8 * self.d)):
            x = float(self.rng.uniform(-300, 1380))
            y = float(self.rng.uniform(100, 1100))
            r = float(self.rng.uniform(40, 140))
            c.ellipse(
                "far",
                x,
                y,
                r,
                r,
                lighten(self.col(cols[i % 5]), 0.55),
                alpha=0.32,
                tag="party_circle",
                lod=2,
                shadow=False,
            )
        # foil fringe along the top
        for i, x in enumerate(range(-560, 1640, 24)):
            ln = 175 + 45 * math.sin(i * 1.3) + 30 * math.sin(i * 0.37)
            colr = cols[i % 5]
            c.rect(
                "back",
                x,
                Y0,
                14,
                ln - Y0,
                colr,
                6,
                tag="foil",
                material="paper",
                lod=2,
                shadow=False,
                alpha=0.9,
            )
            c.rect(
                "back",
                x + 3,
                Y0,
                4,
                ln - Y0,
                "white",
                2,
                tag="foil",
                lod=2,
                shadow=False,
                alpha=0.35,
            )
        self.fade_rect("back", X0, Y0, X1 - X0, 700, "wall", 0.0, 0.0, tag="foil_fade")
        # bunting swags
        for x0, x1, ys, sag in ((-80.0, 540.0, 260.0, 70.0), (540.0, 1160.0, 260.0, 70.0)):
            cx_, cy_ = (x0 + x1) / 2, ys + sag * 2
            c.path(
                "back",
                [("M", x0, ys), ("Q", cx_, cy_, x1, ys)],
                None,
                stroke="ink",
                sw=4,
                alpha=0.7,
                tag="bunting_string",
                lod=1,
                shadow=False,
            )
            n = 10
            for k in range(n):
                t = (k + 0.5) / n
                px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx_ + t * t * x1
                py = (1 - t) ** 2 * ys + 2 * (1 - t) * t * cy_ + t * t * ys
                colr = cols[(k * 2 + int(x0)) % 5]
                c.poly(
                    "back",
                    [(px - 30, py - 2), (px + 30, py - 2), (px, py + 70)],
                    colr,
                    smooth=0.06,
                    tag="bunting",
                    material="paper",
                    elev=4,
                    lod=0,
                    anim=Anim(
                        "sway", amp=3.0, freq=0.18, phase=k * 0.9 + x0 * 0.01, pivot=(px, py - 2)
                    ),
                )
        # balloons in the corners
        for bi, (bx, by, colr, sc) in enumerate(
            (
                (60, 1020, "accent", 1.0),
                (160, 930, "accent3", 0.9),
                (30, 840, "fabric", 1.05),
                (1030, 1000, "pink", 1.0),
                (940, 920, "accent2", 0.95),
                (1060, 820, "accent3", 1.0),
            )
        ):
            anchor = (bx + (30 if bx < 540 else -30), FLOOR_Y + 40)
            piv = anchor
            a = Anim("sway", amp=1.4, freq=0.1 + 0.02 * bi, phase=bi * 1.4, pivot=piv)
            c.line(
                "back",
                (bx, by + 70 * sc),
                anchor,
                3,
                "ink",
                alpha=0.5,
                tag="balloon_string",
                lod=0,
                anim=a,
            )
            c.ellipse(
                "back",
                bx,
                by,
                52 * sc,
                66 * sc,
                colr,
                tag="balloon",
                material="paper",
                elev=6,
                lod=0,
                anim=a,
            )
            c.ellipse(
                "back",
                bx - 18 * sc,
                by - 24 * sc,
                10 * sc,
                18 * sc,
                "white",
                rot=25,
                alpha=0.5,
                tag="balloon",
                lod=2,
                shadow=False,
                anim=a,
            )
            c.poly(
                "back",
                [(bx - 7, by + 66 * sc), (bx + 7, by + 66 * sc), (bx, by + 78 * sc)],
                colr,
                tag="balloon",
                lod=2,
                shadow=False,
                anim=a,
            )

    def confetti(self) -> None:
        if not (self.p.backdrop == "confetti" or (self.c.mood == "playful" and self.p.sparkles)):
            return
        n = 56 if self.p.backdrop == "confetti" else 34
        # wide areas so the confetti still fills the frame when the camera pans (planes shift at different rates)
        self.c.emit(
            "near",
            Particles(
                "confetti",
                (X0, -80, X1, 1960),
                int(n * (0.6 + 0.8 * self.d) * 1.8),
                speed=0.9,
                size=22,
                alpha=0.95,
                seed=self.c.seed + 5,
            ),
        )
        self.c.emit(
            "mid_back",
            Particles(
                "confetti",
                (-300, -80, 1380, 1500),
                int(n * 1.1),
                speed=0.65,
                size=15,
                alpha=0.9,
                seed=self.c.seed + 9,
            ),
        )


@register_background(
    "stage",
    params_schema=StageParams,
    summary="Studio / theatre stage for titles and punchlines: curtains, studio or city backdrop (or a "
    "confetti party), polished floor with pulsing spotlights, optional podium and sparkles. Slots: "
    "'spot_left'/'spot_right' stand in the beams, 'podium' behind the lectern, 'apron' down-stage",
    slots={
        "spot_left": (0.27, 0.74),
        "spot_right": (0.73, 0.74),
        "podium": (0.50, 0.74),
        "apron": (0.50, 0.84),
    },
    tags=("stage", "studio", "title", "celebration"),
    ground_y=0.74,
    perspective=0.7,
    horizon=0.62,
)
def stage(ctx: BuildContext, p: StageParams) -> None:
    """Stage set"""
    ctx.graph.scheme = make_scheme(
        ctx.time_of_day, ctx.mood, {**BASE_ROLES, **PALETTES[p.backdrop]}
    )
    s = _Stage(ctx, p)
    if p.backdrop == "curtain":
        s.backdrop_curtain()
    elif p.backdrop == "spotlight":
        s.backdrop_spotlight()
    elif p.backdrop == "city_lights":
        s.backdrop_city()
    else:
        s.backdrop_confetti()
    s.floor("wood" if p.backdrop in ("curtain", "confetti") else "tiles")
    if p.backdrop == "spotlight":
        s.led_reflections()
    apex: dict[int, Pt] = {}
    if p.backdrop == "curtain":
        s.drapes()
    if p.backdrop in ("curtain", "city_lights") and p.beams > 0:
        ly = 250.0 if p.backdrop == "curtain" else 200.0
        left = s.fixture(40, ly, -22, 0.9)
        right = s.fixture(1040, ly, 22, 0.9)
        xs, _ = s.pool_xs()
        for i, xp in enumerate(xs):
            if xp < 500:
                apex[i] = left
            elif xp > 580:
                apex[i] = right
    elif p.backdrop == "spotlight":
        apex = s.truss()
    s.lights(apex)
    if p.backdrop == "curtain":
        s.valance()
    if p.podium:
        s.podium()
    s.confetti()
