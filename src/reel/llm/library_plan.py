"""The asset library in the offline planner: places named in a scene, creatures and roles as characters, objects on the set.

The offline planner reads a script with rules, no model.  These rules add what the library can draw, by the words its assets
answer to (their tags): a scene that says "the village" gets the village place, "the dog" becomes a dog character, "a car
drove past" puts the car on the set and drives it across.  The same words come from the library whatever the user added.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from reel.assets.coverage import analyze_script
from reel.assets.gaps import free_spot, object_scale
from reel.assets.match import Hit, TagIndex, asset_index, phrase_key, tokens
from reel.assets.model import AssetDef
from reel.core.catalog import Catalog

MAX_OBJECTS_PER_SCENE = 2
#: a place named in the scene outweighs the engine's own keyword tables
PLACE_NAMED_SCORE = 3.0
_TRAVEL_VERBS = frozenset(
    {
        *("drive", "drives", "drove", "driven", "driving", "ride", "rides", "rode", "riding"),
        *("fly", "flies", "flew", "flown", "flying", "sail", "sails", "sailed", "sailing"),
        *("race", "races", "raced", "racing", "zoom", "zooms", "zoomed", "speed", "speeds", "sped"),
        *("rush", "rushes", "rushed", "roll", "rolls", "rolled"),
    }
)
#: objects that travel when the text says something travels
_VEHICLE_TAGS = frozenset({"vehicle", "vehicles", "transport"})
_FLOATERS_MAX_ANCHOR_Y = (
    0.6  # an anchor above this height in the art means it floats (a balloon, the sun)
)
_GROUND_Y = 0.8
_SKY_Y = 0.3


def _inside(inner: tuple[int, int], outer: tuple[int, int]) -> bool:
    """Is the span ``inner`` of a text within the span ``outer``?"""
    return outer[0] <= inner[0] and inner[1] <= outer[1]


class LibraryPlanner:
    """Looks up the library's characters, places and objects by word for one planning run."""

    def __init__(self, catalog: Catalog) -> None:
        self.cat = catalog
        self.characters: TagIndex[AssetDef] = asset_index(catalog, ("character",))
        self.places: TagIndex[AssetDef] = asset_index(catalog, ("place",))
        self.objects: TagIndex[AssetDef] = asset_index(catalog, ("object",))

    # -- characters -------------------------------------------------------------------------
    def character_words(self) -> dict[str, str]:
        """``{word: archetype}`` for every single word a character asset answers to (the cast builder's role nouns)."""
        return {w: a.name for w, a in self.characters.single_words().items()}

    def foreign_character_hits(self, text: str) -> list[Hit[AssetDef]]:
        """Characters ``text`` names in words other than English ("किसान", "गाय"), in order.

        The cast builder finds English role nouns after an article ("the dog", "a farmer"); other scripts have no such
        marker, so their words are read straight from the tags.  English words are left to the rules that already read them."""
        return [h for h in self.characters.scan(text) if not h.phrase.isascii()]

    def sprite_roles(self, archetype: str) -> tuple[str, ...] | None:
        """The palette roles of a library character (``None`` for the engine's own bodies)."""
        if archetype in self.cat.assets:
            a = self.cat.assets.get(archetype)
            if a.kind == "character":
                return a.roles
        return None

    # -- places -----------------------------------------------------------------------------
    def place_scores(self, text: str) -> dict[str, float]:
        """Library places the scene's text names, by background name (a place named twice scores higher).

        A place word inside the name of a longer object ("school" in "school building") does not name the place."""
        out: dict[str, float] = {}
        things = [(h.start, h.end) for h in self.objects.scan(text)]
        for h in self.places.scan(text):
            if any(_inside((h.start, h.end), t) and t[1] - t[0] > h.end - h.start for t in things):
                continue
            out[h.asset.name] = out.get(h.asset.name, 0.0) + PLACE_NAMED_SCORE
        return out

    # -- objects ----------------------------------------------------------------------------
    def scene_objects(
        self,
        units: Sequence[tuple[float, float, str]],
        layers: Sequence[Mapping[str, Any]],
        duration: float,
        taken: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """The library objects a scene's lines name, as spec dicts: ``(t0, t1, text)`` per line, in order.

        At most :data:`MAX_OBJECTS_PER_SCENE`; each stands on a spot clear of the characters.  A vehicle in a line that says
        something drives, flies or sails crosses the frame; a thing that floats (a balloon, the sun) hangs high and drifts.
        """
        found: list[tuple[AssetDef, float, float, str]] = []
        seen: set[str] = set(taken or ())
        for s, e, text in units:
            sets = [(h.start, h.end) for h in self.places.scan(text)]
            for h in self.objects.scan(text):
                a = h.asset
                if any(_inside((h.start, h.end), p) for p in sets):
                    continue  # a word that names the set ("school", "hospital") is not also a thing standing on it
                if h.alternatives and h.phrase.lower().replace(" ", "_") != a.name:
                    continue  # a word several things answer to ("fruit", "vehicle"): do not guess which
                if a.name in seen:
                    continue
                seen.add(a.name)
                found.append((a, s, e, text))
                if len(found) >= MAX_OBJECTS_PER_SCENE:
                    break
            if len(found) >= MAX_OBJECTS_PER_SCENE:
                break
        scene = {"layers": list(layers), "objects": []}
        out: list[dict[str, Any]] = []
        for a, s, e, text in found:
            floats = a.anchor[1] <= _FLOATERS_MAX_ANCHOR_Y
            x = free_spot(scene)
            y = _SKY_Y if floats else _GROUND_Y
            ob: dict[str, Any] = {
                "asset": a.name,
                "position": [x, y],
                "scale": object_scale(a),
            }
            motions: list[dict[str, Any]] = []
            words = set(text.lower().replace(",", " ").replace(".", " ").split())
            if (set(a.tags) & _VEHICLE_TAGS) and words & _TRAVEL_VERBS:
                going_left = x >= 0.5
                t0 = round(max(0.2, s - 0.2), 2)
                t1 = round(min(duration - 0.1, max(t0 + 1.4, e + 0.8)), 2)
                if t1 - t0 >= 0.6:
                    motions.append(
                        {"type": "move", "t0": t0, "t1": t1, "to": [-0.3 if going_left else 1.3, y]}
                    )
                    if going_left:
                        ob["facing"] = "left"
            elif floats:
                motions.append({"type": "float", "t0": 0.0, "t1": round(duration, 2)})
            if motions:
                ob["motions"] = motions
            scene["objects"].append(ob)
            out.append(ob)
        return out


def _phrases_of(text: str) -> set[str]:
    """Every run of one to four whole words of ``text`` (so "bag" is found in "a bag" but not in "baggage")."""
    words = [t for t, _s, _e in tokens(text)]
    return {
        " ".join(words[i : i + n])
        for i in range(len(words))
        for n in range(1, 5)
        if i + n <= len(words)
    }


def scene_texts(spec: Mapping[str, Any]) -> dict[str, str]:
    """What each scene's captions say, by scene id (where a script's words ended up)."""
    out: dict[str, str] = {}
    for sc in spec.get("scenes", []) or []:
        if not isinstance(sc, Mapping):
            continue
        caps = [
            c["text"]
            for c in sc.get("captions", []) or []
            if isinstance(c, Mapping) and isinstance(c.get("text"), str)
        ]
        out[str(sc.get("id", ""))] = " ".join(caps)
    return out


def attach_library_gaps(
    spec: dict[str, Any], script: str, catalog: Catalog, notes: list[str] | None = None
) -> bool:
    """Record in ``meta.library_gaps`` what the script mentions that no asset draws (and the engine does not either).

    Whatever a planner already reported stays; a thing is added only when no entry names it.  Returns True if the spec changed.
    """
    meta = spec.get("meta")
    if not isinstance(meta, dict):
        return False
    cov = analyze_script(script, catalog)
    if not cov.missing:
        return False
    have = [g for g in meta.get("library_gaps", []) or [] if isinstance(g, dict)]
    texts = scene_texts(spec)
    added: list[dict[str, Any]] = []
    for m in cov.missing:
        words = {w.lower() for w in m.words} | {m.name.replace("_", " ").lower()}
        if any(
            g.get("kind") == m.kind and str(g.get("name", "")).strip().lower() in words
            for g in [*have, *added]
        ):
            continue  # already reported
        keys = {" ".join(phrase_key(w)) for w in words if phrase_key(w)}
        scenes = [sid for sid, t in texts.items() if keys & _phrases_of(t)]
        added.append({"kind": m.kind, "name": m.name.replace("_", " "), "scenes": scenes})
    if not added:
        return False
    meta["library_gaps"] = [*have, *added][:24]
    if notes is not None:
        notes.append(
            "not in the asset library: "
            + ", ".join(f"{g['name']} ({g['kind']})" for g in added[:6])
            + (" ..." if len(added) > 6 else "")
        )
    return True


__all__ = ["LibraryPlanner", "attach_library_gaps", "scene_texts"]
