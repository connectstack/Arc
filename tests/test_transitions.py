from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from reel.core.catalog import CATALOG
from reel.core.transitions import run_transition, wipe_mask

H, W = 96, 60


def frame(color: tuple[int, int, int]) -> np.ndarray:
    f = np.zeros((H, W, 4), dtype=np.uint8)
    f[..., :3] = color
    f[..., 3] = 255
    return f


A, B = frame((20, 40, 200)), frame((220, 200, 30))


def mean_bgr(f: np.ndarray) -> np.ndarray:
    return f[..., :3].reshape(-1, 3).mean(axis=0)


def test_all_four_transitions_are_registered() -> None:
    assert {"cut", "crossfade", "wipe", "page_flip"} <= set(CATALOG.transitions.names())


def test_crossfade_goes_from_a_to_b_monotonically() -> None:
    means = [
        mean_bgr(run_transition("crossfade", A, B, p, None, {}))[0]
        for p in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert means[0] == pytest.approx(20, abs=1.5) and means[-1] == pytest.approx(220, abs=1.5)
    assert means == sorted(means)  # blue channel climbs monotonically from A to B


def test_cut_switches_halfway() -> None:
    assert np.array_equal(run_transition("cut", A, B, 0.3, None, {}), A)
    assert np.array_equal(run_transition("cut", A, B, 0.7, None, {}), B)


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_wipe_reveals_b_progressively_from_the_right_side(direction: str) -> None:
    covered = [
        float(wipe_mask(H, W, p, direction, 0.05, 0.0).mean()) for p in (0.05, 0.3, 0.6, 0.95)
    ]
    assert covered == sorted(covered) and covered[0] < 0.2 and covered[-1] > 0.8


def test_wipe_left_edge_travels_right_to_left() -> None:
    m = wipe_mask(H, W, 0.5, "left", 0.02, 0.0)
    assert m[:, 0].mean() < 0.2 and m[:, -1].mean() > 0.8  # new scene enters from the right edge
    m = wipe_mask(H, W, 0.5, "right", 0.02, 0.0)
    assert m[:, 0].mean() > 0.8 and m[:, -1].mean() < 0.2


def test_wipe_extremes_are_the_pure_frames() -> None:
    first = run_transition("wipe", A, B, 0.0, None, {"softness": 0.0})
    last = run_transition("wipe", A, B, 1.0, None, {"softness": 0.0})
    assert mean_bgr(first)[0] == pytest.approx(20, abs=3) and mean_bgr(last)[0] == pytest.approx(
        220, abs=3
    )


def test_page_flip_peels_the_old_page_away() -> None:
    early = run_transition("page_flip", A, B, 0.08, None, {})
    mid = run_transition("page_flip", A, B, 0.5, None, {})
    late = run_transition("page_flip", A, B, 0.97, None, {})
    assert early.shape == A.shape and early.dtype == np.uint8
    share_b = [
        float((f[..., 0] > 120).mean()) for f in (early, mid, late)
    ]  # B is the blue-heavy frame
    assert share_b[0] < 0.3 and 0.2 < share_b[1] < 0.9 and share_b[2] > 0.7
    assert (mid[..., 3] == 255).all()


def test_page_flip_direction_mirrors() -> None:
    left = run_transition("page_flip", A, B, 0.5, None, {"direction": "left"})
    right = run_transition("page_flip", A[:, ::-1], B[:, ::-1], 0.5, None, {"direction": "right"})
    assert np.abs(left.astype(int) - right[:, ::-1].astype(int)).mean() < 1.0


def test_params_are_validated() -> None:
    with pytest.raises(ValidationError):
        run_transition("wipe", A, B, 0.5, None, {"direction": "sideways"})
    with pytest.raises(ValidationError):
        run_transition("crossfade", A, B, 0.5, None, {"nope": 1})
    with pytest.raises(KeyError):
        run_transition("star_wipe", A, B, 0.5, None, {})


def test_paper_style_wipe_has_a_ragged_edge_with_a_paper_rim() -> None:
    from reel.styles.base import StyleContext
    from reel.styles.paper_cutout import PaperCutout

    st = PaperCutout()
    ctx = StyleContext(scale=1.0, size=(W, H), scheme=None, seed=3)
    plain = run_transition("wipe", A, B, 0.5, ctx, {"direction": "left", "softness": 0.0})
    torn = st.transition("wipe", A, B, 0.5, ctx, {"direction": "left"})
    assert not np.array_equal(plain, torn)
    # the torn edge is not a straight line: the edge column differs between rows
    edge_cols = [int(np.argmax(torn[r, :, 0] > 120)) for r in range(H)]
    assert len(set(edge_cols)) >= 3
