"""The asset library in the offline planner: places named in a scene, creatures and roles as characters, objects on the set.

The offline planner reads a script with rules, no model.  These rules add what the library can draw, by the words its assets
answer to (their tags): a scene that says "the village" gets the village place, "the dog" becomes a dog character, "a car
drove past" puts the car on the set and drives it across.  The same words come from the library whatever the user added.
"""

from __future__ import annotations

from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from reel.assets.coverage import analyze_script
from reel.assets.gaps import free_spot, object_scale
from reel.assets.match import Hit, TagIndex, asset_index, phrase_key, tokens
from reel.assets.model import AssetDef
from reel.core.catalog import Catalog

MAX_OBJECTS_PER_SCENE = 2
#: Hinglish words of characters that are also English names or words: not read as the character
_AMBIGUOUS_HINGLISH = frozenset({"seth", "lala", "madam", "bagh", "gai"})
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


@dataclass(frozen=True)
class _EnginePlace:
    """One of the engine's own sets, with the words (English phrases, Hindi, Hinglish) a script calls it."""

    name: str
    tags: tuple[str, ...]
    kind: str = "place"
    origin: str = "builtin"


#: what a script in Hindi or Hinglish calls the engine's own sets (the library's places carry their words in their tags; these
#: have no asset to hold them).  English single words are in the planner's own keyword tables.
ENGINE_PLACE_WORDS: dict[str, tuple[str, ...]] = {
    "street": ("गली", "गलियों", "सड़क", "सड़कों", "रास्ता", "चौराहा", "मोहल्ला", "शहर", "फुटपाथ", "दुकान", "दुकानें",
               "gali", "galiyon", "sadak", "sadkon", "chauraha", "mohalla", "shehar", "sheher", "footpath", "dukaan", "dukan", "city road"),
    "rooftop": ("छत", "छतों", "टेरेस", "बालकनी", "chhat", "chhat par", "terrace", "balcony"),
    "room": ("कमरा", "कमरे", "घर", "घर में", "रसोई", "रसोईघर", "बेडरूम", "बैठक",
             "kamra", "kamre", "ghar", "ghar mein", "rasoi", "rasoighar", "bedroom", "living room", "drawing room"),
    "forest": ("जंगल", "जंगलों", "वन", "झील", "नदी", "पेड़ों", "jungle", "jangal", "van", "jheel", "nadi", "woods"),
    "stage": ("मंच", "स्टेज", "manch", "stage"),
}  # fmt: skip


@dataclass(frozen=True)
class Named:
    """A library object a line of the script names: the asset, the line (with when it is on screen) and where in it."""

    asset: AssetDef
    start: float
    end: float
    text: str
    at: tuple[int, int]  # the span of the words in ``text``


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
        have = set(catalog.backgrounds.names())
        self.engine_places: TagIndex[_EnginePlace] = TagIndex(
            _EnginePlace(n, w) for n, w in ENGINE_PLACE_WORDS.items() if n in have
        )
        # the words a library character has in Latin letters after its first Devanagari one are Hinglish ("kutta"): not English
        self.hinglish: set[tuple[str, ...]] = set()
        for e in catalog.assets.entries():
            a = e.obj
            if a.kind != "character":
                continue
            seen_indic = False
            for tag in a.tags:
                if not tag.isascii():
                    seen_indic = True
                elif seen_indic and (key := phrase_key(tag)) and tag not in _AMBIGUOUS_HINGLISH:
                    self.hinglish.add(key)

    # -- characters -------------------------------------------------------------------------
    def character_words(self) -> dict[str, str]:
        """``{word: archetype}`` for every single word a character asset answers to (the cast builder's role nouns)."""
        return {w: a.name for w, a in self.characters.single_words().items()}

    def foreign_character_hits(self, text: str) -> list[Hit[AssetDef]]:
        """Characters ``text`` names in words other than English ("किसान", "गाय", "kutta"), in order.

        The cast builder finds English role nouns after an article ("the dog", "a farmer"); other scripts have no such
        marker, so their words are read straight from the tags (Hinglish too: Hindi in Latin letters).  English words are left
        to the rules that already read them."""
        return [
            h
            for h in self.characters.scan(text)
            if not h.phrase.isascii() or phrase_key(h.phrase) in self.hinglish
        ]

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
        for index in (self.places, self.engine_places):
            for h in index.scan(text):
                if any(
                    _inside((h.start, h.end), t) and t[1] - t[0] > h.end - h.start for t in things
                ):
                    continue
                out[h.asset.name] = out.get(h.asset.name, 0.0) + PLACE_NAMED_SCORE
        return out

    # -- objects ----------------------------------------------------------------------------
    def refers_to(self, phrase: str, candidates: Sequence[AssetDef]) -> AssetDef | None:
        """The one of ``candidates`` that ``phrase`` is another way of saying: "the pot" is the "pot of water" that came
        before it, "the jug" is the water jug.  A candidate answers when it has the phrase among its own words, or when the
        phrase is part of one of its longer ones.  The first (the most recent) that does wins."""
        key = phrase_key(phrase)
        if not key:
            return None
        for a in candidates:
            for tag in (a.name, *a.tags):
                tk = phrase_key(tag)
                if tk == key or (len(tk) > len(key) and _contains(tk, key)):
                    return a
        return None

    def named_objects(
        self,
        units: Sequence[tuple[float, float, str]],
        established: Sequence[AssetDef] = (),
        cast: Container[str] = (),
    ) -> list[Named]:
        """Every library object the lines name, in reading order (the same one again where a line says it again).

        ``established`` are the things already on the set, most recent first: a word that is part of how one of them was named
        ("pot" after "a pot of water") means that one, not the first thing in the library the word fits.  ``cast`` are the library
        characters the story has: a word one of them answers to is that character, not also a picture of it standing about."""
        out: list[Named] = []
        for s, e, text in units:
            sets = [(h.start, h.end) for h in self.places.scan(text)]
            for h in self.objects.scan(text):
                a = h.asset
                if any(_inside((h.start, h.end), p) for p in sets):
                    continue  # a word that names the set ("school", "hospital") is not also a thing standing on it
                actor = self.characters.lookup(h.phrase)
                if actor is not None and actor.name in cast:
                    continue  # "the rat" is the rat of the cast, whatever a photo of a mouse in the library is also called
                same = self.refers_to(h.phrase, established)
                if same is not None:
                    a = same
                elif h.alternatives and h.phrase.lower().replace(" ", "_") != a.name:
                    continue  # a word several things answer to ("fruit", "vehicle"): do not guess which
                out.append(Named(a, s, e, text, (h.start, h.end)))
        return out

    def place_object(
        self,
        a: AssetDef,
        scene: Mapping[str, Any],
        text: str,
        start: float,
        end: float,
        duration: float,
        boost: float = 1.0,
        x: float | None = None,
    ) -> dict[str, Any]:
        """One object as a spec dict on a spot clear of what is in ``scene`` (or on ``x``).  A vehicle in a line that says
        something drives, flies or sails crosses the frame; a thing that floats (a balloon, the sun) hangs high and drifts.
        ``boost`` makes a small thing bigger when the cast is small (see :func:`reel.assets.gaps.object_scale`)."""
        floats = a.anchor[1] <= _FLOATERS_MAX_ANCHOR_Y
        if x is None:
            # the sun does not take the spot of a pot on the ground, nor the pot the sun's
            same_height = [
                o for o in scene.get("objects", []) if (o["position"][1] <= _SKY_Y + 0.1) == floats
            ]
            x = free_spot({"layers": scene.get("layers", []), "objects": same_height})
        y = _SKY_Y if floats else _GROUND_Y
        ob: dict[str, Any] = {
            "asset": a.name,
            "position": [x, y],
            "scale": object_scale(a, boost),
        }
        motions: list[dict[str, Any]] = []
        words = set(text.lower().replace(",", " ").replace(".", " ").split())
        if (set(a.tags) & _VEHICLE_TAGS) and words & _TRAVEL_VERBS:
            going_left = x >= 0.5
            t0 = round(max(0.2, start - 0.2), 2)
            t1 = round(min(duration - 0.1, max(t0 + 1.4, end + 0.8)), 2)
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
        return ob

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
        found: list[Named] = []
        seen: set[str] = set(taken or ())
        for n in self.named_objects(units):
            if n.asset.name in seen:
                continue
            seen.add(n.asset.name)
            found.append(n)
            if len(found) >= MAX_OBJECTS_PER_SCENE:
                break
        scene: dict[str, Any] = {"layers": list(layers), "objects": []}
        out: list[dict[str, Any]] = []
        for n in found:
            ob = self.place_object(n.asset, scene, n.text, n.start, n.end, duration)
            scene["objects"].append(ob)
            out.append(ob)
        return out


def _contains(words: tuple[str, ...], part: tuple[str, ...]) -> bool:
    """Is ``part`` a run of consecutive words of ``words``?"""
    n = len(part)
    return any(words[i : i + n] == part for i in range(len(words) - n + 1))


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


def gap_status(
    spec: Mapping[str, Any], script: str | None = None, catalog: Catalog | None = None
) -> list[dict[str, Any]]:
    """What a spec's script needs that the library lacks, as the Studio shows it: each gap with the scenes that say the word
    and ``asset``, the library asset that answers it *now* (``None`` while it is still missing).

    The gaps a planner recorded in ``meta.library_gaps`` are kept; with the script, whatever else it mentions that nothing draws is
    added (the same reading the planners do), so a spec written by hand, or edited since, is checked too.  A gap lists only
    scenes the spec still has.  The spec itself is not changed.
    """
    import copy

    from reel.assets.gaps import resolve_gap
    from reel.core.catalog import CATALOG

    cat = catalog or CATALOG
    work = copy.deepcopy(dict(spec))
    if script and script.strip():
        attach_library_gaps(work, script, cat)
    meta = work.get("meta")
    raw = meta.get("library_gaps") if isinstance(meta, dict) else None
    ids = {str(sc.get("id")) for sc in work.get("scenes", []) or [] if isinstance(sc, Mapping)}
    out: list[dict[str, Any]] = []
    for g in raw if isinstance(raw, list) else []:
        if not isinstance(g, Mapping) or g.get("kind") not in ("character", "object", "place"):
            continue
        name = g.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        have = resolve_gap(g, cat)
        entry: dict[str, Any] = {
            "kind": g["kind"],
            "name": name,
            "scenes": [str(s) for s in g.get("scenes", []) or [] if str(s) in ids],
            "asset": have.name if have is not None else None,
        }
        for key in ("character", "stand_in"):
            if isinstance(g.get(key), str) and g[key]:
                entry[key] = g[key]
        out.append(entry)
    return out[:24]


__all__ = ["LibraryPlanner", "Named", "attach_library_gaps", "gap_status", "scene_texts"]
