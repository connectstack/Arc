"""paper_cutout: layered paper pieces with fibre texture, cut white edges, drop shadows and
stop-motion wobble; page-flip and torn-paper transitions.

Paints the shared shape IR: every piece gets a soft cast shadow sized by its `elev`, a thin
lighter "cut" edge (the white core of the paper), a gentle tone variation and a tileable paper
texture (soft-light blended).  Characters boil at 12 fps like stop-motion; backgrounds stay still.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import skia

from reel.core import fonts
from reel.core.easing import ease
from reel.core.fx import FxConfig
from reel.core.geometry import adjust, darken, lighten, skcolor
from reel.core.ir import Line, Shape
from reel.core.transitions import blend_masked
from reel.styles.base import StyleContext, StylePack, register_style
from reel.styles.paper_cutout.texture import paper_tile


def _h(x: float, y: float, salt: int = 0) -> int:
    return ((int(x * 7.0) * 73856093) ^ (int(y * 7.0) * 19349663) ^ (salt * 83492791)) & 0xFFFF


@register_style("paper_cutout")
class PaperCutout(StylePack):
    """Layered paper cutouts: fibre texture, white cut edges, soft drop shadows, stop-motion wobble."""

    summary = "Layered paper cutouts with fibre texture, drop shadows, stop-motion wobble and page-flip transitions"
    version = "1"
    character_fps = 12.0
    dof = 0.45
    base_shake = 0.30
    fx = FxConfig(
        grain=0.34,  # 0.55 looked heavy at 1:1 and cost 30+ Mbps to encode
        grain_size=1.5,
        chroma_grain=0.2,
        vignette=0.38,
        vignette_softness=0.6,
        bloom=0.15,
        bloom_threshold=0.74,
        bloom_radius=34.0,
        chroma=0.9,
        saturation=0.96,
        contrast=1.08,
        gamma=1.03,
        warmth=0.28,
        lift=(0.012, 0.006, 0.0),
        gain=(1.0, 0.99, 0.96),
    )
    caption_look = {
        "subtitle": {
            "fonts": fonts.MARKER,
            "fill": "#2b2118",
            "stroke": None,
            "shadow": None,
            "highlight": "#d1432e",
            "plate": ("#fbf4e4", 0.97, 0.18),
            "plate_pad": (0.45, 0.18),
            "plate_jitter": 2.4,
            "plate_shadow": True,
            "plate_rotate": -1.1,
        },
        "title": {
            "fonts": fonts.MARKER,
            "fill": "#fff7e6",
            "stroke": "#3a2a1c",
            "stroke_w": 0.10,
            "shadow": (0.0, 8.0, 0.0, "#3a2a1c"),
            "plate": ("#e4572e", 0.97, 0.10),
            "plate_pad": (0.4, 0.14),
            "plate_jitter": 3.2,
            "plate_shadow": True,
            "plate_rotate": 1.4,
        },
        "shout": {
            "fonts": fonts.HEADLINE,
            "fill": "#ffd23f",
            "stroke": "#3a2a1c",
            "stroke_w": 0.14,
            "shadow": (0.0, 12.0, 0.0, "#3a2a1c"),
        },
    }

    # ---- paint pipeline -------------------------------------------------------------------------------
    def _jitter(self, shape: Shape, path: skia.Path, ctx: StyleContext) -> skia.PathEffect | None:
        b = path.getBounds()
        size = max(b.width(), b.height())
        if size < 22 or shape.tag in (
            "eye",
            "pupil",
            "glint",
            "mouth",
            "brow",
            "nose",
            "tear",
            "blush",
            "sweat",
            "teeth",
            "tongue",
            "lid",
        ):
            return None
        seg = max(14.0, min(60.0, size / 7.0))
        dev = max(1.0, min(3.6, size / 80.0))
        seed = _h(b.centerX(), b.centerY(), ctx.tick)
        return skia.DiscretePathEffect.Make(seg, dev, seed)

    def _tone(self, c: str, shape: Shape, path: skia.Path) -> str:
        b = path.getBounds()
        k = (_h(b.centerX(), b.centerY(), 3) / 65535.0 - 0.5) * 0.07
        return adjust(c, light=1.0 + k) if abs(k) > 0.004 else c

    def paint_shadow(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        if (
            not shape.shadow
            or shape.elev <= 0.2
            or shape.alpha < 0.5
            or isinstance(shape.geom, Line)
        ):
            return
        lx, ly = ctx.scheme.light_dir
        d = 2.5 + shape.elev * 2.0
        sigma = 1.6 + shape.elev * 1.0
        dx, dy = -lx * d * ctx.flip, -ly * d
        p = skia.Paint(
            AntiAlias=True,
            Color=skcolor(ctx.scheme.shadow, min(0.55, 0.30 + 0.025 * shape.elev) * shape.alpha),
        )
        p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, sigma))
        eff = self._jitter(shape, path, ctx)
        if eff is not None:
            p.setPathEffect(eff)
        canvas.save()
        canvas.translate(dx, dy)
        canvas.drawPath(path, p)
        canvas.restore()

    def paint_body(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        eff = self._jitter(shape, path, ctx)
        if shape.fill is not None and not isinstance(shape.geom, Line):
            base = self._tone(self.color(shape.fill, shape, ctx), shape, path)
            b = path.getBounds()
            big = (
                max(b.width(), b.height()) >= 22
                and shape.material != "emissive"
                and shape.alpha >= 0.6
            )
            if big:  # the white core of the paper shows along the cut
                edge = skia.Paint(AntiAlias=True, Color=skcolor(lighten(base, 0.62), shape.alpha))
                edge.setStyle(skia.Paint.kStroke_Style)
                edge.setStrokeWidth(5.0)
                edge.setStrokeJoin(skia.Paint.kRound_Join)
                if eff is not None:
                    edge.setPathEffect(eff)
                canvas.drawPath(path, edge)
            fill = skia.Paint(AntiAlias=True, Color=skcolor(base, shape.alpha))
            if shape.gradient is not None:
                fill = self.fill_paint(shape, path, ctx)
            elif big and shape.tag not in (
                "sky",
            ):  # paper isn't flat: lighter top-left, slightly darker bottom-right
                fill.setShader(
                    skia.GradientShader.MakeLinear(
                        [(b.left(), b.top()), (b.right(), b.bottom())],
                        [
                            skcolor(lighten(base, 0.05), shape.alpha),
                            skcolor(darken(base, 0.06), shape.alpha),
                        ],
                    )
                )
            if eff is not None:
                fill.setPathEffect(eff)
            canvas.drawPath(path, fill)
        if shape.stroke and shape.sw > 0:
            sp = self.stroke_paint(shape, ctx)
            if eff is not None and isinstance(shape.geom, Line):
                sp.setPathEffect(eff)
            canvas.drawPath(path, sp)

    @staticmethod
    def _wants_texture(shape: Shape, path: skia.Path) -> bool:
        if (
            shape.fill is None
            or shape.material == "emissive"
            or shape.alpha < 0.35
            or isinstance(shape.geom, Line)
        ):
            return False
        b = path.getBounds()
        return max(b.width(), b.height()) >= 30

    def _texture_shader(self, shape: Shape, path: skia.Path, ctx: StyleContext) -> skia.Shader:
        b = path.getBounds()
        k = ctx.scale
        ox, oy = (_h(b.centerX(), b.centerY(), 9) % 512), (_h(b.centerX(), b.centerY(), 11) % 512)
        m = skia.Matrix.Translate(-ox * k, -oy * k)
        m.postScale(k, k)
        return paper_tile().makeShader(
            skia.TileMode.kRepeat,
            skia.TileMode.kRepeat,
            skia.SamplingOptions(skia.FilterMode.kLinear),
            m,
        )

    def paint_decor(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        """Soft-light the paper texture over the piece (clip + native blend: ~20x faster than
        folding a blend shader into the fill)."""
        if not self._wants_texture(shape, path):
            return
        paint = skia.Paint(AntiAlias=True, Shader=self._texture_shader(shape, path, ctx))
        paint.setBlendMode(skia.BlendMode.kSoftLight)
        paint.setAlphaf((0.55 if shape.material == "paper" else 0.38) * shape.alpha)
        canvas.save()
        canvas.clipPath(path, doAntiAlias=True)
        canvas.drawPaint(paint)
        canvas.restore()

    def draw_shadow(self, canvas: skia.Canvas, sh: Any, ctx: StyleContext) -> None:
        p = skia.Paint(
            AntiAlias=True, Color=skcolor(ctx.scheme.shadow, min(0.6, sh.strength * 1.25))
        )
        p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(2.0, sh.ry * 0.38)))
        canvas.drawOval(skia.Rect(sh.cx - sh.rx, -sh.ry * 0.9, sh.cx + sh.rx, sh.ry * 0.9), p)

    # ---- transitions --------------------------------------------------------------------------------------
    def transition(
        self,
        kind: str,
        a: np.ndarray,
        b: np.ndarray,
        progress: float,
        ctx: StyleContext,
        params: dict[str, Any],
    ) -> np.ndarray:
        if kind == "wipe":
            return _torn_wipe(a, b, progress, str(params.get("direction", "left")), ctx.seed)
        return super().transition(kind, a, b, progress, ctx, params)


def _torn_wipe(a: np.ndarray, b: np.ndarray, p: float, direction: str, seed: int) -> np.ndarray:
    """Wipe with a ragged torn-paper edge and a pale paper-fibre rim."""
    h, w = a.shape[:2]
    pe = float(ease("ease_in_out", p))
    rng = np.random.default_rng(seed & 0xFFFF)
    horizontal = direction in ("left", "right")
    n_along = h if horizontal else w
    t = np.linspace(0, 1, n_along)
    noise = (
        sum(
            float(rng.uniform(0.4, 1.0)) * np.sin(2 * math.pi * f * t + float(rng.uniform(0, 6.28)))
            for f in (3, 7, 17, 41)
        )
        / 4.0
    )
    noise = (np.asarray(noise) * 0.04).astype(np.float32)
    n_across = w if horizontal else h
    coord = np.linspace(0, 1, n_across, dtype=np.float32)
    if direction in ("right", "down"):
        coord = 1 - coord
    edge = 1.05 - pe * 1.10
    cc = coord[None, :] if horizontal else coord[:, None]
    nn = noise[:, None] if horizontal else noise[None, :]
    d = cc - (edge + nn)
    mask = (d > 0).astype(np.float32)
    out = blend_masked(a, b, mask)
    rim = np.clip(1.0 - np.abs(d - 0.005) / 0.012, 0, 1)
    rim = (rim * (d > -0.002)).astype(np.float32)
    paper = np.array([228, 238, 246, 255], dtype=np.float32)
    mixed = out.astype(np.float32) * (1 - rim[..., None]) + paper * rim[..., None]
    return np.clip(mixed + 0.5, 0, 255).astype(np.uint8)
