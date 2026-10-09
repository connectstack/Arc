"""Match-cut helper: make scene B start exactly where scene A's character ended.

A match cut is a hard cut that hides itself because the subject keeps the same screen position,
size and camera framing across the cut.  `match_cut` bakes the outgoing scene, reads where the
character ends up (and the camera state), and rewrites the incoming scene's layer position/scale
and opening camera to match, then makes the transition a plain cut.  Everything is computed from
the spec - no hand-tuning of coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass

from reel.core.catalog import CATALOG, Catalog
from reel.core.fx import FxConfig
from reel.core.planner import CHAR_UNIT
from reel.core.spec import CameraMoveSpec, ReelSpec, SceneSpec
from reel.core.stage import RenderConfig, SceneStage


@dataclass(frozen=True)
class MatchResult:
    spec: ReelSpec
    position: tuple[float, float]
    scale: float
    camera: dict[str, float]
    notes: list[str]


def _scene_index(spec: ReelSpec, ref: str | int) -> int:
    if isinstance(ref, int):
        if not 0 <= ref < len(spec.scenes):
            raise IndexError(f"scene index {ref} out of range (0..{len(spec.scenes) - 1})")
        return ref
    for i, s in enumerate(spec.scenes):
        if s.id == ref:
            return i
    raise KeyError(f"unknown scene {ref!r}; scenes: {[s.id for s in spec.scenes]}")


def end_state(
    spec: ReelSpec, scene: int, character: str, *, catalog: Catalog | None = None
) -> tuple[tuple[float, float], float, dict[str, float]]:
    """Bake ``scene`` and return (final foot position, effective on-screen scale factor, final camera state)."""
    cat = catalog or CATALOG
    cfg = RenderConfig(
        (54, 96),
        spec.meta.fps,
        spec.meta.seed,
        spec.meta.style,
        True,
        FxConfig(),
        (0.1, 0.2, 0.07, 0.07),
    )
    st = SceneStage(spec, scene, cfg, catalog=cat)
    lr = next((x for x in st.layers if x.char_id == character), None)
    if lr is None:
        raise KeyError(f"character {character!r} has no layer in scene {spec.scenes[scene].id!r}")
    last = lr.baked.n - 1
    x, y = lr.baked.root(last)
    px_scale = lr.plan.px_scale_at(y)  # screen px per design px incl. layer scale and depth
    cam = st.camera.state((st.n_frames - 1) / spec.meta.fps)
    return (
        (x, y),
        px_scale,
        {"pan_x": cam.pan_x, "pan_y": cam.pan_y, "zoom": cam.zoom, "dolly": cam.dolly},
    )


def match_cut(
    spec: ReelSpec,
    from_scene: str | int,
    to_scene: str | int,
    character: str,
    *,
    keep_camera: bool = True,
    catalog: Catalog | None = None,
) -> MatchResult:
    """Return a copy of ``spec`` whose ``to_scene`` opens on ``character`` exactly where ``from_scene`` left them."""
    cat = catalog or CATALOG
    a, b = _scene_index(spec, from_scene), _scene_index(spec, to_scene)
    if b != a + 1:
        raise ValueError(
            "a match cut joins consecutive scenes; to_scene must directly follow from_scene"
        )
    (x, y), px_scale, cam_state = end_state(spec, a, character, catalog=cat)

    out = spec.model_copy(deep=True)
    sb: SceneSpec = out.scenes[b]
    layer = next((ly for ly in sb.layers if ly.character == character), None)
    if layer is None:
        raise KeyError(f"character {character!r} has no layer in scene {sb.id!r}")
    notes: list[str] = []

    # on-screen size is CHAR_UNIT * layer.scale * depth_scale(y); solve for the layer scale in the new set
    stage_b = SceneStage(
        out,
        b,
        RenderConfig(
            (54, 96),
            spec.meta.fps,
            spec.meta.seed,
            spec.meta.style,
            True,
            FxConfig(),
            (0.1, 0.2, 0.07, 0.07),
        ),
        catalog=cat,
    )
    depth_b = stage_b.world.depth_scale(y)
    new_scale = max(0.05, min(4.0, px_scale / (CHAR_UNIT * depth_b)))
    layer.position = [round(x, 4), round(y, 4)]
    layer.scale = round(new_scale, 4)
    # the character should already be standing there when the new scene opens
    for act in layer.actions:
        if act.name in ("enter_from",):
            notes.append(
                f"scene {sb.id!r}: the layer starts with {act.name}; a match cut needs the character on screen at t=0"
            )
    out.scenes[a].transition_out.type = "cut"
    out.scenes[a].transition_out.duration = 0.0

    if (keep_camera and any(abs(v) > 1e-6 for k, v in cam_state.items() if k != "zoom")) or (
        keep_camera and abs(cam_state["zoom"] - 1.0) > 1e-6
    ):
        # open the new scene on the camera framing the old one ended with, then let its own moves take over
        hold = CameraMoveSpec.model_validate(
            {
                "type": "pan",
                "from": [cam_state["pan_x"], cam_state["pan_y"]],
                "to": [cam_state["pan_x"], cam_state["pan_y"]],
                "t0": 0.0,
                "t1": 0.04,
                "ease": "linear",
            }
        )
        moves = [hold]
        if abs(cam_state["zoom"] - 1.0) > 1e-6:
            moves.append(
                CameraMoveSpec.model_validate(
                    {
                        "type": "zoom",
                        "from": cam_state["zoom"],
                        "to": cam_state["zoom"],
                        "t0": 0.0,
                        "t1": 0.04,
                        "ease": "linear",
                    }
                )
            )
        sb.camera.moves = moves + list(sb.camera.moves)
        notes.append("camera opens on the framing the previous scene ended with")
    return MatchResult(out, (x, y), new_scale, cam_state, notes)
