"""The offline planner follows the script: the set a story is on, who is in it and what stands on it.

Backgrounds hold from scene to scene until the text names another place, characters are drawn as what the text says they are
("Chef Marco", "a monkey named Chiku", "Her boss, Mr. Rao,"), creatures that come in once are guests of that scene, and things
named stay on the set for a while.
"""

from __future__ import annotations

from typing import Any

import pytest

from reel.core.catalog import CATALOG
from reel.llm import HeuristicSpecGenerator


def plan(script: str, seed: int = 3) -> dict[str, Any]:
    res = HeuristicSpecGenerator(catalog=CATALOG).generate(script, style="flat_vector", seed=seed)
    assert res.lint.ok, res.lint
    return dict(res.spec)


def cast(spec: dict[str, Any]) -> dict[str, str]:
    return {c["id"]: c["archetype"] for c in spec["characters"]}


def sets(spec: dict[str, Any]) -> list[str]:
    return [s["background"]["template"] for s in spec["scenes"]]


def who(spec: dict[str, Any], scene: int) -> list[str]:
    return [ly["character"] for ly in spec["scenes"][scene]["layers"]]


def objects(spec: dict[str, Any], scene: int) -> list[str]:
    return [o["asset"] for o in spec["scenes"][scene].get("objects", [])]


def scene_with(spec: dict[str, Any], text: str) -> int:
    for i, sc in enumerate(spec["scenes"]):
        if any(text in c["text"] for c in sc["captions"]):
            return i
    raise AssertionError(f"no scene says {text!r}")


OFFICE = """The Monday Meeting

Priya walked into the office with a cup of tea in her hand.
Her boss, Mr. Rao, was already standing next to the whiteboard.
"Good morning, everyone. We have a big deadline today," said the boss.
Priya opened her laptop and started typing fast.
The phone on her desk rang. It was the client.
"We need the report by evening," the client said.
Priya smiled. She had finished it yesterday.
She sent the report by email and the boss clapped.
"Great work!" said Mr. Rao. "You deserve a coffee break."
They walked out of the office together and laughed.
"""

FARM = """A Day on the Farm

Ramu the farmer woke up early and walked to his barn.
His cow, Gauri, was waiting for him near the big tree.
"Good morning, Gauri!" said Ramu, and he gave her some grass.
Then he started his tractor and drove to the field.
A little bird sat on the fence and sang a song.
At noon his daughter brought lunch in a basket.
They ate mangoes under the tree and laughed together.
In the evening Ramu went home happy.
"""


class TestCharactersAreWhatTheScriptSays:
    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            (
                "Chef Marco tasted the soup. Marco smiled. Marco stirred the pot. Marco laughed.",
                {"marco": "chef"},
            ),
            (
                "Doctor Meera arrived early. Meera smiled. Meera waved. Meera left.",
                {"meera": "doctor"},
            ),
            (
                "Dr. Meera arrived early. Meera smiled. Meera waved. Meera left.",
                {"meera": "doctor"},
            ),
            (
                "A clever monkey named Chiku lived here. Chiku jumped. Chiku laughed. Chiku ran.",
                {"chiku": "monkey"},
            ),
            (
                "His cow, Gauri, waited. Gauri mooed. Ramu laughed. Ramu waved. Gauri ate.",
                {"gauri": "cow"},
            ),
            (
                "The teacher, Mrs. Sharma, wrote. Sharma smiled. Sharma waved. Sharma left.",
                {"sharma": "teacher"},
            ),
            # a dog called Max is named, but "The farmer called Mia" is the farmer on the phone
            (
                "Mia had a dog called Max. Max ran. Mia laughed. Max barked. Mia waved.",
                {"max": "dog", "mia": "everyman"},
            ),
        ],
    )
    def test_a_role_beside_the_name_draws_the_character_as_it(
        self, script: str, expected: dict[str, str]
    ) -> None:
        got = cast(plan(script))
        assert {k: got[k] for k in expected} == expected

    def test_the_role_and_the_name_of_one_person_are_one_character(self) -> None:
        spec = plan(OFFICE)
        assert cast(spec) == {
            "priya": "everyman",
            "rao": "boss",
        }  # "the boss" is Mr. Rao: no second boss
        said_by = {
            c["speaker"] for sc in spec["scenes"] for c in sc["captions"] if c.get("speaker")
        }
        assert "rao" in said_by

    def test_a_capitalised_thing_in_the_sky_is_not_somebody(self) -> None:
        spec = plan(
            "Aman flew to the moon with his robot Beep. Beep counted. Aman jumped. Stars twinkled everywhere and Earth "
            "looked like a small blue globe. Beep beeped. Aman laughed."
        )
        assert "earth" not in cast(spec)

    def test_a_name_at_the_start_of_sentences_before_an_irregular_verb_is_a_name(self) -> None:
        spec = plan(
            "Kabir and his little sister Zoya ran to the beach. Kabir threw a ball. Zoya laughed. Kabir ran. Zoya waved."
        )
        assert {"kabir", "zoya"} <= set(cast(spec))

    def test_someone_met_at_school_is_a_child(self) -> None:
        spec = plan(
            "Rohan walked into the classroom with a worried face. Rohan opened his bag. Rohan sat down. Rohan wrote."
        )
        assert cast(spec)["rohan"] == "kid"


class TestGuests:
    SCRIPT = (
        "Chiku the monkey played in the jungle. Chiku jumped. Chiku laughed. An elephant arrived and Chiku waved. "
        "Chiku climbed a tree. Chiku sat down."
    )

    def test_a_creature_that_comes_in_once_is_a_guest_of_that_scene(self) -> None:
        spec = plan(self.SCRIPT)
        assert cast(spec)["elephant"] == "elephant"
        i = scene_with(spec, "An elephant arrived")
        assert "elephant" in who(spec, i)
        assert all(
            "elephant" not in who(spec, k) for k in range(i + 1, len(spec["scenes"]))
        )  # gone after its scene

    def test_a_figure_of_speech_is_not_a_guest(self) -> None:
        spec = plan("Mia was as brave as a lion. Mia ran. Mia jumped. Mia laughed. Mia waved.")
        assert "lion" not in cast(spec)

    def test_a_guest_is_never_the_one_a_later_line_is_about(self) -> None:
        spec = plan(FARM)
        bird = scene_with(spec, "A little bird sat")
        assert "bird" in who(spec, bird)
        assert "bird" not in who(
            spec, bird + 1
        )  # "At noon his daughter brought lunch": not the bird's line


class TestTheSetFollowsTheStory:
    def test_the_set_a_story_names_holds_until_another_place_is_named(self) -> None:
        spec = plan(OFFICE)
        assert set(sets(spec)) == {
            "office"
        }  # a desk, a whiteboard and a phone do not move an office story

    def test_a_song_does_not_move_a_farm_to_a_stage(self) -> None:
        spec = plan(FARM)
        assert sets(spec)[: scene_with(spec, "the big tree") + 6] == ["farm"] * (
            scene_with(spec, "the big tree") + 6
        )
        assert "stage" not in sets(spec)

    def test_the_first_scene_is_on_the_set_the_story_first_names(self) -> None:
        spec = plan(
            "It was a rainy morning. Mia skipped down the street. Mia smiled. Mia jumped. "
            "They searched the whole park. Mia ran. Mia waved."
        )
        assert sets(spec)[0] == "street"  # not the park, which is only named later

    def test_a_place_that_is_only_spoken_about_is_not_where_the_scene_is(self) -> None:
        spec = plan(
            "Rohan walked into the classroom. His teacher, Mrs. Sharma, wrote on the blackboard. "
            '"Where is your homework?" she asked. "I forgot my book at home, madam," he said. Rohan sat down.'
        )
        assert set(sets(spec)) == {"classroom"}

    def test_a_scene_goes_where_the_text_says_it_goes(self) -> None:
        spec = plan(
            "Sita went to the busy market. Sita bought mangoes. Sita smiled. Then Sita walked home. "
            "Sita made a sweet drink in the kitchen. Sita laughed."
        )
        assert sets(spec)[0] == "market" and sets(spec)[-1] == "room"

    def test_a_trade_stays_on_its_own_set(self) -> None:
        spec = plan(
            "Sita went to the busy market. The shopkeeper sold mangoes. Sita paid the shopkeeper. The shopkeeper smiled. "
            "Then Sita walked home. Sita made a sweet drink in the kitchen. Sita laughed."
        )
        home = scene_with(spec, "walked home")
        assert "shopkeeper" not in who(spec, home + 1)
        assert "sita" in who(spec, home + 1)

    def test_hindi_words_find_the_engines_own_sets(self) -> None:
        spec = plan(
            "मिया: मैं गली में खेल रही हूँ!\nपिप: मैं छत पर जा रहा हूँ।\nमिया: चलो पार्क चलते हैं!\nपिप: ठीक है।\n"
            "मिया: बहुत मज़ा आया!\nपिप: हाँ।"
        )
        assert {"street", "rooftop", "park"} <= set(sets(spec))


class TestThingsStayOnTheSet:
    def test_a_named_thing_stays_for_the_next_scenes_on_the_same_set(self) -> None:
        spec = plan(OFFICE)
        i = scene_with(spec, "opened her laptop")
        assert "laptop" in objects(spec, i)
        assert "laptop" in objects(spec, i + 1)

    def test_it_does_not_stay_for_long(self) -> None:
        spec = plan(OFFICE)
        i = scene_with(spec, "opened her laptop")
        assert "laptop" not in objects(spec, i + 4)

    def test_what_drove_off_is_gone(self) -> None:
        spec = plan(
            "Early in the morning the street was full of traffic. A red bus drove past the corner. Arjun waved. Arjun ran. "
            "Arjun smiled. Arjun jumped."
        )
        i = scene_with(spec, "bus drove")
        assert "bus" in objects(spec, i)
        assert "bus" not in objects(spec, i + 1)

    def test_nothing_stays_when_the_story_moves_to_another_set(self) -> None:
        spec = plan(
            "Sita went to the busy market. Sita bought a mango. Sita smiled. Then Sita walked to the railway station. "
            "Sita waited. Sita smiled."
        )
        i = scene_with(spec, "bought a mango")
        j = scene_with(spec, "railway station")
        assert "mango" in objects(spec, i)
        assert "mango" not in objects(spec, j) and "mango" not in objects(spec, j + 1)


class TestHinglish:
    SCRIPT = (
        "Ek din Rahul apne dost Amit ke saath school gaya. Classroom mein teacher pehle se aa chuki thi. "
        "Rahul ne apni copy kholi aur likhna shuru kiya. Ghanti baji aur sab bacche bahar khelne chale gaye. "
        "Park mein ek chhota kutta unke saath khelne laga. Shaam ko dono ghar wapas gaye."
    )

    def test_a_noun_without_the_article_is_still_a_role(self) -> None:
        got = cast(plan(self.SCRIPT))
        assert got["teacher"] == "teacher"

    def test_a_hinglish_creature_comes_in_as_a_guest(self) -> None:
        spec = plan(self.SCRIPT)
        assert cast(spec)["dog"] == "dog"
        i = scene_with(spec, "kutta")
        assert "dog" in who(spec, i)
        assert "dog" not in who(spec, len(spec["scenes"]) - 1)


class TestTheLibraryDrawsWhatStoriesShow:
    @pytest.mark.parametrize(
        ("word", "asset"),
        [
            ("ball", "ball"), ("basket", "basket"), ("door", "door"), ("window", "window"), ("bell", "bell"),
            ("school bag", "backpack"), ("umbrella", "umbrella"), ("kite", "kite"), ("bucket", "bucket"),
            ("candle", "candle"), ("cooking pot", "pot"), ("water bottle", "bottle"), ("fence", "fence"),
            ("ladder", "ladder"), ("tomato", "tomato"), ("cookie", "cookie"), ("teddy bear", "teddy_bear"),
            ("notebook", "notebook"), ("blackboard", "blackboard"), ("flashlight", "torch"),
            ("medicine", "medicine"), ("cookie jar", "jar"), ("cricket bat", "cricket_bat"), ("soup", "bowl"),
            ("छाता", "umbrella"), ("पतंग", "kite"), ("गेंद", "ball"), ("tokri", "basket"), ("ghanti", "bell"),
        ],
    )  # fmt: skip
    def test_a_word_finds_its_drawing(self, word: str, asset: str) -> None:
        from reel.assets.match import asset_index

        found = asset_index(CATALOG, ("object",)).lookup(word)
        assert found is not None and found.name == asset

    def test_a_story_that_shows_them_has_nothing_missing(self) -> None:
        spec = plan(
            "Mia took her school bag and an umbrella to the door. Mia kicked the ball. Mia rang the bell. "
            "Mia flew a kite. Mia filled the bucket. Mia hugged her teddy bear."
        )
        assert not spec["meta"].get("library_gaps")
        shown = {a for i in range(len(spec["scenes"])) for a in objects(spec, i)}
        assert {
            "backpack",
            "umbrella",
            "door",
            "ball",
            "bell",
            "kite",
            "bucket",
            "teddy_bear",
        } & shown
