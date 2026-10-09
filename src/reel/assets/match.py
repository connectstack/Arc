"""Finding library assets in words: a script says "the cars drove off", the library knows ``car`` (tags: cars, taxi, कार, ...).

The index is built from the assets' own names and tags, so whatever a user adds is found the same way as what ships.
Matching is on whole words (or whole phrases: "auto rickshaw"), case-insensitive, with the plain English plurals and
possessives folded in.  Devanagari (and the other Indic scripts) are words too: Python's ``\\w`` splits them at every vowel
sign, so the tokenizer here keeps the whole Indic block together.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from reel.assets.model import AssetDef, AssetKind

if TYPE_CHECKING:
    from reel.core.catalog import Catalog

INDIC = "\u0900-\u0963\u0966-\u096f\u0971-\u0dff"  # Devanagari, Bengali, Gurmukhi, Gujarati, Tamil, ... (letters, signs and digits, but not the danda marks that end a sentence)
#: a word: Latin/other letters and digits, or a run of Indic letters and signs (U+0900-0DFF without the danda); an inner apostrophe stays
WORD = re.compile(rf"[\w{INDIC}]+(?:['’][\w]+)?")
_MAX_PHRASE = 4
#: what ends a phrase: words either side of one of these are not one phrase ("ice, cream").  A line end is not one: text
#: that was wrapped at 80 columns splits phrases across lines.
_BREAK = re.compile(r"[.,;:!?…।॥()\[\]{}\"“”]")


def norm_token(token: str) -> str:
    """One word in its comparable form: NFC, case-folded, possessive dropped."""
    t = unicodedata.normalize("NFC", token).casefold().replace("’", "'")
    if t.endswith("'s"):
        t = t[:-2]
    return t.strip("'_")


def tokens(text: str) -> list[tuple[str, int, int]]:
    """``(normalised word, start, end)`` for every word of ``text``; offsets point into the original string."""
    return [
        (n, m.start(), m.end())
        for m in WORD.finditer(text.replace("_", " ").replace("-", " "))
        if (n := norm_token(m.group()))
    ]


def _plural_variants(token: str) -> tuple[str, ...]:
    """The token and the singular an English plural folds to (cars -> car, cherries -> cherry, boxes -> box)."""
    out = [token]
    if token.isascii() and len(token) > 3:
        if token.endswith("ies"):
            out.append(token[:-3] + "y")
        elif token.endswith(("ches", "shes", "xes", "sses", "zes")):
            out.append(token[:-2])
        elif token.endswith("s") and not token.endswith("ss"):
            out.append(token[:-1])
    return tuple(out)


def phrase_key(text: str) -> tuple[str, ...]:
    return tuple(t for t, _s, _e in tokens(text))


class Taggable(Protocol):
    """Anything with a name, a kind, the words it answers to and where it comes from: a library asset, a lexicon entry."""

    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def tags(self) -> Sequence[str]: ...
    @property
    def origin(self) -> str: ...


T = TypeVar("T", bound=Taggable)


@dataclass(frozen=True)
class Hit(Generic[T]):
    """One place in a text where a tagged thing is named."""

    asset: T
    phrase: str  # the words as the text wrote them
    start: int
    end: int
    alternatives: tuple[T, ...] = ()  # other things with the same word (the first is the best)


def _rank(a: Taggable, phrase: tuple[str, ...]) -> tuple[int, int]:
    """Lower wins: the user's own assets before the built-in ones, an exact name before a synonym."""
    return (0 if a.origin == "user" else 1, 0 if phrase_key(a.name) == phrase else 1)


class TagIndex(Generic[T]):
    """Every thing's name and tags as a phrase table; ``scan`` finds them in a text, ``lookup`` resolves one word."""

    def __init__(self, assets: Iterable[T], kinds: Sequence[str] | None = None) -> None:
        self._phrases: dict[tuple[str, ...], list[T]] = {}
        self._max = 1
        for a in assets:
            if kinds and a.kind not in kinds:
                continue
            for tag in dict.fromkeys([a.name, *a.tags]):
                key = phrase_key(tag)
                if not key or len(key) > _MAX_PHRASE:
                    continue
                bucket = self._phrases.setdefault(key, [])
                if a not in bucket:
                    bucket.append(a)
                self._max = max(self._max, len(key))
        for key, bucket in self._phrases.items():
            bucket.sort(key=partial(_rank, phrase=key))

    def __len__(self) -> int:
        return len(self._phrases)

    def single_words(self) -> dict[str, T]:
        """Every one-word phrase with the best thing that answers to it."""
        return {k[0]: v[0] for k, v in self._phrases.items() if len(k) == 1}

    def _find(self, words: Sequence[str]) -> list[T] | None:
        """Assets for exactly these words (the last may be a plural of the indexed one)."""
        for last in _plural_variants(words[-1]):
            hit = self._phrases.get((*words[:-1], last))
            if hit:
                return hit
        return None

    def lookup(self, text: str) -> T | None:
        """The asset a word or short phrase names ("Cars", "auto rickshaw"), else ``None``."""
        words = phrase_key(text)
        if not words or len(words) > _MAX_PHRASE:
            return None
        found = self._find(words)
        return found[0] if found else None

    def scan(self, text: str) -> list[Hit[T]]:
        """Every asset named in ``text``, in order, longest phrase first at each position."""
        toks = tokens(text)
        original = text.replace("_", " ").replace("-", " ")
        # a phrase never runs over a full stop or a comma: how many words follow token i before the next one
        reach = [1] * len(toks)
        for j in range(len(toks) - 2, -1, -1):
            reach[j] = (
                1 if _BREAK.search(original[toks[j][2] : toks[j + 1][1]]) else reach[j + 1] + 1
            )
        hits: list[Hit[T]] = []
        i = 0
        while i < len(toks):
            matched = 0
            for n in range(min(self._max, len(toks) - i, reach[i]), 0, -1):
                found = self._find([t[0] for t in toks[i : i + n]])
                if found:
                    start, end = toks[i][1], toks[i + n - 1][2]
                    hits.append(Hit(found[0], original[start:end], start, end, tuple(found[1:])))
                    matched = n
                    break
            i += matched or 1
        return hits


def asset_index(
    catalog: Catalog | None = None, kinds: Sequence[AssetKind] | None = None
) -> TagIndex[AssetDef]:
    """The tag index of a catalog's assets (the live catalog by default); built fresh, so it is never stale."""
    from reel.core.catalog import CATALOG

    cat = catalog or CATALOG
    return TagIndex((e.obj for e in cat.assets.entries()), kinds)


__all__ = [
    "WORD",
    "Hit",
    "TagIndex",
    "Taggable",
    "asset_index",
    "norm_token",
    "phrase_key",
    "tokens",
]
