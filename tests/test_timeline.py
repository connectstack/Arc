from __future__ import annotations

from typing import Any

import pytest

from reel.core.spec import ReelSpec
from reel.core.timeline import compute_timeline, total_duration_sec


def build(
    spec_dict: dict[str, Any], durations: list[float], trans: list[tuple[str, float]]
) -> ReelSpec:
    scenes = []
    for i, d in enumerate(durations):
        sc = {
            "id": f"s{i}",
            "duration_sec": d,
            "background": {"template": "room"},
            "transition_out": {"type": trans[i][0], "duration": trans[i][1]},
        }
        scenes.append(sc)
    spec_dict["scenes"] = scenes
    return ReelSpec.model_validate(spec_dict)


def test_cuts_just_add_up(spec_dict: dict[str, Any]) -> None:
    spec = build(spec_dict, [5, 5, 5], [("cut", 0)] * 3)
    tl = compute_timeline(spec)
    assert tl.total_frames == 450 and tl.total_sec == 15
    assert [s.start_frame for s in tl.slots] == [0, 150, 300]
    assert [a.slot for a in tl.active(149)] == [0]
    assert [a.slot for a in tl.active(150)] == [1]


def test_crossfade_overlaps_scenes(spec_dict: dict[str, Any]) -> None:
    spec = build(spec_dict, [5, 5, 5], [("crossfade", 1.0), ("cut", 0), ("cut", 0)])
    tl = compute_timeline(spec)
    assert tl.total_sec == pytest.approx(14.0)
    assert tl.total_frames == 420
    assert tl.slots[1].start_frame == 120  # starts 1 s before scene 0 ends
    act = tl.active(130)
    assert [(a.slot, a.local_frame) for a in act] == [(0, 130), (1, 10)]
    assert tl.transition_progress(130) is not None
    assert tl.transition_progress(100) is None
    # progress is strictly inside (0,1) and monotonic across the overlap
    ps = [tl.transition_progress(f) for f in range(120, 150)]
    assert all(p is not None and 0 < p < 1 for p in ps)
    assert ps == sorted(ps)  # type: ignore[type-var]


def test_overlap_is_clamped_to_neighbour_scenes(spec_dict: dict[str, Any]) -> None:
    spec = build(spec_dict, [5, 0.5, 5], [("wipe", 3.0), ("wipe", 3.0), ("cut", 0)])
    tl = compute_timeline(spec)
    # overlaps can never swallow a scene; every frame still has at least one active scene
    for f in range(tl.total_frames):
        assert 1 <= len(tl.active(f)) <= 2
    assert tl.total_frames > 0


def test_total_duration_helper(spec_dict: dict[str, Any]) -> None:
    spec = build(spec_dict, [10, 10], [("crossfade", 2.0), ("cut", 0)])
    assert total_duration_sec(spec) == pytest.approx(18.0)


def test_last_scene_transition_is_ignored(spec_dict: dict[str, Any]) -> None:
    spec = build(spec_dict, [5, 5], [("cut", 0), ("crossfade", 2.0)])
    assert compute_timeline(spec).total_sec == 10
