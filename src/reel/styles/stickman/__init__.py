"""stickman: marker line art on a whiteboard.

Characters are drawn as thick round-capped marker strokes (the rig's joints, so squash-and-stretch,
walks and gestures all carry over) with a hand-drawn double-stroke wobble that re-rolls ~12 times a
second.  Backgrounds are re-interpreted as line drawings: only essential (lod 0) shapes, light tinted
fills with ink outlines, big backdrops left clean.
"""

from __future__ import annotations

import math
from typing import Any

import skia

from reel.core import fonts
from reel.core.fx import FxConfig
from reel.core.geometry import Pt, Rot2D, adjust, darken, mix, skcolor
from reel.core.ir import Line, PathG, Shape, bbox
from reel.styles.base import StyleContext, StylePack, register_style, shape_path

INK = "#1f2430"
BOARD = "#fbfbf8"
SKIP_TAGS = frozenset({
    "limb", "hand", "finger", "head", "torso", "pelvis", "neck", "hood", "ear", "foot", "sole", "cuff", "collar", "stripe", "pocket", "string", "tie",
    "lapel", "shirt", "placket", "button", "panel", "light", "visor", "antenna", "antenna_tip", "cape", "hair", "glint", "nose", "blush", "teeth", "tongue",
})  # fmt: skip
FACE_FILL = frozenset({"tear", "sweat"})


def _lerp(a: Pt, b: Pt, t: float) -> Pt:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _h(x: float, y: float, salt: int = 0) -> int:
    return ((int(x * 5.0) * 73856093) ^ (int(y * 5.0) * 19349663) ^ (salt * 83492791)) & 0xFFFF


@register_style("stickman")
class Stickman(StylePack):
    """Thick marker stick figures with squash-and-stretch on a whiteboard-style set."""

    summary = "Marker line art: thick round strokes, wobbly hand-drawn feel, whiteboard backgrounds"
    version = "1"
    character_fps = 10.0
    dof = 0.0
    max_lod = 0
    fx = FxConfig(
        grain=0.05,
        grain_size=1.2,
        vignette=0.10,
        bloom=0.0,
        chroma=0.0,
        saturation=1.0,
        contrast=1.03,
        warmth=0.03,
    )
    caption_look = {
        "*": {"fonts": fonts.MARKER},
        "subtitle": {
            "fill": INK,
            "stroke": "#fbfbf8",
            "stroke_w": 0.16,
            "shadow": None,
            "highlight": "#e63946",
        },
        "title": {
            "fill": INK,
            "stroke": "#fbfbf8",
            "stroke_w": 0.12,
            "shadow": None,
            "highlight": "#e63946",
        },
        "shout": {
            "fonts": fonts.HEADLINE,
            "fill": "#e63946",
            "stroke": INK,
            "stroke_w": 0.12,
            "shadow": (0.0, 9.0, 0.0, INK),
        },
    }

    # ---- colours: marker palette ------------------------------------------------------------------------
    def color(self, c: str, shape: Shape | None, ctx: StyleContext) -> str:
        base = ctx.scheme.base(c)
        if shape is not None and shape.material == "emissive":
            return mix(BOARD, base, 0.45)
        return base

    # ---- jittered marker strokes ----------------------------------------------------------------------------
    def _stroke(
        self,
        canvas: skia.Canvas,
        path: skia.Path,
        color: str,
        width: float,
        seed: int,
        *,
        alpha: float = 1.0,
        wobble: float = 1.6,
    ) -> None:
        p = skia.Paint(AntiAlias=True, Color=skcolor(color, alpha))
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(width)
        p.setStrokeCap(skia.Paint.kRound_Cap)
        p.setStrokeJoin(skia.Paint.kRound_Join)
        if wobble > 0:
            b = path.getBounds()
            size = max(b.width(), b.height())
            if size >= 36:  # tiny shapes (hands, pupils) stay clean circles
                p.setPathEffect(
                    skia.DiscretePathEffect.Make(
                        max(6.0, min(22.0, size / 5.0)), min(wobble, size * 0.05), seed
                    )
                )
        canvas.drawPath(path, p)

    def _marker(
        self,
        canvas: skia.Canvas,
        pts: list[Pt],
        color: str,
        width: float,
        ctx: StyleContext,
        salt: int,
        *,
        smooth: bool = False,
    ) -> None:
        path = skia.Path()
        path.moveTo(*pts[0])
        for q in pts[1:]:
            path.lineTo(*q)
        self._marker_path(canvas, path, color, width, ctx, salt)

    def _marker_path(
        self,
        canvas: skia.Canvas,
        path: skia.Path,
        color: str,
        width: float,
        ctx: StyleContext,
        salt: int,
    ) -> None:
        self._stroke(canvas, path, color, width, ctx.tick * 31 + salt)  # main stroke
        self._stroke(
            canvas, path, color, width * 0.5, ctx.tick * 17 + salt + 9, alpha=0.8, wobble=2.4
        )  # second pass: sketchy

    # ---- backgrounds: line drawings ---------------------------------------------------------------------------
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
        self._ink_shape(canvas, shape, path, ctx)
        canvas.restore()

    def _ink_shape(
        self, canvas: skia.Canvas, shape: Shape, path: skia.Path, ctx: StyleContext
    ) -> None:
        x0, y0, x1, y1 = bbox(shape.geom)
        backdrop = (x1 - x0) >= 1500 and (y1 - y0) >= 1500
        band = (x1 - x0) >= 1500 and not backdrop
        seed = _h((x0 + x1) / 2, (y0 + y1) / 2, ctx.tick)
        if isinstance(shape.geom, Line):
            col = self.color(shape.stroke or "ink", shape, ctx)
            self._stroke(
                canvas,
                path,
                darken(col, 0.25) if shape.material != "emissive" else col,
                max(4.0, min(shape.sw, 9.0)),
                seed,
                alpha=shape.alpha,
            )
            return
        if shape.fill is not None and shape.alpha > 0.05:
            tint = (
                0.0
                if backdrop and shape.tag in ("sky", "wall")
                else (0.10 if shape.material != "emissive" else 0.35)
            )
            fillc = mix(BOARD, self.color(shape.fill, shape, ctx), tint)
            canvas.drawPath(
                path, skia.Paint(AntiAlias=True, Color=skcolor(fillc, min(1.0, shape.alpha * 1.1)))
            )
        if (
            shape.alpha < 0.3
            or backdrop
            or shape.tag in ("sky", "sun_halo", "bokeh", "star", "ray")
        ):
            return
        if band:  # ground/floor bands: just the horizon line
            ln = skia.Path()
            ln.moveTo(x0, y0)
            ln.lineTo(x1, y0)
            self._stroke(canvas, ln, INK, 8.0, seed)
            return
        w = 7.0 if max(x1 - x0, y1 - y0) > 70 else 5.0
        self._stroke(canvas, path, INK, w, seed)

    # ---- characters: stick figures ----------------------------------------------------------------------------
    def draw_character(self, canvas: skia.Canvas, build: Any, fig: Any, ctx: StyleContext) -> None:
        d = fig.dims
        P = fig.pts
        pal = build.meta["palette"]
        arch = build.meta["arch"]
        pose = fig.pose
        W = 15.0
        shirt = adjust(pal.get("shirt", "#e4572e"), sat=1.05, light=0.95)
        pants = darken(pal.get("pants", INK), 0.1)
        # legs (pelvis bar + two legs with feet)
        for s in ("l", "r"):
            self._marker(
                canvas,
                [
                    P["hip_c"],
                    P[f"hip_{s}"],
                    P[f"knee_{s}"],
                    P[f"ankle_{s}"],
                    _lerp(P[f"ankle_{s}"], P[f"toe_{s}"], 0.8),
                ],
                INK,
                W,
                ctx,
                3 if s == "l" else 5,
            )
        # arms
        for s in ("l", "r"):
            self._marker(
                canvas,
                [P["shoulder_c"], P[f"shoulder_{s}"], P[f"elbow_{s}"], P[f"wrist_{s}"]],
                INK,
                W * 0.92,
                ctx,
                11 if s == "l" else 13,
            )
            hp = P[f"hand_{s}"]
            hc = skia.Path()
            hc.addCircle(hp[0], hp[1], d.hand_r * 0.62)
            canvas.drawPath(hc, skia.Paint(AntiAlias=True, Color=skcolor(BOARD)))
            self._marker_path(canvas, hc, INK, W * 0.55, ctx, 17 if s == "l" else 19)
        if pose["hand_r_point"] > 0.5:
            a = math.radians(fig.ang["hand_r"])
            hx, hy = P["hand_r"]
            self._marker(
                canvas,
                [(hx, hy), (hx + math.sin(a) * d.hand_r * 2.3, hy + math.cos(a) * d.hand_r * 2.3)],
                INK,
                W * 0.6,
                ctx,
                23,
            )
        # torso: a fat marker stroke in the shirt colour with an ink spine
        self._marker(canvas, [P["hip_c"], P["shoulder_c"]], shirt, W * 1.9, ctx, 29)
        self._marker(canvas, [P["hip_c"], P["shoulder_c"]], INK, W * 0.5, ctx, 31)
        if arch.features.get("accessory") == "cape":
            self._marker(
                canvas,
                [
                    P["shoulder_c"],
                    (
                        P["shoulder_c"][0] - 40 + pose["sway"] * 40,
                        P["shoulder_c"][1] + d.torso_len * 0.9,
                    ),
                ],
                pal.get("accent", "#d7263d"),
                W * 1.1,
                ctx,
                37,
            )
        self._marker(canvas, [P["shoulder_c"], P["chin"]], INK, W, ctx, 41)
        _ = pants
        # head
        hc = P["head_c"]
        r = (d.head_rx + d.head_ry) / 2 * 0.98
        head = skia.Path()
        if arch.features.get("head_shape") == "box":
            head.addRoundRect(
                skia.Rect(
                    hc[0] - d.head_rx, hc[1] - d.head_ry, hc[0] + d.head_rx, hc[1] + d.head_ry
                ),
                24,
                24,
            )
            head.transform(skia.Matrix.RotateDeg(fig.ang["head"], skia.Point(hc[0], hc[1])))
        else:
            head.addCircle(hc[0], hc[1], r)
        canvas.drawPath(head, skia.Paint(AntiAlias=True, Color=skcolor(BOARD)))
        self._marker_path(canvas, head, INK, W * 0.95, ctx, 43)
        self._hair(
            canvas,
            arch.features.get("hair", "short"),
            pal.get("hair", INK),
            Rot2D(hc, fig.ang["head"]),
            d,
            ctx,
        )
        # face, props, fx from the shared IR
        for s in build.shapes:
            tag = s.tag
            if tag in SKIP_TAGS:
                continue
            self._face_or_prop(canvas, s, ctx)

    def _hair(
        self, canvas: skia.Canvas, style: str, color: str, H: Rot2D, d: Any, ctx: StyleContext
    ) -> None:
        if style == "none":
            return
        rx, ry = d.head_rx, d.head_ry
        col = darken(adjust(color, sat=1.1), 0.05)
        w = 9.0

        def arc(a0: float, a1: float, k: float = 1.05, n: int = 8) -> list[Pt]:
            return [
                H.loc(
                    rx * k * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                    ry * k * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
                )
                for i in range(n + 1)
            ]

        if style in ("short", "side_part", "messy"):
            self._marker(canvas, arc(200, 340), col, w, ctx, 47)
            self._marker(
                canvas,
                [H.loc(-rx * 0.5, -ry * 0.72), H.loc(0, -ry * 0.55), H.loc(rx * 0.5, -ry * 0.72)],
                col,
                w * 0.7,
                ctx,
                49,
            )
            if style == "messy":
                for a in (235, 270, 305):
                    p = H.loc(
                        rx * 1.0 * math.cos(math.radians(a)), ry * 1.0 * math.sin(math.radians(a))
                    )
                    q = H.loc(
                        rx * 1.35 * math.cos(math.radians(a + 8)),
                        ry * 1.35 * math.sin(math.radians(a + 8)),
                    )
                    self._marker(canvas, [p, q], col, w * 0.7, ctx, 51 + a)
        elif style == "spiky":
            pts: list[Pt] = []
            for i in range(7):
                ang = 200 + 140 * i / 6
                pts.append(
                    H.loc(
                        rx * 1.04 * math.cos(math.radians(ang)),
                        ry * 1.04 * math.sin(math.radians(ang)),
                    )
                )
                a2 = ang + 10
                pts.append(
                    H.loc(
                        rx * 1.45 * math.cos(math.radians(a2)),
                        ry * 1.45 * math.sin(math.radians(a2)),
                    )
                )
            self._marker(canvas, pts, col, w * 0.9, ctx, 53)
        elif style == "long":
            self._marker(canvas, arc(200, 340), col, w, ctx, 55)
            for sx in (-1, 1):
                self._marker(
                    canvas,
                    [
                        H.loc(sx * rx * 1.05, -ry * 0.3),
                        H.loc(sx * rx * 1.2, ry * 0.7),
                        H.loc(sx * rx * 0.95, ry * 1.5),
                    ],
                    col,
                    w * 0.9,
                    ctx,
                    57 + sx,
                )
        elif style == "balding":
            for sx in (-1, 1):
                self._marker(
                    canvas,
                    [
                        H.loc(sx * rx * 1.0, -ry * 0.4),
                        H.loc(sx * rx * 1.12, -ry * 0.05),
                        H.loc(sx * rx * 0.98, ry * 0.2),
                    ],
                    "#9aa3b2",
                    w * 0.8,
                    ctx,
                    59 + sx,
                )

    def _face_or_prop(self, canvas: skia.Canvas, s: Shape, ctx: StyleContext) -> None:
        path = shape_path(s.geom)
        if path is None:
            return
        canvas.save()
        if s.xf is not None:
            _dx, _dy, rot, _sx, _sy, ox, oy = s.xf
            if rot:
                canvas.rotate(rot, ox, oy)
        tag = s.tag
        b = path.getBounds()
        seed = _h(b.centerX(), b.centerY(), ctx.tick)
        if tag in ("eye",):
            canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=skcolor("#ffffff")))
            self._stroke(canvas, path, INK, 4.5, seed, wobble=0.8)
        elif tag in ("pupil",):
            canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=skcolor(INK)))
        elif tag in ("lid", "brow", "mouth", "mustache", "glasses", "visor") or isinstance(
            s.geom, (Line, PathG)
        ):
            col = self.color(s.stroke, s, ctx) if s.stroke else INK
            col = INK if tag in ("brow", "lid", "mouth", "glasses") else darken(col, 0.1)
            if s.fill is not None and tag == "mouth":
                canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=skcolor("#7a1f2b", 0.9)))
            self._stroke(
                canvas, path, col, max(4.0, min(s.sw or 6.0, 8.5)), seed, alpha=s.alpha, wobble=0.9
            )
        elif tag in FACE_FILL:
            canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=skcolor("#7cc8f0", s.alpha)))
        elif tag in ("thought",):
            canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=skcolor("#ffffff", s.alpha)))
            self._stroke(canvas, path, INK, 4.0, seed, alpha=s.alpha, wobble=1.0)
        elif tag in ("exclaim",):
            canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=skcolor("#e63946", s.alpha)))
            self._stroke(canvas, path, INK, 4.0, seed, alpha=s.alpha)
        elif tag.startswith("prop") or tag == "prop":
            if s.fill is not None:
                canvas.drawPath(
                    path,
                    skia.Paint(
                        AntiAlias=True,
                        Color=skcolor(mix(BOARD, self.color(s.fill, s, ctx), 0.35), s.alpha),
                    ),
                )
            self._stroke(canvas, path, INK, 6.0, seed, alpha=s.alpha, wobble=1.4)
        canvas.restore()

    def draw_shadow(self, canvas: skia.Canvas, sh: Any, ctx: StyleContext) -> None:
        """A quick marker scribble under the feet instead of a soft shadow."""
        p = skia.Path()
        p.moveTo(sh.cx - sh.rx * 0.8, 3)
        p.lineTo(sh.cx + sh.rx * 0.8, -2)
        self._stroke(canvas, p, "#b8bdc9", 7.0, ctx.tick * 7 + 3, alpha=0.8, wobble=1.2)
