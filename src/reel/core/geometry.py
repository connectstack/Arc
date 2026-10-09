"""Colour maths, 2D transforms and skia path builders shared by templates, figures and styles."""

from __future__ import annotations

import colorsys
import math
from collections.abc import Sequence
from functools import lru_cache

import skia

Pt = tuple[float, float]


# ------------------------------------------------------------------------------- colour
@lru_cache(maxsize=4096)
def hex_to_rgba(h: str) -> tuple[int, int, int, int]:
    s = h.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        s += "ff"
    if len(s) != 8:
        raise ValueError(f"bad colour {h!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(v))) for v in (r, g, b)))


def mix(a: str, b: str, t: float) -> str:
    """Linear blend of two hex colours (t=0 -> a, t=1 -> b)."""
    ra, ga, ba, _ = hex_to_rgba(a)
    rb, gb, bb, _ = hex_to_rgba(b)
    return rgb_to_hex(ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)


def hls(c: str) -> tuple[float, float, float]:
    r, g, b, _ = hex_to_rgba(c)
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)


def from_hls(h: float, light: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, light)), max(0.0, min(1.0, s)))
    return rgb_to_hex(r * 255, g * 255, b * 255)


def adjust(
    c: str, *, hue: float = 0.0, sat: float = 1.0, light: float = 1.0, dlight: float = 0.0
) -> str:
    """Shift hue (turns), scale saturation/lightness, add to lightness."""
    h, lum, s = hls(c)
    return from_hls(h + hue, lum * light + dlight, s * sat)


def lighten(c: str, amt: float) -> str:
    return mix(c, "#ffffff", amt)


def darken(c: str, amt: float) -> str:
    return mix(c, "#000000", amt)


def luminance(c: str) -> float:
    r, g, b, _ = hex_to_rgba(c)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def skcolor(c: str, alpha: float = 1.0) -> int:
    r, g, b, a = hex_to_rgba(c)
    return int(skia.Color(r, g, b, max(0, min(255, round(a * alpha)))))


# ------------------------------------------------------------------------------- transforms
class Frame2D:
    """A local frame: origin + rotation (degrees; 0 = local +y points down, like a hanging limb)."""

    __slots__ = ("c", "ox", "oy", "s")

    def __init__(self, origin: Pt, angle_deg: float = 0.0) -> None:
        a = math.radians(angle_deg)
        self.ox, self.oy = origin
        self.c = math.cos(a)
        self.s = math.sin(a)

    def pt(self, lx: float, ly: float) -> Pt:
        """Local (x right, y down along the limb) -> parent coordinates."""
        return (self.ox + lx * self.c + ly * self.s, self.oy - lx * self.s + ly * self.c)

    def pts(self, pts: Sequence[Pt]) -> list[Pt]:
        return [self.pt(x, y) for x, y in pts]


class Rot2D:
    """Rotation by ``deg`` about ``origin`` using the standard y-down matrix (+ = clockwise)."""

    __slots__ = ("c", "ox", "oy", "s")

    def __init__(self, origin: Pt, deg: float) -> None:
        self.ox, self.oy = origin
        self.c = math.cos(math.radians(deg))
        self.s = math.sin(math.radians(deg))

    def pt(self, x: float, y: float) -> Pt:
        dx, dy = x - self.ox, y - self.oy
        return (self.ox + dx * self.c - dy * self.s, self.oy + dx * self.s + dy * self.c)

    @property
    def deg(self) -> float:
        return math.degrees(math.atan2(self.s, self.c))

    def loc(self, lx: float, ly: float) -> Pt:
        """Local (figure-aligned: x right, y down) offset from the origin -> rotated absolute point."""
        return (self.ox + lx * self.c - ly * self.s, self.oy + lx * self.s + ly * self.c)

    def locs(self, pts: Sequence[Pt]) -> tuple[Pt, ...]:
        return tuple(self.loc(x, y) for x, y in pts)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ------------------------------------------------------------------------------- paths
def limb_path(a: Pt, b: Pt, w0: float, w1: float) -> skia.Path:
    """Tapered capsule from a (diameter w0) to b (diameter w1): two circles plus the tangent quad.

    Built as overlapping same-orientation sub-paths, so non-zero winding fills the union.
    """
    r0, r1 = w0 / 2, w1 / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy)
    path = skia.Path()
    if d < 1e-6 or d <= abs(r0 - r1):
        c, r = (a, r0) if r0 >= r1 else (b, r1)
        path.addCircle(c[0], c[1], r)
        return path
    ang = math.atan2(dy, dx)
    k = math.acos(max(-1.0, min(1.0, (r0 - r1) / d)))
    ca, sa = math.cos(ang + k), math.sin(ang + k)
    cb, sb = math.cos(ang - k), math.sin(ang - k)
    quad = [
        (a[0] + r0 * ca, a[1] + r0 * sa),
        (b[0] + r1 * ca, b[1] + r1 * sa),
        (b[0] + r1 * cb, b[1] + r1 * sb),
        (a[0] + r0 * cb, a[1] + r0 * sb),
    ]
    area = sum(
        quad[i][0] * quad[(i + 1) % 4][1] - quad[(i + 1) % 4][0] * quad[i][1] for i in range(4)
    )
    if area < 0:  # match addCircle's clockwise (y-down) orientation
        quad.reverse()
    path.addCircle(a[0], a[1], r0)
    path.addCircle(b[0], b[1], r1)
    path.addPoly([skia.Point(x, y) for x, y in quad], True)
    return path


def rounded_poly(pts: Sequence[Pt], smooth: float, closed: bool = True) -> skia.Path:
    """Polygon with rounded corners (smooth 0 = sharp .. 0.5 = fully blobby)."""
    path = skia.Path()
    n = len(pts)
    if n < 2:
        return path
    if smooth <= 1e-4 or n < 3:
        path.moveTo(*pts[0])
        for p in pts[1:]:
            path.lineTo(*p)
        if closed:
            path.close()
        return path
    s = clamp(smooth, 0.0, 0.5)

    def cut(p: Pt, q: Pt) -> Pt:  # point on p->q, a fraction s of the edge away from p
        return (p[0] + (q[0] - p[0]) * s, p[1] + (q[1] - p[1]) * s)

    if closed:
        start = cut(pts[0], pts[-1])
        path.moveTo(*start)
        for i in range(n):
            p, nxt, prv = pts[i], pts[(i + 1) % n], pts[(i - 1) % n]
            path.lineTo(*cut(p, prv))
            path.quadTo(p[0], p[1], *cut(p, nxt))
        path.close()
    else:
        path.moveTo(*pts[0])
        for i in range(1, n - 1):
            p, nxt, prv = pts[i], pts[i + 1], pts[i - 1]
            path.lineTo(*cut(p, prv))
            path.quadTo(p[0], p[1], *cut(p, nxt))
        path.lineTo(*pts[-1])
    return path


def smooth_curve(pts: Sequence[Pt], closed: bool = True, tension: float = 0.5) -> skia.Path:
    """Catmull-Rom spline through the points as cubic Beziers (organic blobs, hills, hair)."""
    n = len(pts)
    path = skia.Path()
    if n < 3:
        return rounded_poly(pts, 0.0, closed)
    path.moveTo(*pts[0])
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        p1, p2 = pts[i], pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) * tension / 3 * 2, p1[1] + (p2[1] - p0[1]) * tension / 3 * 2)
        c2 = (p2[0] - (p3[0] - p1[0]) * tension / 3 * 2, p2[1] - (p3[1] - p1[1]) * tension / 3 * 2)
        path.cubicTo(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
    if closed:
        path.close()
    return path


def path_from_cmds(cmds: Sequence[tuple[object, ...]]) -> skia.Path:
    """Build a skia path from IR commands: ("M",x,y) ("L",x,y) ("Q",cx,cy,x,y) ("C",..6) ("Z",)."""
    p = skia.Path()
    for c in cmds:
        op = c[0]
        v = [float(x) for x in c[1:]]  # type: ignore[arg-type]
        if op == "M":
            p.moveTo(*v)
        elif op == "L":
            p.lineTo(*v)
        elif op == "Q":
            p.quadTo(*v)
        elif op == "C":
            p.cubicTo(*v)
        elif op == "Z":
            p.close()
        else:
            raise ValueError(f"unknown path command {op!r}")
    return p


def ellipse_pts(
    cx: float, cy: float, rx: float, ry: float, a0: float, a1: float, n: int = 12, rot: float = 0.0
) -> list[Pt]:
    """Points along an elliptical arc from angle a0 to a1 (degrees; 0 = +x, 90 = +y/down)."""
    out: list[Pt] = []
    cr, sr = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        x, y = rx * math.cos(a), ry * math.sin(a)
        out.append((cx + x * cr - y * sr, cy + x * sr + y * cr))
    return out
