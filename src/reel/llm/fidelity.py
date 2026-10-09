"""Keep the words of the reel the words of the script.

A model asked to turn a script into scenes paraphrases, shortens, drops lines and invents new ones, and puts a character's
*name* where the line belongs.  The captions (and, with a voice-over, everything that is said) then no longer follow the
script.  :class:`ScriptLock` compares the captions of a spec with the script and brings them back, deterministically:

* a caption that is (most of) a stretch of the script is rewritten to that stretch *exactly*: a word the model dropped is
  put back, a word it added is removed, and the speaker is the one the script names;
* a caption that holds only a speaker's label (``"Mia:"``) or words the script does not have is removed;
* every line of the script that no caption shows is added: beside the caption before it when they share a scene, otherwise
  as a short scene of its own after that caption's scene (the same set, the same characters standing there);
* the result reads the script from its first line to its last, in order, and nothing else.

The scenes, sets, gestures and camera stay the model's.  Only the words are the script's.  A script that cannot be shown
word for word in a reel (it would take longer than the budget to read) is left to the model to condense: ``fits()`` says so.
"""

from __future__ import annotations

import copy
import difflib
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from reel.core.catalog import CATALOG, Catalog
from reel.llm.base import READ_WPS, guess_archetype

# ------------------------------------------------------------------------------------------------ tokens
_JOINERS = dict.fromkeys(map(ord, "​‌‍­﻿"))
#: scripts written without spaces between words: every character is a word of its own (with its combining marks)
_PER_CHAR = re.compile("[⺀-鿿぀-ヿ豈-﫿฀-໿က-႟ក-៿]")
_APOSTROPHES = "'’"


def _word_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LMN" and ord(ch) not in _JOINERS


def _norm(word: str) -> str:
    return unicodedata.normalize("NFKC", word.translate(_JOINERS)).casefold().replace("’", "'")


@dataclass(frozen=True)
class Tok:
    """One word of a text: its normalised form and where it sits in the original string."""

    text: str
    start: int
    end: int


def tokens(text: str) -> list[Tok]:
    """Words of ``text`` in any script: letters, combining marks and digits make a word (so a Devanagari word with its vowel
    signs stays whole); punctuation, spaces and zero-width joiners separate them.  Scripts without spaces give one token per
    character."""
    out: list[Tok] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if not _word_char(ch):
            i += 1
            continue
        j = i + 1
        if _PER_CHAR.match(ch):
            while j < n and unicodedata.category(text[j])[0] == "M":
                j += 1
        else:
            while j < n:
                c = text[j]
                if _PER_CHAR.match(c):
                    break
                inside = (
                    (c in _APOSTROPHES or ord(c) in _JOINERS)
                    and j + 1 < n
                    and _word_char(text[j + 1])
                )
                if _word_char(c) or inside:
                    j += 1
                else:
                    break
        word = _norm(text[i:j])
        if word:
            out.append(Tok(word, i, j))
        i = j
    return out


def _words(toks: list[Tok]) -> int:
    """Words for pacing: one per token, except that two characters of an unspaced script count as one."""
    per = sum(1 for t in toks if _PER_CHAR.match(t.text))
    return len(toks) - per + math.ceil(per / 2)


def read_sec(toks: list[Tok]) -> float:
    """Comfortable on-screen time of a caption: about 2.6 words a second plus a beat."""
    return max(1.3, _words(toks) / READ_WPS + 0.4)


# ------------------------------------------------------------------------------------------------ the script
@dataclass
class Unit:
    """One caption's worth of the script: the title, a sentence of narration, or a sentence somebody says."""

    index: int
    kind: str  # title | narration | dialogue
    text: str
    speaker: str | None  # as the script writes the name
    toks: list[Tok]
    first: int = 0  # global index of its first token


@dataclass
class _Cap:
    scene: dict[str, Any]
    cap: dict[str, Any]
    text: str
    toks: list[Tok]
    label: bool
    lo: int = -1  # the stretch of the script it shows (token indices), once matched
    hi: int = -1
    slot: bool = (
        False  # not the script's words: it only holds the place of the next line its speaker says
    )


@dataclass
class _Add:
    """A stretch of the script that no caption shows."""

    lo: int
    hi: int
    text: str
    unit: Unit


@dataclass
class LockReport:
    """What :meth:`ScriptLock.apply` did (all zero: the captions already were the script)."""

    snapped: int = 0  # captions rewritten to the script's own words
    added: int = 0  # lines of the script that no caption showed, now captions
    removed: int = 0  # captions that were labels or words the script does not have
    new_scenes: int = 0
    dropped_scenes: int = 0  # empty scenes left after the end of the script
    covered: float = 1.0  # share of the script's words the captions showed before the fix

    def changed(self) -> bool:
        return bool(self.snapped or self.added or self.removed)

    def notes(self) -> list[str]:
        if not self.changed():
            return []
        bits = []
        if self.snapped:
            bits.append(f"{self.snapped} caption(s) put back to the script's own words")
        if self.added:
            bits.append(f"{self.added} line(s) of the script that no caption showed added")
        if self.removed:
            bits.append(f"{self.removed} caption(s) that were not in the script removed")
        extra = f" ({self.new_scenes} new scene(s))" if self.new_scenes else ""
        if self.dropped_scenes:
            extra += f" (left out {self.dropped_scenes} scene(s) that would have played after the story ended)"
        return [
            f"kept the script word for word: {'; '.join(bits)}{extra}; the captions covered "
            f"{round(100 * self.covered)}% of it before"
        ]


def script_units(script: str) -> list[Unit]:
    """The script as the lines that have to be shown, in order (a title first when it has one)."""
    from reel.llm.heuristic import (
        parse_script,
    )  # here: the planner imports the generator, which imports this module

    parsed = parse_script(script)
    units: list[Unit] = []
    if parsed.title:
        units.append(Unit(0, "title", parsed.title.strip(), None, tokens(parsed.title)))
    for s in parsed.sents:
        text = (s.text or "").strip()
        toks = tokens(text)
        if not toks:
            continue
        kind = "dialogue" if s.kind == "dialogue" and s.speaker_name else "narration"
        units.append(
            Unit(len(units), kind, text, s.speaker_name if kind == "dialogue" else None, toks)
        )
    first = 0
    for u in units:
        u.first = first
        first += len(u.toks)
    return units


# ------------------------------------------------------------------------------------------------ the lock
class ScriptLock:
    """Compare a spec's captions with a script and bring them back to it (see the module docstring)."""

    #: longest a script may take to read for it to be shown word for word (the reel is 45-60 s, scene changes take some)
    MAX_READING_SEC = 55.0

    def __init__(self, script: str, catalog: Catalog | None = None) -> None:
        self.catalog = catalog or CATALOG
        self.units = script_units(script)
        self.script_tokens = [t.text for u in self.units for t in u.toks]
        self._owner = [u.index for u in self.units for _ in u.toks]

    # -- what the script is ---------------------------------------------------------------------------
    @property
    def reading_sec(self) -> float:
        return sum(read_sec(u.toks) + 0.15 for u in self.units)

    @property
    def has_title(self) -> bool:
        return any(u.kind == "title" for u in self.units)

    def fits(self) -> bool:
        """Whether the whole script can be read in a reel (a longer one has to be condensed, not copied)."""
        return bool(self.units) and self.reading_sec <= self.MAX_READING_SEC

    def speakers(self) -> list[str]:
        """Names of the people who speak, in order of first appearance."""
        seen: list[str] = []
        for u in self.units:
            if u.speaker and u.speaker not in seen:
                seen.append(u.speaker)
        return seen

    # -- reading a spec -------------------------------------------------------------------------------
    def _caps(self, spec: dict[str, Any]) -> list[_Cap]:
        names = {_norm(n) for n in self.speakers()}
        for c in spec.get("characters") or []:
            if isinstance(c, dict):
                names |= {_norm(str(c.get("id", ""))), _norm(str(c.get("name", "")))}
        names.discard("")
        out: list[_Cap] = []
        for sc in spec.get("scenes") or []:
            if not isinstance(sc, dict):
                continue
            caps = [c for c in sc.get("captions") or [] if isinstance(c, dict)]
            for c in sorted(caps, key=lambda c: float(c.get("t0") or 0.0)):
                text = str(c.get("text") or "")
                toks = tokens(text)
                stripped = text.strip()
                label = bool(toks) and (
                    (stripped.endswith((":", "：")) and len(toks) <= 3)
                    or (len(toks) <= 2 and " ".join(t.text for t in toks) in names)
                )
                out.append(_Cap(sc, c, text, toks, label))
        return out

    def _match(self, caps: list[_Cap], ids: dict[str, str]) -> None:
        """Set ``lo``/``hi`` on every caption that shows a stretch of the script (the rest keep -1).

        First the captions that already are the script (or most of it); then the ones that are not (a label, a paraphrase,
        an invented line) become *slots*: a caption said by Pip takes Pip's next line of the script, a narration caption the
        next line of narration.  The model's scenes keep their place in the story, with the script's own words in them."""
        stream = [t.text for c in caps for t in c.toks]
        owner = [k for k, c in enumerate(caps) for _ in c.toks]
        hits: list[list[int]] = [[] for _ in caps]
        if stream and self.script_tokens:
            sm = difflib.SequenceMatcher(None, self.script_tokens, stream, autojunk=False)
            for i, j, n in sm.get_matching_blocks():
                for d in range(n):
                    hits[owner[j + d]].append(i + d)
        cursor = 0
        for cap, found in zip(caps, hits, strict=True):
            if cap.label or not cap.toks or not found:
                continue
            run = _longest_run(found)
            n = len(cap.toks)
            if n == 1:
                # one word is only the caption if the script's line is that word (not any line that contains it)
                if len(self.units[self._owner[run[0]]].toks) > 2:
                    continue
            elif len(run) < max(2, math.ceil(0.5 * n)):
                continue
            lo, hi = max(run[0], cursor), run[-1]
            if hi < lo:  # a repeat of something already shown
                continue
            cap.lo, cap.hi = lo, hi
            cursor = hi + 1
        self._slots(caps, {v: k for k, v in ids.items()})

    def _slots(self, caps: list[_Cap], name_of: dict[str, str]) -> None:
        prev_hi = -1
        for k, c in enumerate(caps):
            if c.lo >= 0:
                prev_hi = c.hi
                continue
            if c.cap.get("style") == "title" or not (c.toks or c.label):
                continue
            upper = next((x.lo for x in caps[k + 1 :] if x.lo >= 0), len(self.script_tokens))
            who = name_of.get(str(c.cap.get("speaker") or ""))
            if c.cap.get("speaker") and who is None:
                continue  # said by somebody the script does not know
            u = next(
                (
                    u
                    for u in self.units[
                        self._owner[prev_hi + 1]
                        if prev_hi + 1 < len(self._owner)
                        else len(self.units) :
                    ]
                    if u.kind != "title"
                    and u.first + len(u.toks) <= upper
                    and (
                        u.kind == "dialogue" and u.speaker == who if who else u.kind == "narration"
                    )
                ),
                None,
            )
            if u is None:
                continue
            last, words = u, len(u.toks)
            for nxt in self.units[u.index + 1 :]:
                if (
                    nxt.kind != u.kind
                    or nxt.speaker != u.speaker
                    or nxt.first + len(nxt.toks) > upper
                    or words + len(nxt.toks) > 14
                ):
                    break
                last, words = nxt, words + len(nxt.toks)
            c.lo, c.hi = u.first, last.first + len(last.toks) - 1
            c.label, c.slot = False, True
            prev_hi = c.hi

    def _covered(self, caps: list[_Cap], *, slots: bool = True) -> set[int]:
        """Script words that captions show; ``slots=False``: only those the captions *already* had right."""
        done: set[int] = set()
        for c in caps:
            if c.lo >= 0 and (slots or not c.slot):
                done.update(range(c.lo, c.hi + 1))
        return done

    def _runs(self, done: set[int]) -> list[tuple[int, int]]:
        """Stretches of the script that are not shown, one run never crossing from a line into the next."""
        runs: list[tuple[int, int]] = []
        for u in self.units:
            start: int | None = None
            for k in range(len(u.toks)):
                g = u.first + k
                if g in done:
                    if start is not None:
                        runs.append((start, g - 1))
                        start = None
                elif start is None:
                    start = g
            if start is not None:
                runs.append((start, u.first + len(u.toks) - 1))
        return runs

    def _render(self, lo: int, hi: int) -> str:
        """The script's own text for tokens ``lo..hi``: per line, from the first word's start to the last word's end (and the
        sentence's closing punctuation when the stretch reaches the end of the line)."""
        parts: list[str] = []
        for u in self.units:
            a, b = max(lo, u.first), min(hi, u.first + len(u.toks) - 1)
            if a > b:
                continue
            first, last = u.toks[a - u.first], u.toks[b - u.first]
            end = len(u.text) if b == u.first + len(u.toks) - 1 else last.end
            parts.append(u.text[first.start : end].strip())
        return " ".join(parts)

    def _analyse(
        self, spec: dict[str, Any]
    ) -> tuple[list[_Cap], list[_Add], float, dict[str, str]]:
        caps = self._caps(spec)
        ids = self._speaker_ids(spec, caps)
        self._match(caps, ids)
        # a word or two the model dropped at the edge of a line joins the caption beside it instead of a caption of its own
        by_lo = {c.lo: c for c in caps if c.lo >= 0}
        by_hi = {c.hi: c for c in caps if c.lo >= 0}
        for lo, hi in self._runs(self._covered(caps)):
            if hi - lo >= 2 or self._owner[lo] != self._owner[hi]:
                continue
            left, right = by_hi.get(lo - 1), by_lo.get(hi + 1)
            if left is not None and self._owner[lo - 1] == self._owner[lo]:
                by_hi.pop(left.hi)
                left.hi = hi
                by_hi[hi] = left
            elif right is not None and self._owner[hi + 1] == self._owner[hi]:
                by_lo.pop(right.lo)
                right.lo = lo
                by_lo[lo] = right
        done = self._covered(caps)
        total = len(self.script_tokens)
        covered = len(self._covered(caps, slots=False)) / total if total else 1.0
        adds = [
            _Add(lo, hi, self._render(lo, hi), self.units[self._owner[lo]])
            for lo, hi in self._runs(done)
        ]
        return caps, adds, covered, ids

    # -- how well a spec follows the script -----------------------------------------------------------
    def coverage(self, spec: dict[str, Any]) -> dict[str, Any]:
        """What the captions of ``spec`` do with the script: the share of its words shown, the lines that are missing, and
        the captions that are labels or not in the script.  Nothing is changed."""
        caps, adds, covered, _ids = self._analyse(copy.deepcopy(spec))
        return {
            "covered": round(covered, 4),
            "units": len(self.units),
            "words": len(self.script_tokens),
            "missing": [
                {"text": a.text, "speaker": a.unit.speaker, "kind": a.unit.kind} for a in adds
            ],
            "labels": sum(1 for c in caps if c.label),
            "foreign": sum(
                1
                for c in caps
                if (c.lo < 0 or c.slot)
                and not c.label
                and c.toks
                and not (c.cap.get("style") == "title" and not self.has_title)
            ),
            "ok": covered >= 0.999
            and not any(
                (c.lo < 0 or c.slot)
                and c.toks
                and not (c.cap.get("style") == "title" and not self.has_title)
                for c in caps
            ),
            "fits": self.fits(),
        }

    # -- putting a spec right -------------------------------------------------------------------------
    def apply(self, spec: dict[str, Any]) -> tuple[dict[str, Any], LockReport]:
        """A copy of ``spec`` whose captions are the script (see the module docstring), and what was done."""
        out = copy.deepcopy(spec)
        scenes: list[dict[str, Any]] = [s for s in out.get("scenes") or [] if isinstance(s, dict)]
        out["scenes"] = scenes
        for sc in scenes:
            sc["captions"] = [c for c in sc.get("captions") or [] if isinstance(c, dict)]
        caps, adds, covered, ids = self._analyse(out)
        report = LockReport(covered=covered)
        self._cast(out, ids)
        emptied: set[int] = set()  # id(scene dict) of scenes that lost their only captions
        order: dict[int, int] = {}  # id(caption dict) -> where its words sit in the script
        touched: set[int] = set()  # id(scene dict) of scenes that got new lines

        # 1. the captions that show the script now show it exactly; the others go
        keep: list[_Cap] = []
        for c in caps:
            if c.lo < 0:
                if c.cap.get("style") == "title" and not self.has_title:
                    continue  # the script has no title line: the model's headline stays
                _remove(c.scene["captions"], c.cap)
                if c.toks:
                    report.removed += 1
                if not c.scene["captions"]:
                    emptied.add(id(c.scene))
                continue
            text = self._render(c.lo, c.hi)
            unit = self.units[self._owner[c.lo]]
            if text != c.text:
                c.cap["text"] = text
                report.snapped += 1
            self._set_speaker(c.cap, unit, ids)
            c.cap["style"] = (
                "title"
                if unit.kind == "title"
                else (
                    "subtitle" if c.cap.get("style") == "title" else c.cap.get("style", "subtitle")
                )
            )
            order[id(c.cap)] = c.lo
            keep.append(c)

        # 2. the lines nobody shows are added where they belong
        for group in self._groups([a for a in adds if a.unit.kind != "title"], keep):
            self._place(group, keep, scenes, ids, out, report, touched, order)
        for a in [
            a for a in adds if a.unit.kind == "title"
        ]:  # first of all: after the scenes that were added
            self._add_title(a, scenes, ids, order)
            report.added += 1

        # 3. scenes that got new lines read them one after the other
        for sc in scenes:
            if id(sc) in touched or any(_too_fast(c) for c in sc["captions"]):
                _retime(sc, order)
            sc["captions"] = sorted(sc["captions"], key=lambda c: float(c.get("t0") or 0.0))
            _tidy_talk(sc)
        # scenes whose (invented) captions were all taken out and that come after the last line of the script would
        # play after the story has ended
        # (a script too short to fill the reel keeps them: they are what makes it long enough)
        while (
            scenes
            and id(scenes[-1]) in emptied
            and not scenes[-1]["captions"]
            and _total(scenes[:-1]) >= MIN_KEPT_SEC
        ):
            scenes.pop()
            report.dropped_scenes += 1
        _renumber(scenes)
        return out, report

    # -- who says what ------------------------------------------------------------------------------
    def _speaker_ids(self, spec: dict[str, Any], caps: list[_Cap]) -> dict[str, str]:
        """Script speaker name -> character id: by ``name`` or id, by the label captions the model wrote ("Mia:" said by
        ``mia``), then in order of first appearance."""
        chars = [c for c in spec.get("characters") or [] if isinstance(c, dict) and c.get("id")]
        ids = {str(c["id"]) for c in chars}
        by_name: dict[str, str] = {}
        for c in chars:
            for key in (str(c.get("id", "")), str(c.get("name", ""))):
                if key:
                    by_name.setdefault(_norm(key), str(c["id"]))
        learned: dict[str, str] = {}
        for cap in caps:
            who = cap.cap.get("speaker")
            if cap.label and isinstance(who, str) and who in ids and cap.toks:
                learned.setdefault(" ".join(t.text for t in cap.toks), who)
        out: dict[str, str] = {}
        for name in self.speakers():
            hit = by_name.get(_norm(name)) or learned.get(" ".join(t.text for t in tokens(name)))
            if hit:
                out[name] = hit
        # the cast usually lists people in the order they first speak
        free = [str(c["id"]) for c in chars if str(c["id"]) not in out.values()]
        for name in self.speakers():
            if name not in out and free:
                out[name] = free.pop(0)
        return out

    def _cast(self, spec: dict[str, Any], ids: dict[str, str]) -> None:
        """Characters carry the name the script gives them (the studio shows it); a speaker the cast lacks joins it."""
        chars = spec.setdefault("characters", [])
        have = {str(c.get("id")): c for c in chars if isinstance(c, dict)}
        available = set(self.catalog.archetypes.names())
        for name in self.speakers():
            if name in ids:
                if not have[ids[name]].get("name"):
                    have[ids[name]]["name"] = name
                continue
            if "everyman" not in available and not available:
                continue
            cid = _fresh_id(_slug(name), set(have))
            arch = guess_archetype([w.text for w in tokens(name)], available)
            chars.append(
                {
                    "id": cid,
                    "archetype": arch if arch in available else sorted(available)[0],
                    "name": name,
                }
            )
            have[cid] = chars[-1]
            ids[name] = cid

    def _set_speaker(self, cap: dict[str, Any], unit: Unit, ids: dict[str, str]) -> None:
        if unit.kind == "dialogue" and unit.speaker and unit.speaker in ids:
            cap["speaker"] = ids[unit.speaker]
        else:
            cap.pop("speaker", None)

    # -- where the missing lines go -------------------------------------------------------------------
    def _groups(self, adds: list[_Add], keep: list[_Cap]) -> list[list[_Add]]:
        """Missing stretches that sit between the same two captions go together."""
        groups: list[list[_Add]] = []
        last: tuple[int, int] | None = None
        for a in adds:
            prev = max((k for k, c in enumerate(keep) if c.hi < a.lo), default=-1)
            nxt = min((k for k, c in enumerate(keep) if c.lo > a.hi), default=len(keep))
            if last == (prev, nxt) and groups:
                groups[-1].append(a)
            else:
                groups.append([a])
            last = (prev, nxt)
        return groups

    def _caption(self, a: _Add, ids: dict[str, str]) -> dict[str, Any]:
        cap: dict[str, Any] = {
            "text": a.text,
            "t0": 0.5,
            "t1": round(0.5 + read_sec(tokens(a.text)), 3),
            "style": "title" if a.unit.kind == "title" else "subtitle",
        }
        self._set_speaker(cap, a.unit, ids)
        return cap

    def _add_title(
        self, a: _Add, scenes: list[dict[str, Any]], ids: dict[str, str], order: dict[int, int]
    ) -> None:
        """The title card sits at the start of the first scene."""
        if not scenes:
            return
        cap = self._caption(a, ids)
        dur = float(scenes[0].get("duration_sec") or 5.0)
        cap["t0"], cap["t1"] = 0.4, round(min(3.0, max(1.4, dur - 0.3)), 3)
        scenes[0]["captions"].append(cap)
        order[id(cap)] = a.lo

    def _place(
        self,
        group: list[_Add],
        keep: list[_Cap],
        scenes: list[dict[str, Any]],
        ids: dict[str, str],
        spec: dict[str, Any],
        report: LockReport,
        touched: set[int],
        order: dict[int, int],
    ) -> None:
        prev = max((c for c in keep if c.hi < group[0].lo), key=lambda c: c.hi, default=None)
        nxt = min((c for c in keep if c.lo > group[-1].hi), key=lambda c: c.lo, default=None)
        new = [(a, self._caption(a, ids)) for a in group]
        report.added += len(new)
        for a, cap in new:
            order[id(cap)] = a.lo
        if (
            prev is not None and nxt is not None and prev.scene is nxt.scene
        ):  # between two captions of one scene
            prev.scene["captions"].extend(cap for _, cap in new)
            touched.add(id(prev.scene))
            return
        anchor = prev.scene if prev is not None else (nxt.scene if nxt is not None else None)
        at = (
            (_index(scenes, anchor) + (1 if prev is not None else 0))
            if anchor is not None
            else len(scenes)
        )
        batch: list[tuple[_Add, dict[str, Any]]] = []
        for pair in new:
            batch.append(pair)
            if len(batch) >= 3 or sum(read_sec(tokens(c["text"])) for _, c in batch) >= 8.0:
                scenes.insert(at, self._scene(batch, anchor, scenes, spec, ids))
                touched.add(id(scenes[at]))
                at += 1
                report.new_scenes += 1
                batch = []
        if batch:
            scenes.insert(at, self._scene(batch, anchor, scenes, spec, ids))
            touched.add(id(scenes[at]))
            report.new_scenes += 1

    def _scene(
        self,
        batch: list[tuple[_Add, dict[str, Any]]],
        anchor: dict[str, Any] | None,
        scenes: list[dict[str, Any]],
        spec: dict[str, Any],
        ids: dict[str, str],
    ) -> dict[str, Any]:
        """A scene that only says these lines: the anchor's set, with the people who were standing there."""
        base = anchor if anchor is not None else (scenes[0] if scenes else {})
        dur = round(0.5 + sum(read_sec(tokens(c["text"])) + 0.15 for _, c in batch) + 0.6, 3)
        idle = lambda: [{"name": "idle", "t0": 0.0, "t1": dur}]  # noqa: E731
        layers = copy.deepcopy([ly for ly in base.get("layers") or [] if isinstance(ly, dict)])
        for ly in layers:
            ly["actions"] = idle()
        have = {str(ly.get("character")) for ly in layers}
        used = {str(ly.get("position")) for ly in layers}
        for a, _ in batch:
            cid = ids.get(a.unit.speaker or "")
            if cid and cid not in have:
                spot = next((s for s in ("left", "right", "center") if s not in used), "center")
                layers.append({"character": cid, "position": spot, "actions": idle()})
                have.add(cid)
                used.add(spot)
        chars = [c for c in spec.get("characters") or [] if isinstance(c, dict) and c.get("id")]
        if not layers and chars:
            layers.append({"character": chars[0]["id"], "position": "center", "actions": idle()})
        return {
            "id": "",
            "duration_sec": dur,
            "background": copy.deepcopy(
                base.get("background") or {"template": "abstract", "params": {}}
            ),
            "layers": layers,
            "captions": [c for _, c in batch],
            "transition_out": {"type": "cut", "duration": 0.0},
        }


# ------------------------------------------------------------------------------------------------ helpers
def _longest_run(found: list[int]) -> list[int]:
    """The longest stretch of matched script positions with no more than two words skipped between them."""
    best: list[int] = []
    cur: list[int] = []
    for x in found:
        if cur and x - cur[-1] > 3:
            if len(cur) > len(best):
                best = cur
            cur = []
        cur.append(x)
    return cur if len(cur) > len(best) else best


def _index(scenes: list[dict[str, Any]], target: dict[str, Any]) -> int:
    return next(i for i, s in enumerate(scenes) if s is target)


#: the reel is 45-60 s: scenes are only left out while at least this much remains
MIN_KEPT_SEC = 50.0


def _total(scenes: list[dict[str, Any]]) -> float:
    """Length of a reel made of these scenes: their durations minus the overlaps of the transitions between them."""
    durs = [float(s.get("duration_sec") or 0.0) for s in scenes]
    total = sum(durs)
    for k, sc in enumerate(scenes[:-1]):
        tr: dict[str, Any] = (
            sc["transition_out"] if isinstance(sc.get("transition_out"), dict) else {}
        )
        over = 0.0 if tr.get("type") in (None, "cut") else float(tr.get("duration") or 0.0)
        total -= min(over, durs[k], durs[k + 1])
    return total


def _too_fast(cap: dict[str, Any]) -> bool:
    """A caption whose words need clearly more time than its window gives them (the model timed other words)."""
    have = float(cap.get("t1") or 0.0) - float(cap.get("t0") or 0.0)
    return read_sec(tokens(str(cap.get("text") or ""))) > have * 1.1 + 0.05


def _remove(items: list[dict[str, Any]], target: dict[str, Any]) -> None:
    for k, x in enumerate(items):
        if x is target:
            del items[k]
            return


def _retime(sc: dict[str, Any], order: dict[int, int]) -> None:
    """Captions of a scene that changed read one after the other (script order), each for the time it needs."""
    caps = sorted(sc["captions"], key=lambda c: order.get(id(c), 0))
    titles = [c for c in caps if c.get("style") == "title"]
    plain = [c for c in caps if c.get("style") != "title"]
    t = 0.5
    for c in plain:
        need = read_sec(tokens(str(c.get("text") or "")))
        have = float(c.get("t1") or 0) - float(c.get("t0") or 0)
        span = max(need, min(have, need * 1.4)) if have > 0 else need
        c["t0"], c["t1"] = round(t, 3), round(t + span, 3)
        t += span + 0.15
    if t + 0.5 > float(sc.get("duration_sec") or 0.0):
        sc["duration_sec"] = round(t + 0.5, 3)
    sc["captions"] = [*titles, *plain]


def _tidy_talk(sc: dict[str, Any]) -> None:
    """A ``talk`` gesture speaks what its character's caption says; one that speaks words no caption shows is dropped."""
    caps = [c for c in sc.get("captions") or [] if isinstance(c, dict)]
    for ly in sc.get("layers") or []:
        if not isinstance(ly, dict):
            continue
        keep = []
        for a in ly.get("actions") or []:
            if not isinstance(a, dict) or a.get("name") != "talk":
                keep.append(a)
                continue
            params: dict[str, Any] = a["params"] if isinstance(a.get("params"), dict) else {}
            if not params.get("text"):
                keep.append(a)
                continue
            t0, t1 = float(a.get("t0") or 0.0), float(a.get("t1") or 0.0)
            mine = [
                c
                for c in caps
                if c.get("speaker") == ly.get("character")
                and min(t1, float(c.get("t1") or 0.0)) - max(t0, float(c.get("t0") or 0.0)) > 0.2
            ]
            if mine:
                params["text"] = str(mine[0]["text"])
                a["params"] = params
                keep.append(a)
        ly["actions"] = keep


def _renumber(scenes: list[dict[str, Any]]) -> None:
    """Scene ids stay ``s01, s02, ...`` in order when that is how the model named them (new scenes get theirs)."""
    ids = [str(s.get("id") or "") for s in scenes]
    if all(re.fullmatch(r"s\d+", i) or not i for i in ids):
        width = max(2, len(str(len(scenes))))
        for k, s in enumerate(scenes, 1):
            s["id"] = f"s{k:0{width}d}"
    else:
        taken = {i for i in ids if i}
        for s in scenes:
            if not s.get("id"):
                s["id"] = _fresh_id("s", taken)
                taken.add(s["id"])


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_") or "char"


def _fresh_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    k = 2
    while f"{base}_{k}" in taken:
        k += 1
    return f"{base}_{k}"


__all__ = ["LockReport", "ScriptLock", "Tok", "Unit", "read_sec", "script_units", "tokens"]
