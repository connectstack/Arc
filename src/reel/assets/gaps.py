"""Library gaps: what a script needs that the asset library lacked when the spec was written, and the swap that puts it in.

A planner that cannot find a character, place or object in the library uses the closest one (or leaves it out) and records
the gap in ``meta.library_gaps``.  Once the user adds the asset, :func:`apply_library_gap` makes the spec use it: the stand-in
character becomes the new one, the scenes get the new place, the missing object is placed.  All of this works on the plain
spec dict, so the server and the command line share it.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from reel.assets.match import asset_index
from reel.assets.model import AssetDef
from reel.core.catalog import CATALOG, Catalog

#: where a layer standing on a slot is, in frame fractions of x (for keeping a placed object clear of the characters)
_SLOT_X = {"far_left": 0.1, "left": 0.27, "center": 0.5, "right": 0.73, "far_right": 0.9}
_FREE_X = (0.14, 0.86, 0.5, 0.3, 0.7)
_OBJECT_Y = 0.8
#: a thing shorter than this share of the frame is "small": it grows with a small cast
_SMALL_OBJECT = 0.2
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


def object_scale(asset: AssetDef, boost: float = 1.0) -> float:
    """A scale that keeps a newly placed object a reasonable size: never enlarged, except that a small thing (under a fifth of
    the frame's height) grows by ``boost`` (a small cast is drawn bigger, and what it uses with it)."""
    from reel.assets.art import ArtError, load_art
    from reel.core.planner import CHAR_UNIT

    try:
        art = load_art(asset)
    except ArtError:
        return 1.0
    w, h = max(art.width, 1.0) * CHAR_UNIT, max(art.height, 1.0) * CHAR_UNIT
    grow = boost if h / FRAME_H < _SMALL_OBJECT else 1.0
    return round(max(0.1, min(max(grow, 1.0), 0.42 * FRAME_W / w, 0.5 * FRAME_H / h)), 3)


def _accepted_params(cat: Catalog, template: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """The background params the new template also has (a swap must not leave params it does not know)."""
    model = (
        getattr(cat.backgrounds.get(template), "params_model", None)
        if template in cat.backgrounds
        else None
    )
    fields = set(getattr(model, "model_fields", {}) or {})
    return {k: v for k, v in params.items() if k in fields}


_ON_SCREEN = 3  # the most characters a scene holds
_SLOTS = ("left", "center", "right")


def _join_scenes(
    spec: MutableMapping[str, Any],
    scene_list: list[MutableMapping[str, Any]],
    chars: list[MutableMapping[str, Any]],
    asset: AssetDef,
    word: str,
    targets: set[str],
) -> set[str]:
    """Put the character the library has now into the scenes in ``targets`` that have room (up to three on screen), on a free slot, standing
    still.  The character is added to the reel once (or the one already drawn as this asset is used).  Returns the scenes it joined."""
    from reel.assets.model import slug

    who = next((c for c in chars if c.get("archetype") == asset.name), None)
    joined: set[str] = set()
    for sc in scene_list:
        if sc.get("id") not in targets:
            continue
        layers = sc.setdefault("layers", [])
        if who is not None and any(
            isinstance(ly, Mapping) and ly.get("character") == who.get("id") for ly in layers
        ):
            joined.add(str(sc.get("id")))  # already there
            continue
        if len(layers) >= _ON_SCREEN:
            continue
        if who is None:
            ids = {str(c.get("id")) for c in chars}
            base = slug(word) or asset.name
            cid, n = base, 2
            while cid in ids:
                cid, n = f"{base}_{n}", n + 1
            who = {
                "id": cid,
                "archetype": asset.name,
                "palette": {},
                "name": word.strip().capitalize(),
                "props": [],
            }
            spec.setdefault("characters", []).append(who)
            chars.append(who)
        xs = _layer_xs(sc)  # the slot that is farthest from everyone already there
        slot = max(_SLOTS, key=lambda p: min((abs(_SLOT_X[p] - x) for x in xs), default=1.0))
        dur = float(sc.get("duration_sec") or 5)
        layers.append(
            {
                "character": who["id"],
                "position": slot,
                "actions": [
                    {"name": "idle", "t0": 0.2, "t1": round(max(0.5, min(dur - 0.2, 2.2)), 2)}
                ],
            }
        )
        joined.add(str(sc.get("id")))
    return joined


def apply_library_gap(
    spec: MutableMapping[str, Any],
    gap: Mapping[str, Any],
    asset: AssetDef | str,
    catalog: Catalog | None = None,
    scenes: Sequence[str] | None = None,
) -> bool:
    """Make ``spec`` (a spec dict, changed in place) use the library asset that answers ``gap``; True if anything changed.

    Characters: the stand-in character's archetype becomes the asset (a character is the same in every scene, so ``scenes``
    does not narrow it); with no stand-in, the asset joins the scenes asked for (the gap's, or ``scenes``) as a character of its own.  Places: the gap's scenes (or, with none listed, the scenes on the stand-in) take the asset as their
    background, keeping the params it also has.  Objects: the asset is placed in each of the gap's scenes on a spot clear of the
    characters.  With ``scenes`` (scene ids) only those scenes are changed, whatever the gap lists, and the gap stays in
    ``meta.library_gaps`` for the scenes that are left; without it the whole gap is answered and its entry removed.
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
    stood_in = False  # a character that something else was standing in for is changed everywhere
    answered: set[str] = set()  # scenes that show the asset now, placed or already there
    scene_list = [s for s in spec.get("scenes", []) if isinstance(s, MutableMapping)]
    wanted = {str(x) for x in gap.get("scenes", []) or []}
    scope = {str(x) for x in scenes} if scenes is not None else None
    if kind == "character":
        cid = gap.get("character")
        chars = [c for c in spec.get("characters", []) or [] if isinstance(c, MutableMapping)]
        stood_in = any(ch.get("id") == cid for ch in chars)
        for ch in chars:
            if ch.get("id") == cid and ch.get("archetype") != name:
                ch["archetype"] = name
                changed = True
                roles = set(
                    a.roles
                )  # a picture is painted by the parts it marks and holds no props
                if isinstance(ch.get("palette"), MutableMapping):
                    ch["palette"] = {k: v for k, v in ch["palette"].items() if k in roles}
                ch["props"] = []
        if not any(ch.get("id") == cid for ch in chars):
            # nobody stood in for it: it joins the scenes asked for, as a character of its own
            joined = _join_scenes(
                spec,
                scene_list,
                chars,
                a,
                str(gap.get("name") or name),
                scope if scope is not None else wanted,
            )
            answered |= joined
            changed = changed or bool(joined)
        if changed:  # the picture may be wider than what stood in for it
            from reel.assets.layout import fit_spec

            fit_spec(spec, cat)
    elif kind == "place":
        stand = gap.get("stand_in")
        for sc in scene_list:
            bg = sc.get("background")
            if not isinstance(bg, MutableMapping):
                continue
            if scope is not None:
                hit = sc.get("id") in scope
            else:
                hit = sc.get("id") in wanted or bool(
                    not wanted and stand and bg.get("template") == stand
                )
            if not hit:
                continue
            answered.add(str(sc.get("id")))
            if bg.get("template") != name:
                bg["template"] = name
                bg["params"] = _accepted_params(cat, name, bg.get("params") or {})
                changed = True
    elif kind == "object":
        targets = scope if scope is not None else wanted
        for sc in scene_list:
            if sc.get("id") not in targets:
                continue
            objs = sc.setdefault("objects", [])
            answered.add(str(sc.get("id")))
            if any(isinstance(o, Mapping) and o.get("asset") == name for o in objs):
                continue  # somebody placed it already: nothing to add, and the gap is answered
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
    if (changed or answered) and isinstance(gaps, list):
        key = (str(gap.get("kind")), str(gap.get("name", "")).lower())
        left: list[Any] = []
        for g in gaps:
            if not isinstance(g, Mapping):
                continue
            if (str(g.get("kind")), str(g.get("name", "")).lower()) != key:
                left.append(g)
            elif scope is not None and not stood_in:
                rest = [s for s in g.get("scenes", []) or [] if str(s) not in scope]
                if rest:
                    left.append({**g, "scenes": rest})
        if left:
            meta["library_gaps"] = left  # type: ignore[index]
        else:
            meta.pop("library_gaps", None)  # type: ignore[union-attr]
        return True
    return changed


__all__ = ["apply_library_gap", "free_spot", "object_scale", "resolve_gap"]
