"""The captions of a reel are the script's words: ``ScriptLock`` and the way the LLM stage uses it."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from reel.core.lint import lint_data
from reel.llm import generate_spec
from reel.llm.client import ReplayClient
from reel.llm.fidelity import LockReport, ScriptLock, read_sec, script_units, tokens
from reel.llm.heuristic import _split_speaker

ROOT = Path(__file__).resolve().parent.parent
HINDI = (ROOT / "examples/scripts/hindi_khoya_chhata.txt").read_text(encoding="utf-8")
#: what gpt-4o-mini wrote for that script: speaker labels where the lines belong, lines of its own, pans written as positions
OPENAI_REPLY: dict[str, Any] = json.loads(
    (ROOT / "tests/fixtures/openai_khoya_chhata.json").read_text(encoding="utf-8")
)

ENGLISH = """The Cookie Heist

One night the cookie jar was empty.
Mia: Who took my cookies?
Pip: Not me, I was asleep!
The jar sat there, silent and suspicious.
"""


def scene(
    sid: str, captions: list[dict[str, Any]], *, who: str = "mia", dur: float = 6.0
) -> dict[str, Any]:
    return {
        "id": sid,
        "duration_sec": dur,
        "background": {"template": "room", "params": {}},
        "layers": [
            {
                "character": who,
                "position": "center",
                "actions": [{"name": "idle", "t0": 0.0, "t1": dur}],
            }
        ],
        "captions": captions,
        "transition_out": {"type": "cut", "duration": 0.0},
    }


def cap(text: str, t0: float = 0.5, t1: float = 3.0, **kw: Any) -> dict[str, Any]:
    return {"text": text, "t0": t0, "t1": t1, "style": "subtitle", **kw}


def spec_of(*scenes: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "1.0",
        "meta": {"title": "t", "style": "flat_vector", "seed": 1},
        "characters": [
            {"id": "mia", "archetype": "kid"},
            {"id": "pip", "archetype": "elder"},
        ],
        "scenes": list(scenes),
        "audio": {"music": None, "voiceover": "tts"},
    }


def lines_of(spec: dict[str, Any]) -> list[str]:
    """Caption texts in reading order, the title card left out."""
    return [
        c["text"]
        for sc in spec["scenes"]
        for c in sorted(sc["captions"], key=lambda c: c["t0"])
        if c["style"] != "title"
    ]


def texts(spec: dict[str, Any]) -> list[str]:
    """Every caption text in reading order."""
    return [
        c["text"] for sc in spec["scenes"] for c in sorted(sc["captions"], key=lambda c: c["t0"])
    ]


# --------------------------------------------------------------------------------- tokens
class TestTokens:
    def test_a_devanagari_word_stays_whole_with_its_vowel_signs(self) -> None:
        # a plain \w would cut "मिया" and "मज़ेदार" at the vowel signs
        words = [t.text for t in tokens("मिया: बारिश में घूमना कितना मज़ेदार है!")]
        assert words == ["मिया", "बारिश", "में", "घूमना", "कितना", "मज़ेदार", "है"]

    def test_punctuation_case_and_joiners_do_not_matter_but_apostrophes_stay(self) -> None:
        a = [t.text for t in tokens("Don’t PANIC, it's fine!")]
        assert a == ["don't", "panic", "it's", "fine"]
        assert [t.text for t in tokens("क्‍ष")] == [t.text for t in tokens("क्ष")]

    def test_scripts_without_spaces_give_a_token_per_character(self) -> None:
        assert [t.text for t in tokens("小明说：你好！")] == ["小", "明", "说", "你", "好"]

    def test_tokens_remember_where_they_were(self) -> None:
        text = "Hi, Mia!"
        toks = tokens(text)
        assert [text[t.start : t.end] for t in toks] == ["Hi", "Mia"]

    def test_reading_time_grows_with_the_words(self) -> None:
        assert read_sec(tokens("Oh.")) == 1.3
        assert read_sec(tokens("one two three four five six seven eight")) == pytest.approx(
            8 / 2.6 + 0.4
        )


# --------------------------------------------------------------------------------- the script
class TestScript:
    def test_names_in_any_script_are_speakers(self) -> None:
        assert _split_speaker("मिया: बारिश में घूमना") == ("मिया", "बारिश में घूमना")
        assert _split_speaker("Mr. Pip (whispering): shh") == ("Mr. Pip", "shh")
        assert _split_speaker("小明：你好") == ("小明", "你好")
        assert _split_speaker("Миа: Привет") == ("Миа", "Привет")
        assert _split_speaker("note: not a speaker") is None
        assert _split_speaker("What time: now") is None  # cased words that are not names

    def test_the_hindi_script_has_three_speakers_and_its_lines(self) -> None:
        units = script_units(HINDI)
        assert [u.kind for u in units[:4]] == ["title", "narration", "narration", "dialogue"]
        assert units[3].speaker == "मिया" and units[3].text == "बारिश में घूमना कितना मज़ेदार है!"
        lock = ScriptLock(HINDI)
        assert lock.speakers() == ["मिया", "पिप", "बोल्ट"]
        assert lock.fits() and lock.has_title

    def test_a_script_that_cannot_be_read_in_a_reel_does_not_fit(self) -> None:
        assert not ScriptLock(
            " ".join(["The quick brown fox jumps over the lazy dog."] * 30)
        ).fits()
        assert not ScriptLock("").fits()

    def test_the_offline_planner_now_finds_non_latin_speakers(self) -> None:
        res = generate_spec(HINDI, "paper_cutout", None, seed=1, target_duration=50)
        who = {c["id"]: c.get("name") for c in res.spec["characters"]}
        assert set(who.values()) >= {"मिया", "पिप", "बोल्ट"}
        said = {
            cp["text"]: who[cp["speaker"]]
            for sc in res.spec["scenes"]
            for cp in sc["captions"]
            if cp.get("speaker")
        }
        assert said["बारिश में घूमना कितना मज़ेदार है!"] == "मिया"
        assert said["चिंता मत कीजिए, मैं ढूंढ दूँगी!"] == "मिया"


# --------------------------------------------------------------------------------- the lock
class TestLock:
    def test_captions_that_are_the_script_need_nothing(self) -> None:
        spec = spec_of(
            scene("s01", [cap("The Cookie Heist", style="title")]),
            scene("s02", [cap("One night the cookie jar was empty.")]),
            scene("s03", [cap("Who took my cookies?", speaker="mia")]),
            scene("s04", [cap("Not me, I was asleep!", speaker="pip")]),
            scene("s05", [cap("The jar sat there, silent and suspicious.")]),
        )
        lock = ScriptLock(ENGLISH)
        cov = lock.coverage(spec)
        assert cov["ok"] and cov["covered"] == 1.0 and cov["missing"] == []
        out, rep = lock.apply(spec)
        assert not rep.changed() and rep.notes() == []
        assert texts(out) == texts(spec)

    def test_a_paraphrase_is_put_back_to_the_scripts_words(self) -> None:
        spec = spec_of(
            scene("s01", [cap("The Cookie Heist", style="title")]),
            scene("s02", [cap("The cookie jar was totally empty one night.")]),
            scene("s03", [cap("Who took my cookies??", speaker="mia")]),
            scene("s04", [cap("Not me! I was asleep.", speaker="pip")]),
            scene("s05", [cap("The jar sat there, silent and suspicious.")]),
        )
        out, rep = ScriptLock(ENGLISH).apply(spec)
        assert texts(out) == [
            "The Cookie Heist",
            "One night the cookie jar was empty.",
            "Who took my cookies?",
            "Not me, I was asleep!",
            "The jar sat there, silent and suspicious.",
        ]
        assert rep.snapped >= 2

    def test_a_word_the_model_dropped_comes_back_and_one_it_added_goes(self) -> None:
        spec = spec_of(
            scene("s01", [cap("One night the cookie empty.")]),
            scene("s02", [cap("Who took my very precious cookies?", speaker="mia")]),
        )
        out, _ = ScriptLock(ENGLISH).apply(spec)
        assert lines_of(out)[:2] == ["One night the cookie jar was empty.", "Who took my cookies?"]

    def test_a_speakers_name_in_the_caption_is_not_the_line(self) -> None:
        spec = spec_of(
            scene("s01", [cap("Mia:", speaker="mia")]),
            scene("s02", [cap("Pip:", speaker="pip")], who="pip"),
        )
        out, rep = ScriptLock(ENGLISH).apply(spec)
        # the labels were slots for the next line each person says
        said = {c["text"]: c.get("speaker") for sc in out["scenes"] for c in sc["captions"]}
        assert said["Who took my cookies?"] == "mia" and said["Not me, I was asleep!"] == "pip"
        # ... and they stayed in the scenes the model made for them
        mia_scene = next(
            sc
            for sc in out["scenes"]
            if any(c["text"] == "Who took my cookies?" for c in sc["captions"])
        )
        assert mia_scene["layers"][0]["character"] == "mia"
        assert all(not c["text"].endswith(":") for sc in out["scenes"] for c in sc["captions"])
        assert rep.removed == 0

    def test_a_line_nobody_shows_is_added_in_its_place(self) -> None:
        spec = spec_of(
            scene("s01", [cap("One night the cookie jar was empty.")]),
            scene("s02", [cap("The jar sat there, silent and suspicious.")]),
        )
        out, rep = ScriptLock(ENGLISH).apply(spec)
        assert texts(out) == [
            "The Cookie Heist",
            "One night the cookie jar was empty.",
            "Who took my cookies?",
            "Not me, I was asleep!",
            "The jar sat there, silent and suspicious.",
        ]
        assert ScriptLock(ENGLISH).coverage(out)["ok"]
        assert rep.added == 3 and rep.new_scenes >= 1  # the title, Mia's line and Pip's line

    def test_what_is_added_is_said_by_the_right_person(self) -> None:
        spec = spec_of(scene("s01", [cap("One night the cookie jar was empty.")]))
        out, _ = ScriptLock(ENGLISH).apply(spec)
        said = {c["text"]: c.get("speaker") for sc in out["scenes"] for c in sc["captions"]}
        assert said["Who took my cookies?"] == "mia" and said["Not me, I was asleep!"] == "pip"
        assert said["One night the cookie jar was empty."] is None  # narration has no speaker

    def test_an_unknown_speaker_joins_the_cast_with_the_name_the_script_gives(self) -> None:
        script = "Zed: Hello there, friend!\nMia: Hi Zed!"
        out, _ = ScriptLock(script).apply(spec_of(scene("s01", [cap("Hello.")])))
        zed = next(c for c in out["characters"] if c.get("name") == "Zed")
        assert any(
            c.get("speaker") == zed["id"] and c["text"] == "Hello there, friend!"
            for sc in out["scenes"]
            for c in sc["captions"]
        )

    def test_the_title_is_a_title_card_at_the_start(self) -> None:
        out, _ = ScriptLock(ENGLISH).apply(spec_of(scene("s01", [cap("Something else entirely.")])))
        first = out["scenes"][0]["captions"][0]
        assert first["text"] == "The Cookie Heist" and first["style"] == "title"

    def test_a_headline_stays_when_the_script_has_no_title(self) -> None:
        script = "One night the cookie jar was empty.\nMia: Who took my cookies?"
        spec = spec_of(scene("s01", [cap("Cookie Mystery", style="title")]))
        out, _ = ScriptLock(script).apply(spec)
        assert out["scenes"][0]["captions"][0]["text"] == "Cookie Mystery"

    def test_the_result_is_stable_and_never_changes_the_input(self) -> None:
        spec = spec_of(scene("s01", [cap("Mia:", speaker="mia")]))
        before = copy.deepcopy(spec)
        lock = ScriptLock(ENGLISH)
        out, rep = lock.apply(spec)
        assert spec == before and rep.changed()
        again, rep2 = lock.apply(out)
        assert not rep2.changed()
        assert texts(again) == texts(out)

    def test_scenes_that_would_play_after_the_story_are_left_out_when_the_reel_is_long_enough(
        self,
    ) -> None:
        lines = [cap(t) for t in ("One night the cookie jar was empty.",)]
        spec = spec_of(
            scene("s01", lines, dur=20.0),
            *[scene(f"x{k}", [cap("invented")], dur=14.0) for k in range(4)],
        )
        out, rep = ScriptLock(ENGLISH).apply(spec)
        assert rep.dropped_scenes >= 1 and len(out["scenes"]) < len(spec["scenes"]) + rep.new_scenes
        assert (
            sum(sc["duration_sec"] for sc in out["scenes"]) >= 50
        )  # ... but only down to a reel that is long enough

    def test_a_short_script_keeps_the_scenes_that_make_the_reel_long_enough(self) -> None:
        spec = spec_of(*[scene(f"s{k:02d}", [cap(f"Scene {k}")], dur=5.5) for k in range(1, 11)])
        out, _ = ScriptLock("One night the cookie jar was empty.").apply(spec)
        assert (
            len(out["scenes"]) >= 9
        )  # 55 s of scene time stay: nothing but the first caption was a line

    def test_talking_gestures_speak_what_their_caption_says(self) -> None:
        spec = spec_of(scene("s01", [cap("Mia:", 0.5, 3.0, speaker="mia")]))
        spec["scenes"][0]["layers"][0]["actions"].append(
            {"name": "talk", "t0": 0.6, "t1": 2.8, "params": {"text": "something invented"}}
        )
        out, _ = ScriptLock(ENGLISH).apply(spec)
        mine = next(
            sc for sc in out["scenes"] if any(c.get("speaker") == "mia" for c in sc["captions"])
        )
        talk = next(a for a in mine["layers"][0]["actions"] if a["name"] == "talk")
        assert talk["params"]["text"] == "Who took my cookies?"

    def test_report_notes_say_what_was_done(self) -> None:
        assert LockReport().notes() == []
        notes = LockReport(snapped=2, added=1, removed=3, new_scenes=1, covered=0.5).notes()
        assert "word for word" in notes[0] and "50%" in notes[0]


# --------------------------------------------------------------------------------- the whole reply
class TestOpenAIReply:
    """The spec the user got from OpenAI for the Hindi example: every caption a speaker's name, pans of 0.5."""

    def test_it_was_not_the_script(self) -> None:
        cov = ScriptLock(HINDI).coverage(OPENAI_REPLY)
        assert not cov["ok"] and cov["covered"] < 0.1 and cov["labels"] >= 4

    def test_after_the_lock_every_line_of_the_script_is_there_in_order(self) -> None:
        lock = ScriptLock(HINDI)
        out, rep = lock.apply(OPENAI_REPLY)
        assert rep.changed()
        shown = " ".join(texts(out))
        wanted = " ".join(u.text for u in lock.units)
        assert shown == wanted
        assert lock.coverage(out)["ok"]
        assert all(not t.endswith(":") for t in texts(out))

    def test_the_lines_are_said_by_the_people_who_say_them(self) -> None:
        out, _ = ScriptLock(HINDI).apply(OPENAI_REPLY)
        name = {c["id"]: c.get("name") for c in out["characters"]}
        assert name == {"mia": "मिया", "pip": "पिप", "bolt": "बोल्ट"}
        said = {
            c["text"]: name.get(c.get("speaker")) for sc in out["scenes"] for c in sc["captions"]
        }
        assert said["बारिश में घूमना कितना मज़ेदार है!"] == "मिया"
        assert said["अरे नहीं! मेरा छाता कहाँ गया?"] == "पिप"
        assert said["बीप। बारिश का पता चला। छाता खुल गया।"] == "बोल्ट"
        assert said["वे पूरे पार्क में ढूंढते रहे।"] is None

    def test_the_whole_pipeline_gives_a_lint_clean_faithful_reel(self) -> None:
        res = generate_spec(HINDI, "paper_cutout", ReplayClient([OPENAI_REPLY]), seed=1248)
        assert res.attempts == 1 and res.ok and not res.lint.warnings
        assert any("word for word" in n for n in res.notes)
        assert ScriptLock(HINDI).coverage(res.spec)["ok"]
        assert lint_data(res.spec).ok
        for sc in res.spec["scenes"]:
            for m in sc.get("camera", {}).get("moves", []):
                if m["type"] == "pan":  # the screen positions it wrote are a drift now
                    for v in (m["from"], m["to"]):
                        assert abs(v[0]) <= 0.25 and abs(v[1]) <= 0.1
        assert any("screen positions" in n for n in res.notes)

    def test_the_model_may_be_left_to_condense_when_asked(self) -> None:
        res = generate_spec(
            HINDI, "paper_cutout", ReplayClient([OPENAI_REPLY]), seed=1248, verbatim=False
        )
        assert not any("word for word" in n for n in res.notes)
        assert not ScriptLock(HINDI).coverage(res.spec)["ok"]

    def test_the_prompt_demands_the_scripts_words(self) -> None:
        client = ReplayClient([OPENAI_REPLY])
        generate_spec(HINDI, "paper_cutout", client, seed=1)
        (req,) = client.requests
        assert "THE SCRIPT IS THE TEXT OF THE REEL" in req.system
        assert "own words" in req.messages[0].content
        assert "never a position on the screen" in req.system or "never a position" in req.system
