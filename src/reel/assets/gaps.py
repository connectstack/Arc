"""Library gaps: what a script needs that the asset library lacked when the spec was written, and the swap that puts it in.

A planner that cannot find a character, place or object in the library uses the closest one (or leaves it out) and records
the gap in ``meta.library_gaps``.  Once the user adds the asset, :func:`apply_library_gap` makes the spec use it: the stand-in
character becomes the new one, the scenes get the new place, the missing object is placed.  All of this works on the plain
spec dict, so the server and the command line share it.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from reel.assets.match import asset_index
from reel.assets.model import AssetDef
from reel.core.catalog import CATALOG, Catalog

#: where a layer standing on a slot is, in frame fractions of x (for keeping a placed object clear of the characters)
_SLOT_X = {"far_left": 0.1, "left": 0.27, "center": 0.5, "right": 0.73, "far_right": 0.9}
_FREE_X = (0.14, 0.86, 0.5, 0.3, 0.7)
_OBJECT_Y = 0.8
FRAME_W, FRAME_H = 1080.0, 1920.0


def resolve_gap(gap: Mapping[str, Any], catalog: Catalog | None = None) -> AssetDef | None:
    """The library asset that now answers a gap (by its name or one of its tags), if there is one of the right kind."""
    cat = catalog or CATALOG
    kind = gap.get("kind")
    name = gap.get("name")
    if kind not in ("character", "object", "place") or not isinstance(name, str):
        return None
    return asset_index(cat, (kind,)).lookup(name)


def _layer_xs(scene: Mapping[str, Any]) -> list[float]:
    xs: list[float] = []
    for ly in scene.get("layers", []) or []:
        pos = ly.get("position") if isinstance(ly, Mapping) else None
        if isinstance(pos, list) and pos and isinstance(pos[0], (int, float)):
            xs.append(float(pos[0]))
        elif isinstance(pos, str) and pos in _SLOT_X:
            xs.append(_SLOT_X[pos])
    for ob in scene.get("objects", []) or []:
        pos = ob.get("position") if isinstance(ob, Mapping) else None
        if isinstance(pos, list) and pos and isinstance(pos[0], (int, float)):
            xs.append(float(pos[0]))
    return xs


def free_spot(scene: Mapping[str, Any]) -> float:
    taken = _layer_xs(scene)
    best = max(_FREE_X, key=lambda x: min((abs(x - t) for t in taken), default=1.0))
    return best


def object_scale(asset: AssetDef) -> float:
    """A scale that keeps a newly placed object a reasonable size (never enlarged)."""
    from reel.assets.art import ArtError, load_art
    from reel.core.planner import CHAR_UNIT

    try:
        art = load_art(asset)
    except ArtError:
        return 1.0
    w, h = max(art.width, 1.0) * CHAR_UNIT, max(art.height, 1.0) * CHAR_UNIT
    return round(max(0.1, min(1.0, 0.42 * FRAME_W / w, 0.5 * FRAME_H / h)), 3)


def _accepted_params(cat: Catalog, template: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """The background params the new template also has (a swap must not leave params it does not know)."""
    model = (
        getattr(cat.backgrounds.get(template), "params_model", None)
        if template in cat.backgrounds
        else None
    )
    fields = set(getattr(model, "model_fields", {}) or {})
    return {k: v for k, v in params.items() if k in fields}


def apply_library_gap(
    spec: MutableMapping[str, Any],
    gap: Mapping[str, Any],
    asset: AssetDef | str,
    catalog: Catalog | None = None,
) -> bool:
    """Make ``spec`` (a spec dict, changed in place) use the library asset that answers ``gap``; True if anything changed.

    Characters: the stand-in character's archetype becomes the asset.  Places: the gap's scenes (or, with none listed, the
    scenes on the stand-in) take the asset as their background, keeping the params it also has.  Objects: the asset is
    placed in each of the gap's scenes on a spot clear of the characters.  The matching ``meta.library_gaps`` entry is
    removed.
    """
    cat = catalog or CATALOG
    name = asset if isinstance(asset, str) else asset.name
    if name not in cat.assets:
        return False
    a = cat.assets.get(name)
    kind = gap.get("kind")
    if a.kind != kind:
        return False
    changed = False
    scenes = [s for s in spec.get("scenes", []) if isinstance(s, MutableMapping)]
    wanted = {str(x) for x in gap.get("scenes", []) or []}
    if kind == "character":
        cid = gap.get("character")
        for ch in spec.get("characters", []) or []:
            if (
                isinstance(ch, MutableMapping)
                and ch.get("id") == cid
                and ch.get("archetype") != name
            ):
                ch["archetype"] = name
                changed = True
                roles = set(
                    a.roles
                )  # a picture is painted by the parts it marks and holds no props
                if isinstance(ch.get("palette"), MutableMapping):
                    ch["palette"] = {k: v for k, v in ch["palette"].items() if k in roles}
                ch["props"] = []
        if changed:  # the picture may be wider than what stood in for it
            from reel.assets.layout import fit_spec

            fit_spec(spec, cat)
    elif kind == "place":
        stand = gap.get("stand_in")
        for sc in scenes:
            bg = sc.get("background")
            if not isinstance(bg, MutableMapping):
                continue
            hit = sc.get("id") in wanted or (not wanted and stand and bg.get("template") == stand)
            if hit and bg.get("template") != name:
                bg["template"] = name
                bg["params"] = _accepted_params(cat, name, bg.get("params") or {})
                changed = True
    elif kind == "object":
        for sc in scenes:
            if sc.get("id") not in wanted:
                continue
            objs = sc.setdefault("objects", [])
            if any(isinstance(o, Mapping) and o.get("asset") == name for o in objs):
                continue
            objs.append(
                {
                    "asset": name,
                    "position": [free_spot(sc), _OBJECT_Y],
                    "scale": object_scale(a),
                }
            )
            changed = True
    meta = spec.get("meta")
    gaps = meta.get("library_gaps") if isinstance(meta, MutableMapping) else None
    if changed and isinstance(gaps, list):
        key = (str(gap.get("kind")), str(gap.get("name", "")).lower())
        left = [
            g
            for g in gaps
            if isinstance(g, Mapping)
            and (str(g.get("kind")), str(g.get("name", "")).lower()) != key
        ]
        if left:
            meta["library_gaps"] = left  # type: ignore[index]
        else:
            meta.pop("library_gaps", None)  # type: ignore[union-attr]
    return changed


__all__ = ["apply_library_gap", "free_spot", "object_scale", "resolve_gap"]
