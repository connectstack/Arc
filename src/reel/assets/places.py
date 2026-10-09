"""Places made from art: a beach, a village, a classroom.

A place asset registers as a background template, so it is used exactly like the built-in sets:
``"background": {"template": "beach", "params": {"time_of_day": "dusk"}}``.  A drawing or picture is scaled to cover the
frame with a margin for camera moves; what lies beyond it is a soft gradient of its own top and bottom edge colours.  A
layered SVG (groups marked ``data-plane="far" | "back" | "near" ...``) gets real parallax; anything else sits on the far plane.

The place's own facts (where feet stand, named spots, how much characters grow towards the camera) come from its sidecar.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import skia

from reel.assets.art import load_art, recolor, split_role
from reel.assets.model import Art, AssetDef, PlaceSpec
from reel.assets.raster import image_for
from reel.core.geometry import hex_to_rgba, rgb_to_hex
from reel.core.ir import ImageG, bbox
from reel.styles.base import shape_path
from reel.templates.base import X0, X1, Y0, Y1, BackgroundDef, BgParams, BuildContext

FRAME_W, FRAME_H = 1080.0, 1920.0
BLEED = 1.22  # the art covers the frame plus a margin, so a camera drift or a push-in never shows its edge
DEFAULT_PLANE = "far"


def _sample_edges(art: Art) -> tuple[str, str]:
    """The average colour of the top and of the bottom edge of the art (for the gradient that extends it)."""
    if art.fmt == "raster":
        img = image_for(next(s.geom.key for s in art.shapes if isinstance(s.geom, ImageG)))
        arr = np.asarray(img.toarray(colorType=skia.kRGBA_8888_ColorType))
        band = max(2, arr.shape[0] // 40)

        def mean(rows: np.ndarray) -> str:
            px = rows.reshape(-1, 4).astype(np.float32)
            w = px[:, 3:4] / 255.0
            rgb = (px[:, :3] * w).sum(0) / max(float(w.sum()), 1.0)
            return rgb_to_hex(*[float(c) for c in rgb])

        return mean(arr[:band]), mean(arr[-band:])
    x0, y0, x1, y1 = art.bounds
    w = 40
    h = max(8, round(w * (y1 - y0) / max(x1 - x0, 1e-6)))
    surf = skia.Surface(w, h)
    cv = surf.getCanvas()
    cv.clear(skia.ColorTRANSPARENT)
    cv.scale(w / (x1 - x0), h / (y1 - y0))
    cv.translate(-x0, -y0)
    for s in art.shapes:
        if s.fill is None:
            continue
        col = s.fill if s.fill.startswith("#") else (split_role(s.fill)[1] or "#9aa3ad")
        r, g, b, _ = hex_to_rgba(col)
        path = shape_path(s.geom)
        if path is not None:
            cv.drawPath(
                path, skia.Paint(AntiAlias=True, Color=skia.Color(r, g, b, round(255 * s.alpha)))
            )
    arr = np.asarray(surf.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)).astype(
        np.float32
    )

    def mean_drawn(rows: np.ndarray) -> str:
        w_ = rows[..., 3:4] / 255.0
        if float(w_.sum()) < 1.0:
            return "#9ec9e8"  # nothing is drawn there: a sky-ish default
        rgb = (rows[..., :3] * w_).sum((0, 1)) / float(w_.sum())
        return rgb_to_hex(*[float(c) for c in rgb])

    return mean_drawn(arr[:2]), mean_drawn(arr[-2:])


def place_background(asset: AssetDef) -> BackgroundDef:
    """The background template for a place asset."""
    place = asset.place or PlaceSpec()
    slots = {name: (float(v[0]), float(v[1])) for name, v in place.slots.items()}

    def build(ctx: BuildContext, params: Any) -> None:
        art = load_art(asset)
        x0, y0, x1, y1 = art.bounds
        aw, ah = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        s = max(FRAME_W * BLEED / aw, FRAME_H * BLEED / ah)
        xf = (FRAME_W / 2 - s * (x0 + x1) / 2, FRAME_H / 2 - s * (y0 + y1) / 2, 0.0, s, s, 0.0, 0.0)
        top, bottom = _sample_edges(art)
        ctx.rect(
            "sky",
            X0,
            Y0,
            X1 - X0,
            Y1 - Y0,
            top,
            gradient=(top, bottom, 90.0),
            material="emissive",
            shadow=False,
            tag="sky",
        )
        groups = art.planes or {DEFAULT_PLANE: art.shapes}
        if art.planes:  # shapes the author left outside any plane go to the far plane
            loose = [
                sh
                for sh in art.shapes
                if not any(sh is p for pl in art.planes.values() for p in pl)
            ]
            if loose:
                groups = {
                    DEFAULT_PLANE: loose + groups.get(DEFAULT_PLANE, []),
                    **{k: v for k, v in groups.items() if k != DEFAULT_PLANE},
                }
        for plane, shapes in groups.items():
            target = ctx.graph.planes[plane]
            for sh in recolor(
                shapes, art.roles
            ):  # a place takes no palette: roles show the drawing's own colours
                bx0, by0, bx1, by1 = bbox(sh.geom)
                backdrop = (bx1 - bx0) * (by1 - by0) > 0.6 * aw * ah
                tag = sh.tag or ("backdrop" if backdrop else "")
                target.append(
                    replace(
                        sh, xf=xf, z=float(len(target)), shadow=sh.shadow and not backdrop, tag=tag
                    )
                )

    return BackgroundDef(
        asset.name,
        build,
        BgParams,
        asset.summary,
        slots,
        tags=asset.tags,
        ground_y=place.ground_y,
        perspective=place.perspective,
        horizon=place.horizon,
    )


__all__ = ["BLEED", "place_background"]
