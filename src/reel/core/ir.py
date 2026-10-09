"""The shape IR: what templates and the figure builder emit, and what every style paints.

"Templates describe, styles render": a background template or a posed character is
turned into a flat list of `Shape` objects that carry *semantics* (colour roles,
materials, tags, elevation) rather than pixels.  A style pack interprets them its own way -
flat vector fills them, paper cutout adds layered paper + shadows, stickman turns them into
marker lines.  That is what lets every style render the same spec unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Union

from reel.core.geometry import Pt, hex_to_rgba, mix, rgb_to_hex
from reel.core.rng import derive_rng

# ------------------------------------------------------------------------------- geometry
Xf = tuple[float, float, float, float, float, float, float]  # dx, dy, rot_deg, sx, sy, ox, oy


@dataclass(slots=True, frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float
    r: float = 0.0


@dataclass(slots=True, frozen=True)
class Ellipse:
    cx: float
    cy: float
    rx: float
    ry: float
    rot: float = 0.0


@dataclass(slots=True, frozen=True)
class Poly:
    pts: tuple[Pt, ...]
    closed: bool = True
    smooth: float = 0.0  # corner rounding 0..0.5
    curve: bool = False  # True: pass a Catmull-Rom spline through the points instead


@dataclass(slots=True, frozen=True)
class Limb:
    """Tapered capsule between two points; widths are diameters."""

    a: Pt
    b: Pt
    w0: float
    w1: float


@dataclass(slots=True, frozen=True)
class Line:
    a: Pt
    b: Pt
    w: float


@dataclass(slots=True, frozen=True)
class PathG:
    cmds: tuple[tuple[Any, ...], ...]


Geom = Union[Rect, Ellipse, Poly, Limb, Line, PathG]  # noqa: UP007


def bbox(g: Geom) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of a geometry."""
    if isinstance(g, Rect):
        return g.x, g.y, g.x + g.w, g.y + g.h
    if isinstance(g, Ellipse):
        r = max(g.rx, g.ry) if g.rot else 0
        return g.cx - (r or g.rx), g.cy - (r or g.ry), g.cx + (r or g.rx), g.cy + (r or g.ry)
    if isinstance(g, Poly):
        xs = [p[0] for p in g.pts]
        ys = [p[1] for p in g.pts]
        return min(xs), min(ys), max(xs), max(ys)
    if isinstance(g, Limb):
        r = max(g.w0, g.w1) / 2
        return (
            min(g.a[0], g.b[0]) - r,
            min(g.a[1], g.b[1]) - r,
            max(g.a[0], g.b[0]) + r,
            max(g.a[1], g.b[1]) + r,
        )
    if isinstance(g, Line):
        r = g.w / 2
        return (
            min(g.a[0], g.b[0]) - r,
            min(g.a[1], g.b[1]) - r,
            max(g.a[0], g.b[0]) + r,
            max(g.a[1], g.b[1]) + r,
        )
    xs, ys = [], []
    for c in g.cmds:
        v = c[1:]
        xs += [float(v[i]) for i in range(0, len(v), 2)]
        ys += [float(v[i]) for i in range(1, len(v), 2)]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)


# ------------------------------------------------------------------------------- animation
@dataclass(slots=True, frozen=True)
class Anim:
    """Procedural motion for a background shape (evaluated per frame by `apply_anim`).

    kind: sway (rotate about a pivot, deg) | bob (vertical px) | drift (move along axis, wraps) |
          flicker (alpha noise) | twinkle (alpha pulse) | pulse (scale) | spin (continuous rotation)
    """

    kind: str
    amp: float = 1.0
    freq: float = 0.3
    phase: float = 0.0
    axis: float = 0.0  # drift direction in degrees (0 = right)
    wrap: float = 0.0  # drift: distance after which the shape wraps back
    pivot: Pt | None = None  # sway/spin/pulse origin; default = bbox bottom-centre (sway) / centre


def apply_anim(shape: Shape, t: float) -> Shape:
    """A copy of ``shape`` with its animation evaluated at time ``t`` (seconds)."""
    a = shape.anim
    if a is None:
        return shape
    x0, y0, x1, y1 = bbox(shape.geom)
    if a.kind == "sway":
        px, py = a.pivot or ((x0 + x1) / 2, y1)
        rot = a.amp * math.sin(2 * math.pi * a.freq * t + a.phase)
        return replace(shape, xf=(0.0, 0.0, rot, 1.0, 1.0, px, py))
    if a.kind == "bob":
        return replace(
            shape,
            xf=(0.0, a.amp * math.sin(2 * math.pi * a.freq * t + a.phase), 0.0, 1.0, 1.0, 0.0, 0.0),
        )
    if a.kind == "drift":
        d = (t * a.freq * 60.0 + a.phase) % (a.wrap or 1e9)
        rad = math.radians(a.axis)
        return replace(shape, xf=(d * math.cos(rad), d * math.sin(rad), 0.0, 1.0, 1.0, 0.0, 0.0))
    if a.kind == "pulse":
        k = 1.0 + a.amp * math.sin(2 * math.pi * a.freq * t + a.phase)
        px, py = a.pivot or ((x0 + x1) / 2, (y0 + y1) / 2)
        return replace(shape, xf=(0.0, 0.0, 0.0, k, k, px, py))
    if a.kind == "spin":
        px, py = a.pivot or ((x0 + x1) / 2, (y0 + y1) / 2)
        return replace(shape, xf=(0.0, 0.0, 360.0 * a.freq * t * a.amp + a.phase, 1.0, 1.0, px, py))
    if a.kind == "twinkle":
        k = 0.5 + 0.5 * math.sin(2 * math.pi * a.freq * t + a.phase)
        return replace(shape, alpha=shape.alpha * (1.0 - a.amp + a.amp * k))
    if a.kind == "flicker":
        n = math.sin(2 * math.pi * a.freq * t + a.phase) * math.sin(
            2 * math.pi * a.freq * 2.37 * t + a.phase * 1.7
        )
        return replace(shape, alpha=shape.alpha * (1.0 - a.amp * (0.5 + 0.5 * n)))
    return shape


# ------------------------------------------------------------------------------- shapes
@dataclass(slots=True)
class Shape:
    geom: Geom
    fill: str | None = None  # colour role (e.g. "wall") or hex; None = no fill
    stroke: str | None = None
    sw: float = 0.0  # stroke width
    alpha: float = 1.0
    z: float = 0.0
    material: str = "flat"  # flat paper wood glass metal grass fabric skin emissive water stone
    tag: str = ""  # semantic hint for styles: head torso limb eye mouth ...
    lod: int = 0  # 0 essential, 1 detail, 2 fine (stick figures draw only lod 0)
    elev: float = 0.0  # visual height above its backdrop in px: drives paper shadows
    shadow: bool = True
    glow: float = 0.0  # emissive halo radius (lamps, windows)
    cap: str = "round"
    gradient: tuple[str, str, float] | None = None  # (from, to, angle deg) across the bbox
    anim: Anim | None = None
    xf: Xf | None = None  # runtime transform set by apply_anim

    def replace(self, **kw: Any) -> Shape:
        return replace(self, **kw)


@dataclass(slots=True)
class Particles:
    """A cloud of small moving shapes, expanded deterministically per frame."""

    kind: str  # rain snow dust leaves sparkle embers bubbles confetti
    area: tuple[float, float, float, float]
    count: int
    speed: float = 1.0
    size: float = 6.0
    color: str = "#ffffff"
    alpha: float = 0.8
    wind: float = 0.0
    seed: int = 0
    z: float = 0.0
    material: str = "flat"


def expand_particles(p: Particles, t: float) -> list[Shape]:
    x0, y0, x1, y1 = p.area
    w, h = x1 - x0, y1 - y0
    rng = derive_rng(p.seed, "particles", p.kind, p.count)
    px = rng.random(p.count)
    py = rng.random(p.count)
    ph = rng.random(p.count) * 6.283
    sz = 0.6 + 0.8 * rng.random(p.count)
    out: list[Shape] = []
    for i in range(p.count):
        if p.kind == "rain":
            fall = 900.0 * p.speed
            y = y0 + (py[i] * h + t * fall * (0.8 + 0.4 * sz[i] / 1.4)) % h
            x = x0 + (px[i] * w + y * p.wind * 0.25 + t * p.wind * 40) % w
            ln = p.size * 3.0 * sz[i]
            out.append(
                Shape(
                    Line((x, y), (x + p.wind * 0.18 * ln, y + ln), max(1.5, p.size * 0.18)),
                    stroke=p.color,
                    sw=max(1.5, p.size * 0.18),
                    alpha=p.alpha * (0.5 + 0.5 * sz[i] / 1.4),
                    z=p.z,
                    shadow=False,
                    material="water",
                    tag="rain",
                    lod=1,
                )
            )
        elif p.kind in ("snow", "dust", "sparkle", "bubbles", "embers"):
            rise = -1.0 if p.kind in ("embers", "bubbles") else 1.0
            speed = {"snow": 70, "dust": 14, "sparkle": 6, "bubbles": 45, "embers": 60}[
                p.kind
            ] * p.speed
            y = y0 + (py[i] * h + rise * t * speed * (0.5 + sz[i])) % h
            x = x0 + (px[i] * w + 30 * math.sin(t * 0.7 + ph[i]) + t * p.wind * 20) % w
            r = p.size * sz[i] * 0.5
            a = (
                p.alpha * (0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 2.2 + ph[i])))
                if p.kind in ("sparkle", "dust")
                else p.alpha
            )
            out.append(
                Shape(
                    Ellipse(x, y, r, r),
                    fill=p.color,
                    alpha=a,
                    z=p.z,
                    shadow=False,
                    material=p.material,
                    tag=p.kind,
                    lod=1,
                    glow=r * 3 if p.kind == "sparkle" else 0.0,
                )
            )
        elif p.kind == "leaves":
            speed = 55.0 * p.speed
            y = y0 + (py[i] * h + t * speed * (0.6 + sz[i])) % h
            x = x0 + (px[i] * w + 60 * math.sin(t * 0.9 + ph[i]) + t * p.wind * 25) % w
            rot = 40 * math.sin(t * 1.4 + ph[i]) + ph[i] * 20
            out.append(
                Shape(
                    Ellipse(x, y, p.size * sz[i] * 0.9, p.size * sz[i] * 0.45, rot),
                    fill=p.color,
                    alpha=p.alpha,
                    z=p.z,
                    shadow=False,
                    material="grass",
                    tag="leaf",
                    lod=1,
                )
            )
        elif p.kind == "confetti":
            speed = 160.0 * p.speed
            y = y0 + (py[i] * h + t * speed * (0.6 + sz[i])) % h
            x = x0 + (px[i] * w + 40 * math.sin(t * 2 + ph[i])) % w
            hue = (i * 0.137) % 1.0
            from reel.core.geometry import from_hls

            out.append(
                Shape(
                    Rect(x, y, p.size * sz[i], p.size * sz[i] * 0.5),
                    fill=from_hls(hue, 0.6, 0.85),
                    alpha=p.alpha,
                    z=p.z,
                    shadow=False,
                    tag="confetti",
                    lod=1,
                    xf=(0, 0, math.degrees(t * 4 + ph[i]) % 360, 1, 1, x, y),
                )
            )
    return out


# ------------------------------------------------------------------------------- colours
@dataclass
class ColorScheme:
    """Role -> colour table plus the scene's lighting.  Styles resolve every colour through it."""

    roles: dict[str, str] = field(default_factory=dict)
    ambient: str = "#ffffff"  # light colour multiplied into non-emissive surfaces
    ambient_amt: float = 0.0
    exposure: float = 1.0
    shadow: str = "#201a33"  # colour of cast shadows
    light_dir: Pt = (-0.55, -0.83)  # direction light comes FROM; shadows fall the other way

    def base(self, c: str) -> str:
        """Resolve a role name (or pass a hex through) without lighting."""
        if c.startswith("#"):
            return c
        try:
            return self.roles[c]
        except KeyError:
            raise KeyError(f"unknown colour role {c!r}; defined: {sorted(self.roles)}") from None

    def lit(self, c: str) -> str:
        """Resolve and apply ambient light + exposure."""
        b = self.base(c)
        if self.ambient_amt <= 0 and self.exposure == 1.0:
            return b
        r, g, bl, _ = hex_to_rgba(b)
        ar, ag, ab, _ = hex_to_rgba(self.ambient)
        k = self.ambient_amt
        e = self.exposure
        return rgb_to_hex(
            r * (1 - k + k * ar / 255) * e,
            g * (1 - k + k * ag / 255) * e,
            bl * (1 - k + k * ab / 255) * e,
        )

    def with_roles(self, **roles: str) -> ColorScheme:
        return replace(self, roles={**self.roles, **roles})

    def blend(self, other: ColorScheme, t: float) -> ColorScheme:
        keys = set(self.roles) | set(other.roles)
        roles = {
            k: mix(
                self.roles.get(k, other.roles.get(k, "#000000")),
                other.roles.get(k, self.roles.get(k, "#000000")),
                t,
            )
            for k in keys
        }
        return ColorScheme(
            roles,
            mix(self.ambient, other.ambient, t),
            self.ambient_amt + (other.ambient_amt - self.ambient_amt) * t,
            self.exposure + (other.exposure - self.exposure) * t,
            mix(self.shadow, other.shadow, t),
            self.light_dir,
        )
