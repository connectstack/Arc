"""whiteboard: an explainer set - a clean off-white board filling the frame, marker doodles in the
margins, an aluminium marker tray as the ledge characters stand on.  Reads as line art on a board."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field

from reel.core.geometry import Pt, darken, lighten
from reel.core.ir import Anim
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

LEDGE_Y = 1440.0  # front of the standing area (ground line when there is no tray; markers lie here)
TRAY_BACK = 1330.0  # where the board meets the tray
TRAY_FRONT = (
    1458.0  # front edge of the tray's top face: characters stand anywhere on it (feet y 0.69..0.76)
)
LIP_BOTTOM = 1498.0  # bottom of the tray's front lip

ROLES: dict[str, str] = {
    "board": "#f3f5f8",
    "grid": "#bfcbd8",
    "ink": "#2a2e3a",
    "accent": "#e2483d",
    "accent2": "#1f9d8b",
    "accent3": "#f4a712",
    "fabric": "#3a67b3",
    "marker_blue": "#2e5bb0",
    "metal": "#a9b4bf",
    "wall": "#cdd6dd",
    "wall2": "#bac5ce",
    "paper": "#fffbe8",
}


class WhiteboardParams(BgParams):
    grid: Literal["none", "dots", "lines"] = Field(
        "dots",
        description="faint pattern printed on the board: dots (dot grid), lines (graph-paper squares) "
        "or none (blank board)",
    )
    doodles: Literal["ideas", "business", "science", "none"] = Field(
        "ideas",
        description="theme of the hand-drawn marker doodles in the margins: ideas (lightbulb, chart, "
        "arrows, stars, underline), business (coins, rising chart, target), science (atom, flask, "
        "gear) or none",
    )
    marker: Literal["multi", "black", "blue"] = Field(
        "multi",
        description="marker colours of the doodles: multi (several bright colours), black or blue (one ink)",
    )
    tray: bool = Field(
        True,
        description="show the aluminium marker tray (with markers and an eraser) as the ledge the "
        "characters stand on; false = just a hand-drawn ground line on the board",
    )


# ----------------------------------------------------------------------------------- path helpers
Cmd = tuple[Any, ...]


def _cr(pts: list[Pt], closed: bool = False, tension: float = 0.5) -> list[Cmd]:
    """Catmull-Rom spline through ``pts`` as M + C commands."""
    n = len(pts)
    cmds: list[Cmd] = [("M", pts[0][0], pts[0][1])]
    for i in range(n if closed else n - 1):
        p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        p1, p2 = pts[i], pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
        k = tension / 3 * 2
        cmds.append(
            ("C", p1[0] + (p2[0] - p0[0]) * k, p1[1] + (p2[1] - p0[1]) * k,
             p2[0] - (p3[0] - p1[0]) * k, p2[1] - (p3[1] - p1[1]) * k, p2[0], p2[1])
        )  # fmt: skip
    if closed:
        cmds.append(("Z",))
    return cmds


def _poly(pts: list[Pt], closed: bool = True) -> list[Cmd]:
    cmds: list[Cmd] = [("M", pts[0][0], pts[0][1])]
    cmds += [("L", x, y) for x, y in pts[1:]]
    if closed:
        cmds.append(("Z",))
    return cmds


class _Board:
    def __init__(self, ctx: BuildContext, p: WhiteboardParams) -> None:
        self.c = ctx
        self.p = p
        self.rng = ctx.rng
        self.d = ctx.density
        self.tod = ctx.time_of_day
        self.lit = ctx.graph.lamps_on

    # -- colours -------------------------------------------------------------------------------------
    def col(self, c: str) -> str:
        return str(self.c.scheme.base(c))

    def mk(self, role: str) -> str:
        """Marker colour for a doodle: its own colour in 'multi' mode, one ink otherwise."""
        return {"multi": role, "black": "ink", "blue": "marker_blue"}[self.p.marker]

    def j(self, v: float) -> float:  # hand-drawn wobble
        return float(self.rng.uniform(-v, v))

    # -- primitives -----------------------------------------------------------------------------------------
    def stroke(
        self, cmds: list[Cmd], color: str, sw: float = 8.0, *, anim: Anim | None = None, alpha: float = 1.0,
        lod: int = 0, tag: str = "doodle", fill: str | None = None, fill_alpha: float = 0.0,
    ) -> None:  # fmt: skip
        self.c.path(
            "back",
            cmds,
            fill if fill_alpha else None,
            stroke=color,
            sw=sw,
            alpha=alpha,
            tag=tag,
            lod=lod,
            shadow=False,
            anim=anim,
        )

    def wash(
        self,
        cmds: list[Cmd],
        color: str,
        alpha: float = 0.3,
        *,
        anim: Anim | None = None,
        tag: str = "doodle_fill",
    ) -> None:
        """Highlighter-like colour fill under an outline (decoration: skipped by line-art styles)."""
        if self.p.marker != "multi":
            alpha *= 0.45
        self.c.path("back", cmds, color, alpha=alpha, tag=tag, lod=1, shadow=False, anim=anim)

    def hand_circle(
        self,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        *,
        rot: float = 0.0,
        start: float = -110.0,
        sweep: float = 372.0,
        wob: float = 0.035,
    ) -> list[Pt]:
        n = 14
        pts: list[Pt] = []
        cr, sr = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        for i in range(n + 1):
            a = math.radians(start + sweep * i / n)
            k = 1.0 + self.j(wob) + 0.03 * i / n  # slight spiral so the ends do not meet exactly
            x, y = rx * k * math.cos(a), ry * k * math.sin(a)
            pts.append((cx + x * cr - y * sr, cy + x * sr + y * cr))
        return pts

    def anim_sway(self, pivot: Pt, amp: float = 1.4, freq: float = 0.11) -> Anim:
        return Anim("sway", amp=amp, freq=freq, phase=float(self.rng.uniform(0, 6.28)), pivot=pivot)

    # -- the board ---------------------------------------------------------------------------------------
    def surface(self) -> None:
        c = self.c
        bottom = Y1  # the tray and the wall below it (a nearer plane) cover the lower part
        top_c = lighten(self.col("board"), 0.35)
        c.rect(
            "back", X0, Y0, X1 - X0, bottom - Y0, "board", tag="board", material="paper", shadow=False,
            gradient=(top_c, self.col("board"), 90.0),
        )  # fmt: skip
        # slow diagonal sheen (gloss) and a soft shade near the top
        for x0, w0, a in ((-260.0, 230.0, 0.16), (240.0, 70.0, 0.10), (420.0, 26.0, 0.09)):
            c.poly(
                "back", [(x0, Y0), (x0 + w0, Y0), (x0 + w0 + 1250, TRAY_BACK), (x0 + 1250, TRAY_BACK)],
                "#ffffff", alpha=a, material="emissive", shadow=False, tag="gloss", lod=2,
            )  # fmt: skip
        sh0, sh1 = "#00000000", _rgba(self.col("shadow"), 0.10)
        c.rect(
            "back",
            X0,
            Y0,
            X1 - X0,
            520,
            sh1,
            material="emissive",
            shadow=False,
            tag="board_shade",
            lod=2,
            gradient=(sh1, sh0, 90.0),
        )
        if self.lit:  # a wall-washer lamp above the board
            wash = _rgba(self.col("glow"), 0.34)
            c.rect(
                "back",
                X0,
                Y0,
                X1 - X0,
                1250,
                wash,
                material="emissive",
                shadow=False,
                tag="board_light",
                lod=2,
                gradient=(wash, _rgba(self.col("glow"), 0.0), 90.0),
            )
        # frame rails at the far edges of the stage (only seen when the camera swings wide)
        for x, w in ((X0, 26.0), (X1 - 26.0, 26.0)):
            c.rect("back", x, Y0, w, bottom - Y0, "metal", tag="frame", elev=4, lod=1)
        c.rect("back", X0, Y0, X1 - X0, 24, "metal", tag="frame", elev=4, lod=1)
        # pattern
        cmds: list[Cmd] = []
        gy1 = TRAY_BACK - 20 if self.p.tray else LEDGE_Y - 20
        if self.p.grid == "dots":
            for y in range(-300 + 30, int(gy1), 60):
                for x in range(-540 + 30, 1620, 60):
                    cmds += [
                        ("M", x, y - 3.4),
                        ("L", x + 3.4, y),
                        ("L", x, y + 3.4),
                        ("L", x - 3.4, y),
                        ("Z",),
                    ]
            c.path("back", cmds, "grid", alpha=0.75, tag="board_pattern", lod=2, shadow=False)
        elif self.p.grid == "lines":
            for y in range(-300, int(gy1), 60):
                cmds += [("M", X0, y), ("L", X1, y)]
            for x in range(-540, 1620, 60):
                cmds += [("M", x, Y0), ("L", x, gy1)]
            c.path(
                "back",
                cmds,
                None,
                stroke="grid",
                sw=1.8,
                alpha=0.6,
                tag="board_pattern",
                lod=2,
                shadow=False,
            )
        # ghosts of old writing wiped off the board
        for _ in range(4 + int(3 * self.d)):
            cx = float(self.rng.uniform(-100, 1180))
            cy = float(self.rng.uniform(40, 1300))
            c.ellipse(
                "back", cx, cy, float(self.rng.uniform(70, 170)), float(self.rng.uniform(12, 28)), "grid",
                rot=float(self.rng.uniform(-25, 25)), alpha=0.07, glow=40, tag="smudge", lod=2, shadow=False,
            )  # fmt: skip

    def ground(self) -> None:
        """Marker tray (or a drawn line) that the characters stand on, and the wall below the board."""
        c = self.c
        if not self.p.tray:
            ys = LEDGE_Y
            pts = [(float(x), ys + self.j(3.0)) for x in range(-560, 1660, 120)]
            c.path("mid_back", _cr(pts), None, stroke="ink", sw=9, tag="ledge", lod=0, shadow=False)
            ticks: list[Cmd] = []
            for x in range(-540, 1620, 46):
                ticks += [("M", x, ys + 16), ("L", x - 20, ys + 38 + self.j(4))]
            c.path(
                "mid_back",
                ticks,
                None,
                stroke="ink",
                sw=5,
                alpha=0.45,
                tag="ground_hatch",
                lod=1,
                shadow=False,
            )
            return
        # wall below the board
        c.rect(
            "mid_back", X0, LIP_BOTTOM, X1 - X0, Y1 - LIP_BOTTOM, "wall", tag="wall", material="paper", lod=1, shadow=False,
            gradient=("wall", "wall2", 90.0),
        )  # fmt: skip
        # tray: shadow, back rail, top face, front lip
        sh = _rgba(self.col("shadow"), 0.30)
        c.rect(
            "mid_back",
            X0,
            LIP_BOTTOM,
            X1 - X0,
            70,
            sh,
            material="emissive",
            shadow=False,
            tag="tray_shadow",
            lod=2,
            gradient=(sh, _rgba(self.col("shadow"), 0.0), 90.0),
        )
        c.rect(
            "mid_back",
            X0,
            TRAY_BACK - 8,
            X1 - X0,
            16,
            "metal",
            tag="tray_rail",
            material="metal",
            elev=3,
            lod=1,
        )
        top_light = lighten(self.col("metal"), 0.45)
        c.rect(
            "mid_back", X0, TRAY_BACK, X1 - X0, TRAY_FRONT - TRAY_BACK, "metal", tag="tray", material="metal", elev=6, lod=0,
            gradient=(top_light, lighten(self.col("metal"), 0.1), 90.0),
        )  # fmt: skip
        c.rect(
            "mid_back",
            X0,
            TRAY_FRONT,
            X1 - X0,
            LIP_BOTTOM - TRAY_FRONT,
            "metal",
            tag="tray_lip",
            material="metal",
            elev=7,
            lod=0,
            gradient=(darken(self.col("metal"), 0.1), darken(self.col("metal"), 0.28), 90.0),
        )
        c.rect(
            "mid_back",
            X0,
            TRAY_FRONT,
            X1 - X0,
            5,
            "white",
            alpha=0.55,
            tag="tray",
            lod=2,
            shadow=False,
        )
        c.rect(
            "mid_back",
            X0,
            TRAY_BACK + 3,
            X1 - X0,
            4,
            "white",
            alpha=0.5,
            tag="tray",
            lod=2,
            shadow=False,
        )
        # markers and an eraser lying on the tray
        for mx, ang, colr in (
            (22, -3, "accent"),
            (66, 2, "fabric"),
            (906, -2, "accent2"),
            (944, 3, "ink"),
            (990, -1, "accent3"),
        ):
            self.marker(mx, LEDGE_Y - 8 + (ang * 2), ang, colr)
        self.eraser(826, LEDGE_Y - 14)

    def marker(self, x: float, y: float, ang: float, colr: str) -> None:
        c = self.c
        ln = 118.0
        a = math.radians(ang)
        ux, uy = math.cos(a), math.sin(a)
        c.ellipse(
            "mid_back",
            x + ln / 2 * ux,
            y + 14,
            ln * 0.52,
            7,
            "shadow",
            alpha=0.22,
            tag="marker_shadow",
            lod=2,
            shadow=False,
        )
        x1, y1 = x + ln * ux, y + ln * uy
        c.limb(
            "mid_back",
            (x + 4, y),
            (x1 - 4, y1),
            22,
            22,
            colr,
            tag="marker",
            material="metal",
            elev=3,
            lod=1,
        )
        c.limb(
            "mid_back",
            (x + 4, y - 4),
            (x1 - 4, y1 - 4),
            6,
            6,
            "white",
            alpha=0.35,
            tag="marker",
            lod=2,
            shadow=False,
        )
        capx = x + ln * 0.68
        c.limb(
            "mid_back",
            (capx, y + (capx - x) * math.tan(a)),
            (x1 + 4, y1),
            26,
            26,
            darken(self.col(colr), 0.25),
            tag="marker_cap",
            material="metal",
            elev=3,
            lod=2,
            shadow=False,
        )
        c.limb(
            "mid_back",
            (x - 10, y),
            (x + 6, y),
            12,
            12,
            "ink",
            tag="marker_tip",
            lod=2,
            shadow=False,
        )

    def eraser(self, x: float, y: float) -> None:
        c = self.c
        c.ellipse(
            "mid_back",
            x + 36,
            y + 22,
            50,
            8,
            "shadow",
            alpha=0.22,
            tag="marker_shadow",
            lod=2,
            shadow=False,
        )
        c.rect("mid_back", x, y - 8, 76, 30, "paper", 8, tag="eraser", elev=4, lod=1)
        c.rect("mid_back", x, y - 8, 76, 14, "ink", 7, tag="eraser", lod=2, shadow=False)
        c.rect(
            "mid_back",
            x + 6,
            y + 10,
            64,
            6,
            "grid",
            3,
            tag="eraser",
            lod=2,
            shadow=False,
            alpha=0.8,
        )

    # -- doodles -------------------------------------------------------------------------------------------
    def bulb(self, cx: float, cy: float, s: float) -> None:
        R = 62 * s
        mono = self.p.marker != "multi"
        ink, glow = self.mk("ink"), self.mk("accent3")
        pts: list[Pt] = []
        for i in range(17):  # big arc over the top (lower-left round to lower-right) ...
            a = math.radians(128 + 284 * i / 16)
            pts.append(
                (
                    cx + R * math.cos(a) * (1 + self.j(0.012)),
                    cy + R * math.sin(a) * (1 + self.j(0.012)),
                )
            )
        pts += [(cx + R * 0.34, cy + R * 1.14), (cx - R * 0.34, cy + R * 1.14)]  # ... and the neck
        piv = (cx, cy + R * 1.2)
        an = self.anim_sway(piv, 1.2, 0.1)
        # glow disc + colour wash (decoration)
        self.c.ellipse(
            "back",
            cx,
            cy - 4 * s,
            R * 1.7,
            R * 1.7,
            glow,
            alpha=0.06 if mono else 0.16,
            material="emissive",
            shadow=False,
            glow=R * 0.7,
            tag="doodle_glow",
            lod=2,
            anim=Anim("pulse", amp=0.06, freq=0.17, phase=1.0, pivot=(cx, cy)),
        )
        self.wash(_cr(pts, True), glow, 0.12 if mono else 0.32, anim=an)
        self.stroke(_cr(pts, True), ink, 9 * s**0.5, anim=an, tag="doodle_bulb")
        y0 = cy + R * 1.14
        base: list[Cmd] = [
            ("M", cx - 28 * s, y0 + 14 * s),
            ("L", cx + 28 * s, y0 + 12 * s),
            ("M", cx - 24 * s, y0 + 32 * s),
            ("L", cx + 24 * s, y0 + 33 * s),
            ("M", cx - 11 * s, y0 + 50 * s),
            ("L", cx + 11 * s, y0 + 50 * s),
        ]
        self.stroke(base, ink, 8 * s**0.5, anim=an, tag="doodle_bulb")
        fil: list[Cmd] = [
            ("M", cx - 16 * s, cy + R * 0.98),
            ("L", cx - 16 * s, cy + 14 * s),
            ("Q", cx - 8 * s, cy - 18 * s, cx, cy + 4 * s),
            ("Q", cx + 8 * s, cy - 18 * s, cx + 16 * s, cy + 14 * s),
            ("L", cx + 16 * s, cy + R * 0.98),
        ]
        self.stroke(fil, self.mk("accent"), 6 * s**0.5, anim=an, tag="doodle_bulb")
        rays: list[Cmd] = []
        for k in range(7):
            a = math.radians(-180 + 30 * k)
            r0, r1 = R * 1.3, R * (1.6 + 0.06 * (k % 2))
            rays += [
                ("M", cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                ("L", cx + r1 * math.cos(a), cy + r1 * math.sin(a)),
            ]
        self.stroke(
            rays,
            glow if not mono else ink,
            8 * s**0.5,
            anim=Anim("twinkle", amp=0.45, freq=0.2, phase=0.5),
            tag="doodle_rays",
        )

    def star(self, cx: float, cy: float, r: float, role: str = "accent3") -> None:
        pts: list[Pt] = []
        rot = self.j(0.35)
        for i in range(10):
            a = rot - math.pi / 2 + i * math.pi / 5
            rr = r * (1.0 if i % 2 == 0 else 0.46) * (1 + self.j(0.06))
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        an = Anim(
            "pulse",
            amp=0.07,
            freq=0.2 + 0.05 * float(self.rng.uniform(0, 1)),
            phase=float(self.rng.uniform(0, 6.28)),
            pivot=(cx, cy),
        )
        self.wash(_poly(pts), self.mk(role), 0.35, anim=an)
        self.stroke(
            _poly(pts),
            self.mk(role) if self.p.marker != "multi" else "ink",
            7,
            anim=an,
            tag="doodle_star",
        )

    def arrow(self, p0: Pt, p1: Pt, bend: float, role: str = "accent", sw: float = 9.0) -> None:
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        ln = math.hypot(dx, dy)
        nx, ny = -dy / ln, dx / ln
        cxp, cyp = (p0[0] + p1[0]) / 2 + nx * bend * ln, (p0[1] + p1[1]) / 2 + ny * bend * ln
        tx, ty = p1[0] - cxp, p1[1] - cyp
        tl = math.hypot(tx, ty)
        tx, ty = tx / tl, ty / tl
        an = self.anim_sway(p0, 1.5, 0.09)
        col = self.mk(role)
        cmds: list[Cmd] = [("M", p0[0], p0[1]), ("Q", cxp, cyp, p1[0], p1[1])]
        for sgn in (-1, 1):
            a = math.radians(sgn * 27 + self.j(4))
            hx = -(tx * math.cos(a) - ty * math.sin(a)) * 42
            hy = -(tx * math.sin(a) + ty * math.cos(a)) * 42
            cmds += [("M", p1[0], p1[1]), ("L", p1[0] + hx, p1[1] + hy)]
        self.stroke(cmds, col, sw, anim=an, tag="doodle_arrow")

    def underline(self, x0: float, x1: float, y: float, role: str = "accent") -> None:
        col = self.mk(role)
        an = self.anim_sway(((x0 + x1) / 2, y), 0.5, 0.08)
        pts = [(float(x), y + self.j(4.5)) for x in range(int(x0), int(x1) + 1, 44)]
        self.stroke(_cr(pts), col, 8, anim=an, tag="doodle_underline")
        pts2 = [(float(x), y + 24 + self.j(4)) for x in range(int(x0) + 70, int(x1) - 50, 44)]
        self.stroke(_cr(pts2), col, 7, anim=an, tag="doodle_underline", alpha=0.9)

    def qmark(self, x: float, y: float, s: float, role: str = "fabric") -> None:
        col = self.mk(role)
        an = Anim("bob", amp=5, freq=0.13, phase=float(self.rng.uniform(0, 6.28)))
        hook: list[Cmd] = [("M", x - 20 * s, y - 28 * s), ("C", x - 20 * s, y - 66 * s, x + 24 * s, y - 66 * s, x + 24 * s, y - 36 * s),
                           ("C", x + 24 * s, y - 10 * s, x + 2 * s, y - 12 * s, x + 2 * s, y + 14 * s)]  # fmt: skip
        self.stroke(hook, col, 9 * s**0.5, anim=an, tag="doodle_mark")
        self.stroke(
            [("M", x + 2 * s, y + 38 * s), ("L", x + 3 * s, y + 40 * s)],
            col,
            13 * s**0.5,
            anim=an,
            tag="doodle_mark",
        )

    def exclam(self, x: float, y: float, s: float, role: str = "accent") -> None:
        col = self.mk(role)
        an = Anim("bob", amp=5, freq=0.15, phase=float(self.rng.uniform(0, 6.28)))
        self.stroke(
            [("M", x, y - 52 * s), ("Q", x + 6 * s, y - 20 * s, x + 2 * s, y + 8 * s)],
            col,
            11 * s**0.5,
            anim=an,
            tag="doodle_mark",
        )
        self.stroke(
            [("M", x + 2 * s, y + 38 * s), ("L", x + 3 * s, y + 40 * s)],
            col,
            13 * s**0.5,
            anim=an,
            tag="doodle_mark",
        )

    def plus(self, x: float, y: float, s: float, role: str = "accent2") -> None:
        an = Anim(
            "pulse", amp=0.08, freq=0.17, phase=float(self.rng.uniform(0, 6.28)), pivot=(x, y)
        )
        cmds: list[Cmd] = [
            ("M", x - 22 * s, y + self.j(2)),
            ("L", x + 22 * s, y + 1 + self.j(2)),
            ("M", x + self.j(2), y - 22 * s),
            ("L", x + 1 + self.j(2), y + 22 * s),
        ]
        self.stroke(cmds, self.mk(role), 8, anim=an, tag="doodle_mark")

    def spiral(self, cx: float, cy: float, s: float, role: str = "fabric") -> None:
        pts: list[Pt] = []
        t = 0.0
        while t < 3.3 * math.pi:
            r = (3 + 5.4 * t) * s
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
            t += 0.55
        self.stroke(
            _cr(pts),
            self.mk(role),
            6,
            anim=Anim("spin", amp=1.0, freq=1 / 140, pivot=(cx, cy)),
            tag="doodle_spiral",
        )

    def chart(self, x: float, y: float, w: float, h: float) -> None:
        ink = self.mk("ink")
        s = w / 250
        axes: list[Cmd] = [
            ("M", x + self.j(2), y),
            ("L", x + self.j(2), y + h),
            ("L", x + w, y + h + self.j(2)),
        ]
        self.stroke(
            axes, ink, 9 * s**0.5, anim=self.anim_sway((x, y + h), 0.8, 0.09), tag="doodle_chart"
        )
        heights = (0.34, 0.52, 0.44, 0.74)
        cols = ("accent3", "accent2", "fabric", "accent")
        bw = w * 0.17
        for i, (hh, cr) in enumerate(zip(heights, cols)):
            bx = x + w * (0.12 + 0.21 * i)
            top = y + h - h * hh
            pts = [
                (bx + self.j(3), top + self.j(3)),
                (bx + bw + self.j(3), top + self.j(3)),
                (bx + bw + self.j(3), y + h - 4),
                (bx + self.j(3), y + h - 4),
            ]
            an = Anim(
                "pulse", amp=0.035, freq=0.14 + 0.02 * i, phase=i * 1.1, pivot=(bx + bw / 2, y + h)
            )
            self.c.path(
                "back",
                _poly(pts),
                self.mk(cr),
                stroke=ink,
                sw=6.5 * s**0.5,
                alpha=1.0,
                tag="doodle_bar",
                lod=0,
                shadow=False,
                anim=an,
            )
        # fill alpha is applied through a separate wash so line-art styles keep clean outlines
        trend = [
            (x + w * 0.08, y + h * 0.56),
            (x + w * 0.34, y + h * 0.36),
            (x + w * 0.54, y + h * 0.44),
            (x + w * 0.9, y + h * 0.04),
        ]
        self.arrow_path(trend, "accent", 8 * s**0.5)

    def arrow_path(self, pts: list[Pt], role: str, sw: float) -> None:
        col = self.mk(role)
        cmds = _cr(pts)
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        tx, ty = x1 - x0, y1 - y0
        tl = math.hypot(tx, ty)
        tx, ty = tx / tl, ty / tl
        for sgn in (-1, 1):
            a = math.radians(sgn * 28)
            hx = -(tx * math.cos(a) - ty * math.sin(a)) * 40
            hy = -(tx * math.sin(a) + ty * math.cos(a)) * 40
            cmds += [("M", x1, y1), ("L", x1 + hx, y1 + hy)]
        self.stroke(cmds, col, sw, anim=self.anim_sway(pts[0], 1.0, 0.1), tag="doodle_arrow")

    def coin(self, cx: float, cy: float, s: float) -> None:
        R = 58 * s
        ink, gold = self.mk("ink"), self.mk("accent3")
        an = self.anim_sway((cx, cy + R), 1.2, 0.1)
        outer = _cr(self.hand_circle(cx, cy, R, R * 0.97))
        self.wash(_cr(self.hand_circle(cx, cy, R, R)[:-1], True), gold, 0.4, anim=an)
        self.stroke(outer, ink, 9 * s**0.5, anim=an, tag="doodle_coin")
        self.stroke(
            _cr(self.hand_circle(cx, cy, R * 0.72, R * 0.7, start=-60)),
            ink,
            5,
            anim=an,
            tag="doodle_coin",
            lod=1,
            alpha=0.8,
        )
        sp = [
            (cx + 17 * s, cy - 26 * s),
            (cx - 2 * s, cy - 34 * s),
            (cx - 18 * s, cy - 18 * s),
            (cx - 2 * s, cy),
            (cx + 18 * s, cy + 16 * s),
            (cx + 3 * s, cy + 34 * s),
            (cx - 17 * s, cy + 26 * s),
        ]
        self.stroke(
            [*_cr(sp), ("M", cx, cy - 44 * s), ("L", cx + 1, cy + 44 * s)],
            ink,
            7 * s**0.5,
            anim=an,
            tag="doodle_coin",
        )

    def target(self, cx: float, cy: float, s: float) -> None:
        R = 70 * s
        an = self.anim_sway((cx, cy), 1.0, 0.09)
        for k, (f, role) in enumerate(((1.0, "ink"), (0.66, "accent"), (0.32, "ink"))):
            self.stroke(
                _cr(self.hand_circle(cx, cy, R * f, R * f * 0.98, start=-90 + k * 40)),
                self.mk(role),
                8 * s**0.5,
                anim=an,
                tag="doodle_target",
            )
        self.c.ellipse(
            "back",
            cx,
            cy,
            8 * s,
            8 * s,
            self.mk("accent"),
            tag="doodle_target",
            lod=1,
            shadow=False,
            anim=an,
        )
        dart: list[Cmd] = [
            ("M", cx + 4 * s, cy - 3 * s),
            ("L", cx + R * 1.2, cy - R * 1.1),
            ("M", cx + R * 1.2, cy - R * 1.1),
            ("L", cx + R * 1.2 - 26 * s, cy - R * 1.1 - 4 * s),
            ("M", cx + R * 1.2, cy - R * 1.1),
            ("L", cx + R * 1.2 - 6 * s, cy - R * 1.1 + 26 * s),
        ]
        self.stroke(dart, self.mk("fabric"), 7 * s**0.5, anim=an, tag="doodle_target")

    def atom(self, cx: float, cy: float, s: float) -> None:
        ink = self.mk("ink")
        for k, (rot, fr, role) in enumerate(
            ((0.0, 1 / 90, "ink"), (60.0, -1 / 110, "fabric"), (120.0, 1 / 140, "accent"))
        ):
            pts = self.hand_circle(cx, cy, 78 * s, 28 * s, rot=rot, start=-90 + 20 * k, sweep=368)
            self.stroke(
                _cr(pts),
                self.mk(role),
                7 * s**0.5,
                anim=Anim("spin", amp=1.0, freq=fr, pivot=(cx, cy)),
                tag="doodle_atom",
            )
        self.c.ellipse(
            "back",
            cx,
            cy,
            11 * s,
            11 * s,
            self.mk("accent3"),
            stroke=ink,
            sw=5,
            tag="doodle_atom",
            lod=1,
            shadow=False,
        )

    def flask(self, cx: float, cy: float, s: float) -> None:
        ink = self.mk("ink")
        outline: list[Cmd] = [("M", cx - 22 * s, cy - 82 * s), ("L", cx - 22 * s + self.j(2), cy - 30 * s), ("L", cx - 64 * s, cy + 50 * s), ("Q", cx - 72 * s, cy + 76 * s, cx - 46 * s, cy + 76 * s),
                              ("L", cx + 46 * s, cy + 76 * s + self.j(2)), ("Q", cx + 72 * s, cy + 76 * s, cx + 64 * s, cy + 50 * s), ("L", cx + 22 * s, cy - 30 * s), ("L", cx + 22 * s + self.j(2), cy - 82 * s),
                              ("M", cx - 34 * s, cy - 82 * s), ("L", cx + 34 * s, cy - 83 * s)]  # fmt: skip
        an = self.anim_sway((cx, cy + 76 * s), 1.0, 0.1)
        liquid = [
            (cx - 50 * s, cy + 14 * s),
            (cx + 6 * s, cy + 20 * s),
            (cx + 50 * s, cy + 12 * s),
            (cx + 64 * s, cy + 50 * s),
            (cx + 66 * s, cy + 70 * s),
            (cx - 66 * s, cy + 70 * s),
            (cx - 64 * s, cy + 50 * s),
        ]
        self.wash(_poly(liquid), self.mk("accent2"), 0.5, anim=an)
        self.stroke(outline, ink, 9 * s**0.5, anim=an, tag="doodle_flask")
        for k, (bx, by, br) in enumerate(
            (
                (cx - 10 * s, cy - 112 * s, 10 * s),
                (cx + 16 * s, cy - 138 * s, 7 * s),
                (cx - 4 * s, cy - 156 * s, 5 * s),
            )
        ):
            self.c.ellipse(
                "back",
                bx,
                by,
                br,
                br,
                self.mk("accent2"),
                stroke=ink,
                sw=4,
                alpha=0.9,
                tag="doodle_bubble",
                lod=1,
                shadow=False,
                anim=Anim("bob", amp=6, freq=0.18, phase=k * 1.7),
            )

    def gear(self, cx: float, cy: float, s: float, role: str = "accent") -> None:
        R, d = 56 * s, 15 * s
        pts: list[Pt] = []
        for i in range(8):
            a0 = math.radians(i * 45)
            for da, rr in ((-13, R - d), (-8, R), (8, R), (13, R - d)):
                a = a0 + math.radians(da)
                pts.append(
                    (cx + (rr + self.j(1.5)) * math.cos(a), cy + (rr + self.j(1.5)) * math.sin(a))
                )
        an = Anim("spin", amp=1.0, freq=1 / 100, pivot=(cx, cy))
        self.wash(_poly(pts), self.mk(role), 0.3, anim=an)
        self.stroke(
            _poly(pts) + _cr(self.hand_circle(cx, cy, 18 * s, 18 * s)),
            self.mk(role),
            8 * s**0.5,
            anim=an,
            tag="doodle_gear",
        )

    def note(self, x: float, y: float, w: float, ang: float, role: str = "accent3") -> None:
        c = self.c
        cx, cy = x + w / 2, y + w / 2
        a = math.radians(ang)
        pts = [(x, y), (x + w, y), (x + w, y + w), (x, y + w)]
        pts = [
            (
                cx + (px - cx) * math.cos(a) - (py - cy) * math.sin(a),
                cy + (px - cx) * math.sin(a) + (py - cy) * math.cos(a),
            )
            for px, py in pts
        ]
        c.poly(
            "back",
            [(px + 6, py + 9) for px, py in pts],
            "shadow",
            alpha=0.14,
            tag="note_shadow",
            lod=2,
            shadow=False,
        )
        c.poly("back", pts, role, tag="note", elev=3, lod=1)
        lines: list[Cmd] = []
        for k in range(3):
            yy = y + w * (0.34 + 0.2 * k)
            ln: list[Pt] = [(x + w * 0.16, yy), (x + w * (0.78 - 0.1 * k), yy + 1)]
            lines += [("M", *self.rot_pt(ln[0], cx, cy, a)), ("L", *self.rot_pt(ln[1], cx, cy, a))]
        c.path(
            "back", lines, None, stroke="ink", sw=4, alpha=0.5, tag="note_text", lod=2, shadow=False
        )
        mx, my = self.rot_pt((x + w / 2, y + 10), cx, cy, a)
        c.ellipse("back", mx, my, 11, 11, "accent", tag="magnet", elev=3, lod=1)
        c.ellipse(
            "back", mx - 3, my - 3, 4, 4, "white", alpha=0.5, tag="magnet", lod=2, shadow=False
        )

    @staticmethod
    def rot_pt(p: Pt, cx: float, cy: float, a: float) -> Pt:
        return (
            cx + (p[0] - cx) * math.cos(a) - (p[1] - cy) * math.sin(a),
            cy + (p[0] - cx) * math.sin(a) + (p[1] - cy) * math.cos(a),
        )

    def doodle_layout(self) -> None:
        theme = self.p.doodles
        if theme == "none":
            return
        d = self.d
        n_extra = round(d * 7)
        if theme == "ideas":
            self.bulb(196, 300, 1.2)
            self.chart(748, 168, 250, 230)
            self.arrow((344, 214), (690, 196), -0.16)
            extras = [
                lambda: self.star(548, 98, 34),
                lambda: self.underline(318, 770, 486),
                lambda: self.qmark(62, 800, 1.0),
                lambda: self.exclam(1020, 850, 1.0),
                lambda: self.star(950, 520, 30, "accent"),
                lambda: self.plus(58, 1010, 1.0),
                lambda: self.spiral(1018, 1090, 1.0),
                lambda: self.star(80, 520, 26, "accent2"),
            ]
        elif theme == "business":
            self.coin(196, 292, 1.2)
            self.chart(748, 168, 250, 230)
            self.arrow((340, 330), (690, 210), -0.2, "accent2")
            extras = [
                lambda: self.target(540, 96, 0.62),
                lambda: self.underline(318, 770, 486, "accent2"),
                lambda: self.star(950, 520, 28, "accent3"),
                lambda: self.plus(60, 840, 1.0, "accent"),
                lambda: self.coin(1016, 860, 0.5),
                lambda: self.target(70, 1080, 0.5),
                lambda: self.plus(1020, 1120, 0.9, "fabric"),
                lambda: self.star(84, 540, 24, "accent"),
            ]
        else:  # science
            self.atom(200, 292, 1.15)
            self.flask(840, 296, 1.15)
            self.arrow((350, 216), (700, 190), -0.15, "fabric")
            extras = [
                lambda: self.gear(548, 100, 0.62),
                lambda: self.underline(318, 770, 486, "fabric"),
                lambda: self.qmark(62, 800, 1.0, "accent"),
                lambda: self.star(950, 520, 28, "accent3"),
                lambda: self.gear(1016, 880, 0.5, "accent2"),
                lambda: self.plus(56, 1040, 1.0, "accent3"),
                lambda: self.exclam(1022, 1130, 0.9, "fabric"),
                lambda: self.star(84, 540, 24, "accent"),
            ]
        for fn in extras[: max(2, n_extra + 1)]:
            fn()
        if d > 0.3:
            self.note(48, 92, 92, -7)
        if d > 0.6:
            self.note(962, 650, 84, 6, "accent2" if self.p.marker == "multi" else "accent3")


def _rgba(hex6: str, a: float) -> str:
    return f"{hex6[:7]}{round(max(0.0, min(1.0, a)) * 255):02x}"


@register_background(
    "whiteboard",
    params_schema=WhiteboardParams,
    summary="Explainer set: a clean whiteboard filling the frame with hand-drawn marker doodles "
    "(bulb, chart, arrows, stars) and a marker-tray ledge for characters to stand on (slots "
    "tray_left / tray_center / tray_right); the base for the stick-figure / marker style",
    slots={
        "tray_left": (0.20, 0.74),
        "tray_center": (0.50, 0.74),
        "tray_right": (0.80, 0.74),
    },
    tags=("explainer", "whiteboard", "stickman"),
    ground_y=0.74,
    perspective=0.0,
    horizon=0.74,
)
def whiteboard(ctx: BuildContext, p: WhiteboardParams) -> None:
    """Whiteboard explainer set"""
    ctx.graph.scheme = make_scheme(ctx.time_of_day, ctx.mood, {**BASE_ROLES, **ROLES})
    b = _Board(ctx, p)
    b.surface()
    b.doodle_layout()
    b.ground()
