"""The things on the set, from a story's first scene to its last.

The scene-by-scene object planner (:meth:`reel.llm.library_plan.LibraryPlanner.scene_objects`) places what one scene names.
A story needs more than that: the pot found in the first line is still *the pot* when a later line says only "the pot", the
tree the friends sit under is there in every scene on that set, and what the lines say happens to a thing (the water level rose,
stones were dropped into the pot) is something the viewer should see happen.  :class:`PropTracker` follows the story:

* **what stays**: a thing the story names more than once, and a thing so big it is part of the place (a tree), stay on the set
  until the story leaves it; anything else stays for :data:`OBJECT_STAYS` scenes after the one that named it, as before.
  What drove off or flew away is gone, and a new set starts bare.  At most :data:`MAX_ON_SET` things are on a set at a time;
* **what a word means**: a word that is part of how an earlier line named a thing ("pot" after "a pot of water") means that
  thing, not another one the library also calls by the word;
* **how full it is**: a picture with a part that fills (``data-tag="fill"``: the water of a jug) shows what the lines say of it,
  low at first when a later line says it is low, rising while the line that says it rose is read, full after;
* **what is moved**: "dropped them into the pot", "put the cake on the table": the thing is set down beside whoever does it and
  travels to where the line says, and what went *into* another thing is gone;
* **what goes away**: "the kite flew away", "the balloon popped": the thing leaves the frame (or is gone where it stood) and is not
  on the set again;
* **what grows**: "the flower bloomed", "a tree grew": it grows from a small one while the line is read (and is small before it);
* **how big**: "a tiny flag", "a small tree": drawn smaller, and not part of the place.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from reel.assets.art import ArtError, load_art
from reel.assets.gaps import _SLOT_X, _layer_xs
from reel.assets.match import norm_token, phrase_key, tokens
from reel.assets.model import AssetDef
from reel.core.planner import CHAR_UNIT
from reel.llm.library_plan import MAX_OBJECTS_PER_SCENE, LibraryPlanner, Named

#: scenes a thing the story names once stays on the set after the one that named it
OBJECT_STAYS = 2
#: the most things on a set at a time
MAX_ON_SET = 3
#: a thing taller than this share of the frame is part of the place (a tree, a house): it stays as long as the set does
FIXTURE_HEIGHT = 0.25
#: "a tiny flag", "a small tree": the line says it is small, so it is drawn smaller and is not part of the place
SMALL_WORDS = frozenset({"tiny", "little", "small", "mini", "miniature", "baby"})
SMALL_FACTOR = 0.6
#: how full a picture that fills is when a line says "low", and when it is full
LEVEL_LOW, LEVEL_FULL = 0.4, 1.0
#: a thing needs this much room from the others on its spot (frame fractions of x)
CLEAR_X = 0.12
FRAME_H = 1920.0

#: what the lines say of how full something is
LOW_WORDS = frozenset(
    {
        *("low", "lower", "empty", "dry", "shallow", "drained", "scarce"),
        *(norm_token(w) for w in ("कम", "नीचे", "खाली", "kam", "neeche", "khali")),
    }
)
RISE_WORDS = frozenset(
    {
        *("rose", "rise", "rises", "rising", "risen", "climbed", "climbs", "climbing"),
        *(
            "fill",
            "fills",
            "filled",
            "filling",
            "overflow",
            "overflows",
            "overflowed",
            "overflowing",
        ),
        *("brim", "brims", "brimmed", "brimming"),
        *(
            norm_token(w)
            for w in ("ऊपर", "बढ़", "बढ़ा", "बढ़ने", "भर", "upar", "badh", "badha", "badhne", "bhar")
        ),
    }
)
FULL_WORDS = frozenset(
    {"full", "topped", *(norm_token(w) for w in ("भरा", "लबालब", "bhara", "labalab"))}
)
#: what the lines say of a thing that grows, and how big it is before it does (a share of its size)
GROW_WORDS = frozenset(
    {
        *("grew", "grow", "grows", "growing", "sprouted", "sprouts", "sprouting"),
        *("bloomed", "blooms", "blooming", "blossomed", "blossoms", "swelled", "inflated"),
        *(
            norm_token(w)
            for w in (
                "उगा",
                "उगने",
                "उग",
                "खिला",
                "खिले",
                "बड़ा",
                "uga",
                "ugne",
                "khila",
                "khile",
                "bada",
            )
        ),
    }
)
GROW_FROM = 0.1
#: a growth word counts for a thing when it comes this many words after the thing's own word ("the flower slowly bloomed")
_GROW_REACH = 5

_TRANSFER = re.compile(
    r"\b(?:drop(?:s|ped|ping)?|put(?:s|ting)?|plac(?:e|es|ed|ing)|throw(?:s|ing)?|threw|thrown"
    r"|toss(?:es|ed|ing)?|dump(?:s|ed|ing)?|pour(?:s|ed|ing)?|slip(?:s|ped|ping)?|drip(?:s|ped|ping)?)\b",
    re.I,
)
#: a thing that goes away: it travels out of the frame ("the kite flew away") or is gone where it stands ("the balloon popped")
_TRAVELS = re.compile(
    r"\b(?:flew|fly|flies|flying|floated|floats|drifted|drifts|blew|blown|rolled|rolls|slid|escaped|sailed|swam|soared|sped"
    r"|raced|zoomed|bounced|hopped|ran|rushed)\b(?:\s+\w+){0,2}?\s+(?:away|off)\b",
    re.I,
)
_VANISHES = re.compile(
    r"\b(?:disappeared|disappears|vanished|vanishes|popped|burst|melted|exploded|(?:was|were|got)\s+(?:gone|lost))\b",
    re.I,
)
_INTO = re.compile(r"\b(?:into|inside|in|within)\b", re.I)
_ONTO = re.compile(r"\b(?:onto|on|over|atop|upon)\b", re.I)
#: how far (in characters) the words of a move may be from each other
_REACH = 48


@dataclass
class _Prop:
    """One thing on the set, as the story has it so far."""

    asset: AssetDef
    last: int  # the scene that last named it
    x: float | None = None  # where it stood in the last scene
    persistent: bool = False
    gone: bool = False  # drove off, flew away, or went into something else
    level: float | None = None  # how full (None: the story never says)
    on: _Prop | None = None  # what it was put on: it stays there
    factor: float = 1.0  # how big the lines say it is ("a tiny flag"), against the library's size
    size: float | None = None  # the scale it was given when it was put there


#: one line of a scene: when it is read, what it says and who does it
Beat = tuple[float, float, str, "str | None"]


def _layer_x(layer: Mapping[str, Any]) -> float | None:
    pos = layer.get("position")
    if isinstance(pos, str):
        return _SLOT_X.get(pos)
    if isinstance(pos, list) and pos and isinstance(pos[0], (int, float)):
        return float(pos[0])
    return None


class PropTracker:
    """Plans the objects of every scene of one story, in order (one tracker per story)."""

    def __init__(
        self,
        library: LibraryPlanner,
        texts: Sequence[str],
        boost: float = 1.0,
        cast: Container[str] = (),
    ) -> None:
        self.library = library
        self.cast = cast  # the library characters of the story (names of assets)
        self.texts = list(texts)  # what each scene says, to look ahead
        self.boost = boost  # how much bigger small things are drawn (a small cast is)
        self.on_set: list[_Prop] = []
        self.template: str | None = None
        self.index = -1
        self._fills: dict[str, bool] = {}
        self.recurring = self._recurring()

    # ------------------------------------------------------------------ the story as a whole
    def _recurring(self) -> set[str]:
        """The things the story names in more than one scene."""
        stood: list[AssetDef] = []
        count: Counter[str] = Counter()
        for text in self.texts:
            now = list(
                dict.fromkeys(
                    n.asset
                    for n in self.library.named_objects([(0.0, 0.0, text)], stood, self.cast)
                )
            )
            for a in now:
                count[a.name] += 1
                if a not in stood:
                    stood.insert(0, a)
        return {name for name, k in count.items() if k >= 2}

    def fills(self, asset: AssetDef) -> bool:
        """Does the picture have a part that fills up?"""
        if asset.name not in self._fills:
            try:
                self._fills[asset.name] = any(s.tag == "fill" for s in load_art(asset).shapes)
            except ArtError:
                self._fills[asset.name] = False
        return self._fills[asset.name]

    @staticmethod
    def _words_of(asset: AssetDef) -> set[str]:
        """The words that name a thing and what it holds: its own words, and the level."""
        out = {w for tag in (asset.name, *asset.tags) for w in phrase_key(tag)}
        return out | {"level", "liquid"}

    @staticmethod
    def _says(text: str) -> set[str]:
        return {t for t, _s, _e in tokens(text)}

    # ------------------------------------------------------------------ one scene
    def scene(
        self,
        template: str,
        units: Sequence[Beat],
        layers: Sequence[Mapping[str, Any]],
        dur: float,
    ) -> list[dict[str, Any]]:
        """The objects of the next scene of the story, as spec dicts."""
        self.index += 1
        i = self.index
        if template != self.template:  # a new set starts bare
            self.on_set, self.template = [], template
        stood = [p.asset for p in sorted(self.on_set, key=lambda p: -p.last)]
        named = self.library.named_objects([(s, e, t) for s, e, t, _w in units], stood, self.cast)
        fresh: list[Named] = []
        for n in named:
            if all(f.asset.name != n.asset.name for f in fresh):
                fresh.append(n)
            if len(fresh) >= MAX_OBJECTS_PER_SCENE:
                break

        scene: dict[str, Any] = {"layers": list(layers), "objects": []}
        placed: list[tuple[_Prop, dict[str, Any]]] = []
        # what the story already has on the set keeps its spot, so it is placed first; what is new takes the spots that are left
        mine = {
            n.asset.name: next((p for p in self.on_set if p.asset.name == n.asset.name), None)
            for n in fresh
        }
        for n in fresh:
            prop = mine[n.asset.name]
            if prop is not None:
                prop.last = i
                prop.factor = self._factor(n, prop.factor)
                ob = self._place(prop, scene, n.text, n.start, n.end, dur)
                placed.append((prop, ob))
        room = MAX_ON_SET - len(fresh)
        for prop in sorted(self.on_set, key=lambda p: -p.last):
            if room <= 0:
                break
            if prop.gone or any(prop is q for q, _ in placed) or prop.last == i:
                continue
            if not (prop.persistent or i - prop.last <= OBJECT_STAYS):
                continue
            placed.append((prop, self._place(prop, scene, "", 0.0, 0.0, dur)))
            room -= 1
        for n in fresh:
            if mine[n.asset.name] is None:
                prop = _Prop(n.asset, i, factor=self._factor(n, 1.0))
                self.on_set.append(prop)
                ob = self._place(prop, scene, n.text, n.start, n.end, dur)
                self._settle(prop, ob)
                placed.append((prop, ob))
        for prop, ob in placed:
            if prop.last == i and not prop.persistent and not prop.gone:
                self._settle(
                    prop, ob
                )  # a thing that is named again is judged again (it may be recurring now)

        for prop, ob in placed:
            self._level(prop, ob, i, units, dur)
        self._rest(placed)
        self._moves(placed, units, layers, named, dur)
        self._leaves(placed, units, named, dur)
        self.on_set = [p for p in self.on_set if not p.gone]
        return [ob for _p, ob in placed]

    # ------------------------------------------------------------------ placing
    @staticmethod
    def _factor(n: Named, before: float) -> float:
        """How big the line says the thing is: smaller when a word like "tiny" or "small" comes just before it."""
        words = [t for t, _s, _e in tokens(n.text[: n.at[0]])][-2:]
        return SMALL_FACTOR if any(w in SMALL_WORDS for w in words) else before

    def _place(
        self, prop: _Prop, scene: dict[str, Any], text: str, s: float, e: float, dur: float
    ) -> dict[str, Any]:
        """The thing on a spot clear of the cast and the others; where it stood last time when that is still clear."""
        x: float | None = None
        if prop.x is not None and all(abs(prop.x - t) >= CLEAR_X for t in _layer_xs(scene)):
            x = prop.x
        ob = self.library.place_object(prop.asset, scene, text, s, e, dur, self.boost, x=x)
        if prop.factor != 1.0:
            ob["scale"] = round(max(0.1, float(ob["scale"]) * prop.factor), 3)
        scene["objects"].append(ob)
        prop.x = float(ob["position"][0])
        return ob

    def _settle(self, prop: _Prop, ob: dict[str, Any]) -> None:
        """What staying means for a thing placed because a line named it."""
        a = prop.asset
        floats = any(m["type"] == "float" for m in ob.get("motions", []))
        left = any(m["type"] == "move" for m in ob.get("motions", []))
        if left:  # it drove off
            prop.gone = True
            return
        try:
            art = load_art(a)
            tall = art.height * CHAR_UNIT * float(ob["scale"]) / FRAME_H
        except ArtError:
            tall = 0.0
        fixture = tall >= FIXTURE_HEIGHT and not floats and prop.factor >= 1.0
        prop.persistent = a.name in self.recurring or fixture

    # ------------------------------------------------------------------ how full
    def _classify(self, text: str, prop: _Prop) -> str | None:
        """``rise`` | ``low`` | ``full`` when a line talks about the thing and says how full it is."""
        said = self._says(text)
        words = self._words_of(prop.asset)
        if not said & words:
            return None
        if not self.fills(
            prop.asset
        ):  # a thing that does not fill can only grow ("the flower bloomed")
            return "rise" if self._grows(text, words) else None
        if said & RISE_WORDS:
            return "rise"
        if said & LOW_WORDS:
            return "low"
        if said & FULL_WORDS:
            return "full"
        return None

    @staticmethod
    def _grows(text: str, words: set[str]) -> bool:
        """Does a growth word come just after one of the thing's own words?"""
        seq = [t for t, _s, _e in tokens(text)]
        return any(
            w in GROW_WORDS and any(seq[j] in words for j in range(max(0, i - _GROW_REACH), i))
            for i, w in enumerate(seq)
        )

    def _starts(self, prop: _Prop, i: int) -> float | None:
        """How full it is when the story first shows it: low when a later line says it is low or that it rose."""
        for text in self.texts[i:]:
            kind = self._classify(text, prop)
            if kind in ("low", "rise"):
                return LEVEL_LOW if self.fills(prop.asset) else GROW_FROM
            if kind == "full":
                return LEVEL_FULL
        return None

    def _level(
        self, prop: _Prop, ob: dict[str, Any], i: int, units: Sequence[Beat], dur: float
    ) -> None:
        """How full (or how grown) the thing is in this scene, as a motion: a ``rise`` of the water of a jug, a ``grow`` of a
        flower.  Nothing when the story never says (the picture as drawn)."""
        kind = "rise" if self.fills(prop.asset) else "grow"
        if prop.level is None:
            prop.level = self._starts(prop, i)
        if prop.level is None:
            return
        start = prop.level
        rising = next(((s, e) for s, e, t, _w in units if self._classify(t, prop) == "rise"), None)
        kinds = {self._classify(t, prop) for _s, _e, t, _w in units}
        if rising is not None:
            s, e = rising
            t0 = round(max(0.0, s), 2)
            t1 = round(min(dur - 0.1, max(t0 + 1.2, e + 0.4)), 2)
            prop.level = LEVEL_FULL
            if t1 - t0 >= 0.4:
                self._add(ob, {"type": kind, "t0": t0, "t1": t1, "from": start, "to": LEVEL_FULL})
                return
            start = LEVEL_FULL
        elif "low" in kinds:
            start = prop.level = LEVEL_LOW
        elif "full" in kinds:
            start = prop.level = LEVEL_FULL
        if start < LEVEL_FULL:  # still: the level it has
            self._add(
                ob, {"type": kind, "t0": 0.0, "t1": round(dur, 2), "from": start, "to": start}
            )

    @staticmethod
    def _add(ob: dict[str, Any], motion: dict[str, Any]) -> None:
        ob.setdefault("motions", []).append(
            {k: (round(v, 3) if isinstance(v, float) else v) for k, v in motion.items()}
        )

    # ------------------------------------------------------------------ what is moved
    @staticmethod
    def _tall(asset: AssetDef, ob: Mapping[str, Any]) -> float:
        """How tall a placed thing stands, as a share of the frame."""
        try:
            return load_art(asset).height * CHAR_UNIT * float(ob["scale"]) / FRAME_H
        except ArtError:
            return 0.0

    def _rest(self, placed: Sequence[tuple[_Prop, dict[str, Any]]]) -> None:
        """What was put on something is on it again in the next scenes, where it was put."""
        spots = {id(p): (p, ob) for p, ob in placed}
        for prop, ob in placed:
            if prop.on is None:
                continue
            under = spots.get(id(prop.on))
            if under is None:  # what it stood on is not on the set any more
                prop.on = None
                continue
            base, o_base = under
            ob["position"] = [
                o_base["position"][0],
                round(o_base["position"][1] - self._tall(base.asset, o_base), 3),
            ]
            if prop.size is not None:
                ob["scale"] = prop.size
            ob["layer"] = "front"
            prop.x = float(o_base["position"][0])

    def _moves(
        self,
        placed: Sequence[tuple[_Prop, dict[str, Any]]],
        units: Sequence[Beat],
        layers: Sequence[Mapping[str, Any]],
        named: Sequence[Named],
        dur: float,
    ) -> None:
        by_name = {p.asset.name: (p, ob) for p, ob in placed}
        for s, e, text, who in units:
            found = self._transfer(text, [n for n in named if n.start == s and n.text == text])
            if found is None:
                continue
            thing, target, inside = found
            if thing.name not in by_name or target.name not in by_name or thing.name == target.name:
                continue
            p_thing, o_thing = by_name[thing.name]
            p_target, o_target = by_name[target.name]
            if any(m["type"] == "move" for m in o_thing.get("motions", [])):
                continue
            doer = next((_layer_x(ly) for ly in layers if ly.get("character") == who), None)
            tx, ty = float(o_target["position"][0]), float(o_target["position"][1])
            try:
                a_t, a_o = load_art(target), load_art(thing)
            except ArtError:
                continue
            tall = self._tall(target, o_target)
            wide = a_t.width * CHAR_UNIT * float(o_target["scale"])
            # it starts on the ground beside whoever does it (on the side of the target), and is small enough for the mouth
            start_x = (doer if doer is not None else tx) + (0.09 if tx >= (doer or 0.0) else -0.09)
            o_thing["position"] = [round(min(0.92, max(0.08, start_x)), 3), 0.8]
            fit = 0.5 * wide / max(a_o.width * CHAR_UNIT, 1.0)
            o_thing["scale"] = round(max(0.15, min(float(o_thing["scale"]), fit)), 3)
            t0 = round(s + max(1.2, 0.35 * (e - s)), 2)
            t1 = round(min(dur - 0.2, e - 0.2, t0 + 1.3), 2)
            if t1 - t0 < 0.6:
                continue
            depth = 0.55 if inside else 1.0  # into it: down to the middle; onto it: on the top
            self._add(
                o_thing,
                {
                    "type": "move",
                    "t0": t0,
                    "t1": t1,
                    "to": [round(tx, 3), round(ty - depth * tall, 3)],
                },
            )
            if not inside:  # it stays where it was put, and what holds it stays too
                p_thing.on, p_thing.size = p_target, float(o_thing["scale"])
                p_target.persistent = True
                o_thing["layer"] = "front"
            if inside:  # it goes in and is gone
                self._add(
                    o_thing,
                    {
                        "type": "fade",
                        "t0": round(t1 - 0.2, 2),
                        "t1": round(t1 + 0.15, 2),
                        "from": 1.0,
                        "to": 0.0,
                    },
                )
                o_thing["t1"] = round(t1 + 0.2, 2)
                o_thing["layer"] = "front"
                p_thing.gone = True

    def _leaves(
        self,
        placed: Sequence[tuple[_Prop, dict[str, Any]]],
        units: Sequence[Beat],
        named: Sequence[Named],
        dur: float,
    ) -> None:
        """What the line says goes away leaves: out of the frame ("flew away"), or is gone where it stands ("popped"); it is not
        on the set after this scene."""
        by_name = {p.asset.name: (p, ob) for p, ob in placed}
        for s, e, text, _who in units:
            travels, vanishes = _TRAVELS.search(text), _VANISHES.search(text)
            hit = travels or vanishes
            if hit is None:
                continue
            before = [
                n for n in named if n.start == s and n.text == text and n.at[1] <= hit.start()
            ]
            if not before or before[-1].asset.name not in by_name:
                continue
            prop, ob = by_name[before[-1].asset.name]
            if any(m["type"] == "move" for m in ob.get("motions", [])):
                continue  # it already goes somewhere (a vehicle that drives off, a thing that is put away)
            t0 = round(s + max(0.3, 0.3 * (e - s)), 2)
            if travels is not None:
                x, y = float(ob["position"][0]), float(ob["position"][1])
                aloft = any(m["type"] == "float" for m in ob.get("motions", []))
                t1 = round(min(dur - 0.1, max(t0 + 1.0, e + 0.4)), 2)
                if t1 - t0 < 0.8:
                    continue
                self._add(
                    ob,
                    {
                        "type": "move",
                        "t0": t0,
                        "t1": t1,
                        "to": [1.3 if x >= 0.5 else -0.3, 0.05 if aloft else y],
                    },
                )
            else:
                self._add(
                    ob, {"type": "fade", "t0": t0, "t1": round(t0 + 0.4, 2), "from": 1.0, "to": 0.0}
                )
                ob["t1"] = round(t0 + 0.4, 2)
            prop.gone = True

    @staticmethod
    def _transfer(text: str, hits: Sequence[Named]) -> tuple[AssetDef, AssetDef, bool] | None:
        """``(thing, where it goes, goes inside)`` when the line moves one named thing to another ("dropped them into the pot")."""
        verb = _TRANSFER.search(text)
        if verb is None or len(hits) < 2:
            return None
        inside = _INTO.search(text, verb.end(), verb.end() + _REACH)
        onto = _ONTO.search(text, verb.end(), verb.end() + _REACH)
        prep = min((m for m in (inside, onto) if m), key=lambda m: m.start(), default=None)
        if prep is None:
            return None
        goes_in = prep is inside
        where = next(
            (n for n in hits if n.at[0] >= prep.end() and n.at[0] - prep.end() <= _REACH), None
        )
        if where is None:
            return None
        before = [n for n in hits if n.at[1] <= prep.start() and n.asset.name != where.asset.name]
        if not before:
            return None
        thing = before[
            -1
        ]  # the nearest thing named before the little word: "the stones ... into the pot"
        return thing.asset, where.asset, goes_in


__all__ = ["FIXTURE_HEIGHT", "MAX_ON_SET", "OBJECT_STAYS", "PropTracker"]
