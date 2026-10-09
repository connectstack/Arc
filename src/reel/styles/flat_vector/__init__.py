"""flat_vector: clean shapes, bold palette, a touch of cel shading."""

from __future__ import annotations

import skia

from reel.core import fonts
from reel.core.fx import FxConfig
from reel.core.geometry import darken, skcolor
from reel.core.ir import Line, Shape
from reel.styles.base import SHADED_TAGS, StyleContext, StylePack, register_style


@register_style("flat_vector")
class FlatVector(StylePack):
    """Clean flat shapes with a bold palette, soft cel shading and crisp bold captions."""

    summary = "Clean vector shapes, bold palette, soft cel shading"
    version = "1"
    character_fps = None
    dof = 0.0
    fx = FxConfig(
        grain=0.10,
        grain_size=1.0,
        vignette=0.20,
        bloom=0.14,
        bloom_threshold=0.78,
        chroma=0.5,
        saturation=1.07,
        contrast=1.05,
        gamma=1.0,
        warmth=0.04,
    )
    caption_look = {
        "*": {"fonts": fonts.SANS_BOLD},
        "subtitle": {
            "fill": "#ffffff",
            "stroke": "#1b1b2a",
            "stroke_w": 0.16,
            "highlight": "#ffd23f",
            "shadow": (0.0, 6.0, 7.0, "#00000066"),
        },
        "title": {
            "fill": "#ffffff",
            "stroke": "#1b1b2a",
            "stroke_w": 0.13,
            "shadow": (0.0, 9.0, 0.0, "#1b1b2a"),
        },
        "shout": {
            "fill": "#ffd23f",
            "stroke": "#1b1b2a",
            "stroke_w": 0.14,
            "shadow": (0.0, 12.0, 0.0, "#1b1b2a"),
        },
    }

    def paint_body(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        if (
            shape.fill is None
            or isinstance(shape.geom, Line)
            or shape.tag not in SHADED_TAGS
            or shape.alpha < 0.5
        ):
            super().paint_body(canvas, shape, path, ctx)
            return
        base = self.color(shape.fill, shape, ctx)
        canvas.drawPath(
            path, skia.Paint(AntiAlias=True, Color=skcolor(darken(base, 0.17), shape.alpha))
        )
        b = path.getBounds()
        k = max(3.0, min(13.0, min(b.width(), b.height()) * 0.12))
        lx, ly = ctx.scheme.light_dir
        canvas.save()
        canvas.clipPath(path, doAntiAlias=True)
        canvas.translate(lx * ctx.flip * k, ly * k)
        canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=skcolor(base, shape.alpha)))
        canvas.restore()
        if shape.stroke and shape.sw > 0:
            canvas.drawPath(path, self.stroke_paint(shape, ctx))

    def paint_image(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        """A picture gets the flat style's clean dark edge (a backdrop does not)."""
        if shape.tag != "backdrop" and shape.alpha > 0.3:
            edge = skia.Paint(AntiAlias=True, Color=skcolor("#1b1b2a", 0.85 * shape.alpha))
            edge.setStyle(skia.Paint.kStroke_Style)
            edge.setStrokeWidth(7.0)
            edge.setStrokeJoin(skia.Paint.kRound_Join)
            canvas.drawPath(path, edge)
        self.draw_picture(canvas, shape, self.image_paint(shape, ctx))
