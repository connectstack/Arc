"""Turn spec layers into animation plans (and bake them).

This is the glue between the spec ("walk from t=1 to t=3") and the animator: it
resolves characters/archetypes/props, validates action params, applies the lenient
fallbacks (unknown action -> idle, bad params -> defaults) and injects automatic
`talk` animation for captions that name a speaker.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from reel.core.animation import (
    BakedLayer,
    CharacterInfo,
    LayerPlan,
    PlacedAction,
    Point,
    bake_layer,
)
from reel.core.archetypes import PropDef
from reel.core.catalog import CATALOG, Catalog
from reel.core.rig import Rig
from reel.core.rng import stable_int
from reel.core.spec import CaptionSpec, CharacterSpec, LayerSpec, ReelSpec, SceneSpec

#: Global character size: screen px per design px at layer scale 1 and depth scale 1.
CHAR_UNIT = 1.12
DEFAULT_GROUND_Y = 0.80

UNIVERSAL_SLOT_X: dict[str, float] = {
    "far_left": 0.13,
    "left": 0.27,
    "center": 0.50,
    "right": 0.73,
    "far_right": 0.87,
    "off_left": -0.62,
    "off_right": 1.62,
}


class PlanError(ValueError):
    """A spec problem that cannot be papered over (only raised when not lenient)."""


@dataclass
class SimpleWorld:
    """Minimal StageWorld: universal slots, a flat ground and optional perspective."""

    frame_size: tuple[int, int] = (1080, 1920)
    ground_y: float = DEFAULT_GROUND_Y
    perspective: float = 1.0  # scale change per unit of screen-y
    extra_slots: dict[str, Point] = field(default_factory=dict)
    positions: Callable[[str, float], Point | None] | None = None

    def slot(self, name: str) -> Point | None:
        if name in self.extra_slots:
            return self.extra_slots[name]
        if name in UNIVERSAL_SLOT_X:
            return (UNIVERSAL_SLOT_X[name], self.ground_y)
        return None

    def position_of(self, char_id: str, t: float) -> Point | None:
        return self.positions(char_id, t) if self.positions else None

    def depth_scale(self, y: float) -> float:
        return max(0.35, 1.0 + self.perspective * (y - DEFAULT_GROUND_Y))


@dataclass
class PlanReport:
    warnings: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


def character_info(
    ch: CharacterSpec, catalog: Catalog, *, lenient: bool, report: PlanReport
) -> tuple[CharacterInfo, Rig]:
    if ch.archetype in catalog.archetypes:
        arch = catalog.archetypes.get(ch.archetype)
    elif lenient:
        report.warn(f"unknown archetype {ch.archetype!r} for {ch.id!r}; using 'everyman'")
        arch = catalog.archetypes.get("everyman")
    else:
        raise PlanError(f"unknown archetype {ch.archetype!r} for character {ch.id!r}")
    props: list[str] = []
    defs: list[PropDef] = []
    for name in ch.props:
        if name in catalog.props:
            props.append(name)
            defs.append(catalog.props.get(name))
        elif lenient:
            report.warn(f"unknown prop {name!r} on {ch.id!r}; skipped")
        else:
            raise PlanError(f"unknown prop {name!r} on character {ch.id!r}")
    return CharacterInfo(ch.id, arch.name, tuple(props), tuple(defs)), Rig(arch)


def _speaks(c: CaptionSpec) -> bool:
    return c.speak if c.speak is not None else c.style == "subtitle"


def _depth_scaler(unit: float, world: Any) -> Callable[[float], float]:
    def scale_at(y: float) -> float:
        return float(unit * world.depth_scale(y))

    return scale_at


def plan_layer(
    spec: ReelSpec,
    scene: SceneSpec,
    layer: LayerSpec,
    layer_index: int,
    world: Any,
    *,
    catalog: Catalog | None = None,
    lenient: bool = False,
    report: PlanReport | None = None,
    word_timings: dict[int, list[tuple[float, float, str]]] | None = None,
) -> LayerPlan | None:
    """Plan one layer.  Returns None if the layer's character is missing and ``lenient``."""
    cat = catalog or CATALOG
    rep = report if report is not None else PlanReport()
    ch = spec.character(layer.character)
    if ch is None:
        if lenient:
            rep.warn(f"scene {scene.id!r}: undefined character {layer.character!r}; layer skipped")
            return None
        raise PlanError(f"scene {scene.id!r}: undefined character {layer.character!r}")
    info, rig = character_info(ch, cat, lenient=lenient, report=rep)

    pos: Point
    if isinstance(layer.position, str):
        slot = world.slot(layer.position)
        if slot is None:
            if not lenient:
                raise PlanError(f"scene {scene.id!r}: unknown position slot {layer.position!r}")
            rep.warn(f"scene {scene.id!r}: unknown slot {layer.position!r}; using center")
            slot = world.slot("center") or (0.5, DEFAULT_GROUND_Y)
        pos = slot
    else:
        pos = (float(layer.position[0]), float(layer.position[1]))

    unit = CHAR_UNIT * layer.scale
    plan = LayerPlan(
        char=info,
        rig=rig,
        position=pos,
        px_scale_at=_depth_scaler(unit, world),
        facing=layer.facing,
        seed=stable_int(spec.meta.seed, scene.id, layer_index, layer.character),
    )

    placed: list[PlacedAction] = []
    for i, a in enumerate(layer.actions):
        if a.t1 <= a.t0:
            rep.warn(f"scene {scene.id!r}: action {a.name!r} has t1 <= t0; skipped")
            continue
        name = a.name
        if name not in cat.actions:
            if not lenient:
                raise PlanError(f"scene {scene.id!r}: unknown action {a.name!r}")
            rep.warn(f"scene {scene.id!r}: unknown action {a.name!r}; falling back to 'idle'")
            name = "idle"
        defn = cat.actions.get(name)
        raw = a.params if name == a.name else {}
        try:
            params = defn.parse(raw)
        except ValidationError as exc:
            if not lenient:
                raise PlanError(f"scene {scene.id!r}: bad params for {a.name!r}: {exc}") from exc
            rep.warn(f"scene {scene.id!r}: bad params for {a.name!r}; using defaults")
            params = defn.parse({})
        placed.append(PlacedAction(i, name, a.t0, a.t1, params, defn.fn, defn.moves_root))

    # captions that name a speaker make that character talk (unless a talk action already does)
    if "talk" in cat.actions:
        talk_def = cat.actions.get("talk")
        for ci, cap in enumerate(scene.captions):
            if cap.speaker != layer.character or not _speaks(cap) or cap.t1 <= cap.t0:
                continue
            if any(p.name == "talk" and p.t0 < cap.t1 and p.t1 > cap.t0 for p in placed):
                continue
            talk_raw: dict[str, Any] = {"text": cap.text}
            if word_timings and ci in word_timings:
                talk_raw["words"] = [
                    (w0 - cap.t0, w1 - cap.t0, w) for (w0, w1, w) in word_timings[ci]
                ]
            placed.append(
                PlacedAction(
                    1000 + ci, "talk", cap.t0, cap.t1, talk_def.parse(talk_raw), talk_def.fn
                )
            )
    plan.actions = placed
    return plan


def bake_plan(plan: LayerPlan, world: Any, *, fps: int, duration: float) -> BakedLayer:
    return bake_layer(plan, world, fps=fps, duration=duration)
