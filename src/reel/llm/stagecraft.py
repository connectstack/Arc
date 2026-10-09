"""The stagecraft the offline planner and the enrichment pass share.

Everything that turns *words* into *staging* lives here, once:

* the verb and emotion lexicons (``CUE_WORDS`` / ``MOOD_WORDS``) and :func:`extract_cues`, which read a
  sentence and say who does what (walk, ran, jumped, waved, laughed, thought, pointed, fell, picked
  up, gasped ...);
* :func:`plan_cue` - a cue becomes an action request (name, params, length) that the catalog accepts;
  :func:`enter_request`, :func:`listener_request`, :func:`flourish_request` and
  :func:`bridge_requests` are the other staging beats (arrivals, listeners, a punchline flourish,
  business for silent scenes);
* :func:`schedule_requests` - requests become non-overlapping action windows on a layer, around
  whatever the layer already does;
* the sfx lexicon (``SFX_WORDS``) with :class:`CatalogView` ``.sfx(kind)`` (matches the *live* sfx
  registry by name and summary) and :func:`plan_sfx`;
* :func:`plan_camera` and :func:`plan_transition`, the rotating slow camera moves and scene changes.

:mod:`reel.llm.heuristic` (script -> spec without a model) and :mod:`reel.llm.enrich` (fill what a weak
model left empty) both call these, so a verb means the same thing in both.  Nothing here knows about
scripts, specs or models - only text, requests and the catalog - and it never names a registry
entry it has not checked exists.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Container, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from reel.core.catalog import Catalog
from reel.llm.base import enum_values


def clean_text(text: str) -> str:
    """Collapse whitespace and curly apostrophes."""
    return re.sub(r"\s+", " ", text.replace("\u2019", "'")).strip()


# =============================================================================== lexicons
#: verb lexicon: cue -> word forms (lowercase, phrases allowed)
CUE_WORDS: dict[str, tuple[str, ...]] = {
    "enter": ("enter", "enters", "entered", "entering", "arrive", "arrives", "arrived", "arriving",
              "appears", "appeared", "shows up", "showed up", "walks in", "walked in", "comes in",
              "came in", "bursts in", "burst in", "steps in", "stepped in"),
    "exit": ("leave", "leaves", "left", "leaving", "exit", "exits", "exited", "depart", "departs",
             "departed", "walks away", "walked away", "walks out", "walked out", "goes away"),
    "run": ("run", "runs", "ran", "running", "rush", "rushes", "rushed", "hurry", "hurries",
            "hurried", "sprint", "sprints", "sprinted", "race", "races", "raced", "dash", "dashes",
            "dashed", "flee", "flees", "fled", "chase", "chases", "chased", "bolt", "bolts"),
    "jump": ("jump", "jumps", "jumped", "jumping", "leap", "leaps", "leapt", "leaped", "hop",
             "hops", "hopped", "bounce", "bounces", "bounced", "dance", "dances", "danced",
             "dancing", "cheer", "cheers", "cheered", "celebrate", "celebrates", "celebrated"),
    "wave": ("wave", "waves", "waved", "waving", "greet", "greets", "greeted", "hello", "hi",
             "goodbye", "bye", "farewell"),
    "laugh": ("laugh", "laughs", "laughed", "laughing", "giggle", "giggles", "giggled",
              "chuckle", "chuckles", "chuckled", "joke", "jokes", "joked", "haha", "funny",
              "grin", "grins", "grinned"),
    "cry": ("cry", "cries", "cried", "crying", "sob", "sobs", "sobbed", "sobbing", "weep",
            "weeps", "wept", "weeping", "tears"),
    "think": ("think", "thinks", "thought", "thinking", "wonder", "wonders", "wondered",
              "ponder", "ponders", "pondered", "imagine", "imagines", "imagined", "idea", "ideas",
              "realize", "realizes", "realized", "realise", "realises", "realised", "remember",
              "remembers", "remembered", "consider", "considers", "considered", "hmm", "decide",
              "decides", "decided", "figure", "figures", "figured", "guess", "guesses",
              "curious", "puzzled", "mystery", "why"),
    "point": ("point", "points", "pointed", "pointing", "indicate", "indicates", "gesture",
              "gestures"),
    "fall": ("fall", "falls", "fell", "falling", "slip", "slips", "slipped", "trip", "trips",
             "tripped", "tumble", "tumbles", "tumbled", "faint", "faints", "fainted", "collapse",
             "collapses", "collapsed", "crash", "crashes", "crashed"),
    "pick": ("pick", "picks", "picked", "grab", "grabs", "grabbed", "lift", "lifts", "lifted",
             "collect", "collects", "collected", "gather", "gathers", "gathered", "snatch",
             "snatches", "snatched", "seize", "seizes", "seized"),
    "surprise": ("gasp", "gasps", "gasped", "shock", "shocked", "shocking", "surprise",
                 "surprised", "surprising", "startle", "startled", "astonished", "amazed",
                 "stunned", "wow", "whoa", "scream", "screams", "screamed", "scared", "frozen",
                 "froze", "freeze", "freezes", "empty", "gone", "missing", "vanished"),
    "look": ("look", "looks", "looked", "looking", "glance", "glances", "glanced", "stare",
             "stares", "stared", "watch", "watches", "watched", "notice", "notices", "noticed",
             "spot", "spots", "spotted", "peek", "peeks", "peeked", "gaze", "gazes", "gazed",
             "see", "sees", "saw"),
    "walk": ("walk", "walks", "walked", "walking", "stroll", "strolls", "strolled", "wander",
             "wanders", "wandered", "tiptoe", "tiptoes", "tiptoed", "tiptoeing", "sneak",
             "sneaks", "sneaked", "sneaking", "creep", "creeps", "crept", "heads", "headed",
             "follow", "follows", "followed", "following", "march", "marches", "marched",
             "step", "steps", "stepped", "approach", "approaches", "approached", "go", "goes",
             "went", "come", "comes", "came", "coming", "move", "moves", "moved"),
}  # fmt: skip
MOOD_WORDS: dict[str, tuple[str, ...]] = {
    "sad": ("sad", "sadly", "unhappy", "lonely", "gloomy", "miserable", "disappointed", "sorry",
            "upset", "lost"),
    "happy": ("happy", "happily", "joy", "joyful", "glad", "delighted", "excited", "cheerful",
              "smile", "smiles", "smiled", "smiling", "love", "loved", "fun"),
    "angry": ("angry", "mad", "furious", "annoyed", "grumpy", "cross", "rage"),
    "tired": ("tired", "sleepy", "yawn", "yawns", "yawned", "yawning", "exhausted", "drowsy"),
    "nervous": ("nervous", "worried", "worry", "afraid", "anxious", "shy", "scary", "spooky"),
    "bored": ("bored", "boring", "dull"),
    "proud": ("proud", "confident", "triumph", "winner", "won"),
}  # fmt: skip

#: sfx wanted per kind of moment, as words to look for in the registry's names and summaries
SFX_WORDS: dict[str, tuple[str, ...]] = {
    "step": ("footstep", "footsteps", "step", "steps", "walk"),
    "whoosh": ("whoosh", "swoosh", "swish", "woosh", "wind", "swipe"),
    "pop": ("pop", "bubble", "blip", "plop", "boop"),
    "boing": ("boing", "spring", "bounce", "hop", "jump"),
    "thud": ("thud", "thump", "bump", "impact", "crash", "slam", "hit"),
    "ding": ("ding", "chime", "bell", "ping", "bling", "sparkle", "idea"),
    "laugh": ("laugh", "giggle", "chuckle", "haha"),
    "sad": ("sad", "trombone", "wah", "sigh"),
    "gasp": ("gasp", "surprise", "shock", "gulp", "oh"),
    "click": ("click", "tap", "tick", "button", "switch"),
    "cheer": ("cheer", "applause", "clap", "tada", "fanfare", "success", "win", "yay"),
}  # fmt: skip


#: "they", "everyone" ...: narration that makes the whole cast act together
PLURAL_WORDS = frozenset(
    ["they", "them", "their", "everyone", "everybody", "both", "together", "all"]
)


# =============================================================================== cast and cues
@dataclass
class Char:
    id: str
    name: str
    archetype: str = "everyman"
    gender: str = ""  # m | f | ""
    role: str = ""  # set for characters found through a role noun ("robot")
    count: int = 0
    props: list[str] = field(default_factory=list)
    palette: dict[str, str] = field(default_factory=dict)
    first: int = 0  # index of the first sentence that mentions it


@dataclass
class Cast:
    chars: list[Char]
    explainer: bool  # one narrator on screen who speaks every line
    by_name: dict[str, Char] = field(default_factory=dict)

    def get(self, cid: str | None) -> Char | None:
        return next((c for c in self.chars if c.id == cid), None)


@dataclass
class Cue:
    kind: str  # walk | run | jump | ... | mood:<name>
    subject: str | None  # character id performing it
    word: str = ""


def mentions(text: str, cast: Cast) -> list[tuple[int, str]]:
    """(position, character id) of every name / role noun in ``text``, in reading order."""
    found: list[tuple[int, str]] = []
    for c in cast.chars:
        for label in {c.name, c.role} - {""}:
            flags = re.I if label == c.role else 0
            for m in re.finditer(rf"\b{re.escape(label)}\b", text, flags):
                found.append((m.start(), c.id))
    found.sort()
    return found


def extract_cues(text: str, default_subject: str | None, cast: Cast) -> list[Cue]:
    """Verb and emotion cues in reading order, each tied to the nearest character mentioned before it
    (else ``default_subject``).  At most two actions and one mood per sentence."""
    low = " " + clean_text(text).lower() + " "
    for c in cast.chars:  # a name is not a verb: Bolt the robot never "bolts", Pat never "pats"
        for label in {c.name, c.role} - {""}:
            low = re.sub(
                rf"(?<![a-z]){re.escape(label.lower())}(?![a-z])",
                lambda m: "#" * len(m.group(0)),
                low,
            )
    ments = mentions(text, cast)
    hits: list[tuple[int, str, str]] = []
    for table, prefix in ((CUE_WORDS, ""), (MOOD_WORDS, "mood:")):
        for kind, forms in table.items():
            for form in forms:
                for m in re.finditer(rf"(?<![a-z]){re.escape(form)}(?![a-z])", low):
                    hits.append((m.start() - 1, prefix + kind, form))
    hits.sort()
    kinds = {k for _, k, _ in hits}
    cues: list[Cue] = []
    seen: set[str] = set()
    n_actions = n_moods = 0
    for pos, kind, form in hits:
        if (
            kind in seen
            or (kind == "walk" and "enter" in kinds)
            or (kind == "walk" and "exit" in kinds)
        ):
            continue
        is_mood = kind.startswith("mood:")
        if (is_mood and n_moods >= 1) or (not is_mood and n_actions >= 2):
            continue
        before = [cid for p, cid in ments if p <= pos]
        cues.append(Cue(kind, before[-1] if before else default_subject, form))
        seen.add(kind)
        n_moods += is_mood
        n_actions += not is_mood
    return cues


# =============================================================================== the catalog, as the planner sees it
class CatalogView:
    """What the planner needs from the registries, looked up lazily at call time and cached."""

    def __init__(self, catalog: Catalog) -> None:
        self.c = catalog
        self._sfx: dict[str, str | None] = {}
        self._bg: dict[str, dict[str, Any]] | None = None

    def has_action(self, name: str) -> bool:
        return name in self.c.actions

    def action_min(self, name: str) -> float:
        return float(getattr(self.c.actions.get(name), "min_duration", 0.3) or 0.3)

    def action_default(self, name: str) -> float:
        return float(getattr(self.c.actions.get(name), "default_duration", 1.6) or 1.6)

    def moves_root(self, name: str) -> bool:
        return bool(getattr(self.c.actions.get(name), "moves_root", False))

    def params(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """``params`` if the action's own model accepts them, else ``{}``."""
        if not params:
            return {}
        model = getattr(self.c.actions.get(name), "params_model", None)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            return {}
        try:
            model.model_validate(params)
        except ValidationError:
            return {}
        return params

    def sfx(self, kind: str) -> str | None:
        """A registered sfx for this kind of moment: matched on names first, then on summaries."""
        if kind in self._sfx:
            return self._sfx[kind]
        entries = self.c.sfx.entries()
        found: str | None = None
        for field_of in ("name", "summary"):
            for word in SFX_WORDS.get(kind, ()):
                for e in entries:
                    if field_of == "name":
                        hay = e.name.lower()
                    else:
                        hay = str(
                            getattr(e.obj, "summary", "") or e.meta.get("summary", "")
                        ).lower()
                    if word in re.split(r"[_\W]+", hay):
                        found = e.name
                        break
                if found:
                    break
            if found:
                break
        self._sfx[kind] = found
        return found

    def backgrounds(self) -> dict[str, dict[str, Any]]:
        if self._bg is None:
            meta: dict[str, dict[str, Any]] = {}
            for name in self.c.backgrounds.names():
                obj = self.c.backgrounds.get(name)
                model = getattr(obj, "params_model", None)
                enums: dict[str, list[str]] = {}
                if isinstance(model, type) and issubclass(model, BaseModel):
                    for key in model.model_fields:
                        vals = enum_values(model, key)
                        if vals:
                            enums[key] = vals
                tags = [str(t).lower() for t in (getattr(obj, "tags", ()) or ())]
                meta[name] = {"model": model, "enums": enums, "tags": tags}
            self._bg = meta
        return self._bg


# =============================================================================== action requests
@dataclass
class ActionRequest:
    """A wish for an action on a layer: start no earlier than ``t0``, about ``dur`` long."""

    name: str
    t0: float
    dur: float
    params: dict[str, Any] = field(default_factory=dict)
    prio: int = 5


# =============================================================================== acting
def enter_style(archetype: str | None, rng: random.Random) -> str | None:
    """How a newcomer arrives: robots slide in, kids and heroes sometimes run (one draw of ``rng``)."""
    r = rng.random()
    if archetype == "robot" and r < 0.5:
        return "slide"
    if archetype in ("kid", "hero") and r < 0.45:
        return "run"
    return None


def flavor_cue(
    text: str,
    performer: str | None,
    rng: random.Random,
    *,
    lively: bool = False,
    glance: bool = True,
) -> Cue:
    """A gesture to match the punctuation when the text has no verb for us: questions think,
    exclamations point - or, ``lively``, react (surprise / jump / laugh) - statements glance or point.
    ``glance=False`` leaves out the looks (a head turn is hardly a gesture): questions think and
    statements think or point."""
    t = text.strip()
    if t.endswith("?"):
        kind = rng.choice(("think", "look")) if lively and glance else "think"
    elif t.endswith("!"):
        kind = rng.choice(("surprise", "jump", "laugh", "point")) if lively else "point"
    elif glance:
        kind = rng.choice(("look", "point", "look"))
    else:
        kind = rng.choice(("think", "point"))
    return Cue(kind, performer, "flavor")


def plan_cue(
    cat: CatalogView,
    cue: Cue,
    *,
    start: float,
    span: float,
    end: float,
    x_now: float,
    toward: float,
    target: str | None,
    last: bool,
) -> ActionRequest | None:
    """One cue as an action request, or None when the catalog cannot act it out.

    ``start`` is when it should begin, ``span`` roughly how long the beat lasts, ``end`` when the beat
    ends; ``x_now`` is where the character stands (screen fraction) and ``toward`` the direction
    (+1 / -1) a walk or run goes; ``target`` is who a look or a point is aimed at (a character id
    present in the scene, or None); ``last`` says the beat is the last thing in the scene (only then
    may someone leave).
    """
    kind = cue.kind

    def shift(amount: float) -> float:  # keep a walk inside the frame's comfortable band
        return round(max(0.2 - x_now, min(0.8 - x_now, amount * toward)), 2)

    if kind == "exit":  # only as the very last thing of a scene
        if last and cat.has_action("exit_to"):
            return ActionRequest(
                "exit_to", max(start, end - 1.3), 1.5, cat.params("exit_to", {}), prio=1
            )
        return None
    if kind == "enter":
        return None  # entrances are staged per scene
    if kind == "look":
        if not cat.has_action("look_at"):
            return None
        params = cat.params("look_at", {"target": target or "camera"})
        return ActionRequest("look_at", start, 1.4, params, prio=4)
    flavored = cue.word == "flavor"
    table: dict[str, tuple[str, dict[str, Any], float]] = {
        "walk": ("walk", {"dx": shift(0.18)}, 2.0),
        "run": ("run", {"dx": shift(0.28)}, 1.6),
        "jump": (
            "jump",
            {"style": "cheer"} if cue.word.startswith(("cheer", "celebrat", "danc")) else {},
            1.0,
        ),
        "wave": ("wave", {}, 1.8),
        "laugh": ("laugh", {}, min(2.2, span)),
        "cry": ("cry", {}, min(2.4, max(1.2, span))),
        "think": (
            "think",
            {"aha": True} if cue.word.startswith(("idea", "realiz", "realis")) else {},
            2.4,
        ),
        "point": (
            "point",
            {"target": target} if target and not flavored else {"direction": "up", "jabs": 2},
            1.5,
        ),
        "fall": ("fall", {"kind": "trip"}, 1.7),
        "pick": ("pick_up", {}, 1.8),
        "surprise": ("surprise", {}, 1.1),
    }
    if kind not in table or not cat.has_action(table[kind][0]):
        return None
    name, params, d = table[kind]
    return ActionRequest(name, start, d, cat.params(name, params), 1 if cat.moves_root(name) else 3)


def enter_request(
    cat: CatalogView, archetype: str | None, t0: float, rng: random.Random
) -> ActionRequest | None:
    """How a newcomer arrives: ``enter_from`` at ``t0`` (1.5 s, the entrance style from one draw of
    ``rng``), or None - without a draw - when the catalog has no ``enter_from``."""
    if not cat.has_action("enter_from"):
        return None
    style = enter_style(archetype, rng)
    return ActionRequest(
        "enter_from", t0, 1.5, cat.params("enter_from", {"style": style} if style else {}), prio=0
    )


def listener_request(
    cat: CatalogView, speaker: str, start: float, span: float
) -> ActionRequest | None:
    """A listener turns to the ``speaker`` from ``start`` for about as long as the line lasts
    (``span`` seconds, kept between 0.8 and 2.4 s)."""
    if not cat.has_action("look_at"):
        return None
    params = cat.params("look_at", {"target": speaker})
    if not params:
        return None
    return ActionRequest("look_at", start, min(2.4, max(0.8, span)), params, prio=7)


def flourish_request(cat: CatalogView, start: float) -> ActionRequest | None:
    """The punchline's flourish: a cheering jump, else a surprise, else a wave (the first one the
    catalog has)."""
    for name, params in (("jump", {"style": "cheer"}), ("surprise", {}), ("wave", {})):
        if cat.has_action(name):
            return ActionRequest(
                name, start, cat.action_default(name) * 0.8, cat.params(name, params), prio=2
            )
    return None


def bridge_requests(
    cat: CatalogView,
    cast_ids: Sequence[str],
    dur: float,
    rng: random.Random,
    *,
    skip: Container[str] = (),
    who: Container[str] | None = None,
) -> dict[str, list[ActionRequest]]:
    """Business for a scene nobody speaks in: the cast ponders, looks around, smiles or waves - two
    beats each, one near the start and one in the middle.  ``who`` limits the result to some of the
    characters (the draws of ``rng`` are made for everyone, so the choice for one character does not
    depend on the others being included); ``skip`` names actions to leave out of the choice."""
    options = [
        n for n in ("think", "look_at", "idle", "wave") if cat.has_action(n) and n not in skip
    ]
    out: dict[str, list[ActionRequest]] = {cid: [] for cid in cast_ids}
    for k, cid in enumerate(cast_ids):
        if not options:
            break
        others = [c for c in cast_ids if c != cid]
        for j, t0 in enumerate((0.5, dur * 0.5)):
            name = options[(rng.randrange(len(options)) + k + j) % len(options)]
            params: dict[str, Any] = {}
            if name == "look_at":
                params = {"target": others[0] if others else "camera"}
            elif name == "idle":
                params = {"mood": rng.choice(("happy", "proud", "calm"))}
            if who is None or cid in who:
                out[cid].append(
                    ActionRequest(
                        name, t0, cat.action_default(name), cat.params(name, params), prio=3
                    )
                )
    return out


#: breathing room after an action: before the next request, and after a busy window
GAP_SEC = 0.05


def schedule_requests(
    cat: CatalogView,
    reqs: Iterable[ActionRequest],
    dur: float,
    busy: Sequence[tuple[float, float]] = (),
) -> list[dict[str, Any]]:
    """Lay a layer's requests out one after another so nothing overlaps - each other, or the
    ``busy`` windows of actions the layer already has (requests slide past those, keeping
    :data:`GAP_SEC` clear of them; one that no longer fits is dropped).  Everything is decided on
    times rounded to hundredths, the way they are written, so the same plan laid out again around
    its own result places nothing new.  Returns spec-ready ``{"name", "t0", "t1"[, "params"]}``
    dicts."""
    placed: list[dict[str, Any]] = []
    cursor, limit = 0.0, dur - 0.15
    obstacles = sorted(busy)

    def clash(a: float, b: float) -> tuple[float, float] | None:
        return next(
            ((o0, o1) for o0, o1 in obstacles if a < o1 + GAP_SEC - 1e-9 and b > o0 + 1e-9), None
        )

    for r in sorted(reqs, key=lambda r: (r.t0, r.prio)):
        need = cat.action_min(r.name)
        start = max(r.t0, cursor)
        want = max(r.dur, need)
        slid = False
        while (hit := clash(start, start + want)) is not None:
            start, slid = hit[1] + GAP_SEC, True
        end = min(start + want, limit)
        if end - start < need:
            start = max(cursor, end - want)
        # (a start that slid past a busy window rounds up, so that it stays clear of it)
        start = math.ceil(start * 100 - 1e-6) / 100 if slid else round(start, 2)
        end = round(end, 2)
        if end - start < need - 1e-6 or clash(start, end) is not None:
            continue
        item: dict[str, Any] = {"name": r.name, "t0": start, "t1": end}
        if r.params:
            item["params"] = r.params
        placed.append(item)
        cursor = end + GAP_SEC
    return placed


#: where the universal slots stand (screen fraction); a background's own slots carry their own x
SLOT_X: dict[str, float] = {
    "far_left": 0.13,
    "left": 0.27,
    "center": 0.5,
    "right": 0.73,
    "far_right": 0.87,
}

#: slow camera moves, rotated from scene to scene: (type, from, to)
CAMERA_CYCLE: tuple[tuple[str, Any, Any], ...] = (
    ("dolly", 0.0, 0.18),
    ("pan", [-0.02, 0.0], [0.03, 0.0]),
    ("zoom", 1.0, 1.08),
    ("pan", [0.03, 0.0], [-0.02, 0.0]),
    ("dolly", 0.2, 0.0),
)


def plan_camera(
    cat: CatalogView, dur: float, index: int, punchline: bool = False
) -> list[dict[str, Any]]:
    """One slow camera move for a scene (``index`` picks where in the rotation to start); a
    punchline gets a shake and a push in.  Only move types the catalog has are used."""
    cams = cat.c.camera_moves
    t1 = round(max(1.0, dur - 0.2), 2)
    if punchline and "shake" in cams:
        t0 = round(dur * 0.3, 2)
        moves: list[dict[str, Any]] = [
            {"type": "shake", "from": 0.0, "to": 0.5, "t0": t0, "t1": round(min(dur, t0 + 0.6), 2)}
        ]
        if "zoom" in cams:
            moves.append({"type": "zoom", "from": 1.0, "to": 1.12, "t0": 0.0, "t1": t1})
        return moves
    for k in range(len(CAMERA_CYCLE)):
        typ, a, b = CAMERA_CYCLE[(index + k) % len(CAMERA_CYCLE)]
        if typ in cams:
            return [{"type": typ, "from": a, "to": b, "t0": 0.0, "t1": t1}]
    return []


def plan_sfx(
    cat: CatalogView,
    layers: Sequence[dict[str, Any]],
    caps: Sequence[dict[str, Any]],
    dur: float,
    prev_transition: str | None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Sound effects for a scene from what happens in it: steps for walking and entering, a boing for
    a jump, a thud for a fall, a gasp for a surprise, a ding for an idea ... plus a whoosh after a
    soft scene change and a hit for a title or a shout.  Names come from the live sfx registry
    (``CatalogView.sfx``); nothing is returned when it has none.  At most ``limit``, earliest first."""
    if not cat.c.sfx.names():
        return []
    want: list[tuple[float, str, float]] = []  # (time, sfx, volume)

    def add(t: float, kinds: tuple[str, ...], vol: float = 1.0) -> None:
        for kind in kinds:
            name = cat.sfx(kind)
            if name:
                want.append((round(min(max(0.05, t), dur - 0.1), 2), name, vol))
                return

    if prev_transition not in (None, "cut"):
        add(0.1, ("whoosh",), 0.6)
    for c in caps:
        if c["style"] == "title":
            add(c["t0"] + 0.1, ("ding", "pop", "whoosh"), 0.8)
        elif c["style"] == "shout":
            add(c["t0"] + 0.05, ("thud", "cheer", "pop"))
    for layer in layers:
        for a in layer["actions"]:
            n, t0, t1 = a["name"], a["t0"], a["t1"]
            if n in ("walk", "run", "enter_from", "exit_to"):
                add(t0 + 0.2, ("step",), 0.5)
            elif n == "jump":
                add(t0 + 0.28 * (t1 - t0), ("boing", "pop"))
            elif n == "fall":
                add(t0 + 0.46 * (t1 - t0), ("thud",))
            elif n == "surprise":
                add(t0 + 0.05, ("gasp", "pop"))
            elif n == "think" and a.get("params", {}).get("aha"):
                add(t1 - 0.4, ("ding",))
            elif n == "laugh":
                add(t0 + 0.1, ("laugh",), 0.7)
            elif n == "cry":
                add(t0 + 0.1, ("sad",), 0.6)
            elif n == "pick_up":
                add(t0 + 0.5 * (t1 - t0), ("pop", "click"), 0.8)
    if not want:
        add(0.2, ("pop", "click", "whoosh", "ding"), 0.7)
    want.sort()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t, name, vol in want:
        if len(out) >= limit:
            break
        if name in seen and len(want) > limit:  # prefer variety when there is a choice
            continue
        seen.add(name)
        item: dict[str, Any] = {"name": name, "t": t}
        if abs(vol - 1.0) > 1e-9:
            item["volume"] = vol
        out.append(item)
    return out


#: scene-change rotation per style (a style's own transition is the one it shows off)
TRANSITION_ORDER: dict[str, tuple[str, ...]] = {
    "paper_cutout": ("page_flip", "crossfade", "wipe"),
    "stickman": ("wipe", "crossfade", "wipe"),
}
DEFAULT_TRANSITION_ORDER = ("crossfade", "wipe", "page_flip")
#: how long each kind of scene change overlaps the next scene
TRANSITION_SEC = {"page_flip": 0.8, "wipe": 0.55, "crossfade": 0.6}


def cut_value(cat: CatalogView) -> dict[str, Any] | None:
    """A hard cut, or None (leave ``transition_out`` out) when the catalog has no ``cut``."""
    return {"type": "cut", "duration": 0} if "cut" in cat.c.transitions else None


def plan_transition(
    cat: CatalogView, style: str, k: int, dur: float, next_is_punchline: bool = False
) -> dict[str, Any] | None:
    """The ``k``-th scene change: crossfade / wipe / page_flip in the style's order, every fourth
    one (and the one into a punchline) a hard cut."""
    trans = cat.c.transitions
    if k % 4 == 3 or next_is_punchline:
        return cut_value(cat)
    order = TRANSITION_ORDER.get(style, DEFAULT_TRANSITION_ORDER)
    pick = order[k % len(order)]
    if pick not in trans:
        pick = next((t for t in DEFAULT_TRANSITION_ORDER if t in trans), "")
    if not pick:
        return cut_value(cat)
    base = TRANSITION_SEC.get(pick, 0.5)
    out: dict[str, Any] = {"type": pick, "duration": round(min(base, 0.35 * dur), 2)}
    model = getattr(trans.get(pick), "params_model", None)
    if pick in ("wipe", "page_flip") and isinstance(model, type):
        dirs = [v for v in ("left", "right") if v in (enum_values(model, "direction") or [])]
        if dirs:
            out["params"] = {"direction": dirs[k % len(dirs)]}
    return out
