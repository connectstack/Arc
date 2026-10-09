"""SVG -> the shape IR.

An imported drawing becomes the same `Shape` list a built-in template emits, so every style paints it its own way
(paper cutout adds layered paper and shadows, stickman turns it into marker lines, flat vector fills it) and the scene's
lighting tints it.  This reads the practical subset of SVG that icon sets, clip art and most editors export:

* shapes: ``path`` (every command, arcs included), ``rect`` (rounded too), ``circle``, ``ellipse``, ``line``,
  ``polyline``, ``polygon``; groups, ``transform`` (translate, scale, rotate, skew, matrix), ``use`` of an id
* paint: ``fill`` and ``stroke`` (hex, rgb(), names), widths, opacity, ``fill-rule``, linear gradients (two colours),
  CSS from ``style="..."`` and a ``<style>`` block with simple selectors (``.class``, ``tag``, ``#id``)

What it does not draw it says so in ``warnings`` and skips: text, filters, masks, clip paths, embedded pictures,
radial gradients (the middle colour is used).

Authors can steer the result with attributes on any element (they are inherited by what is inside it):
``data-role="body"`` lets a character's palette recolour that part, ``data-lod="1"`` marks fine detail (the stickman
style leaves it out), ``data-elev`` / ``data-shadow="false"`` tune the paper shadows, ``data-material`` and
``data-tag`` set the shape's material and tag, and ``data-plane="far"`` puts a group of a place on that depth plane.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from typing import Any

from reel.assets.model import MAX_ART_BYTES, PLACE_PLANES, Art
from reel.core.geometry import mix, path_from_cmds
from reel.core.ir import Ellipse, Geom, Line, PathG, Poly, Rect, Shape, bbox

Matrix = tuple[
    float, float, float, float, float, float
]  # a b c d e f: x' = a x + c y + e, y' = b x + d y + f
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
MAX_ELEMENTS = 20000
MAX_DEPTH = 24
MAX_CSS_BYTES = 64 * 1024  # a <style> block is a handful of rules, not a document
MAX_CSS_RULES = 1000

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_UNITS = {
    "px": 1.0,
    "pt": 96 / 72,
    "pc": 16.0,
    "mm": 96 / 25.4,
    "cm": 96 / 2.54,
    "in": 96.0,
    "": 1.0,
}

_NAMED = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
    "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080", "pink": "#ffc0cb", "brown": "#a52a2a",
    "gray": "#808080", "grey": "#808080", "cyan": "#00ffff", "aqua": "#00ffff", "magenta": "#ff00ff",
    "fuchsia": "#ff00ff", "lime": "#00ff00", "navy": "#000080", "teal": "#008080", "maroon": "#800000",
    "olive": "#808000", "silver": "#c0c0c0", "gold": "#ffd700", "beige": "#f5f5dc", "ivory": "#fffff0",
    "tan": "#d2b48c", "coral": "#ff7f50", "salmon": "#fa8072", "khaki": "#f0e68c", "violet": "#ee82ee",
    "indigo": "#4b0082", "crimson": "#dc143c", "turquoise": "#40e0d0", "skyblue": "#87ceeb", "lightblue": "#add8e6",
    "darkblue": "#00008b", "darkgreen": "#006400", "lightgreen": "#90ee90", "lightgray": "#d3d3d3",
    "lightgrey": "#d3d3d3", "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "dimgray": "#696969", "dimgrey": "#696969",
    "darkred": "#8b0000", "orangered": "#ff4500", "hotpink": "#ff69b4", "chocolate": "#d2691e",
    "sienna": "#a0522d", "peru": "#cd853f", "wheat": "#f5deb3", "linen": "#faf0e6", "lavender": "#e6e6fa",
    "plum": "#dda0dd", "orchid": "#da70d6", "steelblue": "#4682b4", "royalblue": "#4169e1",
    "forestgreen": "#228b22", "seagreen": "#2e8b57", "limegreen": "#32cd32", "goldenrod": "#daa520",
    "snow": "#fffafa", "whitesmoke": "#f5f5f5", "gainsboro": "#dcdcdc", "slategray": "#708090",
    "slategrey": "#708090", "midnightblue": "#191970", "cornflowerblue": "#6495ed", "tomato": "#ff6347",
    "firebrick": "#b22222", "saddlebrown": "#8b4513", "burlywood": "#deb887", "moccasin": "#ffe4b5",
}  # fmt: skip


class SvgError(ValueError):
    """The file is not an SVG this importer can read (the message says why, in plain words)."""


# ------------------------------------------------------------------------------- matrices
def mmul(m: Matrix, n: Matrix) -> Matrix:
    """The matrix that applies ``n`` first and then ``m``."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def mapply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def mscale(m: Matrix) -> float:
    """How much the matrix scales lengths on average (for stroke widths)."""
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


def parse_transform(text: str | None) -> Matrix:
    m = IDENTITY
    if not text:
        return m
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", text):
        v = [float(x) for x in _NUM.findall(args)]
        if not all(math.isfinite(x) for x in v):
            continue  # a transform with a number that is not a number (1e999) is left out
        t: Matrix
        if name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            c, s = math.cos(math.radians(v[0])), math.sin(math.radians(v[0]))
            t = (c, s, -s, c, 0, 0)
            if len(v) >= 3:
                t = mmul((1, 0, 0, 1, v[1], v[2]), mmul(t, (1, 0, 0, 1, -v[1], -v[2])))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        elif name == "matrix" and len(v) == 6:
            t = (v[0], v[1], v[2], v[3], v[4], v[5])
        else:
            continue
        m = mmul(m, t)
    return m


# ------------------------------------------------------------------------------- numbers and colours
def length(text: str | None, default: float = 0.0, ref: float = 0.0) -> float:
    """A length such as ``12``, ``3.5mm`` or ``50%`` (of ``ref``) in user units."""
    if text is None:
        return default
    m = re.match(r"\s*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)\s*([a-z%]*)", str(text))
    if not m:
        return default
    v = float(m.group(1))
    unit = m.group(2)
    out = v / 100.0 * ref if unit == "%" else v * _UNITS.get(unit, 1.0)
    return out if math.isfinite(out) else default


def parse_color(text: str | None) -> tuple[str, float] | None:
    """(``#rrggbb``, alpha) of a CSS colour, or None for ``none`` / ``transparent`` / something unreadable."""
    if text is None:
        return None
    s = text.strip().lower()
    if not s or s in ("none", "transparent"):
        return None
    if s in _NAMED:
        return _NAMED[s], 1.0
    if s == "currentcolor":
        return "#262a36", 1.0
    if s.startswith("#"):
        h = s[1:]
        try:
            if len(h) in (3, 4):
                h = "".join(c * 2 for c in h)
            if len(h) == 6:
                int(h, 16)
                return "#" + h, 1.0
            if len(h) == 8:
                return "#" + h[:6], int(h[6:], 16) / 255.0
        except ValueError:
            return None
        return None
    m = re.match(r"rgba?\(([^)]*)\)", s)
    if m:
        parts = [p for p in re.split(r"[\s,/]+", m.group(1).strip()) if p]
        if len(parts) >= 3:
            try:
                rgb = []
                for p in parts[:3]:
                    rgb.append(round(float(p[:-1]) * 2.55) if p.endswith("%") else round(float(p)))
                a = 1.0
                if len(parts) > 3:
                    a = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
                r, g, b = (max(0, min(255, c)) for c in rgb)
                return f"#{r:02x}{g:02x}{b:02x}", max(0.0, min(1.0, a))
            except (ValueError, OverflowError):
                return None
    return None


# ------------------------------------------------------------------------------- paths
def _arc_cubics(
    x0: float,
    y0: float,
    rx: float,
    ry: float,
    phi_deg: float,
    large: bool,
    sweep: bool,
    x1: float,
    y1: float,
) -> list[tuple[float, float, float, float, float, float]]:
    """An SVG elliptical arc as cubic Bezier segments (the standard endpoint -> centre conversion)."""
    if (x0, y0) == (x1, y1):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-9 or ry < 1e-9:
        return [(x0, y0, x1, y1, x1, y1)]
    phi = math.radians(phi_deg % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x0 - x1) / 2, (y0 - y1) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (x0 + x1) / 2, sp * cxp + cp * cyp + (y0 + y1) / 2

    def ang(ux: float, uy: float, vx: float, vy: float) -> float:
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    n = max(1, math.ceil(abs(dth) / (math.pi / 2) - 1e-9))
    seg = dth / n
    k = 4.0 / 3.0 * math.tan(seg / 4)
    out = []
    t = th1
    for _ in range(n):
        c1, s1 = math.cos(t), math.sin(t)
        c2, s2 = math.cos(t + seg), math.sin(t + seg)
        p1 = (c1 - k * s1, s1 + k * c1)
        p2 = (c2 + k * s2, s2 - k * c2)
        p3 = (c2, s2)
        pts = []
        for px, py in (p1, p2, p3):
            ex, ey = rx * px, ry * py
            pts += [cp * ex - sp * ey + cx, sp * ex + cp * ey + cy]
        out.append((pts[0], pts[1], pts[2], pts[3], pts[4], pts[5]))
        t += seg
    return out


def parse_path_d(d: str) -> list[tuple[Any, ...]]:
    """An SVG path's ``d`` as absolute IR commands: M, L, Q, C, Z (H, V, S, T, A and every relative form are folded in)."""
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", d)
    out: list[tuple[Any, ...]] = []
    i = 0
    cmd = ""
    x = y = sx = sy = 0.0
    last_c: tuple[float, float] | None = None  # last cubic control point (for S)
    last_q: tuple[float, float] | None = None  # last quad control point (for T)

    def num() -> float:
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    def flag() -> bool:
        # arc flags may be written without separators ("a1 1 0 00.5.5"): a flag is a single 0 or 1
        nonlocal i
        t = tokens[i]
        if t in ("0", "1"):
            i += 1
            return t == "1"
        if t[0] in "01" and len(t) > 1:  # "00.5" or "10.5": split the flag off
            tokens[i] = t[1:]
            return t[0] == "1"
        raise SvgError("bad arc flag in path data")

    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                if out and out[-1][0] != "Z":
                    out.append(("Z",))
                x, y = sx, sy
                last_c = last_q = None
                continue
        elif not cmd:
            raise SvgError("path data must start with a command")
        rel = cmd.islower()
        c = cmd.upper()
        try:
            if c == "M":
                nx, ny = num(), num()
                if rel:
                    nx, ny = x + nx, y + ny
                x, y = sx, sy = nx, ny
                out.append(("M", x, y))
                cmd = "l" if rel else "L"  # extra pairs after a moveto are linetos
                last_c = last_q = None
            elif c == "L":
                nx, ny = num(), num()
                if rel:
                    nx, ny = x + nx, y + ny
                x, y = nx, ny
                out.append(("L", x, y))
                last_c = last_q = None
            elif c == "H":
                nx = num()
                x = x + nx if rel else nx
                out.append(("L", x, y))
                last_c = last_q = None
            elif c == "V":
                ny = num()
                y = y + ny if rel else ny
                out.append(("L", x, y))
                last_c = last_q = None
            elif c == "C":
                x1, y1, x2, y2, ex, ey = (num() for _ in range(6))
                if rel:
                    x1, y1, x2, y2, ex, ey = x + x1, y + y1, x + x2, y + y2, x + ex, y + ey
                out.append(("C", x1, y1, x2, y2, ex, ey))
                last_c, last_q = (x2, y2), None
                x, y = ex, ey
            elif c == "S":
                x2, y2, ex, ey = (num() for _ in range(4))
                if rel:
                    x2, y2, ex, ey = x + x2, y + y2, x + ex, y + ey
                x1, y1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
                out.append(("C", x1, y1, x2, y2, ex, ey))
                last_c, last_q = (x2, y2), None
                x, y = ex, ey
            elif c == "Q":
                x1, y1, ex, ey = (num() for _ in range(4))
                if rel:
                    x1, y1, ex, ey = x + x1, y + y1, x + ex, y + ey
                out.append(("Q", x1, y1, ex, ey))
                last_q, last_c = (x1, y1), None
                x, y = ex, ey
            elif c == "T":
                ex, ey = num(), num()
                if rel:
                    ex, ey = x + ex, y + ey
                x1, y1 = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                out.append(("Q", x1, y1, ex, ey))
                last_q, last_c = (x1, y1), None
                x, y = ex, ey
            elif c == "A":
                rx, ry, rot = num(), num(), num()
                large, sweep = flag(), flag()
                ex, ey = num(), num()
                if rel:
                    ex, ey = x + ex, y + ey
                for seg in _arc_cubics(x, y, rx, ry, rot, large, sweep, ex, ey):
                    out.append(("C", *seg))
                x, y = ex, ey
                last_c = last_q = None
            else:
                raise SvgError(f"unknown path command {cmd!r}")
        except (IndexError, ValueError) as exc:
            if isinstance(exc, SvgError):
                raise
            raise SvgError("path data ends in the middle of a command") from exc
    return out


def _xf_cmds(cmds: list[tuple[Any, ...]], m: Matrix) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for c in cmds:
        op = c[0]
        if op == "Z":
            out.append(c)
            continue
        vals: list[float] = []
        for j in range(1, len(c), 2):
            px, py = mapply(m, c[j], c[j + 1])
            vals += [px, py]
        out.append((op, *vals))
    return out


def ellipse_cmds(cx: float, cy: float, rx: float, ry: float) -> list[tuple[Any, ...]]:
    k = 0.5522847498
    return [
        ("M", cx + rx, cy),
        ("C", cx + rx, cy + k * ry, cx + k * rx, cy + ry, cx, cy + ry),
        ("C", cx - k * rx, cy + ry, cx - rx, cy + k * ry, cx - rx, cy),
        ("C", cx - rx, cy - k * ry, cx - k * rx, cy - ry, cx, cy - ry),
        ("C", cx + k * rx, cy - ry, cx + rx, cy - k * ry, cx + rx, cy),
        ("Z",),
    ]


def rrect_cmds(
    x: float, y: float, w: float, h: float, rx: float, ry: float
) -> list[tuple[Any, ...]]:
    rx, ry = min(rx, w / 2), min(ry, h / 2)
    k = 0.5522847498
    return [
        ("M", x + rx, y),
        ("L", x + w - rx, y),
        ("C", x + w - rx + k * rx, y, x + w, y + ry - k * ry, x + w, y + ry),
        ("L", x + w, y + h - ry),
        ("C", x + w, y + h - ry + k * ry, x + w - rx + k * rx, y + h, x + w - rx, y + h),
        ("L", x + rx, y + h),
        ("C", x + rx - k * rx, y + h, x, y + h - ry + k * ry, x, y + h - ry),
        ("L", x, y + ry),
        ("C", x, y + ry - k * ry, x + rx - k * rx, y, x + rx, y),
        ("Z",),
    ]


# ------------------------------------------------------------------------------- styles
@dataclass
class _Style:
    fill: str | None = (
        "black"  # CSS value text; resolved to a colour or a gradient when a shape is made
    )
    stroke: str | None = None
    stroke_width: float = 1.0
    opacity: float = 1.0
    fill_opacity: float = 1.0
    stroke_opacity: float = 1.0
    fill_rule: str = "nonzero"
    linecap: str = "butt"
    display: bool = True
    # hints for the engine
    role: str | None = None
    stroke_role: str | None = None
    lod: int | None = None
    elev: float | None = None
    shadow: bool | None = None
    material: str | None = None
    tag: str | None = None
    plane: str | None = None


_PROPS = {
    "fill": "fill", "stroke": "stroke", "stroke-width": "stroke_width", "opacity": "opacity",
    "fill-opacity": "fill_opacity", "stroke-opacity": "stroke_opacity", "fill-rule": "fill_rule",
    "stroke-linecap": "linecap", "display": "display", "visibility": "visibility",
}  # fmt: skip


def _css_rules(text: str) -> list[tuple[str, dict[str, str]]]:
    """The rules of a ``<style>`` block, read in one pass (a hostile block cannot make it slow): comments dropped, at most
    :data:`MAX_CSS_BYTES` of text and :data:`MAX_CSS_RULES` rules."""
    text = text[:MAX_CSS_BYTES]
    clean: list[str] = []
    i = 0
    while i < len(text):
        j = text.find("/*", i)
        if j < 0:
            clean.append(text[i:])
            break
        clean.append(text[i:j])
        k = text.find("*/", j + 2)
        if k < 0:
            break  # an unterminated comment swallows the rest
        i = k + 2
    text = "".join(clean)
    rules: list[tuple[str, dict[str, str]]] = []
    i = 0
    while i < len(text) and len(rules) < MAX_CSS_RULES:
        open_at = text.find("{", i)
        if open_at < 0:
            break
        close_at = text.find("}", open_at + 1)
        if close_at < 0:
            break
        sel, body = text[i:open_at], text[open_at + 1 : close_at]
        i = close_at + 1
        if "{" in sel:  # a stray brace: what is before the last one is the selector
            sel = sel.rsplit("{", 1)[1]
        decl = {}
        for part in body.split(";"):
            if ":" in part:
                k2, v = part.split(":", 1)
                decl[k2.strip().lower()] = v.strip()
        for one in sel.split(","):
            one = one.strip()
            if one and len(rules) < MAX_CSS_RULES:
                rules.append((one, decl))
    return rules


class _RuleIndex:
    """The CSS rules by the simple selectors this importer understands (``tag``, ``.class``, ``#id``, ``tag.class``), so an
    element finds its rules in a few lookups instead of testing every rule."""

    def __init__(self, rules: list[tuple[str, dict[str, str]]]) -> None:
        self.by_selector: dict[str, list[tuple[int, dict[str, str]]]] = {}
        for n, (sel, decl) in enumerate(rules):
            self.by_selector.setdefault(sel, []).append((n, decl))

    def matching(self, el: ET.Element) -> list[dict[str, str]]:
        if not self.by_selector:
            return []
        classes = (el.get("class") or "").split()
        tag = _local(el.tag)
        keys = [tag, *(f".{c}" for c in classes)]
        if el.get("id"):
            keys.append(f"#{el.get('id')}")
        if classes:
            keys.append(f"{tag}.{classes[0]}")
        found = [hit for k in dict.fromkeys(keys) for hit in self.by_selector.get(k, ())]
        return [decl for _n, decl in sorted(found, key=lambda h: h[0])]  # the later rule wins


def _decls(el: ET.Element, rules: _RuleIndex) -> dict[str, str]:
    """Presentation attributes, then CSS rules, then the ``style`` attribute (later wins)."""
    out: dict[str, str] = {}
    for k in _PROPS:
        if k in el.attrib:
            out[k] = el.attrib[k]
    for decl in rules.matching(el):
        out.update({k: v for k, v in decl.items() if k in _PROPS})
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            if k.strip().lower() in _PROPS:
                out[k.strip().lower()] = v.strip()
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _inherit(parent: _Style, el: ET.Element, rules: _RuleIndex) -> _Style:
    st = replace(parent)
    st.opacity = 1.0  # group opacity multiplies, it is applied below
    d = _decls(el, rules)
    for k, v in d.items():
        attr = _PROPS[k]
        if attr == "fill":
            st.fill = None if v.strip().lower() == "none" else v.strip()
        elif attr == "stroke":
            st.stroke = None if v.strip().lower() == "none" else v.strip()
        elif attr == "stroke_width":
            st.stroke_width = length(v, st.stroke_width)
        elif attr == "opacity":
            st.opacity = max(0.0, min(1.0, length(v, 1.0)))
        elif attr == "fill_opacity":
            st.fill_opacity = max(0.0, min(1.0, length(v, 1.0)))
        elif attr == "stroke_opacity":
            st.stroke_opacity = max(0.0, min(1.0, length(v, 1.0)))
        elif attr == "fill_rule":
            st.fill_rule = v.strip()
        elif attr == "linecap":
            st.linecap = v.strip()
        elif attr == "display":
            if v.strip() == "none":
                st.display = False
        elif k == "visibility" and v.strip() in ("hidden", "collapse"):
            st.display = False
    st.opacity = parent.opacity * st.opacity
    a = el.attrib
    if "data-role" in a:
        st.role = a["data-role"].strip().lower() or None
    if "data-stroke-role" in a:
        st.stroke_role = a["data-stroke-role"].strip().lower() or None
    if "data-lod" in a:
        st.lod = 0 if a["data-lod"].strip() == "0" else 1
    if "data-elev" in a:
        st.elev = length(a["data-elev"], 0.0)
    if "data-shadow" in a:
        st.shadow = a["data-shadow"].strip().lower() not in ("false", "0", "no")
    if "data-material" in a:
        st.material = a["data-material"].strip().lower()
    if "data-tag" in a:
        st.tag = a["data-tag"].strip().lower()
    if "data-plane" in a and a["data-plane"].strip().lower() in PLACE_PLANES:
        st.plane = a["data-plane"].strip().lower()
    return st


# ------------------------------------------------------------------------------- the document
@dataclass
class _Raw:
    shape: Shape
    plane: str | None
    lod: int | None  # data-lod, when the author set it
    elev: float | None
    shadow: bool | None


class _Parser:
    def __init__(
        self, root: ET.Element, viewbox: tuple[float, float, float, float] = (0, 0, 100, 100)
    ) -> None:
        self.root = root
        self.vbw = (
            viewbox[2] if viewbox[2] > 0 else 100.0
        )  # what a percentage length is a percentage of
        self.vbh = viewbox[3] if viewbox[3] > 0 else 100.0
        self.ids: dict[str, ET.Element] = {}
        self.warnings: list[str] = []
        self.raw: list[_Raw] = []
        self.roles: dict[str, str] = {}
        self.count = 0
        self._warned: set[str] = set()
        rules: list[tuple[str, dict[str, str]]] = []
        for el in root.iter():
            i = el.get("id")
            if i:
                self.ids[i] = el
            if _local(el.tag) == "style" and el.text and len(rules) < MAX_CSS_RULES:
                rules += _css_rules(el.text)[: MAX_CSS_RULES - len(rules)]
        self.rules = _RuleIndex(rules)

    def _x(self, el: ET.Element, name: str, default: float = 0.0) -> float:
        """A horizontal length of ``el`` (a percentage is of the viewBox's width)."""
        return length(el.get(name), default, self.vbw)

    def _y(self, el: ET.Element, name: str, default: float = 0.0) -> float:
        return length(el.get(name), default, self.vbh)

    def warn(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            self.warnings.append(msg)

    # -- paint ---------------------------------------------------------------------------------
    def _gradient(self, ref: str) -> tuple[str, str, float] | None:
        m = re.match(r"url\(\s*['\"]?#([^)'\"\s]+)", ref)
        if not m:
            return None
        g = self.ids.get(m.group(1))
        for _ in range(4):  # a gradient may take its stops from another one (href)
            if (
                g is None
                or g.findall("{*}stop")
                or not (g.get("{http://www.w3.org/1999/xlink}href") or g.get("href"))
            ):
                break
            g = self.ids.get(
                (g.get("{http://www.w3.org/1999/xlink}href") or g.get("href") or "").lstrip("#")
            )
        if g is None:
            return None
        stops = []
        for s in g.findall("{*}stop"):
            d = dict(_decls(s, self.rules))
            sd = dict(kv.split(":", 1) for kv in (s.get("style") or "").split(";") if ":" in kv)
            sd = {k.strip(): v.strip() for k, v in sd.items()}
            col = parse_color(sd.get("stop-color", s.get("stop-color", d.get("stop-color"))))
            if col:
                stops.append(col[0])
        if not stops:
            return None
        if len(set(stops)) > 2:
            self.warn(
                "a gradient with more than two colours is drawn with its first and last colour"
            )
        if _local(g.tag) == "radialGradient":
            self.warn(
                "radial gradients are drawn as one flat colour (the middle of the first and the last stop)"
            )
            mid = mix(stops[0], stops[-1], 0.5)
            return mid, mid, 0.0
        x1, y1 = length(g.get("x1"), 0.0), length(g.get("y1"), 0.0)
        x2, y2 = (
            length(g.get("x2"), 1.0 if "%" not in (g.get("x2") or "") else 100.0),
            length(g.get("y2"), 0.0),
        )
        if g.get("x2") is None and g.get("y2") is None:
            x2, y2 = 1.0, 0.0
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1)) if (x2, y2) != (x1, y1) else 0.0
        return stops[0], stops[-1], ang

    def _paint(
        self, st: _Style
    ) -> tuple[str | None, float, str | None, float, tuple[str, str, float] | None]:
        fill = st.fill
        grad = None
        fcol: str | None = None
        falpha = 1.0
        if fill and fill.startswith("url("):
            grad = self._gradient(fill)
            if grad:
                fcol = grad[0]
            else:
                self.warn("a fill that points at a pattern or an unknown gradient is drawn grey")
                fcol = "#9aa3ad"
        elif fill:
            c = parse_color(fill)
            if c:
                fcol, falpha = c
        scol: str | None = None
        salpha = 1.0
        if st.stroke:
            c = parse_color(st.stroke)
            if c:
                scol, salpha = c
            elif st.stroke.startswith("url("):
                g = self._gradient(st.stroke)
                scol = g[0] if g else "#9aa3ad"
        if st.role and fcol is not None:
            self.roles.setdefault(st.role, fcol)
            fcol = f"@{st.role}={fcol}"  # the role, and this piece's own colour (a role's pieces may differ a little)
        if st.stroke_role and scol is not None:
            self.roles.setdefault(st.stroke_role, scol)
            scol = f"@{st.stroke_role}={scol}"
        return fcol, falpha * st.fill_opacity, scol, salpha * st.stroke_opacity, grad

    # -- shapes --------------------------------------------------------------------------------
    def _emit(self, el: ET.Element, st: _Style, m: Matrix, kind: str, data: Any) -> None:
        fcol, falpha, scol, salpha, grad = self._paint(st)
        sw = st.stroke_width * mscale(m) if scol else 0.0
        if fcol is None and (scol is None or sw <= 0):
            return
        alpha = st.opacity * (falpha if fcol is not None else salpha)
        if alpha <= 0.002:
            return
        a, b, c, d, _e, _f = m
        axis = abs(b) < 1e-9 and abs(c) < 1e-9
        uniform = abs(a - d) < 1e-9 * max(1.0, abs(a)) and abs(b + c) < 1e-9 * max(1.0, abs(a))
        geom: Geom
        even = st.fill_rule == "evenodd"
        if kind == "rect":
            x, y, w, h, rx, ry = data
            if rx <= 0 and ry <= 0 and axis:
                x0, y0 = mapply(m, x, y)
                x1, y1 = mapply(m, x + w, y + h)
                geom = Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            elif axis and rx > 0 and abs(abs(a) - abs(d)) < 1e-9:
                x0, y0 = mapply(m, x, y)
                x1, y1 = mapply(m, x + w, y + h)
                geom = Rect(
                    min(x0, x1),
                    min(y0, y1),
                    abs(x1 - x0),
                    abs(y1 - y0),
                    r=min(rx, ry or rx) * abs(a),
                )
            else:
                geom = PathG(tuple(_xf_cmds(rrect_cmds(x, y, w, h, rx or ry, ry or rx), m)))
        elif kind == "ellipse":
            cx, cy, rx, ry = data
            if rx <= 0 or ry <= 0:
                return
            if axis:
                px, py = mapply(m, cx, cy)
                geom = Ellipse(px, py, rx * abs(a), ry * abs(d))
            elif uniform and abs(a) > 1e-9:
                px, py = mapply(m, cx, cy)
                geom = Ellipse(
                    px,
                    py,
                    rx * math.hypot(a, b),
                    ry * math.hypot(a, b),
                    math.degrees(math.atan2(b, a)),
                )
            else:
                geom = PathG(tuple(_xf_cmds(ellipse_cmds(cx, cy, rx, ry), m)))
        elif kind == "line":
            x1, y1, x2, y2 = data
            p0, p1 = mapply(m, x1, y1), mapply(m, x2, y2)
            if scol is None or sw <= 0:
                return
            geom = Line(p0, p1, sw)
            fcol = None
        elif kind == "poly":
            pts, closed = data
            if len(pts) < 2:
                return
            q = tuple(mapply(m, px, py) for px, py in pts)
            geom = Poly(q, closed=closed or fcol is not None)
        else:  # path
            cmds = _xf_cmds(data, m)
            if not cmds:
                return
            geom = PathG(tuple(cmds), even_odd=even)
        if not all(math.isfinite(v) for v in bbox(geom)):
            self.warn("a shape with coordinates that are not numbers is skipped")
            return
        shape = Shape(
            geom,
            fcol,
            stroke=scol if (scol and sw > 0) else None,
            sw=sw if scol else 0.0,
            alpha=alpha,
            material=st.material or "flat",
            tag=st.tag or "",
            cap="round" if st.linecap == "round" else "butt",
            gradient=grad if (grad and fcol is not None) else None,
        )
        self.raw.append(_Raw(shape, st.plane, st.lod, st.elev, st.shadow))

    def _element(self, el: ET.Element, parent: _Style, m: Matrix, depth: int) -> None:
        if depth > MAX_DEPTH:
            self.warn("the drawing is nested too deeply; the rest is skipped")
            return
        self.count += 1
        if self.count > MAX_ELEMENTS:
            raise SvgError(f"the drawing has more than {MAX_ELEMENTS} elements: simplify it first")
        tag = _local(el.tag)
        if tag in ("defs", "style", "title", "desc", "metadata", "linearGradient", "radialGradient", "symbol",
                   "clipPath", "mask", "pattern", "marker", "script", "namedview", "foreignObject"):  # fmt: skip
            return
        st = _inherit(parent, el, self.rules)
        if not st.display:
            return
        m = mmul(m, parse_transform(el.get("transform")))
        for bad, why in (
            ("filter", "filters (blur, drop shadows)"),
            ("mask", "masks"),
            ("clip-path", "clip paths"),
        ):
            if el.get(bad) and el.get(bad) != "none":
                self.warn(f"{why} are not drawn")
        if tag in ("g", "svg", "a", "switch"):
            for child in el:
                self._element(child, st, m, depth + 1)
            return
        if tag == "use":
            href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href") or ""
            target = self.ids.get(href.lstrip("#"))
            if target is None:
                self.warn("a <use> that points outside the file is skipped")
                return
            um = mmul(m, (1, 0, 0, 1, self._x(el, "x"), self._y(el, "y")))
            if _local(target.tag) == "symbol":
                for child in target:
                    self._element(child, st, um, depth + 1)
            else:
                self._element(target, st, um, depth + 1)
            return
        if tag == "rect":
            w, h = self._x(el, "width"), self._y(el, "height")
            if w > 0 and h > 0:
                rx = self._x(el, "rx", -1.0)
                ry = self._y(el, "ry", -1.0)
                rx, ry = (ry if rx < 0 else rx), (rx if ry < 0 else ry)
                self._emit(
                    el,
                    st,
                    m,
                    "rect",
                    (self._x(el, "x"), self._y(el, "y"), w, h, max(rx, 0.0), max(ry, 0.0)),
                )
        elif tag == "circle":
            r = length(el.get("r"), 0.0, math.hypot(self.vbw, self.vbh) / math.sqrt(2))
            self._emit(el, st, m, "ellipse", (self._x(el, "cx"), self._y(el, "cy"), r, r))
        elif tag == "ellipse":
            self._emit(
                el,
                st,
                m,
                "ellipse",
                (self._x(el, "cx"), self._y(el, "cy"), self._x(el, "rx"), self._y(el, "ry")),
            )
        elif tag == "line":
            self._emit(
                el,
                st,
                m,
                "line",
                (self._x(el, "x1"), self._y(el, "y1"), self._x(el, "x2"), self._y(el, "y2")),
            )
        elif tag in ("polyline", "polygon"):
            v = [float(x) for x in _NUM.findall(el.get("points") or "")]
            pts = list(zip(v[0::2], v[1::2], strict=False))
            self._emit(el, st, m, "poly", (pts, tag == "polygon"))
        elif tag == "path":
            self._emit(el, st, m, "path", parse_path_d(el.get("d") or ""))
        elif tag == "text":
            self.warn("text is not drawn: convert it to outlines in your editor")
        elif tag == "image":
            self.warn(
                "pictures embedded in an SVG are not drawn: import the picture itself as a PNG asset"
            )
        elif tag not in ("tspan", "textPath", "stop", "animate", "animateTransform", "set"):
            self.warn(f"<{tag}> is not drawn")


def _shape_extent(sh: Shape) -> tuple[float, float, float, float]:
    """The box a piece really covers.  A curve's control points lie outside it, so a path is measured as skia draws it."""
    if isinstance(sh.geom, PathG) and sh.geom.cmds:
        t = path_from_cmds(sh.geom.cmds).computeTightBounds()
        x0, y0, x1, y1 = t.left(), t.top(), t.right(), t.bottom()
    else:
        x0, y0, x1, y1 = bbox(sh.geom)
    r = sh.sw / 2 if sh.stroke else 0.0
    return x0 - r, y0 - r, x1 + r, y1 + r


def _xf_geom(g: Geom, k: float, tx: float, ty: float, mirror: bool) -> Geom:
    """Scale by ``k`` about the origin, move by (tx, ty) and optionally mirror left-right."""
    sx = -1.0 if mirror else 1.0

    def p(x: float, y: float) -> tuple[float, float]:
        return sx * (x * k + tx), y * k + ty

    if isinstance(g, Rect):
        x0, y0 = p(g.x, g.y)
        x1, y1 = p(g.x + g.w, g.y + g.h)
        return Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), g.r * k)
    if isinstance(g, Ellipse):
        cx, cy = p(g.cx, g.cy)
        return Ellipse(cx, cy, g.rx * k, g.ry * k, -g.rot if mirror else g.rot)
    if isinstance(g, Poly):
        return Poly(tuple(p(x, y) for x, y in g.pts), g.closed, g.smooth, g.curve)
    if isinstance(g, Line):
        return Line(p(*g.a), p(*g.b), g.w * k)
    if isinstance(g, PathG):
        cmds = []
        for c in g.cmds:
            if c[0] == "Z":
                cmds.append(c)
                continue
            vals: list[float] = []
            for j in range(1, len(c), 2):
                vals += list(p(c[j], c[j + 1]))
            cmds.append((c[0], *vals))
        return PathG(tuple(cmds), g.even_odd)
    return g


def parse_svg(
    data: bytes | str,
    *,
    height: float,
    anchor: tuple[float, float] = (0.5, 1.0),
    facing: str = "right",
    fit: str = "content",
) -> Art:
    """Read an SVG into shapes scaled so the art is ``height`` design px tall, its anchor at the origin, facing +x."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_ART_BYTES:
        raise SvgError(f"the file is larger than {MAX_ART_BYTES // (1024 * 1024)} MB")
    if re.search(rb"<!ENTITY", raw, re.I):
        raise SvgError(
            "SVG files with entity declarations are refused (they can be used to blow up a parser)"
        )
    try:
        root = ET.fromstring(raw)
    except (
        ET.ParseError,
        ValueError,
        LookupError,
    ) as exc:  # a character set Python does not know is a LookupError
        raise SvgError(f"this is not a readable SVG ({exc})") from exc
    if _local(root.tag) != "svg":
        raise SvgError("the file is XML but not an SVG (the first element is not <svg>)")

    vb = re.findall(_NUM, root.get("viewBox") or "")
    if len(vb) == 4:
        viewbox = (float(vb[0]), float(vb[1]), float(vb[2]), float(vb[3]))
    else:
        viewbox = (0.0, 0.0, length(root.get("width"), 100.0), length(root.get("height"), 100.0))
    if not all(math.isfinite(v) for v in viewbox):
        raise SvgError("the viewBox has a number that is not a number")
    parser = _Parser(root, viewbox)
    base = _Style()
    try:
        # the root's own paint attributes and transform apply to everything
        parser._element(root, base, IDENTITY, 0)
    except SvgError:
        raise
    except (ArithmeticError, ValueError, TypeError, IndexError, KeyError, RecursionError) as exc:
        raise SvgError(
            f"the drawing could not be read ({type(exc).__name__}): check its numbers and paths"
        ) from exc
    raws = parser.raw
    if not raws:
        raise SvgError(
            "nothing in this drawing can be drawn (it is empty, or only has text, filters or masks)"
        )

    # the art's box
    if fit == "viewbox" and viewbox[2] > 0 and viewbox[3] > 0:
        x0, y0, x1, y1 = viewbox[0], viewbox[1], viewbox[0] + viewbox[2], viewbox[1] + viewbox[3]
    else:
        ext = [_shape_extent(r.shape) for r in raws]
        x0, y0 = min(e[0] for e in ext), min(e[1] for e in ext)
        x1, y1 = max(e[2] for e in ext), max(e[3] for e in ext)
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        raise SvgError("the drawing has coordinates that are not numbers")
    bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    k = height / bh
    ax, ay = anchor
    mirror = facing == "left"
    # _xf_geom maps x to x*k + tx: put the anchor (a fraction of the box) at the origin
    tx, ty = -(x0 + ax * bw) * k, -(y0 + ay * bh) * k

    shapes: list[Shape] = []
    planes: dict[str, list[Shape]] = {}
    area = (bw * k) * (bh * k)
    moved = [_xf_geom(r.shape.geom, k, tx, ty, mirror) for r in raws]
    sizes = []
    for g in moved:
        gx0, gy0, gx1, gy1 = bbox(g)
        sizes.append((gx1 - gx0) * (gy1 - gy0))
    # fine detail (small pieces) is marked lod 1, so the line-art style can leave it out; the biggest pieces always stay
    biggest = {i for _s, i in sorted(((sz, i) for i, sz in enumerate(sizes)), reverse=True)[:8]}
    for i, (r, g) in enumerate(zip(raws, moved, strict=True)):
        lod = (
            r.lod if r.lod is not None else (0 if (sizes[i] >= 0.012 * area or i in biggest) else 1)
        )
        ns = replace(
            r.shape,
            geom=g,
            sw=r.shape.sw * k,
            lod=lod,
            z=float(i),
            elev=r.elev if r.elev is not None else (1.5 if lod == 0 else 0.6),
            shadow=r.shadow if r.shadow is not None else True,
        )
        shapes.append(ns)
        if r.plane:
            planes.setdefault(r.plane, []).append(ns)
    left = -(1 - ax if mirror else ax) * bw * k
    bounds = (left, -ay * bh * k, left + bw * k, (1 - ay) * bh * k)
    return Art(shapes, planes, bounds, dict(parser.roles), parser.warnings, "svg", viewbox)
