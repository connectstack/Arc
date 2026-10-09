"""What a script needs that the library has, and what it lacks.

Reading a script before anything is planned, this finds the characters, places and objects it mentions and sorts them:

* **covered**: the library (or the engine's own sets, creatures and props) can draw it: "the car" -> ``car``
* **missing**: it is something a story would show, and nothing draws it: "a dragon", "the castle"

The words come from two places.  The library names itself through every asset's tags (so whatever a user adds is found
the same way as what ships).  What is *missing* needs a general vocabulary of everyday visual things, independent of the
library: ``lexicon.json`` (English, Hindi in Devanagari and Hinglish).  A lexicon entry counts as covered when any of its
words names a library asset, so a script that says "kutta" is covered by a ``dog`` asset tagged only "dog".

This is advice for the person writing the script, not a gate: a word the lexicon does not know is never reported, and a
report can be ignored.  The planners record what they actually had to leave out in ``meta.library_gaps``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from reel.assets.match import TagIndex, asset_index
from reel.core.catalog import CATALOG, Catalog

LEXICON_FILE = Path(__file__).with_name("lexicon.json")
_KINDS = ("character", "object", "place")
_SENTENCE_END = re.compile(r"[.!?\n।]")
MAX_MENTIONS = 6


@dataclass(frozen=True)
class LexEntry:
    """One everyday visual thing: its canonical name and every word a script uses for it."""

    name: str
    kind: str
    tags: tuple[str, ...]
    origin: str = "lexicon"


@lru_cache(maxsize=1)
def load_lexicon() -> tuple[LexEntry, ...]:
    """The shipped vocabulary (empty if the file is missing or unreadable: the report then lists nothing as missing)."""
    try:
        data = json.loads(LEXICON_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    out: list[LexEntry] = []
    for e in data.get("entries", []) if isinstance(data, dict) else []:
        if not isinstance(e, dict) or e.get("kind") not in _KINDS or not e.get("name"):
            continue
        words = tuple(str(w) for w in e.get("words", []) if str(w).strip())
        out.append(
            LexEntry(str(e["name"]), str(e["kind"]), (str(e["name"]).replace("_", " "), *words))
        )
    return tuple(out)


def _engine_entries() -> tuple[LexEntry, ...]:
    from reel.llm.heuristic import engine_vocabulary

    return tuple(LexEntry(n, k, w, "engine") for n, k, w in engine_vocabulary())


@dataclass
class Covered:
    asset: str  # the library asset (or engine set) that draws it
    kind: str
    source: str  # builtin | user | engine
    words: list[str] = field(default_factory=list)  # as the script wrote them
    count: int = 0
    at: list[tuple[int, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "kind": self.kind,
            "source": self.source,
            "words": self.words,
            "count": self.count,
        }


@dataclass
class Missing:
    name: str  # what to call the asset when adding it
    kind: str
    words: list[str] = field(default_factory=list)
    count: int = 0
    snippet: str = ""
    at: list[tuple[int, int]] = field(default_factory=list)
    tags: list[str] = field(
        default_factory=list
    )  # every word the lexicon knows for it: the tags to give the new asset

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "words": self.words,
            "count": self.count,
            "snippet": self.snippet,
            "at": [list(a) for a in self.at],
            "tags": self.tags,
        }


@dataclass
class Coverage:
    covered: list[Covered] = field(default_factory=list)
    missing: list[Missing] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered": [c.to_dict() for c in self.covered],
            "missing": [m.to_dict() for m in self.missing],
        }


def snippet(text: str, start: int, end: int, width: int = 90) -> str:
    """The sentence a mention sits in, shortened to about ``width`` characters around it."""
    a = start
    while a > 0 and not _SENTENCE_END.match(text[a - 1]):
        a -= 1
    m = _SENTENCE_END.search(text, end)
    b = m.start() if m else len(text)
    line = " ".join(text[a:b].split())
    if len(line) <= width:
        return line
    rel = max(0, start - a)
    lo = max(0, min(rel - width // 3, len(line) - width))
    cut = line[lo : lo + width]
    return ("..." if lo > 0 else "") + cut.strip() + ("..." if lo + width < len(line) else "")


def analyze_script(
    script: str,
    catalog: Catalog | None = None,
    *,
    lexicon: tuple[LexEntry, ...] | None = None,
    max_missing: int = 40,
) -> Coverage:
    """Sort what ``script`` mentions into what the library can draw and what it cannot."""
    cat = catalog or CATALOG
    lib = asset_index(cat)
    lex_entries = load_lexicon() if lexicon is None else lexicon
    lex = TagIndex(lex_entries)
    engine = TagIndex(_engine_entries())

    covered: dict[tuple[str, str], Covered] = {}
    missing: dict[tuple[str, str], Missing] = {}
    taken: list[tuple[int, int]] = []

    def add_covered(name: str, kind: str, source: str, word: str, span: tuple[int, int]) -> None:
        c = covered.setdefault((kind, name), Covered(name, kind, source))
        c.count += 1
        if word.lower() not in (w.lower() for w in c.words):
            c.words.append(word)
        if len(c.at) < MAX_MENTIONS:
            c.at.append(span)

    for lib_hit in lib.scan(script):
        taken.append((lib_hit.start, lib_hit.end))
        a = lib_hit.asset
        add_covered(a.name, a.kind, a.origin, lib_hit.phrase, (lib_hit.start, lib_hit.end))

    answered: dict[str, tuple[str, str, str] | None] = {}

    def answer(entry: LexEntry) -> tuple[str, str, str] | None:
        """Which library asset (or engine set) draws this lexicon entry, by any of its words."""
        if entry.name in answered:
            return answered[entry.name]
        found: tuple[str, str, str] | None = None
        for w in entry.tags:
            a = lib.lookup(w)
            if a is not None:
                found = (a.name, a.kind, a.origin)
                break
        if found is None:
            for w in entry.tags:
                e = engine.lookup(w)
                if e is not None:
                    found = (e.name, e.kind, "engine")
                    break
        answered[entry.name] = found
        return found

    for h in lex.scan(script):
        if any(s < h.end and h.start < e for s, e in taken):
            continue  # the library already named this word
        entry = h.asset
        got = answer(entry)
        if got is not None:
            add_covered(got[0], got[1], got[2], h.phrase, (h.start, h.end))
            continue
        m = missing.setdefault(
            (entry.kind, entry.name),
            Missing(
                entry.name,
                entry.kind,
                tags=[t for t in entry.tags if t != entry.name.replace("_", " ")],
            ),
        )
        m.count += 1
        if h.phrase.lower() not in (w.lower() for w in m.words):
            m.words.append(h.phrase)
        if len(m.at) < MAX_MENTIONS:
            m.at.append((h.start, h.end))
        if not m.snippet:
            m.snippet = snippet(script, h.start, h.end)

    out = Coverage(
        covered=sorted(covered.values(), key=lambda c: (-c.count, c.kind, c.asset)),
        missing=sorted(missing.values(), key=lambda m: (-m.count, m.at[0][0] if m.at else 0))[
            :max_missing
        ],
    )
    return out


__all__ = [
    "Coverage",
    "Covered",
    "LexEntry",
    "Missing",
    "analyze_script",
    "load_lexicon",
    "snippet",
]
