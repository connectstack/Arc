"""A story with small animals and a thing that changes: the thirsty crow and the little rat.

The offline planner has to cast both animals and keep them on screen, put the pot of water (not a cooking pot) on the set with the
tree it stands near, show the water low and then rising, drop the stones into it, let the animals step up and drink, draw a small
cast bigger and keep the subtitles off its feet.  The object motion that makes the water rise is tested here too.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from reel.assets.art import load_art
from reel.assets.library import register_asset
from reel.assets.objects import MOTIONS, build_object, motion_problems
from reel.core.catalog import CATALOG
from reel.core.spec import ObjectMotionSpec, ObjectSpec
from reel.llm import HeuristicSpecGenerator
from reel.llm.library_plan import LibraryPlanner
from reel.llm.props_plan import MAX_ON_SET, PropTracker
from reel.llm.stagecraft import Cast, CatalogView, Char, extract_cues

CROW = """One sunny day, a thirsty crow and a little rat found a pot of water near a tree.
The crow said, “I’m so thirsty! Let’s drink some water.”
The rat replied, “Wait! The water is too low. How will we reach it?”
The clever crow picked up a few small stones and dropped them into the pot. Slowly, the water level rose.
The rat happily drank some water, and then the crow drank too.
The rat smiled and said, “Teamwork makes everything easier!”
"""


def plan(script: str = CROW, seed: int = 3) -> dict[str, Any]:
    res = HeuristicSpecGenerator(catalog=CATALOG).generate(script, style="paper_cutout", seed=seed)
    assert res.lint.ok, res.lint
    return dict(res.spec)


def scene_with(spec: dict[str, Any], text: str) -> int:
    for i, sc in enumerate(spec["scenes"]):
        if any(text in c["text"] for c in sc["captions"]):
            return i
    raise AssertionError(f"no scene says {text!r}")


def things(scene: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["asset"]: o for o in scene.get("objects", [])}


def motions(ob: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [m for m in ob.get("motions", []) if m["type"] == kind]


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return plan()


# =============================================================================== the animals
class TestBothAnimalsAreThere:
    def test_the_library_draws_a_rat(self) -> None:
        a = CATALOG.assets.get("rat")
        assert a.kind == "character" and a.origin == "builtin"
        assert CATALOG.assets.get("pitcher").kind == "object"
        assert CATALOG.assets.get("pebbles").kind == "object"

    def test_the_rat_and_the_crow_are_the_cast_and_nothing_is_missing(
        self, spec: dict[str, Any]
    ) -> None:
        assert {c["id"]: c["archetype"] for c in spec["characters"]} == {
            "rat": "rat",
            "crow": "crow",
        }
        assert not spec["meta"].get("library_gaps")

    def test_both_are_on_screen_in_every_scene(self, spec: dict[str, Any]) -> None:
        for sc in spec["scenes"]:
            assert {ly["character"] for ly in sc["layers"]} == {"rat", "crow"}, sc["id"]

    def test_each_line_is_said_by_whoever_says_it(self, spec: dict[str, Any]) -> None:
        said = {c["text"]: c.get("speaker") for sc in spec["scenes"] for c in sc["captions"]}
        assert said["I'm so thirsty! Let's drink some water."] == "crow"
        assert said["Wait! The water is too low. How will we reach it?"] == "rat"
        assert said["Teamwork makes everything easier!"] == "rat"

    def test_a_small_cast_is_drawn_bigger_and_the_subtitles_leave_its_feet(
        self, spec: dict[str, Any]
    ) -> None:
        for sc in spec["scenes"]:
            assert all(ly.get("scale", 1.0) > 1.0 for ly in sc["layers"])
            assert all(c.get("anchor") == "center" for c in sc["captions"] if c["style"] != "title")

    def test_a_person_is_not_drawn_bigger(self) -> None:
        sp = plan("Mia walked to the park. Mia saw a dog. Mia smiled. The dog barked.")
        assert all("scale" not in ly for sc in sp["scenes"] for ly in sc["layers"])
        assert all(c.get("anchor", "auto") == "auto" for sc in sp["scenes"] for c in sc["captions"])

    def test_a_photo_of_a_mouse_is_not_a_second_rat(self) -> None:
        """A library object that answers to "rat" does not stand about next to the rat of the cast."""
        cat = CATALOG.fork()
        photo = replace(
            CATALOG.assets.get("pebbles"), name="mouse_photo", tags=("rat", "rodent"), origin="user"
        )
        register_asset(photo, cat)
        res = HeuristicSpecGenerator(catalog=cat).generate(CROW, style="paper_cutout", seed=3)
        used = {o["asset"] for sc in res.spec["scenes"] for o in sc.get("objects", [])}
        assert "mouse_photo" not in used
        assert "rat" in {c["id"] for c in res.spec["characters"]}


# =============================================================================== the set
class TestThePotOfWater:
    def test_the_pot_of_water_is_the_pitcher_in_every_scene_after_it_is_found(
        self, spec: dict[str, Any]
    ) -> None:
        first = scene_with(spec, "found a pot of water")
        for sc in spec["scenes"][first:]:
            assert "pitcher" in things(sc), sc["id"]
            assert "pot" not in things(sc), sc[
                "id"
            ]  # the cooking pot is not what "the pot" means here

    def test_the_tree_it_stands_near_is_there_too(self, spec: dict[str, Any]) -> None:
        first = scene_with(spec, "found a pot of water")
        assert all("tree" in things(sc) for sc in spec["scenes"][first:])

    def test_nothing_changes_places_from_scene_to_scene(self, spec: dict[str, Any]) -> None:
        first = scene_with(spec, "found a pot of water")
        spots = {
            tuple(things(sc)[name]["position"])
            for sc in spec["scenes"][first:]
            for name in ("pitcher", "tree")
        }
        assert len(spots) == 2

    def test_no_more_than_three_things_stand_on_a_set(self, spec: dict[str, Any]) -> None:
        assert all(len(sc.get("objects", [])) <= MAX_ON_SET for sc in spec["scenes"])

    def test_the_sun_of_a_sunny_day_is_in_the_sky_not_in_the_way(
        self, spec: dict[str, Any]
    ) -> None:
        sun = things(spec["scenes"][0])["sun"]
        assert sun["position"][1] < 0.5
        pitcher = things(spec["scenes"][scene_with(spec, "found a pot of water")])["pitcher"]
        assert pitcher["position"][1] > 0.7  # on the ground, not pushed off the middle by the sun


class TestTheWaterLevel:
    def test_the_water_is_low_until_the_line_that_says_it_rose(self, spec: dict[str, Any]) -> None:
        for sc in spec["scenes"][
            scene_with(spec, "found a pot of water") : scene_with(spec, "dropped them")
        ]:
            (m,) = motions(things(sc)["pitcher"], "rise")
            assert m["from"] == m["to"] < 1.0

    def test_it_rises_while_the_line_that_says_so_is_read(self, spec: dict[str, Any]) -> None:
        i = scene_with(spec, "water level rose")
        (m,) = motions(things(spec["scenes"][i])["pitcher"], "rise")
        assert m["from"] < m["to"] == 1.0
        cap = next(c for c in spec["scenes"][i]["captions"] if "rose" in c["text"])
        assert m["t0"] <= cap["t0"] + 0.1 and m["t1"] >= cap["t1"] - 0.1

    def test_it_is_full_after(self, spec: dict[str, Any]) -> None:
        for sc in spec["scenes"][scene_with(spec, "water level rose") + 1 :]:
            assert not motions(things(sc)["pitcher"], "rise"), sc["id"]  # as drawn: full

    def test_a_story_that_never_says_how_full_leaves_the_picture_as_drawn(self) -> None:
        sp = plan(
            "The farmer found a pot of water under a tree. He looked at it. He smiled. He sat down."
        )
        assert not any(motions(o, "rise") for sc in sp["scenes"] for o in sc.get("objects", []))

    def test_the_pitcher_has_a_part_that_fills(self) -> None:
        art = load_art(CATALOG.assets.get("pitcher"))
        assert {s.tag for s in art.shapes} >= {"fill"}


class TestTheStones:
    def test_they_travel_from_the_crow_to_the_pot_and_are_gone(self, spec: dict[str, Any]) -> None:
        i = scene_with(spec, "dropped them into the pot")
        sc = spec["scenes"][i]
        stone, pitcher = things(sc)["pebbles"], things(sc)["pitcher"]
        (move,) = motions(stone, "move")
        assert move["to"][0] == pitcher["position"][0]
        assert move["to"][1] < pitcher["position"][1]  # to the mouth of the pot, above its foot
        assert motions(stone, "fade")  # it goes in and is gone
        assert stone["t1"] < sc["duration_sec"]
        assert "pebbles" not in things(spec["scenes"][i + 1])

    def test_they_start_next_to_the_one_who_drops_them(self, spec: dict[str, Any]) -> None:
        sc = spec["scenes"][scene_with(spec, "dropped them into the pot")]
        crow = next(ly for ly in sc["layers"] if ly["character"] == "crow")
        at = {"left": 0.27, "center": 0.5, "right": 0.73}[crow["position"]]
        pot = things(sc)["pitcher"]["position"][0]
        x = things(sc)["pebbles"]["position"][0]
        assert min(at, pot) < x < max(at, pot)  # between the crow and the pot

    def test_they_are_heard_going_in(self, spec: dict[str, Any]) -> None:
        sc = spec["scenes"][scene_with(spec, "dropped them into the pot")]
        move = motions(things(sc)["pebbles"], "move")[0]
        pops = [x for x in sc["sfx"] if abs(x["t"] - move["t1"]) <= 0.1]
        assert [x["name"] for x in pops] == [CatalogView(CATALOG).sfx("pop")]

    def test_the_crow_bends_down_for_them(self, spec: dict[str, Any]) -> None:
        sc = spec["scenes"][scene_with(spec, "dropped them into the pot")]
        crow = next(ly for ly in sc["layers"] if ly["character"] == "crow")
        assert [a["name"] for a in crow["actions"]] == ["pick_up"]
        move = motions(things(sc)["pebbles"], "move")[0]
        assert (
            move["t0"] >= crow["actions"][0]["t1"] - 0.3
        )  # they leave the ground once she has bent down

    def test_a_cake_is_put_on_a_table_and_stays_there(self) -> None:
        sp = plan(
            "Mia carried a cake into the kitchen. Mia put the cake on the table. Mia smiled. Mia clapped."
        )
        i = scene_with(sp, "put the cake")
        here = things(sp["scenes"][i])
        (move,) = motions(here["cake"], "move")
        assert not motions(here["cake"], "fade")  # onto the table, not into it
        assert (
            move["to"][0] == here["table"]["position"][0]
            and move["to"][1] < here["table"]["position"][1]
        )
        for sc in sp["scenes"][i + 1 :]:  # and it is on it still, in every scene that follows
            now = things(sc)
            assert now["cake"]["position"][0] == now["table"]["position"][0]
            assert now["cake"]["position"][1] < now["table"]["position"][1]


class TestTheAnimalsDrink:
    def test_the_rat_goes_first_and_the_crow_after_it(self, spec: dict[str, Any]) -> None:
        sc = spec["scenes"][scene_with(spec, "happily drank")]
        acts = {ly["character"]: ly["actions"] for ly in sc["layers"]}
        bows = {who: next(a for a in al if a["name"] == "pick_up") for who, al in acts.items()}
        assert bows["rat"]["t1"] <= bows["crow"]["t0"] + 0.01
        # each steps up to the water first
        assert acts["rat"][0]["name"] == "walk" and acts["crow"][0]["name"] == "walk"

    def test_a_person_who_drinks_does_not_bow_like_an_animal(self) -> None:
        sp = plan("Mia walked into the kitchen. Mia drank a glass of water. Mia smiled. Mia waved.")
        i = scene_with(sp, "drank")
        acts = [a["name"] for ly in sp["scenes"][i]["layers"] for a in ly["actions"]]
        assert "pick_up" not in acts

    def test_two_people_who_do_the_same_thing_in_one_line_both_get_the_cue(self) -> None:
        cast = Cast(
            [Char("rat", "Rat", "rat", role="rat"), Char("crow", "Crow", "crow", role="crow")],
            False,
        )
        cues = extract_cues("The rat drank some water, and then the crow drank too.", None, cast)
        assert [(c.kind, c.subject) for c in cues] == [("drink", "rat"), ("drink", "crow")]

    def test_the_same_one_doing_it_twice_is_one_cue(self) -> None:
        cast = Cast([Char("rat", "Rat", "rat", role="rat")], False)
        cues = extract_cues("The rat drank, and drank again.", None, cast)
        assert [c.kind for c in cues] == ["drink"]


# =============================================================================== the pieces
class TestWhatAWordMeans:
    def test_a_word_that_is_part_of_an_earlier_name_means_that_thing(self) -> None:
        lib = LibraryPlanner(CATALOG)
        pitcher = CATALOG.assets.get("pitcher")
        (hit,) = lib.named_objects([(0.0, 1.0, "Put it in the pot.")], [pitcher])
        assert hit.asset.name == "pitcher"
        (alone,) = lib.named_objects([(0.0, 1.0, "Put it in the pot.")])
        assert alone.asset.name == "pot"  # with nothing before it, "pot" is the cooking pot

    def test_a_longer_name_beats_the_short_one(self) -> None:
        lib = LibraryPlanner(CATALOG)
        (hit,) = lib.named_objects([(0.0, 1.0, "She found a pot of water.")])
        assert hit.asset.name == "pitcher"

    def test_a_character_of_the_cast_is_not_also_a_thing(self) -> None:
        cat = CATALOG.fork()
        photo = replace(
            CATALOG.assets.get("pebbles"), name="mouse_photo", tags=("rat", "rodent"), origin="user"
        )
        register_asset(photo, cat)
        lib = LibraryPlanner(cat)
        line = [(0.0, 1.0, "The rat looked up.")]
        assert [n.asset.name for n in lib.named_objects(line)] == [
            "mouse_photo"
        ]  # a thing the library has
        assert (
            lib.named_objects(line, (), {"rat"}) == []
        )  # ... but the rat is somebody in this story


class TestStayingOnTheSet:
    def test_what_the_story_names_again_stays_until_the_set_changes(self) -> None:
        lib = LibraryPlanner(CATALOG)
        t = PropTracker(lib, ["a kite", "", "", "", "", "the kite flew", ""])
        beat = [(0.0, 2.0, "x", None)]
        first = t.scene("park", [(0.0, 2.0, "Mia flew a kite", None)], [], 6.0)
        assert [o["asset"] for o in first] == ["kite"]
        for _ in range(4):  # nothing names it for four scenes ...
            assert any(o["asset"] == "kite" for o in t.scene("park", beat, [], 6.0))
        assert not t.scene("street", beat, [], 6.0)  # ... until the story leaves the set

    def test_what_is_named_once_goes_after_two_scenes(self) -> None:
        lib = LibraryPlanner(CATALOG)
        t = PropTracker(lib, ["a kite", "", "", "", ""])
        beat = [(0.0, 2.0, "x", None)]
        t.scene("park", [(0.0, 2.0, "Mia flew a kite", None)], [], 6.0)
        stayed = [bool(t.scene("park", beat, [], 6.0)) for _ in range(4)]
        assert stayed == [True, True, False, False]


# =============================================================================== the engine: a rise
def _pitcher(*mots: dict[str, Any], scale: float = 1.0) -> Any:
    spec = ObjectSpec.model_validate(
        {"asset": "pitcher", "position": [0.5, 0.8], "scale": scale, "motions": list(mots)}
    )
    return build_object(
        0,
        spec,
        CATALOG.assets.get("pitcher"),
        slot=lambda _n: (0.5, 0.8),
        depth_scale=lambda _y: 1.0,
        lenient=False,
        warn=print,
    )


class TestARise:
    def test_it_is_a_motion_the_engine_and_the_linter_know(self) -> None:
        assert "rise" in MOTIONS
        assert (
            motion_problems(ObjectMotionSpec(type="rise", t0=0, t1=1, **{"from": 0.2}, to=0.9))
            == []
        )
        assert motion_problems(ObjectMotionSpec(type="rise", t0=0, t1=1, to="left"))

    def test_it_stands_where_it_starts_until_it_begins_and_where_it_ends_after(self) -> None:
        ob = _pitcher({"type": "rise", "t0": 1, "t1": 3, "from": 0.2, "to": 0.8})
        assert ob.state(0.0).rise == pytest.approx(0.2)
        assert ob.state(2.0).rise == pytest.approx(0.5)
        assert ob.state(9.0).rise == pytest.approx(0.8)

    def test_it_runs_from_nothing_to_full_by_default(self) -> None:
        ob = _pitcher({"type": "rise", "t0": 0, "t1": 2})
        assert ob.state(0.0).rise == 0.0 and ob.state(2.0).rise == 1.0

    def test_one_rise_continues_from_where_the_one_before_it_ended(self) -> None:
        later_first = [
            {"type": "rise", "t0": 3, "t1": 4, "from": 0.6, "to": 1.0},
            {"type": "rise", "t0": 1, "t1": 2, "from": 0.2, "to": 0.6},
        ]
        ob = _pitcher(*later_first)
        assert [round(ob.state(t).rise, 2) for t in (0.0, 2.5, 3.5 + 0.5)] == [0.2, 0.6, 1.0]

    def test_only_the_water_of_a_jug_stands_taller_not_the_jug(self) -> None:
        ob = _pitcher({"type": "rise", "t0": 0, "t1": 2, "from": 0.4, "to": 0.4})
        shapes = ob.shapes_for(ob.state(1.0))
        sy = {s.tag: round(s.xf[4], 3) for s in shapes if s.xf}
        assert sy["fill"] == pytest.approx(0.4 * sy[""])
        assert sy["shadow_caster"] == sy[""]

    def test_a_picture_without_such_a_part_stretches_as_a_whole(self) -> None:
        spec = ObjectSpec.model_validate(
            {
                "asset": "tree",
                "position": [0.5, 0.8],
                "motions": [{"type": "rise", "t0": 0, "t1": 2, "from": 0.5, "to": 0.5}],
            }
        )
        ob = build_object(
            0,
            spec,
            CATALOG.assets.get("tree"),
            slot=lambda _n: (0.5, 0.8),
            depth_scale=lambda _y: 1.0,
            lenient=False,
            warn=print,
        )
        assert {round(s.xf[4] / s.xf[3], 3) for s in ob.shapes_for(ob.state(1.0)) if s.xf} == {0.5}

    def test_nothing_of_it_is_drawn_while_it_has_not_risen_at_all(self) -> None:
        spec = ObjectSpec.model_validate(
            {
                "asset": "tree",
                "position": [0.5, 0.8],
                "motions": [{"type": "rise", "t0": 1, "t1": 2}],
            }
        )
        ob = build_object(
            0,
            spec,
            CATALOG.assets.get("tree"),
            slot=lambda _n: (0.5, 0.8),
            depth_scale=lambda _y: 1.0,
            lenient=False,
            warn=print,
        )
        assert ob.shapes_for(ob.state(0.5)) == []
        assert ob.shapes_for(ob.state(3.0))

    def test_the_water_stays_inside_the_jug_at_every_level(self) -> None:
        """The walls are drawn over the water, and the water is no wider than the jug's body: so it never shows outside."""
        from reel.core.ir import bbox

        art = load_art(CATALOG.assets.get("pitcher"))
        walls = [bbox(s.geom) for s in art.shapes if (s.fill or "").startswith("@clay=")]
        left, right = min(b[0] for b in walls), max(b[2] for b in walls)
        for s in (s for s in art.shapes if s.tag == "fill"):
            x0, _y0, x1, _y1 = bbox(s.geom)
            assert left <= x0 and x1 <= right


# =============================================================================== a story nobody tuned the planner for
KITE = """Ravi bought a red kite from the market. He ran to the park and flew the kite high in the sky.
Suddenly the string broke and the kite flew away. Ravi cried.
His friend Meena brought him a new kite. They flew it together and laughed.
"""
SEED = """Mia planted a tiny seed in the garden and watered it every day.
Slowly, a green plant grew taller and taller.
One morning a beautiful flower bloomed. Mia smiled and picked the flower.
"""


class TestAnyStory:
    def test_a_heroine_who_is_named_once_and_then_called_she_is_cast(self) -> None:
        sp = plan(
            "Sita carried an empty bucket to the market. She put it down and smiled. She sat on a box. She waved at the shopkeeper."
        )
        assert "sita" in {c["id"] for c in sp["characters"]}

    @pytest.mark.parametrize(
        "script",
        [
            "Night fell over the village. She walked home slowly. She smiled. She slept.",
            "Birds sang in the trees. She listened and smiled. She waved. She laughed.",
            "Thunder rolled across the sky. He ran inside. He sat down. He laughed.",
        ],
    )
    def test_a_word_that_only_opens_the_sentence_is_nobody(self, script: str) -> None:
        sp = plan(script)
        assert not {"night", "birds", "thunder"} & {c["id"] for c in sp["characters"]}

    def test_a_story_of_animals_that_names_no_place_is_in_the_woods(self) -> None:
        sp = plan(
            'A hare laughed at a slow tortoise. "Let\'s have a race!" said the tortoise. The hare ran fast and took a nap. '
            "The tortoise kept walking slowly. When the hare woke up, the tortoise had won the race."
        )
        assert {sc["background"]["template"] for sc in sp["scenes"]} == {"forest"}

    def test_farm_animals_are_on_the_farm(self) -> None:
        sp = plan(
            "A cow met a horse. The cow said hello. The horse laughed. The cow smiled. The horse waved."
        )
        assert {sc["background"]["template"] for sc in sp["scenes"]} == {"farm"}

    def test_a_place_named_only_at_the_end_is_where_the_story_goes_not_where_it_starts(
        self,
    ) -> None:
        sp = plan(
            "A hungry dog found a big bone near a river. He picked up the bone and walked across a bridge. "
            "He saw his reflection in the water and barked. The bone fell into the river. The dog went home hungry and sad."
        )
        sets = [sc["background"]["template"] for sc in sp["scenes"]]
        assert sets[0] != "room" and sets[-1] == "room"

    def test_what_flew_away_is_gone_until_the_story_brings_a_new_one(self) -> None:
        sp = plan(KITE)
        i = scene_with(sp, "flew away")
        (leave,) = motions(things(sp["scenes"][i])["kite"], "move")
        assert leave["to"][0] > 1.0 or leave["to"][0] < 0.0  # out of the frame
        j = scene_with(sp, "a new kite")
        assert all("kite" not in things(sc) for sc in sp["scenes"][i + 1 : j])
        assert "kite" in things(sp["scenes"][j])

    def test_a_balloon_that_popped_is_gone_where_it_stood(self) -> None:
        sp = plan(
            "Mia held a red balloon. The balloon popped. Mia cried. Mia sat down. Mia smiled."
        )
        i = scene_with(sp, "popped")
        balloon = things(sp["scenes"][i])["balloon"]
        assert motions(balloon, "fade") and not motions(balloon, "move")
        assert all("balloon" not in things(sc) for sc in sp["scenes"][i + 1 :])

    def test_a_thing_that_grows_grows_from_a_small_one(self) -> None:
        sp = plan(SEED)
        i = scene_with(sp, "bloomed")
        (m,) = motions(things(sp["scenes"][i])["flower"], "grow")
        assert m["from"] < 0.3 and m["to"] == 1.0
        assert not motions(things(sp["scenes"][i + 1])["flower"], "grow")  # grown

    def test_it_is_small_in_the_scenes_before_when_the_story_says_it_grew_later(self) -> None:
        sp = plan(
            "Mia planted a flower in the garden. Mia watered the flower. Mia sat down. Slowly the flower grew. Mia smiled."
        )
        first = next(i for i, sc in enumerate(sp["scenes"]) if "flower" in things(sc))
        (m,) = motions(things(sp["scenes"][first])["flower"], "grow")
        assert m["from"] == m["to"] < 0.3

    def test_a_growth_word_about_somebody_else_does_not_grow_the_thing(self) -> None:
        sp = plan(
            "Ravi grew tired of waiting. Ravi saw a kite in the sky. Ravi smiled. Ravi laughed. Ravi waved."
        )
        assert not any(motions(o, "grow") for sc in sp["scenes"] for o in sc.get("objects", []))


HOSPITAL = """Dr. Meera walked into the hospital early in the morning. A little boy sat on the bed and cried.
"My tummy hurts," said the boy. The doctor smiled and listened to his heartbeat.
She gave him some medicine and a lollipop. The boy stopped crying and thanked the doctor.
"""
HINGLISH_CROW = """Ek garmi ke din ek pyasa kauwa paani dhoondh raha tha. Usne ek ghade mein thoda paani dekha.
Kauwa ki chonch paani tak nahi pahunchi. Phir usne kai chhote kankad uthaye aur ghade mein daal diye.
Paani upar aa gaya aur kauwe ne paani pee liya. Kauwa bahut khush hua.
"""


class TestWhatTheOtherStoriesShowed:
    def test_an_english_script_that_says_the_a_lot_is_not_hinglish(self) -> None:
        """ "the" is one word in eight of this one, and a Hinglish "the" (they were) is no reason to read it without its articles."""
        from reel.llm.heuristic import _is_hinglish, parse_script

        assert not _is_hinglish(parse_script(HOSPITAL).sents)
        sp = plan(HOSPITAL)
        assert {c["archetype"] for c in sp["characters"]} == {
            "doctor",
            "kid",
        }  # the boy is somebody

    def test_real_hinglish_is_still_hinglish(self) -> None:
        from reel.llm.heuristic import _is_hinglish, parse_script

        assert _is_hinglish(parse_script(HINGLISH_CROW).sents)

    def test_a_little_girl_who_comes_in_is_a_guest_of_that_scene(self) -> None:
        sp = plan(
            "A small robot named Bolt woke up in an old factory. He looked around. Bolt waved. Bolt danced. "
            'A little girl heard the music and came in. "Is that your toy?" asked Bolt.'
        )
        assert "kid" in {c["archetype"] for c in sp["characters"]}
        i = scene_with(sp, "little girl")
        assert any(ly["character"] == "kid" for ly in sp["scenes"][i]["layers"])
        assert not any(ly["character"] == "kid" for ly in sp["scenes"][0]["layers"])

    def test_a_tiny_flag_is_drawn_small_and_does_not_stay_for_the_whole_story(self) -> None:
        sp = plan(
            "Ria built a sandcastle at the beach. She decorated it with a tiny flag. A wave washed it away. Ria cried. "
            "Ria sat down. Ria smiled. Ria laughed. Ria waved."
        )
        i = scene_with(sp, "tiny flag")
        sizes = [
            things(sc)["indian_flag"]["scale"] for sc in sp["scenes"] if "indian_flag" in things(sc)
        ]
        assert sizes and all(x < 0.5 for x in sizes)
        assert "indian_flag" not in things(sp["scenes"][-1]) and i < len(sp["scenes"]) - 1

    def test_the_water_rises_in_a_hinglish_story_too(self) -> None:
        sp = plan(HINGLISH_CROW)
        i = scene_with(sp, "upar aa gaya")
        (m,) = motions(things(sp["scenes"][i])["pitcher"], "rise")
        assert m["from"] < m["to"] == 1.0
        first = next(sc for sc in sp["scenes"] if "pitcher" in things(sc))
        (low,) = motions(things(first)["pitcher"], "rise")
        assert low["from"] == low["to"] < 1.0

    def test_a_small_rock_is_not_a_pile_of_pebbles(self) -> None:
        """ "rock" is also a kind of music: it is not what the pile of pebbles answers to."""
        assert "rock" not in CATALOG.assets.get("pebbles").tags
