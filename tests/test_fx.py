from __future__ import annotations

import numpy as np
import pytest

from reel.core.fx import FxConfig, apply_fx, finish
from reel.core.spec import FxSpec
from reel.styles.base import StyleContext

H, W = 160, 90
CTX = StyleContext(scale=W / 1080, size=(W, H), scheme=None, seed=1)


def flat(c: int = 128) -> np.ndarray:
    f = np.full((H, W, 4), c, dtype=np.uint8)
    f[..., 3] = 255
    return f


def test_default_config_is_the_identity() -> None:
    f = flat(100)
    assert np.array_equal(apply_fx(f, FxConfig(), CTX), f)


def test_vignette_darkens_corners_more_than_the_centre() -> None:
    out = apply_fx(flat(), FxConfig(vignette=0.8), CTX)
    centre = out[H // 2, W // 2, 0]
    corner = out[2, 2, 0]
    assert corner < centre - 25 and abs(int(centre) - 128) <= 3


def test_saturation_zero_is_grey_and_contrast_widens_the_range() -> None:
    f = np.zeros((H, W, 4), dtype=np.uint8)
    f[..., 0], f[..., 1], f[..., 2], f[..., 3] = 60, 120, 200, 255
    grey = apply_fx(f, FxConfig(saturation=0.0), CTX)
    assert np.ptp(grey[0, 0, :3].astype(int)) <= 2
    ramp = flat()
    ramp[:, : W // 2, :3] = 100
    ramp[:, W // 2 :, :3] = 156
    hi = apply_fx(ramp, FxConfig(contrast=1.5), CTX)
    assert int(hi[0, -1, 0]) - int(hi[0, 0, 0]) > 56


def test_warmth_shifts_red_up_and_blue_down() -> None:
    out = apply_fx(flat(), FxConfig(warmth=1.0), CTX)
    assert out[0, 0, 2] > out[0, 0, 0]  # BGRA: index 2 = red, 0 = blue


def test_bloom_spreads_light_from_bright_areas() -> None:
    f = flat(20)
    f[H // 2 - 14 : H // 2 + 14, W // 2 - 14 : W // 2 + 14, :3] = 255
    out = apply_fx(f, FxConfig(bloom=1.0, bloom_threshold=0.5, bloom_radius=150.0), CTX)
    ring = out[H // 2 - 22, W // 2, 0]  # just outside the bright square
    far = out[2, 2, 0]
    assert int(ring) > int(far) + 5  # glow around the bright square, nothing in the far corner


def test_chromatic_aberration_fringes_edges_but_not_the_centre() -> None:
    f = flat(30)
    f[:, W - 8 : W - 6, :3] = 240  # a bright vertical bar near the right edge
    f[:, W // 2 - 1 : W // 2 + 1, :3] = 240  # and one in the centre
    out = apply_fx(f, FxConfig(chroma=6.0), CTX).astype(int)
    edge_split = abs(int(np.argmax(out[H // 2, :, 2])) - int(np.argmax(out[H // 2, :, 0])))
    centre_split = abs(
        int(np.argmax(out[H // 2, : W // 2 + 4, 2])) - int(np.argmax(out[H // 2, : W // 2 + 4, 0]))
    )
    assert edge_split >= 1 and centre_split <= 1


def test_chromatic_aberration_leaves_no_coloured_border() -> None:
    """Regression: the shrinking channel sampled outside the frame and left a 2 px band with no blue at the edges."""
    f = np.empty((H, W, 4), dtype=np.uint8)
    f[..., :3] = (200, 150, 100)  # BGR, a flat colour: nothing to fringe
    f[..., 3] = 255
    for chroma in (0.9, 3.0, 8.0):
        out = apply_fx(f, FxConfig(chroma=chroma), CTX).astype(int)
        assert np.abs(out[..., :3] - f[..., :3]).max() <= 1, (
            f"chroma={chroma}: the border differs from the interior"
        )


@pytest.mark.parametrize("size", [(160, 90), (480, 270), (270, 480)])
def test_chromatic_aberration_border_is_clean_on_tall_wide_and_square_frames(
    size: tuple[int, int],
) -> None:
    """Regression: the edge band was px deep on every side, but a portrait frame is displaced px*h/w at the top and bottom."""
    h, w = size
    ctx = StyleContext(scale=1.0, size=(w, h), scheme=None, seed=1)
    f = np.empty((h, w, 4), dtype=np.uint8)
    f[..., :3] = (200, 150, 100)
    f[..., 3] = 255
    for chroma in (2.0, 4.0, 6.0):
        out = apply_fx(f, FxConfig(chroma=chroma), ctx).astype(int)
        assert np.abs(out[..., :3] - f[..., :3]).max() <= 1, (
            f"{w}x{h} chroma={chroma}: a coloured band at the edge"
        )


def test_grain_is_deterministic_per_key_and_zero_mean() -> None:
    cfg = FxConfig(grain=0.8)
    f = flat()
    a = finish(f, cfg, CTX, seed=5, key=("s", 10))
    b = finish(f, cfg, CTX, seed=5, key=("s", 10))
    c = finish(f, cfg, CTX, seed=5, key=("s", 11))
    assert np.array_equal(a, b) and not np.array_equal(a, c)
    assert abs(float(a[..., :3].mean()) - 128) < 1.5  # grain does not shift brightness
    assert a[..., :3].std() > 2 and (a[..., 3] == 255).all()
    assert np.array_equal(
        finish(f, FxConfig(), CTX, seed=5, key=1), f
    )  # no grain, no letterbox: untouched


def test_letterbox_blanks_top_and_bottom() -> None:
    out = finish(flat(), FxConfig(letterbox=0.1), CTX, seed=1, key=0)
    bar = round(H * 0.1)
    assert (
        out[:bar, :, :3].max() == 0
        and out[H - bar :, :, :3].max() == 0
        and out[H // 2, 0, 0] == 128
    )


def test_spec_overrides_scale_or_enable_effects() -> None:
    base = FxConfig(grain=0.4, vignette=0.2)
    assert base.with_spec(None) == base
    assert base.with_spec(FxSpec(grain=0.0)).grain == 0.0
    assert base.with_spec(FxSpec(grain=2.0)).grain == pytest.approx(0.8)
    assert (
        FxConfig().with_spec(FxSpec(bloom=1.0)).bloom > 0
    )  # switching on an effect the style lacked
    assert base.with_spec(FxSpec(letterbox=0.12)).letterbox == 0.12
    assert base.pre_key() != base.with_spec(FxSpec(vignette=0.0)).pre_key()
