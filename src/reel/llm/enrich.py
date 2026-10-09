"""Fill in what a weak model left empty - deterministically, and only that.

Small models write a spec that lints clean and still renders *static*: characters stand and look,
there is no camera move, no sound effect, every scene change is a cut, and the captions flash by
faster than the voice-over reads them.  :func:`enrich_spec` adds the missing life with the same
stagecraft the offline planner uses (:mod:`reel.llm.stagecraft`: the verb and emotion lexicons, the
sfx mapping, the camera and transition rotations), under one rule: **nothing the model wrote is ever
replaced** - only empty places are filled, and a spec that is already rich comes back unchanged.

Five steps, in this order (the scene-change step comes first because it may rescale the scenes):

1. *scene changes* - when every scene change is the same type (all cuts, say) they are rotated
   through crossfade / wipe / page_flip (every fourth, and the one into a ``shout``, stays a cut) and
   the scene lengths are re-fitted to the 45-60 s budget;
2. *captions* - a window shorter than the voice-over needs (``words / 2.5 + 0.6`` s) is widened, up
   to 0.3 s before the scene ends and never into a caption of the same style (a caption that would
   run into the next one pushes it later when the scene has the room);
3. *acting* - a layer that only stands about (nothing but idle / look_at / enter_from / talk) is
   acted: a verb in its captions says what to do (walk, ran, waved, laughed, pointed ...), a
   question thinks, an exclamation reacts, "look!" points, a ``shout`` gets a flourish; a newcomer
   gets an entrance; the others in the scene look at whoever speaks or acts; "they all ..." moves the
   whole cast; a scene nobody speaks in gets a little business (think, wave), staggered per character;
4. *camera* - a scene without camera moves gets one slow move, varied from scene to scene (a
   ``shout`` scene a shake and a push in);
5. *sound* - a scene without sfx gets one or two that fit what happens in it (steps for an
   entrance, a boing for a jump, a whoosh after a soft scene change ...), half a second apart.

Everything is a pure function of ``(spec, catalog, seed)``: no global randomness, no ``hash()``.  A
second pass changes nothing: every step acts only on what is still empty, and what the plan wants
for a layer does not depend on what the layer already does - it is only laid out *around* it.  Names
come from the live catalog, so a catalog without ``think`` simply never thinks.
"""

from __future__ import annotations

import copy
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from reel.core.catalog import CATALOG, Catalog
from reel.core.rng import stable_int
from reel.llm.base import HARD_MAX_SCENE_SEC as MAX_SCENE_SEC_HARD
from reel.llm.stagecraft import (
    CAMERA_CYCLE,
    PLURAL_WORDS,
    SLOT_X,
    ActionRequest,
    Cast,
    CatalogView,
    Char,
    Cue,
    bridge_requests,
    enter_request,
    extract_cues,
    flavor_cue,
    flourish_request,
    listener_request,
    mentions,
    plan_camera,
    plan_cue,
    plan_sfx,
    plan_transition,
    schedule_requests,
)

#: what a character does while merely standing there (the baseline motion breathes and blinks
#: anyway): a layer with nothing else is "static" and gets acted
RESTING = frozenset({"idle", "look_at", "enter_from", "talk"})
#: actions that a new gesture may overlap (they change the head or nothing at all, not the body)
OVERLAPPABLE = frozenset({"idle", "look_at", "talk"})

#: the voice-over's pace and the breathing room a caption needs around it
SPEECH_WPS = 2.5
READ_PAD_SEC = 0.6
#: a caption stays off the last 0.3 s of its scene, and keeps this gap to the next of its style
CAPTION_TAIL_SEC = 0.3
CAPTION_GAP_SEC = 0.05
#: a caption within this many seconds of its need counts as long enough
CAPTION_TOLERANCE_SEC = 0.02

#: a dialogue line that says "look", "watch", "over there" ...: the speaker points
_POINT_AT = re.compile(
    r"\b(?:look|watch)\b|\b(?:over|up|down)\s+(?:there|here)\b|\bthere\s+(?:it|they|he|she)\s+"
    r"(?:is|are)\b|\b(?:this|that)\s+way\b",
    re.I,
)
#: looking at the sky, the stars, the ceiling ...: the head tilts up instead of turning to somebody
_UP = re.compile(
    r"\b(?:sky|skies|stars?|moon|sun|clouds?|ceiling|roof|rainbow|birds?|planes?|balloons?|above)\b",
    re.I,
)
_WORDS = re.compile(r"[a-z']+")


# =============================================================================== bookkeeping
@dataclass
class _Tally:
    actions: int = 0
    camera: int = 0
    sfx: int = 0
    captions: int = 0
    transitions: int = 0
    scenes: set[int] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.scenes)

    def note(self) -> str:
        parts = [f"actions {self.actions}", f"camera {self.camera}", f"sfx {self.sfx}"]
        if self.captions:
            parts.append(f"captions {self.captions}")
        if self.transitions:
            parts.append(f"transitions {self.transitions}")
        n = len(self.scenes)
        return f"enriched {n} scene{'s' if n != 1 else ''} ({', '.join(parts)})"


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _words(text: str) -> list[str]:
    return _WORDS.findall(text.lower())


# =============================================================================== the entry point
def enrich_spec(
    spec: Mapping[str, Any],
    catalog: Catalog | None = None,
    seed: int | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """A copy of ``spec`` with its empty places filled (see the module docstring); ``spec`` itself is
    not modified.  ``seed`` defaults to ``meta.seed``.  One line is appended to ``notes`` when
    something changed, e.g. ``enriched 8 scenes (actions 11, camera 8, sfx 9)``.

    Meant for a spec that already lints: malformed parts are skipped, never repaired.  The reel stays
    exactly as long as it was (see the scene-change step).
    """
    if not isinstance(spec, Mapping):
        raise TypeError(f"enrich_spec expects a JSON object, got {type(spec).__name__}")
    cat = catalog or CATALOG
    out: dict[str, Any] = copy.deepcopy(dict(spec))
    raw_scenes = out.get("scenes")
    if not isinstance(raw_scenes, list) or not all(isinstance(s, dict) for s in raw_scenes):
        return out
    scenes: list[dict[str, Any]] = raw_scenes
    raw_meta = out.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    style = str(meta.get("style", ""))
    seed = int(seed if seed is not None else (_num(meta.get("seed")) or 0))
    view = CatalogView(cat)
    cast = _cast_of(out.get("characters"))
    arche = {c.id: c.archetype for c in cast.chars}
    tally = _Tally()

    _vary_transitions(scenes, view, style, seed, tally)

    cam_start = random.Random(stable_int(seed, "enrich", "camera")).randrange(len(CAMERA_CYCLE))
    prev_ids: list[str] | None = None
    for i, sc in enumerate(scenes):
        dur = _num(sc.get("duration_sec"))
        layers = [ly for ly in _dicts(sc.get("layers")) if isinstance(ly.get("character"), str)]
        ids = [str(ly["character"]) for ly in layers]
        if dur is None or dur <= 0:
            prev_ids = ids
            continue
        rng = random.Random(stable_int(seed, "enrich", "scene", i, sc.get("id")))
        caps = [c for c in _dicts(sc.get("captions")) if _captioned(c)]
        tally.captions += _count(tally, i, _widen_captions(caps, dur))
        tally.actions += _count(tally, i, _act(layers, caps, cast, arche, view, rng, dur, prev_ids))
        tally.camera += _count(tally, i, _add_camera(sc, view, cam_start + i, caps, dur))
        prev = scenes[i - 1] if i else None
        tally.sfx += _count(tally, i, _add_sfx(sc, layers, caps, view, prev, dur))
        prev_ids = ids

    if notes is not None and tally:
        notes.append(tally.note())
    return out


def _count(tally: _Tally, scene: int, n: int) -> int:
    if n:
        tally.scenes.add(scene)
    return n


def _dicts(v: Any) -> list[dict[str, Any]]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _captioned(c: dict[str, Any]) -> bool:
    return (
        isinstance(c.get("text"), str)
        and bool(c["text"].strip())
        and _num(c.get("t0")) is not None
        and _num(c.get("t1")) is not None
    )


def _cast_of(characters: Any) -> Cast:
    """The spec's characters as the planner's cast: a character is found in a caption by its display
    name (case as written) or its id (any case)."""
    chars: list[Char] = []
    for c in _dicts(characters):
        cid = c.get("id")
        if not isinstance(cid, str) or not cid:
            continue
        label = cid.replace("_", " ").strip() or cid
        name = c.get("name")
        shown = name.strip() if isinstance(name, str) and name.strip() else label
        arch = c.get("archetype")
        chars.append(
            Char(
                id=cid,
                name=shown,
                archetype=arch if isinstance(arch, str) else "everyman",
                role=label,
            )
        )
    return Cast(chars=chars, explainer=len(chars) == 1)


# =============================================================================== scene changes
def _kind(scene: dict[str, Any]) -> str:
    tr = scene.get("transition_out")
    kind = tr.get("type") if isinstance(tr, dict) else None
    return kind if isinstance(kind, str) else "cut"


def _overlap(transition: Any, here: float, there: float) -> float:
    """Seconds a scene change overlaps its neighbours (the timeline's rule)."""
    if not isinstance(transition, dict) or transition.get("type", "cut") == "cut":
        return 0.0
    d = _num(transition.get("duration")) or 0.0
    return min(max(d, 0.0), here, there)


def _vary_transitions(
    scenes: list[dict[str, Any]], view: CatalogView, style: str, seed: int, tally: _Tally
) -> None:
    """All scene changes the same (the last scene has none)?  Rotate them like the offline planner:
    crossfade / wipe / page_flip in the style's order, every fourth - and the one into a ``shout`` -
    a hard cut (a model's soft change is never replaced by a cut, nor made shorter).

    A scene change overlaps the next scene, which shortens the reel; each scene that gets (more)
    overlap is therefore made as much longer, so the reel is exactly as long as before and nothing
    inside a scene has to move."""
    n = len(scenes)
    if n < 4 or len({_kind(sc) for sc in scenes[:-1]}) != 1:
        return
    durs = [_num(sc.get("duration_sec")) for sc in scenes]
    if any(d is None or d <= 0 for d in durs):
        return
    here = [float(d) for d in durs if d is not None]
    k0 = random.Random(stable_int(seed, "enrich", "transitions")).randrange(3)
    plan: list[dict[str, Any] | None] = []
    for k, sc in enumerate(scenes[:-1]):
        punch = any(c.get("style") == "shout" for c in _dicts(scenes[k + 1].get("captions")))
        new = plan_transition(view, style, k0 + k, here[k], punch)
        old = sc.get("transition_out")
        old_ov = _overlap(old, here[k], here[k + 1])
        if new is not None and new["type"] != "cut" and isinstance(old, dict):
            old_d = _num(old.get("duration")) or 0.0
            cap = min(3.0, 0.4 * min(here[k], here[k + 1]))
            new = {**new, "duration": round(min(cap, max(float(new["duration"]), old_d)), 2)}
        if new is None or _overlap(new, here[k], here[k + 1]) < old_ov - 1e-9:
            new = None  # never less overlap than the model chose
        elif new["type"] == "cut" and _kind(sc) == "cut":
            new = None
        plan.append(new)
    final = [new["type"] if new else _kind(sc) for new, sc in zip(plan, scenes)]
    if len(set(final)) < 2:  # nothing to rotate to: leave the model's choice alone
        return
    longer = {
        k: round(
            here[k]
            + _overlap(new, here[k], here[k + 1])
            - _overlap(scenes[k].get("transition_out"), here[k], here[k + 1]),
            2,
        )
        for k, new in enumerate(plan)
        if new is not None
    }
    if any(d > MAX_SCENE_SEC_HARD for d in longer.values()):  # a scene cannot get longer: leave all
        return
    for k, new in enumerate(plan):
        if new is not None:
            scenes[k]["transition_out"] = new
            scenes[k]["duration_sec"] = longer[k]
            tally.transitions += 1
            tally.scenes.add(k)


# =============================================================================== captions
def _need(cap: dict[str, Any]) -> float:
    """Seconds a caption has to stay up to be read as fast as the voice-over says it."""
    return len(str(cap["text"]).split()) / SPEECH_WPS + READ_PAD_SEC


def _widen_captions(caps: list[dict[str, Any]], dur: float) -> int:
    """Lengthen captions that vanish before the voice-over is done; returns how many changed.

    Per caption style, in time order: a caption shorter than its need gets ``t1 = t0 + need`` and the
    captions after it move later if they would now overlap - if all of that still ends 0.3 s before
    the scene does.  Otherwise each caption just grows into whatever room it has before the next
    one (or the end).  Nothing ever gets shorter, and a second pass finds nothing left to do."""
    limit = dur - CAPTION_TAIL_SEC
    groups: dict[str, list[dict[str, Any]]] = {}
    for cap in caps:
        groups.setdefault(str(cap.get("style", "subtitle")), []).append(cap)
    changed = 0
    for group in groups.values():
        group.sort(key=lambda c: float(c["t0"]))
        plan = _cascade(group, limit) or _squeeze(group, limit)
        for cap, (t0, t1) in zip(group, plan):
            if abs(t0 - float(cap["t0"])) > 0.004 or abs(t1 - float(cap["t1"])) > 0.004:
                cap["t0"], cap["t1"] = t0, t1
                changed += 1
    return changed


def _cascade(group: list[dict[str, Any]], limit: float) -> list[tuple[float, float]] | None:
    out: list[tuple[float, float]] = []
    prev_end, grew = 0.0, False
    for cap in group:
        old0, old1 = float(cap["t0"]), float(cap["t1"])
        t0, t1 = old0, old1
        if grew and t0 < prev_end + CAPTION_GAP_SEC - 1e-6:
            t0 = prev_end + CAPTION_GAP_SEC
            t1 = t0 + (old1 - old0)
        if t1 - t0 < _need(cap) - CAPTION_TOLERANCE_SEC:
            t1 = t0 + _need(cap)
        t1 = max(t1, old1)
        moved = t0 > old0 + 0.004 or t1 > old1 + 0.004
        if not moved:  # keep the model's own numbers exactly
            t0, t1 = old0, old1
        else:
            t0, t1 = round(t0, 2), round(t1, 2)
            if t1 > limit + 1e-6:
                return None
        out.append((t0, t1))
        prev_end, grew = t1, moved
    return out


def _squeeze(group: list[dict[str, Any]], limit: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for k, cap in enumerate(group):
        t0, t1 = float(cap["t0"]), float(cap["t1"])
        if t1 - t0 < _need(cap) - CAPTION_TOLERANCE_SEC:
            nxt = next((float(g["t0"]) for g in group[k + 1 :] if float(g["t0"]) > t0), None)
            room = limit if nxt is None else min(limit, nxt - CAPTION_GAP_SEC)
            wider = min(t0 + _need(cap), room)
            if wider > t1 + 0.004:
                t1 = round(wider, 2)
        out.append((t0, t1))
    return out


# =============================================================================== acting
@dataclass
class _Beat:
    """One caption as a moment of the scene: who says it, who it is about, what happens."""

    start: float
    end: float
    text: str
    performer: str
    speaker: str | None
    named: list[str]
    cues: list[Cue]
    plural: bool
    shout: bool


def _is_resting(layer: dict[str, Any]) -> bool:
    acts = layer.get("actions")
    if acts is None:
        return True
    if not isinstance(acts, list):
        return False
    return all(isinstance(a, dict) and a.get("name") in RESTING for a in acts)


def _x_of(layer: dict[str, Any]) -> float:
    pos = layer.get("position")
    if isinstance(pos, list) and pos and _num(pos[0]) is not None:
        return float(pos[0])
    return SLOT_X.get(pos, 0.5) if isinstance(pos, str) else 0.5


def _beats(
    caps: list[dict[str, Any]], ids: list[str], cast: Cast, rng: random.Random
) -> list[_Beat]:
    """The captions that somebody can act out (a ``title`` is a heading), in time order."""
    offset = rng.randrange(max(1, len(ids)))  # who acts the lines nobody claims: take turns
    beats: list[_Beat] = []
    for cap in sorted(caps, key=lambda c: float(c["t0"])):
        style = cap.get("style", "subtitle")
        if style == "title" or not ids:
            continue
        text = str(cap["text"]).strip()
        spk = cap.get("speaker")
        speaker = spk if isinstance(spk, str) and spk in ids else None
        named: list[str] = []
        for _pos, cid in mentions(text, cast):
            if cid in ids and cid not in named:
                named.append(cid)
        performer = speaker or (named[0] if named else ids[(len(beats) + offset) % len(ids)])
        plural = speaker is None and len(ids) > 1 and bool(set(_words(text)[:6]) & PLURAL_WORDS)
        cues = [c for c in extract_cues(text, performer, cast) if not c.kind.startswith("mood:")]
        spoken = speaker is not None or text.endswith(("!", "?"))
        if spoken and _POINT_AT.search(text):  # "Look at that!": the speaker points
            focus = next((m for m in named if m != performer), None)
            cues = [c for c in cues if c.kind not in ("look", "point")][:1]
            cues.append(Cue("point", performer, "deictic" if focus else "flavor"))
        if style != "shout" and all(c.kind == "look" for c in cues):
            # a glance alone would leave the layer as static as it was: add a gesture
            cues = [*cues, flavor_cue(text, performer, rng, lively=True, glance=False)]
        beats.append(
            _Beat(
                float(cap["t0"]),
                float(cap["t1"]),
                text,
                performer,
                speaker,
                named,
                cues,
                plural,
                style == "shout",
            )
        )
    return beats


def _stage(
    layers: list[dict[str, Any]],
    beats: list[_Beat],
    arche: Mapping[str, str],
    view: CatalogView,
    rng: random.Random,
    dur: float,
    prev_ids: list[str] | None,
) -> list[list[ActionRequest]]:
    """What every layer should do, as requests - a function of the captions and the cast alone,
    whatever the layers already do (so a second pass wants the same things)."""
    ids = [str(ly["character"]) for ly in layers]
    first = {cid: i for i, cid in reversed(list(enumerate(ids)))}
    xs = [_x_of(ly) for ly in layers]
    reqs: list[list[ActionRequest]] = [[] for _ in layers]
    entered: dict[int, float] = {}
    newcomers = [i for i, cid in enumerate(ids) if prev_ids is None or cid not in prev_ids]
    for k, i in enumerate(newcomers):
        cid = ids[i]
        appears = next((b.start for b in beats if cid == b.speaker or cid in b.named), None)
        t0 = 0.15 + 0.55 * k
        if prev_ids is not None and appears is not None and appears > 1.4:
            t0 = max(0.15, appears - 0.5)  # walk in when the story brings them in
        req = enter_request(view, arche.get(cid), t0, rng)
        if req:
            reqs[i].append(req)
            entered[i] = t0 + 1.5
    for b in beats:
        home = first[b.performer]
        for cue in b.cues[:2]:
            actors = list(range(len(ids))) if b.plural else [first.get(cue.subject or "", home)]
            for i in actors:
                x_now = xs[i]
                if b.plural:
                    toward = 1.0
                elif x_now != 0.5:
                    toward = 1.0 if x_now < 0.5 else -1.0  # towards the middle of the frame
                else:
                    toward = 1.0 if rng.random() < 0.5 else -1.0
                focus = next((m for m in b.named if m != ids[i]), None)
                if focus is None and cue.kind == "look" and _UP.search(b.text):
                    focus = "up"
                req = plan_cue(
                    view,
                    cue,
                    start=b.start + 0.15,
                    span=max(1.0, b.end - b.start),
                    end=b.end,
                    x_now=x_now,
                    toward=toward,
                    target=focus,
                    last=False,
                )
                if req:
                    reqs[i].append(req)
        if b.shout:
            flourish = flourish_request(view, b.start + 0.15)
            if flourish:
                reqs[home].append(flourish)
        focus = b.speaker or b.performer
        if len(ids) > 1 and not b.plural:  # the others watch whoever is talking or doing
            for j, cid in enumerate(ids):
                if cid != focus and b.start + 0.1 >= entered.get(j, 0.0):
                    look = listener_request(view, focus, b.start + 0.1, b.end - b.start)
                    if look:
                        reqs[j].append(look)
    if not beats:  # nobody speaks: the cast ponders and waves, one after the other
        found = bridge_requests(view, ids, dur, rng, skip=("idle", "look_at"))
        for i, cid in enumerate(ids):
            for r in found.get(cid, []):
                r.t0 += 0.45 * i
                reqs[i].append(r)
    for i, arrived in entered.items():  # nothing happens before a newcomer has walked in
        for r in reqs[i]:
            if r.name != "enter_from":
                r.t0 = max(r.t0, arrived)
    return reqs


def _act(
    layers: list[dict[str, Any]],
    caps: list[dict[str, Any]],
    cast: Cast,
    arche: Mapping[str, str],
    view: CatalogView,
    rng: random.Random,
    dur: float,
    prev_ids: list[str] | None,
) -> int:
    """Give the standing-about layers of a scene something to do; returns how many actions it added.

    A layer is left alone as soon as it has a real action of its own (``RESTING`` lists the ones that
    do not count).  New actions go around the ones it has: gestures and the entrance wait for the
    body actions already there (a ``look_at`` may be overlapped, it only turns the head), and a layer
    that already looks somewhere gets no further look_at - which is also what makes a second pass add
    nothing."""
    if not layers:
        return 0
    ids = [str(ly["character"]) for ly in layers]
    plan = _stage(layers, _beats(caps, ids, cast, rng), arche, view, rng, dur, prev_ids)
    added = 0
    for layer, wants in zip(layers, plan):
        if not _is_resting(layer):
            continue
        existing = [a for a in _dicts(layer.get("actions")) if _timed(a)]
        have = {a.get("name") for a in existing}
        busy = [
            (float(a["t0"]), float(a["t1"])) for a in existing if a.get("name") not in OVERLAPPABLE
        ]
        body = [
            r
            for r in wants
            if r.name != "look_at" and not (r.name == "enter_from" and "enter_from" in have)
        ]
        looks = [] if "look_at" in have else [r for r in wants if r.name == "look_at"]
        placed = schedule_requests(view, body, dur, busy) + schedule_requests(view, looks, dur)
        if placed:
            layer["actions"] = sorted(
                [*_dicts(layer.get("actions")), *placed], key=lambda a: _num(a.get("t0")) or 0.0
            )
            added += len(placed)
    return added


def _timed(a: dict[str, Any]) -> bool:
    return _num(a.get("t0")) is not None and _num(a.get("t1")) is not None


# =============================================================================== camera and sound
def _add_camera(
    sc: dict[str, Any], view: CatalogView, index: int, caps: list[dict[str, Any]], dur: float
) -> int:
    cam = sc.get("camera")
    if cam is not None and not isinstance(cam, dict):
        return 0
    if cam and cam.get("moves"):
        return 0
    moves = plan_camera(view, dur, index, any(c.get("style") == "shout" for c in caps))
    if not moves:
        return 0
    sc["camera"] = {**(cam or {}), "moves": moves}
    return len(moves)


def _add_sfx(
    sc: dict[str, Any],
    layers: list[dict[str, Any]],
    caps: list[dict[str, Any]],
    view: CatalogView,
    prev: dict[str, Any] | None,
    dur: float,
) -> int:
    if sc.get("sfx"):
        return 0
    seen = [{"actions": [a for a in _dicts(ly.get("actions")) if _timed(a)]} for ly in layers]
    heard = [{"style": c.get("style", "subtitle"), "t0": float(c["t0"])} for c in caps]
    before = _kind(prev) if prev is not None else None
    picks = _two(plan_sfx(view, seen, heard, dur, before, limit=6))
    if not picks:
        return 0
    sc["sfx"] = picks
    return len(picks)


def _two(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """At most two cues: the first, and a second one at least half a second away from it that
    sounds different from it if there is such a thing (two sounds on top of each other are a mess)."""
    if len(found) <= 1:
        return found
    head = found[0]
    apart = [s for s in found[1:] if abs(s["t"] - head["t"]) >= 0.5]
    if not apart:
        return [head]
    other = max(apart, key=lambda s: (s["name"] != head["name"], abs(s["t"] - head["t"])))
    return sorted([head, other], key=lambda s: s["t"])


__all__ = ["OVERLAPPABLE", "RESTING", "enrich_spec"]
