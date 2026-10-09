"""Style packs: how shapes, characters, captions, post-FX and transitions *look*.

A style pack never decides what is drawn - templates and the figure builder do (as shape IR).
It decides how: `paint_shape` is the one brush everything flows through, and the five hooks
(`draw_background`, `draw_character`, `draw_caption`, `post_process`, `transition`) can each
be overridden for a completely custom look.  Anything not overridden falls back to the shared
defaults here, so a new style can be a handful of lines (see `reel new-style`).
"""

from __future__ import annotations

import math
from abc import ABC
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import skia

from reel.assets.raster import image_for
from reel.core.catalog import CATALOG, Catalog
from reel.core.figure import FigureBuild, ShadowInfo
from reel.core.fx import FxConfig
from reel.core.geometry import (
    darken,
    hex_to_rgba,
    limb_path,
    path_from_cmds,
    rounded_poly,
    skcolor,
    smooth_curve,
)
from reel.core.ir import Ellipse, ImageG, Limb, Line, PathG, Poly, Rect, Shape, bbox
from reel.core.rig import PosedFigure
from reel.core.rng import derive_rng

if True:  # typing-only imports kept lazy to avoid import cycles
    from reel.core.captions import CaptionLook, CaptionRender

#: tags that get a little cel-shading in styles that shade
SHADED_TAGS = frozenset(
    {"head", "torso", "limb", "hand", "foot", "neck", "prop", "pelvis", "hood", "finger", "ear"}
)


@dataclass
class StyleContext:
    """Per-frame facts handed to every style hook."""

    scale: float  # device px per design px
    size: tuple[int, int]  # device pixels
    scheme: Any  # reel.core.ir.ColorScheme
    seed: int
    t: float = 0.0  # scene-local seconds
    frame: int = 0  # scene-local frame index
    tick: int = (
        0  # quantised time (e.g. 12/s) for line-boil / stop-motion jitter; 0 = static (plates)
    )
    flip: float = 1.0  # -1 while drawing a mirrored (left-facing) character
    fps: int = 30
    safe: tuple[float, float, float, float] = (0.10, 0.20, 0.07, 0.07)  # top, bottom, left, right
    extras: dict[str, Any] = field(default_factory=dict)

    def rng(self, *key: Any) -> np.random.Generator:
        return derive_rng(self.seed, *key)


def shape_path(g: Any) -> skia.Path | None:
    """skia path for a geometry (None for pure strokes handled by the caller)."""
    if isinstance(g, Rect):
        p = skia.Path()
        if g.r > 0:
            p.addRoundRect(skia.Rect(g.x, g.y, g.x + g.w, g.y + g.h), g.r, g.r)
        else:
            p.addRect(skia.Rect(g.x, g.y, g.x + g.w, g.y + g.h))
        return p
    if isinstance(g, Ellipse):
        p = skia.Path()
        p.addOval(skia.Rect(g.cx - g.rx, g.cy - g.ry, g.cx + g.rx, g.cy + g.ry))
        if g.rot:
            p.transform(skia.Matrix.RotateDeg(g.rot, skia.Point(g.cx, g.cy)))
        return p
    if isinstance(g, Poly):
        return smooth_curve(g.pts, g.closed) if g.curve else rounded_poly(g.pts, g.smooth, g.closed)
    if isinstance(g, Limb):
        return limb_path(g.a, g.b, g.w0, g.w1)
    if isinstance(g, Line):
        p = skia.Path()
        p.moveTo(*g.a)
        p.lineTo(*g.b)
        return p
    if isinstance(g, PathG):
        p = path_from_cmds(g.cmds)
        if g.even_odd:
            p.setFillType(skia.PathFillType.kEvenOdd)
        return p
    if isinstance(g, ImageG):  # what is opaque in the picture, else the whole rectangle
        if g.outline:
            return path_from_cmds(g.outline)
        p = skia.Path()
        p.addRect(skia.Rect(g.x, g.y, g.x + g.w, g.y + g.h))
        return p
    raise TypeError(f"unknown geometry {type(g).__name__}")


class StylePack(ABC):  # noqa: B024 - all hooks have working defaults
    """Base class of every style.  Subclass, set the class attributes, override what you need."""

    name: ClassVar[str] = ""
    summary: ClassVar[str] = ""
    version: ClassVar[str] = "1"
    #: pose sampling rate for characters (stop-motion feel); None = every frame
    character_fps: ClassVar[float | None] = None
    #: depth-of-field strength (0 = everything sharp unless a rack_focus move asks for it)
    dof: ClassVar[float] = 0.0
    #: lowest level of detail drawn (0 = only essential shapes, e.g. stick figures)
    max_lod: ClassVar[int] = 2
    fx: ClassVar[FxConfig] = FxConfig()
    caption_look: ClassVar[dict[str, Any]] = {}

    def __init__(self, ctx: StyleContext | None = None) -> None:
        self._noise_cache: dict[Any, Any] = {}

    # ---- colours ---------------------------------------------------------------------------------
    def color(self, c: str, shape: Shape | None, ctx: StyleContext) -> str:
        """Resolve a role/hex to the final hex for this style (lighting applied unless emissive)."""
        if shape is not None and shape.material == "emissive":
            return ctx.scheme.base(c)
        return str(ctx.scheme.lit(c))

    # ---- the brush --------------------------------------------------------------------------------
    def paint_shape(self, canvas: skia.Canvas, shape: Shape, ctx: StyleContext) -> None:
        if shape.lod > self.max_lod:
            return
        path = shape_path(shape.geom)
        if path is None:
            return
        canvas.save()
        if shape.xf is not None:
            dx, dy, rot, sx, sy, ox, oy = shape.xf
            if dx or dy:
                canvas.translate(dx, dy)
            if rot:
                canvas.rotate(rot, ox, oy)
            if sx != 1.0 or sy != 1.0:
                canvas.translate(ox, oy)
                canvas.scale(sx, sy)
                canvas.translate(-ox, -oy)
        if shape.glow > 0 and shape.fill:
            self.paint_glow(canvas, shape, path, ctx)
        self.paint_shadow(canvas, shape, path, ctx)
        if isinstance(shape.geom, ImageG):
            self.paint_image(canvas, shape, path, ctx)
        else:
            self.paint_body(canvas, shape, path, ctx)
            self.paint_decor(canvas, shape, path, ctx)
        canvas.restore()

    def paint_glow(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        assert shape.fill is not None
        p = skia.Paint(
            AntiAlias=True, Color=skcolor(self.color(shape.fill, shape, ctx), 0.55 * shape.alpha)
        )
        p.setMaskFilter(
            skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(1.0, shape.glow * 0.5))
        )
        canvas.drawPath(path, p)

    def paint_shadow(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        """Cast shadow under a shape. Flat styles skip it; layered styles (paper) override."""

    def paint_body(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        if shape.fill is not None and not isinstance(shape.geom, Line):
            canvas.drawPath(path, self.fill_paint(shape, path, ctx))
        if shape.stroke and shape.sw > 0:
            canvas.drawPath(path, self.stroke_paint(shape, ctx))

    def paint_decor(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        """Texture / shading on top of the fill (clipped to the shape by the override)."""

    # ---- pictures (imported PNG/JPG assets) ---------------------------------------------------------------
    def image_paint(
        self, shape: Shape, ctx: StyleContext, *, saturation: float = 1.0
    ) -> skia.Paint:
        """A paint for a picture: the shape's opacity, the scene's light (ambient colour x exposure), optionally washed out."""
        p = skia.Paint(AntiAlias=True)
        p.setAlphaf(max(0.0, min(1.0, shape.alpha)))
        lit = ctx.scheme.lit("#ffffff")
        filt = None
        if lit.lower() != "#ffffff":
            filt = skia.ColorFilters.Blend(skcolor(lit), skia.BlendMode.kModulate)
        if saturation < 0.999:
            s = saturation
            lum = (0.2126, 0.7152, 0.0722)
            m = [
                lum[0] * (1 - s) + s, lum[1] * (1 - s), lum[2] * (1 - s), 0, 0,
                lum[0] * (1 - s), lum[1] * (1 - s) + s, lum[2] * (1 - s), 0, 0,
                lum[0] * (1 - s), lum[1] * (1 - s), lum[2] * (1 - s) + s, 0, 0,
                0, 0, 0, 1, 0,
            ]  # fmt: skip
            sat = skia.ColorFilters.Matrix(m)
            filt = sat if filt is None else skia.ColorFilters.Compose(filt, sat)
        if filt is not None:
            p.setColorFilter(filt)
        return p

    def draw_picture(self, canvas: skia.Canvas, shape: Shape, paint: skia.Paint) -> None:
        g = shape.geom
        assert isinstance(g, ImageG)
        img = image_for(g.key)
        canvas.drawImageRect(
            img,
            skia.Rect(0, 0, img.width(), img.height()),
            skia.Rect(g.x, g.y, g.x + g.w, g.y + g.h),
            skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear),
            paint,
        )

    def paint_image(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        """Draw an imported picture (its shadow, if the style has one, is already down).  Styles override this to give
        pictures their own edge and texture; the default is the picture itself."""
        self.draw_picture(canvas, shape, self.image_paint(shape, ctx))

    def fill_paint(self, shape: Shape, path: skia.Path, ctx: StyleContext) -> skia.Paint:
        assert shape.fill is not None
        paint = skia.Paint(
            AntiAlias=True, Color=skcolor(self.color(shape.fill, shape, ctx), shape.alpha)
        )
        if shape.gradient is not None:
            c0, c1, ang = shape.gradient
            x0, y0, x1, y1 = bbox(shape.geom)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            ex, ey = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            half = abs(ex) * (x1 - x0) / 2 + abs(ey) * (y1 - y0) / 2
            paint.setShader(
                skia.GradientShader.MakeLinear(
                    [(cx - ex * half, cy - ey * half), (cx + ex * half, cy + ey * half)],
                    [
                        skcolor(self.color(c0, shape, ctx), shape.alpha),
                        skcolor(self.color(c1, shape, ctx), shape.alpha),
                    ],
                )
            )
        return paint

    def stroke_paint(self, shape: Shape, ctx: StyleContext) -> skia.Paint:
        assert shape.stroke is not None
        p = skia.Paint(
            AntiAlias=True, Color=skcolor(self.color(shape.stroke, shape, ctx), shape.alpha)
        )
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(shape.sw)
        p.setStrokeCap(skia.Paint.kRound_Cap if shape.cap == "round" else skia.Paint.kButt_Cap)
        p.setStrokeJoin(skia.Paint.kRound_Join)
        return p

    # ---- the five hooks -------------------------------------------------------------------------------
    def draw_background(
        self, canvas: skia.Canvas, shapes: Sequence[Shape], plane: str, ctx: StyleContext
    ) -> None:
        """Paint one depth plane's shapes (already animated for this frame, back to front)."""
        for s in shapes:
            self.paint_shape(canvas, s, ctx)

    def draw_shadow(self, canvas: skia.Canvas, sh: ShadowInfo, ctx: StyleContext) -> None:
        """Contact shadow on the ground (figure space, y=0 is the floor)."""
        p = skia.Paint(AntiAlias=True, Color=skcolor(ctx.scheme.shadow, sh.strength))
        p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(2.0, sh.ry * 0.45)))
        canvas.drawOval(skia.Rect(sh.cx - sh.rx, -sh.ry, sh.cx + sh.rx, sh.ry), p)

    def draw_character(
        self, canvas: skia.Canvas, build: FigureBuild, fig: PosedFigure, ctx: StyleContext
    ) -> None:
        """Paint a posed character (canvas is in figure space, already positioned/scaled/mirrored)."""
        for s in build.shapes:
            self.paint_shape(canvas, s, ctx)

    def draw_caption(self, canvas: skia.Canvas, cap: CaptionRender, ctx: StyleContext) -> None:
        from reel.core.captions import draw_caption_default

        draw_caption_default(canvas, cap, self.caption_style(cap.style, ctx), ctx)

    def caption_style(self, name: str, ctx: StyleContext) -> CaptionLook:
        from reel.core.captions import look_for

        return look_for(name, self.caption_look)

    def post_process(self, frame: np.ndarray, ctx: StyleContext, fx: FxConfig) -> np.ndarray:
        from reel.core.fx import apply_fx

        return apply_fx(frame, fx, ctx)

    def transition(
        self,
        kind: str,
        a: np.ndarray,
        b: np.ndarray,
        progress: float,
        ctx: StyleContext,
        params: dict[str, Any],
    ) -> np.ndarray:
        from reel.core.transitions import run_transition

        return run_transition(kind, a, b, progress, ctx, params, style=self)

    # ---- helpers for subclasses ------------------------------------------------------------------------
    def shade_color(self, c: str, amount: float = 0.16) -> str:
        return darken(c, amount)

    def with_alpha(self, c: str, a: float) -> int:
        r, g, b, _ = hex_to_rgba(c)
        return int(skia.Color(r, g, b, round(255 * a)))


def register_style(
    name: str, *, catalog: Catalog | None = None
) -> Callable[[type[StylePack]], type[StylePack]]:
    """Class decorator: register a StylePack under ``name`` (what specs put in meta.style)."""

    def wrap(cls: type[StylePack]) -> type[StylePack]:
        cls.name = name
        (catalog or CATALOG).styles.register(
            name, cls, summary=cls.summary or (cls.__doc__ or "").strip().splitlines()[0]
        )
        return cls

    return wrap
