"""Keeping a cast in the frame: pictures come in every width, the engine's bodies in one.

A person stands on a slot and is about as wide as the gap between two slots, so a planner can put three of them in a row.
A cow is two and a half people wide: the same row would hide the dog behind it.  :func:`fit_scene` makes room for a scene
whose pictures would stand on one another: the outer characters move to the far slots, and if that is not enough the whole
scene is scaled down together (so a farmer stays taller than his dog), but never below half of full size (or below what the
plan gave, if that was smaller).  A picture on its own is kept narrower than the frame.  It is a plain function of the spec dict, so the offline planner, a
model's plan and the swap of a stand-in for a new asset all share it.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from itertools import pairwise
from math import floor, isfinite
from typing import Any

from reel.assets.art import ArtError, load_art
from reel.core.catalog import Catalog
from reel.core.planner import CHAR_UNIT, UNIVERSAL_SLOT_X

FRAME_W = 1080.0
#: half of an engine body's width at scale 1, in frame px (a body is about 250 design px wide)
BODY_HALF_WIDTH = 0.5 * 250.0 * CHAR_UNIT
#: neighbours' outlines may overlap by this much of the distance between their feet (legs, tails and arms are loose edges)
OVERLAP_ALLOWED = 1.15
#: a crowded scene is never scaled below this (or below the scale the plan gave, if that was smaller)
MIN_SCALE = 0.5
#: below this share the outer characters are moved apart before anything is scaled
WIDEN_BELOW = 0.85
#: no picture is wider than this share of the frame
MAX_FRAME_SHARE = 0.85
_FAR = {"left": "far_left", "right": "far_right"}


def picture_width(archetype: str, catalog: Catalog) -> float | None:
    """The width in frame px at scale 1 of a character that is a picture (``None`` for the engine's bodies)."""
    if archetype not in catalog.archetypes:
        return None
    arch = catalog.archetypes.get(archetype)
    if getattr(arch, "category", "") != "sprite":
        return None
    asset = getattr(arch, "features", {}).get("asset")
    if asset is None:
        return None
    try:
        width = float(load_art(asset).width) * CHAR_UNIT
    except ArtError:
        return None
    return width if isfinite(width) and width > 0 else None


def _slot_x(position: Any) -> float | None:
    if isinstance(position, str):
        return UNIVERSAL_SLOT_X.get(position)
    if isinstance(position, Sequence) and position and isinstance(position[0], (int, float)):
        return float(position[0])
    return None


def _far(position: Any) -> str | None:
    """The far slot an outer ``left`` / ``right`` slot can move to (a point ``[x, y]`` or any other slot has none)."""
    return _FAR.get(position) if isinstance(position, str) else None


def _scale_of(layer: Mapping[str, Any]) -> float:
    s = layer.get("scale", 1.0)
    return float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0 else 1.0


def _share(spots: list[tuple[float, float]]) -> float:
    """The share of its size a row of ``(x as a frame fraction, half width in px)`` must keep so that no neighbours stand
    on one another and nobody leaves the frame (1: it fits as it is)."""
    spots = sorted(spots)
    share = 1.0
    for (xa, ha), (xb, hb) in pairwise(spots):
        room = OVERLAP_ALLOWED * (xb - xa) * FRAME_W
        if ha + hb > room * (1 + 1e-9):
            share = min(share, room / (ha + hb))
    for x, half in spots:
        edge = min(x, 1.0 - x) * FRAME_W  # a picture's box stays inside the frame
        if 0.0 < x < 1.0 and half > edge * (1 + 1e-9):
            share = min(share, edge / half)
    return share


def fit_scene(
    scene: MutableMapping[str, Any], archetypes: Mapping[str, str], catalog: Catalog
) -> list[str]:
    """Make room in one scene whose pictures would stand on one another; returns the ids of the characters it changed.

    Only a layer's ``scale`` (and, to spread the outer two, a ``left`` / ``right`` position) is written.  A scene of
    engine bodies is never touched, and a scene that has been fitted fits again as it is (a second call changes nothing).
    """
    layers = [ly for ly in scene.get("layers", []) or [] if isinstance(ly, MutableMapping)]
    widths = [picture_width(archetypes.get(str(ly.get("character")), ""), catalog) for ly in layers]
    if not any(w is not None for w in widths):
        return []
    caps = [
        MAX_FRAME_SHARE * FRAME_W / w if w else float("inf") for w in widths
    ]  # a picture's own limit
    changed: list[str] = []
    # narrowing one layer can leave the floor of another in the way: settle in a few sweeps
    for sweep in range(4):
        placed: list[tuple[MutableMapping[str, Any], float, float]] = []  # (layer, x, half width)
        for ly, w, cap in zip(layers, widths, caps, strict=True):
            x = _slot_x(ly.get("position"))
            if x is not None:  # a slot only the background knows has no x here: it keeps its size
                half = (w / 2.0 if w else BODY_HALF_WIDTH) * min(_scale_of(ly), cap)
                placed.append((ly, x, half))
        share = _share([(x, half) for _ly, x, half in placed])
        if sweep == 0 and share < WIDEN_BELOW and len(placed) >= 2:
            order = sorted(range(len(placed)), key=lambda i: placed[i][1])
            outer = {
                i: far
                for i in (order[0], order[-1])
                if (far := _far(placed[i][0].get("position"))) is not None
            }
            spread = [
                (UNIVERSAL_SLOT_X[outer[i]] if i in outer else x, half)
                for i, (_ly, x, half) in enumerate(placed)
            ]
            if (
                outer and (wider := _share(spread)) > share + 1e-9
            ):  # the wider stage is kept only if it gives more room
                share = wider
                for i, far in outer.items():
                    placed[i][0]["position"] = far
        moved = False
        for ly, cap in zip(layers, caps, strict=True):
            now = _scale_of(ly)
            fitted = min(now, cap)  # a picture is never wider than the frame allows
            new = min(
                fitted, max(fitted * share, min(now, MIN_SCALE))
            )  # nor smaller than the floor
            if new < now - 1e-3:
                ly["scale"] = (
                    floor(new * 1000) / 1000
                )  # rounded down: rounding up could push an edge out again
                moved = True
                if str(ly.get("character")) not in changed:
                    changed.append(str(ly.get("character")))
        if not moved:
            break
    return changed


def fit_spec(spec: MutableMapping[str, Any], catalog: Catalog) -> list[tuple[str, str]]:
    """:func:`fit_scene` for every scene of a spec; returns ``(scene id, character id)`` for each layer it changed."""
    chars = {
        str(c.get("id")): str(c.get("archetype", ""))
        for c in spec.get("characters", []) or []
        if isinstance(c, Mapping)
    }
    out: list[tuple[str, str]] = []
    for sc in spec.get("scenes", []) or []:
        if isinstance(sc, MutableMapping):
            out += [(str(sc.get("id")), cid) for cid in fit_scene(sc, chars, catalog)]
    return out


__all__ = ["fit_scene", "fit_spec", "picture_width"]
