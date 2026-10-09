"""Background template framework.

A template is a function ``build(ctx, params)`` that fills a `SceneGraph` with style-agnostic
shapes on depth planes.  It adapts to its params (time of day, mood, density ...) - the template
adapts, the caller never edits it.  Register with `@register_background`; the registry feeds
the linter, `reel list backgrounds` and the LLM manifest.

Depth planes (back to front):  sky  far  back  mid_back  [characters]  mid_front  near
Coordinates are design pixels in the 1080x1920 frame; the stage is wider/taller so the camera
can pan: x in [-540, 1620], y in [-300, 2220].
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from reel.core.catalog import CATALOG, Catalog
from reel.core.geometry import Pt, darken, lighten, mix
from reel.core.ir import Anim, Ellipse, Geom, Limb, Line, Particles, PathG, Poly, Rect, Shape
from reel.core.rng import derive_rng
from reel.templates.palette import SKY, make_scheme

W, H = 1080.0, 1920.0
X0, X1, Y0, Y1 = -540.0, 1620.0, -300.0, 2220.0
PLANES = ("sky", "far", "back", "mid_back", "mid_front", "near")


class BgParams(BaseModel):
    """Parameters every background understands (templates add their own)."""

    model_config = ConfigDict(extra="forbid")
    time_of_day: Literal["dawn", "day", "dusk", "night"] = Field(
        "day", description="lighting and sky"
    )
    mood: Literal["neutral", "warm", "cool", "dramatic", "playful", "gloomy"] = Field(
        "neutral", description="colour mood"
    )
    density: float = Field(
        0.5, ge=0.0, le=1.0, description="how much set-dressing / clutter (0 sparse .. 1 busy)"
    )


@dataclass
class SceneGraph:
    scheme: Any  # ColorScheme
    planes: dict[str, list[Shape]] = field(default_factory=lambda: {p: [] for p in PLANES})
    overlay: list[Shape] = field(default_factory=list)  # screen-space atmosphere, drawn last
    particles: list[tuple[str, Particles]] = field(default_factory=list)
    slots: dict[str, Pt] = field(default_factory=dict)
    ground_y: float = 0.80  # default feet line (fraction of frame height)
    perspective: float = 1.0  # character scale change per unit of screen y
    horizon: float = 0.45
    time_of_day: str = "day"
    lamps_on: bool = False


@dataclass
class BuildContext:
    graph: SceneGraph
    rng: np.random.Generator
    seed: int
    time_of_day: str
    mood: str
    density: float

    @property
    def scheme(self) -> Any:
        return self.graph.scheme

    # -- adding shapes -----------------------------------------------------------------------------
    def add(self, plane: str, geom: Geom, fill: str | None = None, **kw: Any) -> Shape:
        plane_list = self.graph.planes[plane]
        kw.setdefault("z", float(len(plane_list)))
        s = Shape(geom, fill, **kw)
        plane_list.append(s)
        return s

    def rect(
        self,
        plane: str,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str | None,
        r: float = 0.0,
        **kw: Any,
    ) -> Shape:
        return self.add(plane, Rect(x, y, w, h, r), fill, **kw)

    def ellipse(
        self,
        plane: str,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        fill: str | None,
        rot: float = 0.0,
        **kw: Any,
    ) -> Shape:
        return self.add(plane, Ellipse(cx, cy, rx, ry, rot), fill, **kw)

    def poly(
        self,
        plane: str,
        pts: list[Pt],
        fill: str | None,
        smooth: float = 0.0,
        curve: bool = False,
        closed: bool = True,
        **kw: Any,
    ) -> Shape:
        return self.add(plane, Poly(tuple(pts), closed, smooth, curve), fill, **kw)

    def line(self, plane: str, a: Pt, b: Pt, w: float, color: str, **kw: Any) -> Shape:
        kw.setdefault("shadow", False)
        return self.add(plane, Line(a, b, w), None, stroke=color, sw=w, **kw)

    def limb(self, plane: str, a: Pt, b: Pt, w0: float, w1: float, fill: str, **kw: Any) -> Shape:
        return self.add(plane, Limb(a, b, w0, w1), fill, **kw)

    def path(
        self,
        plane: str,
        cmds: list[tuple[Any, ...]],
        fill: str | None = None,
        stroke: str | None = None,
        sw: float = 0.0,
        **kw: Any,
    ) -> Shape:
        return self.add(plane, PathG(tuple(cmds)), fill, stroke=stroke, sw=sw, **kw)

    def emit(self, plane: str, p: Particles) -> None:
        self.graph.particles.append((plane, p))

    def slot(self, name: str, x: float, y: float | None = None) -> None:
        self.graph.slots[name] = (x, self.graph.ground_y if y is None else y)

    # -- shared scenery --------------------------------------------------------------------------------
    def sky(
        self, horizon_y: float, *, clouds: bool = True, sun: bool = True, plane: str = "sky"
    ) -> None:
        """Sky gradient down to ``horizon_y`` (design px), sun/moon, stars and drifting clouds."""
        sk = SKY[self.time_of_day]
        self.rect(
            plane,
            X0,
            Y0,
            X1 - X0,
            horizon_y - Y0 + 4,
            "sky_top",
            material="emissive",
            shadow=False,
            tag="sky",
            gradient=("sky_top", "sky_bottom", 90.0),
        )
        night = self.time_of_day == "night"
        if sun:
            cx = {"dawn": 250.0, "day": 760.0, "dusk": 860.0, "night": 270.0}[self.time_of_day]
            cy = {
                "dawn": horizon_y - 60,
                "day": horizon_y - 560,
                "dusk": horizon_y - 70,
                "night": horizon_y - 640,
            }[self.time_of_day]
            col = "#f4f6ff" if night else sk.glow
            r = 70 if night else 92
            self.ellipse(
                plane,
                cx,
                cy,
                r * 2.4,
                r * 2.4,
                col,
                material="emissive",
                alpha=0.18,
                shadow=False,
                tag="sun_halo",
                lod=1,
            )
            self.ellipse(
                plane,
                cx,
                cy,
                r,
                r,
                col,
                material="emissive",
                glow=r * 1.6,
                shadow=False,
                tag="sun" if not night else "moon",
            )
        if night:
            n = int(26 + 50 * self.density)
            for _ in range(n):
                x = float(self.rng.uniform(X0, X1))
                y = float(self.rng.uniform(Y0, horizon_y - 120))
                rr = float(self.rng.uniform(2.0, 5.0))
                self.ellipse(
                    plane,
                    x,
                    y,
                    rr,
                    rr,
                    "#ffffff",
                    material="emissive",
                    shadow=False,
                    tag="star",
                    lod=1,
                    anim=Anim(
                        "twinkle",
                        amp=0.7,
                        freq=float(self.rng.uniform(0.2, 0.7)),
                        phase=float(self.rng.uniform(0, 6.28)),
                    ),
                )
        if clouds and not night:
            n = int(2 + 5 * self.density)
            for _ in range(n):
                x = float(self.rng.uniform(X0, X1 - 200))
                y = float(self.rng.uniform(horizon_y - 900, horizon_y - 220))
                s = float(self.rng.uniform(0.7, 1.5))
                cc = "#ffffff" if self.time_of_day == "day" else mix("#ffffff", sk.glow, 0.45)
                for dx, dy, rx, ry in (
                    (0, 0, 120, 52),
                    (-90, 18, 84, 40),
                    (96, 20, 92, 42),
                    (-30, -26, 80, 44),
                    (40, -30, 74, 40),
                ):
                    self.ellipse(
                        plane,
                        x + dx * s,
                        y + dy * s,
                        rx * s,
                        ry * s,
                        cc,
                        material="emissive",
                        alpha=0.92,
                        shadow=False,
                        tag="cloud",
                        lod=1,
                        anim=Anim(
                            "drift",
                            amp=1.0,
                            freq=float(self.rng.uniform(0.04, 0.09)),
                            axis=0.0,
                            wrap=0.0,
                        ),
                    )

    def ground_band(
        self,
        y_top: float,
        y_bottom: float,
        fill: str,
        *,
        plane: str = "mid_back",
        gradient_to: str | None = None,
        **kw: Any,
    ) -> Shape:
        s = self.rect(plane, X0, y_top, X1 - X0, y_bottom - y_top, fill, tag="ground", **kw)
        if gradient_to:
            s.gradient = (fill, gradient_to, 90.0)
        return s


@dataclass(frozen=True)
class BackgroundDef:
    name: str
    build: Callable[[BuildContext, Any], None]
    params_model: type[BgParams]
    summary: str
    slots: dict[str, Pt]
    tags: tuple[str, ...] = ()
    ground_y: float = 0.80
    perspective: float = 1.0
    horizon: float = 0.45

    def slot_names(self, params: Any = None) -> list[str]:
        return sorted(self.slots)

    def parse(self, raw: dict[str, Any] | None) -> BgParams:
        return self.params_model.model_validate(raw or {})


def register_background(
    name: str,
    *,
    params_schema: type[BgParams] = BgParams,
    summary: str = "",
    slots: dict[str, Pt] | None = None,
    tags: tuple[str, ...] = (),
    ground_y: float = 0.80,
    perspective: float = 1.0,
    horizon: float = 0.45,
    catalog: Catalog | None = None,
) -> Callable[[Callable[[BuildContext, Any], None]], Callable[[BuildContext, Any], None]]:
    """Decorator: register a background template (see docs/adding-a-background.md)."""

    def wrap(fn: Callable[[BuildContext, Any], None]) -> Callable[[BuildContext, Any], None]:
        d = BackgroundDef(
            name,
            fn,
            params_schema,
            summary or (fn.__doc__ or "").strip().splitlines()[0],
            dict(slots or {}),
            tags,
            ground_y,
            perspective,
            horizon,
        )
        (catalog or CATALOG).backgrounds.register(name, d, summary=d.summary)
        return fn

    return wrap


def build_graph(
    defn: BackgroundDef, raw_params: dict[str, Any] | None, seed: int, scene_id: str = ""
) -> SceneGraph:
    """Run a template for one scene: params -> validated -> scheme -> shapes."""
    p = defn.parse(raw_params)
    scheme = make_scheme(p.time_of_day, p.mood, _roles_for(defn))
    graph = SceneGraph(
        scheme=scheme,
        ground_y=defn.ground_y,
        perspective=defn.perspective,
        horizon=defn.horizon,
        time_of_day=p.time_of_day,
        lamps_on=SKY[p.time_of_day].lamps_on,
    )
    ctx = BuildContext(
        graph, derive_rng(seed, "bg", defn.name, scene_id), seed, p.time_of_day, p.mood, p.density
    )
    for name, (x, y) in defn.slots.items():
        graph.slots[name] = (x, y)
    defn.build(ctx, p)
    return graph


#: base roles shared by all templates; a template may extend via `ROLE_SETS`
BASE_ROLES: dict[str, str] = {
    "wall": "#f1dcc0",
    "wall2": "#e6c9a4",
    "floor": "#b58a5e",
    "floor2": "#a07548",
    "trim": "#fbf4e6",
    "wood": "#9b6b43",
    "wood2": "#7b4f2f",
    "accent": "#e4572e",
    "accent2": "#2a9d8f",
    "accent3": "#f3a712",
    "fabric": "#4f6d9a",
    "fabric2": "#d8e2ef",
    "foliage": "#4c9a5b",
    "foliage2": "#357a47",
    "foliage3": "#7fbf6b",
    "trunk": "#7a5238",
    "stone": "#9aa3ad",
    "stone2": "#7c858f",
    "asphalt": "#4b5160",
    "line": "#f5e9a8",
    "glass": "#a9dcf0",
    "metal": "#8a96a3",
    "brick": "#b5654a",
    "brick2": "#9a523b",
    "paper": "#fbf7ee",
    "ink": "#262a36",
    "board": "#f6f7f9",
    "chalk": "#2f4f4f",
    "grid": "#c9d3df",
    "soil": "#7a5b3e",
    "sand": "#e4c98f",
    "water": "#4aa3c7",
}
ROLE_SETS: dict[str, dict[str, str]] = {}


def _roles_for(defn: BackgroundDef) -> dict[str, str]:
    return {**BASE_ROLES, **ROLE_SETS.get(defn.name, {})}


def shade(c: str, amt: float) -> str:
    return darken(c, amt)


def tint(c: str, amt: float) -> str:
    return lighten(c, amt)


def wobble(rng: np.random.Generator, n: int, amp: float) -> list[float]:
    return [float(rng.uniform(-amp, amp)) for _ in range(n)]


def hill_points(
    rng: np.random.Generator,
    x0: float,
    x1: float,
    base_y: float,
    amp: float,
    step: float = 140.0,
    bottom: float | None = None,
) -> list[Pt]:
    """Rolling-hill silhouette from x0..x1 on ``base_y`` (closed down to ``bottom``)."""
    pts: list[Pt] = []
    x = x0
    ph = float(rng.uniform(0, 6.28))
    f1, f2 = float(rng.uniform(0.003, 0.005)), float(rng.uniform(0.008, 0.012))
    while x <= x1 + step:
        y = base_y - amp * (0.6 * math.sin(x * f1 + ph) + 0.4 * math.sin(x * f2 + ph * 1.7))
        pts.append((x, y))
        x += step
    b = base_y + 2000 if bottom is None else bottom
    return [(x0 - step, b), *pts, (x1 + step, b)]
