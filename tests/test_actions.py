"""The starter action library: registry contents, param schemas, motion quality, planner fallbacks."""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from reel.core.catalog import CATALOG, Catalog
from reel.core.debug import bake_action
from reel.core.lint import lint_data
from reel.core.planner import PlanError, PlanReport, SimpleWorld, bake_plan, plan_layer
from reel.core.rig import CHANNELS, Rig
from reel.core.spec import ReelSpec

STARTER = [
    "idle",
    "walk",
    "run",
    "jump",
    "wave",
    "point",
    "talk",
    "laugh",
    "cry",
    "think",
    "fall",
    "pick_up",
    "enter_from",
    "exit_to",
    "look_at",
    "surprise",
]
ARCHETYPES = ["everyman", "kid", "elder", "hero", "robot", "boss"]


def test_all_starter_actions_are_registered_with_metadata() -> None:
    assert set(STARTER) <= set(CATALOG.actions.names())
    for name in STARTER:
        d = CATALOG.actions.get(name)
        assert d.summary, (
            f"{name} needs a summary (it is shown in `reel list` and given to the LLM)"
        )
        assert d.category in {"locomotion", "gesture", "expression", "posture"}
        assert d.min_duration > 0 and d.default_duration >= d.min_duration


@pytest.mark.parametrize("name", STARTER)
@pytest.mark.parametrize("arch", ARCHETYPES)
def test_every_action_bakes_finite_and_within_channel_limits(name: str, arch: str) -> None:
    props = ("briefcase",) if name == "pick_up" else ()
    params = {"prop": "briefcase"} if name == "pick_up" else None
    baked, t0, t1 = bake_action(name, archetype=arch, props=props, params=params)
    assert np.isfinite(baked.matrix).all()
    for ch, spec in CHANNELS.items():
        v = baked.channel(ch)
        assert v.min() >= spec.lo - 1e-9 and v.max() <= spec.hi + 1e-9, f"{name}/{arch}: {ch}"
    # the action must actually change something relative to its own lead-in
    lead = baked.pose(baked.frame_index(t0 - 0.05))
    mid = baked.pose(baked.frame_index((t0 + t1) / 2))
    moved = [c for c in CHANNELS if abs(mid[c] - lead[c]) > 1e-3 and c not in ("breath", "sway")]
    assert moved or name == "idle", f"{name} did not move anything"


@pytest.mark.parametrize("name", STARTER)
def test_actions_survive_very_short_and_very_long_windows(name: str) -> None:
    props = ("briefcase",) if name == "pick_up" else ()
    params = {"prop": "briefcase"} if name == "pick_up" else None
    for dur in (0.35, 6.0):
        baked, _, _ = bake_action(name, duration=dur, props=props, params=params)
        assert np.isfinite(baked.matrix).all()


@pytest.mark.parametrize("name", STARTER)
def test_params_are_strict(name: str) -> None:
    d = CATALOG.actions.get(name)
    with pytest.raises(ValidationError):
        d.parse({"definitely_not_a_param": 1})
    d.parse({})  # all params have defaults


def test_actions_are_deterministic() -> None:
    a, _, _ = bake_action("talk", params={"text": "Hello there, how are you today?"})
    b, _, _ = bake_action("talk", params={"text": "Hello there, how are you today?"})
    assert np.array_equal(a.matrix, b.matrix)


def test_talk_mouth_follows_the_words() -> None:
    quiet, t0, t1 = bake_action("talk", params={"text": "Hi.", "gestures": False}, duration=3.0)
    busy, _, _ = bake_action(
        "talk",
        params={"text": "Hello there, how are you doing today my friend?", "gestures": False},
        duration=3.0,
    )
    a = quiet.channel("mouth_open")[int(t0 * 30) : int(t1 * 30)]
    b = busy.channel("mouth_open")[int(t0 * 30) : int(t1 * 30)]
    assert b.mean() > a.mean() * 1.5  # more syllables -> more mouth activity
    assert b.max() > 0.3


def test_talk_uses_word_timings_when_given() -> None:
    words = [(0.2, 0.5, "hello"), (1.5, 1.8, "world")]
    baked, t0, _t1 = bake_action("talk", params={"words": words, "gestures": False}, duration=2.5)
    m = baked.channel("mouth_open")
    s = int(t0 * 30)
    assert m[s + 9 : s + 15].max() > 0.2  # speaking around 0.2-0.5 s
    assert m[s + 21 : s + 40].max() < 0.1  # silent in the gap between the words


def test_walk_has_no_foot_slide() -> None:
    """While a foot is planted its world x must stay (nearly) put relative to the body's travel."""
    baked, t0, t1 = bake_action("walk", params={"to": [0.8, 0.8]}, duration=3.0)
    rig = Rig(CATALOG.archetypes.get("everyman"))
    world = SimpleWorld()
    scale = 1.12 * world.depth_scale(0.8)
    contact_x: list[tuple[int, str, float]] = []
    lo, hi = int((t0 + 0.7) * 30), int((t1 - 0.7) * 30)  # cruising section only
    for i in range(lo, hi):
        pose = baked.pose(i)
        fig = rig.solve(pose)
        face = 1.0 if pose["face"] >= 0 else -1.0
        for s in ("r", "l"):
            sole = max(fig.pts[f"heel_{s}"][1], fig.pts[f"toe_{s}"][1])
            if abs(sole) < 1.5:  # this foot is on the ground
                wx = baked.root_x[i] * 1080 + face * fig.pts[f"ankle_{s}"][0] * scale
                contact_x.append((i, s, wx))
    assert len(contact_x) > 10
    slides, travels = [], []
    for (i0, s0, x0), (i1, s1, x1) in itertools.pairwise(contact_x):
        if s0 == s1 and i1 == i0 + 1:
            slides.append(abs(x1 - x0))
            travels.append(abs(baked.root_x[i1] - baked.root_x[i0]) * 1080)
    assert slides, "no consecutive contact frames found"
    assert float(np.mean(slides)) < 0.35 * float(np.mean(travels))  # planted feet barely slide


def test_jump_leaves_the_ground_and_lands() -> None:
    baked, t0, _t1 = bake_action("jump", params={"height": 0.4}, duration=1.0)
    lift = baked.channel("lift")
    assert lift[: int(t0 * 30)].max() == 0
    assert lift.max() > 0.3 * CATALOG.archetypes.get("everyman").dims.height
    assert lift[-1] == pytest.approx(0, abs=1e-6)
    sq = baked.channel("squash")
    assert sq.max() > 0.1 and sq.min() < -0.05  # squash on landing/anticipation, stretch in flight
    ev = [e.name for e in baked.events]
    assert ev == ["jump", "land"]


def test_walk_emits_footsteps_alternating_feet() -> None:
    baked, _, _ = bake_action("walk", params={"to": [0.8, 0.8]}, duration=3.0)
    steps = [e.data["foot"] for e in baked.events if e.name == "footstep"]
    assert len(steps) >= 3
    assert all(a != b for a, b in itertools.pairwise(steps))


def test_surprise_has_a_hold() -> None:
    baked, t0, _t1 = bake_action("surprise", params={"hold": 0.6}, duration=1.4)
    mo = baked.channel("mouth_open")
    seg = mo[int((t0 + 0.4) * 30) : int((t0 + 0.9) * 30)]
    assert seg.min() > 0.5 and (seg.max() - seg.min()) < 0.05  # frozen: comedic hold frames


def test_point_direction_param_controls_arm() -> None:
    # hand = right (front) arm at x=0.4 => facing +x; pointing up must raise the arm well above horizontal
    up, t0, t1 = bake_action("point", params={"direction": "up"})
    fwd, _, _ = bake_action("point", params={"direction": "forward"})
    m = int(((t0 + t1) / 2) * 30)
    rig = Rig(CATALOG.archetypes.get("everyman"))
    hy_up = rig.solve(up.pose(m)).pts["wrist_r"][1]
    hy_fwd = rig.solve(fwd.pose(m)).pts["wrist_r"][1]
    assert hy_up < hy_fwd - 40  # y is down: smaller y = higher


def test_look_at_turns_body_when_target_is_behind() -> None:
    from reel.core.animation import CharacterInfo, LayerPlan, PlacedAction, bake_layer
    from reel.core.planner import CHAR_UNIT

    world = SimpleWorld()
    arch = CATALOG.archetypes.get("everyman")
    plan = LayerPlan(
        CharacterInfo("c", "everyman"),
        Rig(arch),
        (0.4, 0.8),
        lambda y: CHAR_UNIT * world.depth_scale(y),
        seed=1,
    )
    d = CATALOG.actions.get("look_at")
    plan.actions = [PlacedAction(0, "look_at", 0.5, 2.0, d.parse({"target": [0.05, 0.8]}), d.fn)]
    b = bake_layer(plan, world, fps=30, duration=3.0)
    assert b.channel("face")[0] > 0.9 and b.channel("face")[-1] < -0.9  # turned to look left


def test_pick_up_unknown_prop_gets_a_check_message() -> None:
    d = CATALOG.actions.get("pick_up")
    assert d.check is not None
    from types import SimpleNamespace

    msgs = d.check({"prop": "briefcase"}, SimpleNamespace(props=["hat"]))
    assert msgs and "briefcase" in msgs[0]
    assert d.check({"prop": "hat"}, SimpleNamespace(props=["hat"])) == []


# ------------------------------------------------------------------ planner
def _spec(
    actions: list[dict[str, Any]], *, captions: list[dict[str, Any]] | None = None
) -> ReelSpec:
    return ReelSpec.model_validate(
        {
            "meta": {"title": "t", "style": "flat_vector", "seed": 3},
            "characters": [{"id": "ana", "archetype": "everyman", "props": ["hat"]}],
            "scenes": [
                {
                    "id": "s",
                    "duration_sec": 6,
                    "background": {"template": "room"},
                    "layers": [{"character": "ana", "position": "left", "actions": actions}],
                    "captions": captions or [],
                }
            ],
        }
    )


def test_planner_strict_raises_on_unknown_action_and_lenient_falls_back_to_idle() -> None:
    spec = _spec([{"name": "moonwalk", "t0": 1, "t1": 3}])
    world = SimpleWorld()
    sc, ly = spec.scenes[0], spec.scenes[0].layers[0]
    with pytest.raises(PlanError, match="moonwalk"):
        plan_layer(spec, sc, ly, 0, world)
    rep = PlanReport()
    plan = plan_layer(spec, sc, ly, 0, world, lenient=True, report=rep)
    assert plan is not None
    assert [a.name for a in plan.actions] == ["idle"]
    assert any("moonwalk" in w and "idle" in w for w in rep.warnings)
    assert np.isfinite(bake_plan(plan, world, fps=30, duration=6).matrix).all()  # still renders


def test_planner_bad_params_strict_vs_lenient() -> None:
    spec = _spec([{"name": "walk", "t0": 1, "t1": 3, "params": {"speeed": 9}}])
    world = SimpleWorld()
    sc, ly = spec.scenes[0], spec.scenes[0].layers[0]
    with pytest.raises(PlanError):
        plan_layer(spec, sc, ly, 0, world)
    rep = PlanReport()
    assert plan_layer(spec, sc, ly, 0, world, lenient=True, report=rep) is not None
    assert any("bad params" in w for w in rep.warnings)


def test_planner_resolves_slot_positions() -> None:
    spec = _spec([])
    plan = plan_layer(spec, spec.scenes[0], spec.scenes[0].layers[0], 0, SimpleWorld())
    assert plan is not None and plan.position == (0.27, 0.8)


def test_speaker_captions_inject_talk_actions_unless_explicit() -> None:
    caps = [{"text": "Hello world", "t0": 1.0, "t1": 2.5, "style": "subtitle", "speaker": "ana"}]
    spec = _spec([], captions=caps)
    world = SimpleWorld()
    plan = plan_layer(spec, spec.scenes[0], spec.scenes[0].layers[0], 0, world)
    assert plan is not None
    talks = [a for a in plan.actions if a.name == "talk"]
    assert len(talks) == 1 and talks[0].t0 == 1.0 and talks[0].params.text == "Hello world"

    spec = _spec([{"name": "talk", "t0": 0.5, "t1": 3.0}], captions=caps)
    plan = plan_layer(spec, spec.scenes[0], spec.scenes[0].layers[0], 0, world)
    assert (
        plan is not None and len([a for a in plan.actions if a.name == "talk"]) == 1
    )  # no duplicate


def test_tts_word_timings_flow_into_talk_params() -> None:
    caps = [{"text": "Hello world", "t0": 1.0, "t1": 2.5, "style": "subtitle", "speaker": "ana"}]
    spec = _spec([], captions=caps)
    plan = plan_layer(
        spec,
        spec.scenes[0],
        spec.scenes[0].layers[0],
        0,
        SimpleWorld(),
        word_timings={0: [(1.1, 1.5, "Hello"), (1.7, 2.2, "world")]},
    )
    assert plan is not None
    talk = next(a for a in plan.actions if a.name == "talk")
    assert talk.params.words == [
        (pytest.approx(0.1), pytest.approx(0.5), "Hello"),
        (pytest.approx(0.7), pytest.approx(1.2), "world"),
    ]


# ------------------------------------------------------------------ lint integration with the real registry
def _lint_spec(extra_actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "meta": {"title": "t", "style": "flat_vector"},
        "characters": [{"id": "ana", "archetype": "everyman", "props": ["hat"]}],
        "scenes": [
            {
                "id": "s",
                "duration_sec": 6,
                "background": {"template": "room"},
                "layers": [{"character": "ana", "position": [0.3, 0.8], "actions": extra_actions}],
            }
        ],
    }


def test_lint_knows_real_actions_and_their_params() -> None:
    cat = CATALOG
    rep = lint_data(
        _lint_spec([{"name": "walk", "t0": 0, "t1": 2, "params": {"spedd": 2}}]),
        options=__import__("reel.core.lint", fromlist=["LintOptions"]).LintOptions(
            check_duration=False
        ),
    )
    assert "PARAMS_INVALID" in rep.codes()
    assert "walk" in cat.actions
    assert "action" not in rep.missing()


def test_lint_reports_unknown_action_against_real_registry() -> None:
    rep = lint_data(
        _lint_spec([{"name": "moonwalk", "t0": 0, "t1": 2}]),
        options=__import__("reel.core.lint", fromlist=["LintOptions"]).LintOptions(
            check_duration=False
        ),
    )
    assert rep.missing()["action"]["moonwalk"]
    issue = next(i for i in rep.issues if i.code == "REGISTRY_MISSING" and i.kind == "action")
    assert "walk" in (issue.hint or "") or "run" in (issue.hint or "")  # suggests near neighbours


def test_a_plugin_action_registered_in_an_isolated_catalog_is_independent() -> None:
    from reel.actions.base import ParamsBase, register_action
    from reel.core.animation import ActionContext, Clip

    iso = Catalog()

    class P(ParamsBase):
        n: int = 1

    @register_action("spin", params_schema=P, catalog=iso, summary="spin round")
    def spin(ctx: ActionContext, p: P) -> Clip:
        c = ctx.clip()
        c.key("rot", [(0, 0), (ctx.duration, 360 * p.n)])
        return c

    assert "spin" in iso.actions and "spin" not in CATALOG.actions
    assert iso.actions.get("spin").parse({"n": 2}).n == 2  # type: ignore[attr-defined]
    assert math.isclose(iso.actions.get("spin").default_duration, 1.5)


def _variants(model: type) -> list[dict[str, Any]]:
    """One-at-a-time param variations: every Literal choice, both bools, numeric bounds."""
    import typing

    out: list[dict[str, Any]] = [{}]
    for name, f in model.model_fields.items():  # type: ignore[attr-defined]
        ann = f.annotation
        origin = typing.get_origin(ann)
        if origin is typing.Literal:
            out += [{name: v} for v in typing.get_args(ann)]
        elif ann is bool:
            out += [{name: True}, {name: False}]
        elif ann is float or ann is int:
            lo = hi = None
            for m in f.metadata:
                lo = getattr(m, "ge", lo)
                hi = getattr(m, "le", hi)
                lo = getattr(m, "gt", lo) if lo is None else lo
            for v in (lo, hi):
                if v is not None:
                    out.append({name: ann(v)})
    return out


@pytest.mark.parametrize("name", STARTER)
def test_every_param_choice_and_bound_bakes_cleanly(name: str) -> None:
    """Sweep each action's parameters one at a time (all enum values, bools, numeric bounds)."""
    d = CATALOG.actions.get(name)
    if d.params_model is None:
        return
    for variant in _variants(d.params_model):
        params = {**({"prop": "briefcase"} if name == "pick_up" else {}), **variant}
        props = ("briefcase",) if name == "pick_up" else ()
        for dur in (d.default_duration, max(d.min_duration, 0.6)):
            baked, _, _ = bake_action(name, params=params, duration=dur, props=props)
            assert np.isfinite(baked.matrix).all(), f"{name} {variant} dur={dur}"


@pytest.mark.parametrize(
    ("word", "syllables"),
    [
        ("cat", 1), ("the", 1), ("make", 1), ("makes", 1), ("walked", 1), ("loved", 1), ("played", 1),
        ("wanted", 2), ("needed", 2), ("boxes", 2), ("wishes", 2), ("places", 2), ("table", 2),
        ("people", 2), ("umbrella", 3), ("beautiful", 3), ("adventures", 3), ("hello", 2), ("", 0), ("...", 0),
    ],
)  # fmt: skip
def test_count_syllables_handles_silent_endings(word: str, syllables: int) -> None:
    from reel.actions.base import count_syllables

    assert count_syllables(word) == syllables
