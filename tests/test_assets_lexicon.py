"""The shipped lexicon of everyday visual things (English, Hindi, Hinglish) that the "not in the library" report reads."""

from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict

import pytest

from reel.assets.coverage import LEXICON_FILE, analyze_script, load_lexicon
from reel.assets.match import phrase_key
from reel.core.catalog import CATALOG

#: words that carry no picture: if one of these were a lexicon word, every script would be nagged about it
FUNCTION_WORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "if",
        "then",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "when",
        "where",
        "what",
        "who",
        "how",
        "why",
        "not",
        "no",
        "yes",
        "all",
        "any",
        "some",
        "one",
        "two",
        "up",
        "down",
        "out",
        "over",
        "under",
        "है",
        "हैं",
        "था",
        "थे",
        "थी",
        "हो",
        "और",
        "या",
        "भी",
        "तो",
        "ही",
        "यह",
        "वह",
        "ये",
        "वो",
        "कि",
        "जो",
        "क्या",
        "का",
        "की",
        "के",
        "को",
        "में",
        "से",
        "पर",
        "ने",
        "एक",
        "दो",
        "कुछ",
        "सब",
        "बहुत",
        "अब",
        "फिर",
        "जब",
        "तब",
        "गया",
        "गई",
        "गए",
        "किया",
        "कर",
        "रहा",
        "रही",
        "रहे",
        "बस",
        "बनी",
        "बना",
        "बने",
        "दिया",
        "लिया",
    ]
)


def words(entry: object) -> list[str]:
    return list(entry.tags)  # type: ignore[attr-defined]


class TestTheFile:
    def test_it_is_a_list_of_entries_with_a_name_a_kind_and_words(self) -> None:
        data = json.loads(LEXICON_FILE.read_text(encoding="utf-8"))
        assert isinstance(data["entries"], list) and len(data["entries"]) >= 500
        for e in data["entries"]:
            assert set(e) == {"name", "kind", "words"}, e
            assert e["kind"] in ("character", "object", "place")
            assert e["name"] and all(isinstance(w, str) and w.strip() for w in e["words"])

    def test_every_entry_is_loaded_and_named_once(self) -> None:
        lex = load_lexicon()
        assert len(lex) == len(json.loads(LEXICON_FILE.read_text(encoding="utf-8"))["entries"])
        counts = Counter((e.name, e.kind) for e in lex)
        assert [k for k, n in counts.items() if n > 1] == []
        assert {e.kind for e in lex} == {"character", "object", "place"}

    def test_words_are_normalised_and_short_enough_to_match(self) -> None:
        for e in load_lexicon():
            for w in words(e):
                assert w == unicodedata.normalize("NFC", w), (e.name, w)
                assert 1 <= len(phrase_key(w)) <= 4, (e.name, w)
                if w.isascii():
                    assert w == w.lower(), (e.name, w)

    def test_a_word_belongs_to_one_entry_only(self) -> None:
        owner: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for e in load_lexicon():
            for w in words(e):
                owner[phrase_key(w)].add(e.name)
        shared = {" ".join(k): sorted(v) for k, v in owner.items() if len(v) > 1}
        assert shared == {}

    def test_no_word_is_a_function_word_or_a_common_hindi_verb(self) -> None:
        offenders = [(e.name, w) for e in load_lexicon() for w in words(e) if w in FUNCTION_WORDS]
        assert offenders == []

    def test_weather_and_relationship_words_are_left_to_the_engine_and_the_planner(self) -> None:
        names = {e.name for e in load_lexicon()}
        assert not names & {
            "rain",
            "snow",
            "fog",
            "storm",
            "lightning",
            "friend",
            "neighbour",
            "stranger",
            "crowd",
        }


def report(script: str) -> tuple[set[str], set[str]]:
    cov = analyze_script(script, CATALOG)
    return {c.asset for c in cov.covered}, {m.name for m in cov.missing}


class TestWhatItReports:
    @pytest.mark.parametrize(
        ("script", "missing"),
        [
            ("The king drew his sword at the castle gate.", {"king", "sword", "castle"}),
            (
                "राजा ने तलवार निकाली और किले की ओर चला।",
                {"king", "sword", "fort"},
            ),  # the castle is a fort here; a word before the danda is a word
            ("Mia ne kutta aur talwar dekhi.", {"sword"}),  # Hinglish
            ("A thief took the cookie jar through the door.", {"thief", "jar", "door"}),
        ],
    )
    def test_things_a_story_shows_that_nothing_draws_are_missing(
        self, script: str, missing: set[str]
    ) -> None:
        assert missing <= report(script)[1]

    @pytest.mark.parametrize(
        ("script", "covered"),
        [
            ("The dragon flew over the village.", {"dragon", "village"}),
            ("कुत्ता गाय के पास बैठा।", {"dog", "cow"}),  # the danda after the last word
            ("Mia kutta ko dekh kar hansi.", {"dog"}),
            ("They waited at the railway station for the train.", {"railway_station", "train"}),
        ],
    )
    def test_things_the_library_draws_are_covered_not_missing(
        self, script: str, covered: set[str]
    ) -> None:
        got_covered, got_missing = report(script)
        assert covered <= got_covered
        assert not covered & got_missing

    def test_everyday_words_that_are_not_pictures_are_never_reported(self) -> None:
        _covered, missing = report(
            "It was raining, and a friend said that the crowd was loud. बस इतना ही, बारिश हो रही थी और दोस्त ने कहा।"
        )
        assert missing == set()

    def test_a_script_that_names_nothing_reports_nothing(self) -> None:
        assert report("Why do we sleep? Because the brain needs rest.") == (set(), set())
