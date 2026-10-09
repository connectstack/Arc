"""Loading an asset's art once per process, and the small operations on it: silhouette, recolour, placement."""

from __future__ import annotations

import contextlib
import math
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import skia

from reel.assets.model import Art, AssetDef
from reel.assets.raster import PictureError, load_raster
from reel.assets.svg import SvgError, ellipse_cmds, parse_svg, rrect_cmds
from reel.core.geometry import from_hls, hls, path_from_cmds
from reel.core.ir import Ellipse, ImageG, Limb, Line, PathG, Poly, Rect, Shape, bbox

_ART: dict[str, Art] = {}
#: how many pieces of a drawing are unioned into its outline (the biggest ones): the work grows much faster than the count
MAX_SILHOUETTE_PIECES = 200


class ArtError(ValueError):
    """An asset's art cannot be read; the message says why in plain words."""


def load_art(asset: AssetDef) -> Art:
    """The asset's drawing or picture as shapes (memoised by the asset's content hash)."""
    art = _ART.get(asset.content_hash)
    if art is not None:
        return art
    try:
        data = asset.path.read_bytes()
    except OSError as exc:
        raise ArtError(f"cannot read {asset.path.name}: {exc.strerror or exc}") from exc
    try:
        if asset.fmt == "svg":
            art = parse_svg(
                data,
                height=asset.height,
                anchor=asset.anchor,
                facing=asset.facing,
                fit="viewbox" if asset.kind == "place" else asset.fit,
            )
        else:
            art = load_raster(
                data,
                height=asset.height,
                anchor=asset.anchor,
                facing=asset.facing,
                cutout=asset.cutout,
                place=asset.kind == "place",
            )
    except (SvgError, PictureError) as exc:
        raise ArtError(f"{asset.path.name}: {exc}") from exc
    except (ArithmeticError, ValueError, TypeError, IndexError, KeyError, RecursionError) as exc:
        # a file that trips the importer in a way nobody foresaw is "unreadable", never a crash of the caller
        raise ArtError(f"{asset.path.name}: could not be read ({type(exc).__name__})") from exc
    if not all(math.isfinite(v) for v in art.bounds):
        raise ArtError(f"{asset.path.name}: the drawing has coordinates that are not numbers")
    _ART[asset.content_hash] = art
    return art


def forget(asset: AssetDef) -> None:
    """Drop what was loaded for an asset (its shapes and, for a picture, the decoded pixels)."""
    from reel.assets.raster import drop_images

    art = _ART.pop(asset.content_hash, None)
    if art is not None:
        drop_images(art.shapes)


def _geom_path(g: Any) -> skia.Path | None:
    """A skia path made only of lines and cubic curves (no conics), so a union of them can be read back as commands."""
    if isinstance(g, Rect):
        return path_from_cmds(rrect_cmds(g.x, g.y, g.w, g.h, g.r, g.r) if g.r else rect_cmds(g))
    if isinstance(g, Ellipse):
        p = path_from_cmds(ellipse_cmds(g.cx, g.cy, g.rx, g.ry))
        if g.rot:
            p.transform(skia.Matrix.RotateDeg(g.rot, skia.Point(g.cx, g.cy)))
        return p
    if isinstance(g, Poly):
        cmds: list[tuple[Any, ...]] = [("M", *g.pts[0]), *[("L", *q) for q in g.pts[1:]]]
        if g.closed:
            cmds.append(("Z",))
        return path_from_cmds(cmds)
    if isinstance(g, PathG):
        p = path_from_cmds(g.cmds)
        if g.even_odd:
            p.setFillType(skia.PathFillType.kEvenOdd)
        return p
    return None  # lines, limbs, pictures: no filled area to add


def rect_cmds(g: Rect) -> list[tuple[Any, ...]]:
    return [
        ("M", g.x, g.y),
        ("L", g.x + g.w, g.y),
        ("L", g.x + g.w, g.y + g.h),
        ("L", g.x, g.y + g.h),
        ("Z",),
    ]


def silhouette(art: Art) -> tuple[tuple[Any, ...], ...]:
    """The outline of everything the drawing fills (the union of its main pieces), as path commands; () for none.

    Paper cutout casts one soft shadow from it instead of one per piece, and the line-art styles can outline it.
    """
    if art.outline is not None:
        return art.outline
    union: skia.Path | None = None
    pieces: list[tuple[float, skia.Path]] = []
    for s in art.shapes:
        if s.lod > 0 or s.fill is None or s.alpha < 0.5 or isinstance(s.geom, (Line, Limb, ImageG)):
            continue
        p = _geom_path(s.geom)
        if p is not None:
            x0, y0, x1, y1 = bbox(s.geom)
            pieces.append(((x1 - x0) * (y1 - y0), p))
    # a union of thousands of pieces takes minutes: the biggest ones carry the outline (the small ones lie inside it)
    pieces.sort(key=lambda t: -t[0])
    for _area, p in pieces[:MAX_SILHOUETTE_PIECES]:
        try:
            union = p if union is None else (skia.Op(union, p, skia.PathOp.kUnion_PathOp) or union)
        except Exception:  # a degenerate piece must not stop the others
            continue
    out: list[tuple[Any, ...]] = []
    if union is not None:
        V = skia.Path.Verb
        for verb, pts in skia.Path.Iter(union, False):
            if verb == V.kMove_Verb:
                out.append(("M", pts[0].x(), pts[0].y()))
            elif verb == V.kLine_Verb:
                out.append(("L", pts[1].x(), pts[1].y()))
            elif verb == V.kQuad_Verb:
                out.append(("Q", pts[1].x(), pts[1].y(), pts[2].x(), pts[2].y()))
            elif verb == V.kCubic_Verb:
                out.append(("C", *(c for pt in pts[1:4] for c in (pt.x(), pt.y()))))
            elif verb == V.kClose_Verb:
                out.append(("Z",))
    art.outline = tuple(out)
    return art.outline


SHADE_SUFFIXES = ("_dark", "_light")
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def clean_palette(palette: Mapping[str, str] | None) -> dict[str, str]:
    """The colours of a palette that are colours (``#rrggbb``): one that is not (a typo, a name) is left out, so the part keeps
    the drawing's own colour instead of stopping the picture (the linter says which colour is wrong)."""
    return {k: v for k, v in (palette or {}).items() if isinstance(v, str) and _HEX_COLOR.match(v)}


def _shade_like(base: str, shade: str, new_base: str) -> str:
    """``new_base`` stepped the way the drawing steps from ``base`` to ``shade``: the same lightness change, the new hue."""
    _hb, lb, sb = hls(base)
    _hs, ls, ss = hls(shade)
    hn, ln, sn = hls(new_base)
    if ls <= lb:  # a darker shade: keep the same share of the base's lightness
        light = ln * (ls / lb) if lb > 1e-3 else ln
    else:  # a lighter shade: move the same share of the way towards white
        light = ln + (1 - ln) * ((ls - lb) / (1 - lb) if lb < 1 - 1e-3 else 0.0)
    sat = sn * (ss / sb) if sb > 1e-3 else sn
    return from_hls(hn, light, sat)


def role_colors(
    roles: Mapping[str, str], palette: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The colour of every role: the palette's, else the drawing's.

    A shade the drawing makes of another role (``body_dark``, ``leaves_light``) that the palette does not name itself
    follows its base role: recolour ``body`` and ``body_dark`` stays one step darker than the new colour.
    """
    palette = clean_palette(palette)
    colors = {**roles, **palette}
    for name, original in roles.items():
        if name in palette:
            continue
        for suffix in SHADE_SUFFIXES:
            base = name[: -len(suffix)] if name.endswith(suffix) else None
            if base and base in palette and base in roles:
                with contextlib.suppress(
                    ValueError
                ):  # a role colour that is not plain hex stays as drawn
                    colors[name] = _shade_like(roles[base], original, palette[base])
    return colors


def split_role(token: str) -> tuple[str, str]:
    """``'@fur=#d57a2c'`` -> ``('fur', '#d57a2c')``: the role a piece belongs to and the colour the drawing gave it."""
    role, _, own = token[1:].partition("=")
    return role, own


def resolve_color(
    token: str, roles: Mapping[str, str], palette: Mapping[str, str], derived: Mapping[str, str]
) -> str:
    """The colour of one ``@role`` fill.

    A role the palette names takes the palette's colour (a piece the drawing made a little darker or lighter than the role's
    first colour keeps that step); a shade that follows a recoloured base takes the derived colour; anything else keeps the
    colour the drawing gave it."""
    role, own = split_role(token)
    base = roles.get(role)
    if role in palette:
        new = palette[role]
        if own and base and own.lower() != base.lower():
            with contextlib.suppress(ValueError):
                return _shade_like(base, own, new)
        return new
    if role in derived and derived[role] != base:
        return derived[role]
    return own or base or "#9aa3ad"


def recolor(
    shapes: list[Shape], roles: Mapping[str, str], palette: Mapping[str, str] | None = None
) -> list[Shape]:
    """Shapes with ``@role`` colours resolved: the palette's colour for a role it names, else the one the drawing gave."""
    palette = clean_palette(palette)
    derived = role_colors(roles, palette)
    out: list[Shape] = []
    for s in shapes:
        fill, stroke = s.fill, s.stroke
        if (fill and fill.startswith("@")) or (stroke and stroke.startswith("@")):
            if fill and fill.startswith("@"):
                fill = resolve_color(fill, roles, palette, derived)
            if stroke and stroke.startswith("@"):
                stroke = resolve_color(stroke, roles, palette, derived)
            s = replace(s, fill=fill, stroke=stroke)
        out.append(s)
    return out
