"""Pictures of assets: one small scene per asset, in any style, for the CLI's contact sheet and the app's library thumbnails.

An object is shown on a plain set, sized to fill the frame sensibly; a character stands and idles; a place is shown with a
person in it for scale.  These are real renders through the same engine, so what the thumbnail shows is what a scene gets.

``true_scale`` shows an object or character at the size a scene gives it (scale 1) next to a person, so a house that is too
small or a phone that is too big is obvious.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from reel.assets.art import load_art
from reel.assets.model import AssetDef
from reel.core.catalog import CATALOG, Catalog
from reel.core.planner import CHAR_UNIT

FRAME_W, FRAME_H = 1080.0, 1920.0
FPS = 30
PERSON_W = 230.0  # a standing person's width on screen at scale 1, arms included
GAP = 50.0
ROW_Y = 0.78


def _side_by_side(art_w: float) -> tuple[float, float, float]:
    """(person x, thing x, scale) for a person and a thing at the same scale, centred and shrunk together to fit."""
    thing = art_w * CHAR_UNIT
    total = PERSON_W + GAP + thing
    k = min(1.0, 0.94 * FRAME_W / total)
    left = (FRAME_W - total * k) / 2
    person_x = (left + PERSON_W * k / 2) / FRAME_W
    thing_x = (left + (PERSON_W + GAP) * k + thing * k / 2) / FRAME_W
    return round(person_x, 4), round(thing_x, 4), round(k, 4)


def preview_spec(
    asset: AssetDef,
    style: str = "flat_vector",
    *,
    time_of_day: str = "day",
    true_scale: bool = False,
) -> dict[str, Any]:
    """A one-scene spec that shows ``asset`` (a plain dict: validate it with ``ReelSpec`` before rendering)."""
    art = load_art(asset)
    w, h = max(art.width, 1.0), max(art.height, 1.0)
    base: dict[str, Any] = {
        "version": "1.0",
        "meta": {
            "title": asset.name,
            "style": style,
            "fps": FPS,
            "seed": 7,
            "target_duration_sec": 50,
        },
        "characters": [],
        "scenes": [],
    }
    scene: dict[str, Any] = {
        "id": "preview",
        "duration_sec": 4.0,
        "background": {"template": "abstract"},
        "layers": [],
        "captions": [],
    }
    if true_scale and asset.kind in ("object", "character"):
        px, tx, k = _side_by_side(w)
        base["characters"] = [{"id": "person", "archetype": "everyman"}]
        scene["layers"] = [
            {
                "character": "person",
                "position": [px, ROW_Y],
                "scale": k,
                "actions": [{"name": "idle", "t0": 0, "t1": 4}],
            }
        ]
        if asset.kind == "object":
            scene["objects"] = [{"asset": asset.name, "position": [tx, ROW_Y], "scale": k}]
        else:
            base["characters"].append({"id": "star", "archetype": asset.name})
            scene["layers"].append(
                {
                    "character": "star",
                    "position": [tx, ROW_Y],
                    "scale": k,
                    "actions": [{"name": "idle", "t0": 0, "t1": 4}],
                }
            )
    elif asset.kind == "object":
        fit = min(0.46 * FRAME_H / (h * CHAR_UNIT), 0.78 * FRAME_W / (w * CHAR_UNIT), 6.0)
        scene["objects"] = [{"asset": asset.name, "position": [0.5, 0.74], "scale": round(fit, 3)}]
    elif asset.kind == "character":
        fit = min(0.5 * FRAME_H / (h * CHAR_UNIT), 0.8 * FRAME_W / (w * CHAR_UNIT), 4.0)
        base["characters"] = [{"id": "star", "archetype": asset.name}]
        scene["layers"] = [
            {
                "character": "star",
                "position": [0.5, 0.76],
                "scale": round(fit, 3),
                "actions": [{"name": "idle", "t0": 0, "t1": 4}],
            }
        ]
    else:
        scene["background"] = {"template": asset.name, "params": {"time_of_day": time_of_day}}
        place = asset.place
        ground = place.ground_y if place else 0.8
        base["characters"] = [{"id": "guest", "archetype": "everyman"}]
        scene["layers"] = [
            {
                "character": "guest",
                "position": [0.5, round(ground + 0.02, 3)],
                "scale": 0.8,
                "actions": [{"name": "idle", "t0": 0, "t1": 4}],
            }
        ]
    base["scenes"] = [scene]
    return base


def render_preview(
    asset: AssetDef,
    style: str = "flat_vector",
    *,
    scale: float = 0.25,
    catalog: Catalog | None = None,
    time_of_day: str = "day",
    true_scale: bool = False,
) -> np.ndarray:
    """The preview frame of an asset as a BGRA array (``scale`` of 1080x1920)."""
    from reel.core.render import Renderer, RenderOptions
    from reel.core.spec import ReelSpec

    cat = catalog or CATALOG
    spec = ReelSpec.model_validate(
        preview_spec(asset, style, time_of_day=time_of_day, true_scale=true_scale)
    )
    r = Renderer(spec, RenderOptions(scale=scale, cache=False, lenient=True), catalog=cat)
    return r.frame(int(0.5 * FPS))
