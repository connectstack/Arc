"""Frame-level post-processing: bloom, chromatic aberration, colour grade, vignette, film grain, letterbox.

Frames are BGRA uint8 numpy arrays (skia's native layout).  The pipeline is split in two so the
expensive, deterministic part can be cached:

* :func:`apply_fx`  - bloom -> chromatic aberration -> grade -> vignette   (cacheable per frame)
* :func:`finish`    - film grain -> letterbox                              (cheap, per-frame seed)

All radii/sizes are given in *design pixels* (the 1080-wide reference frame) and scaled by
``ctx.scale`` so previews look like miniatures of the full render.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageFilter

from reel.core.rng import derive_rng

if TYPE_CHECKING:
    from reel.core.spec import FxSpec

F32 = np.float32


@dataclass(frozen=True)
class FxConfig:
    grain: float = 0.0  # 0..1; 1 ~ strong 16mm grain
    grain_size: float = 1.0  # design px; >1 gives softer, filmier grain
    chroma_grain: float = 0.25  # fraction of grain that is coloured
    vignette: float = 0.0  # 0..1 corner darkening
    vignette_softness: float = 0.62
    bloom: float = 0.0  # 0..1
    bloom_threshold: float = 0.70
    bloom_radius: float = 30.0  # design px
    chroma: float = 0.0  # chromatic aberration at the frame edge, design px
    saturation: float = 1.0
    contrast: float = 1.0
    gamma: float = 1.0
    warmth: float = 0.0  # -1 cool .. +1 warm
    lift: tuple[float, float, float] = (0.0, 0.0, 0.0)  # RGB added in shadows (0..1)
    gain: tuple[float, float, float] = (1.0, 1.0, 1.0)  # RGB multiplied in highlights
    letterbox: float = 0.0  # fraction of the height blanked at top and bottom

    def with_spec(self, fx: FxSpec | None) -> FxConfig:
        """Apply per-spec overrides (`meta.fx`): None keeps the style default."""
        if fx is None:
            return self
        kw: dict[str, Any] = {}
        if fx.grain is not None:
            kw["grain"] = self.grain * fx.grain if self.grain else 0.35 * fx.grain
        if fx.vignette is not None:
            kw["vignette"] = self.vignette * fx.vignette if self.vignette else 0.3 * fx.vignette
        if fx.bloom is not None:
            kw["bloom"] = self.bloom * fx.bloom if self.bloom else 0.25 * fx.bloom
        if fx.chromatic_aberration is not None:
            kw["chroma"] = (
                self.chroma * fx.chromatic_aberration
                if self.chroma
                else 1.5 * fx.chromatic_aberration
            )
        if fx.saturation is not None:
            kw["saturation"] = self.saturation * fx.saturation
        if fx.contrast is not None:
            kw["contrast"] = self.contrast * fx.contrast
        if fx.warmth is not None:
            kw["warmth"] = max(-1.0, min(1.0, self.warmth + fx.warmth))
        if fx.letterbox is not None:
            kw["letterbox"] = fx.letterbox
        return replace(self, **kw)

    def pre_key(self) -> str:
        """Stable string of everything the cacheable stage depends on."""
        keys = (
            "bloom",
            "bloom_threshold",
            "bloom_radius",
            "chroma",
            "saturation",
            "contrast",
            "gamma",
            "warmth",
            "lift",
            "gain",
            "vignette",
            "vignette_softness",
        )
        return "|".join(f"{k}={getattr(self, k)}" for k in keys)

    def post_key(self) -> str:
        return f"grain={self.grain}|gs={self.grain_size}|cg={self.chroma_grain}|lb={self.letterbox}"


def _resize(
    arr: np.ndarray, size: tuple[int, int], resample: Image.Resampling = Image.Resampling.BILINEAR
) -> np.ndarray:
    return np.asarray(Image.fromarray(arr).resize(size, resample))


# ------------------------------------------------------------------------------- grade LUT
@lru_cache(maxsize=32)
def _grade_lut(cfg_key: tuple[Any, ...]) -> np.ndarray:
    """(3, 256) uint8 LUTs in BGR channel order: contrast, gamma, warmth, lift/gain."""
    contrast, gamma, warmth, lift, gain = cfg_key
    x = np.linspace(0, 1, 256, dtype=np.float64)
    out = np.empty((3, 256), dtype=np.uint8)
    # RGB -> BGR channel order: index 0 = blue
    warm = {0: -warmth * 0.06, 1: warmth * 0.012, 2: warmth * 0.06}
    for bgr, rgb in ((0, 2), (1, 1), (2, 0)):
        v = (x - 0.5) * contrast + 0.5
        v = np.clip(v, 0, 1) ** (1.0 / max(gamma, 1e-3))
        v = v * gain[rgb] + lift[rgb] * (1 - v) + warm[bgr] * (0.4 + 0.6 * v)
        out[bgr] = np.clip(v * 255 + 0.5, 0, 255).astype(np.uint8)
    return out


@lru_cache(maxsize=8)
def _vignette_mask(h: int, w: int, strength: float, softness: float) -> np.ndarray:
    ys = (np.arange(h, dtype=F32) - (h - 1) / 2) / (h / 2)
    xs = (np.arange(w, dtype=F32) - (w - 1) / 2) / (w / 2)
    r2 = (xs[None, :] ** 2) * 0.85 + (ys[:, None] ** 2) * 0.55  # ~1 at the corners
    inner = max(0.05, 1.0 - softness)
    v = np.clip((r2 - inner * 0.35) / (1.15 - inner * 0.35), 0, 1)
    mask = 1.0 - strength * v * v * (3 - 2 * v)
    return mask.astype(F32)[:, :, None]


def apply_fx(frame: np.ndarray, cfg: FxConfig, ctx: Any) -> np.ndarray:
    """Cacheable stage: bloom -> chromatic aberration -> saturation -> vignette -> grade LUT."""
    h, w = frame.shape[:2]
    s = float(ctx.scale)
    needs_float = cfg.bloom > 0 or cfg.chroma > 0 or cfg.saturation != 1.0 or cfg.vignette > 0
    bgr = frame[..., :3]
    if needs_float:
        x = bgr.astype(F32) * F32(1 / 255)
        if cfg.bloom > 0:
            x = _bloom(x, cfg, s)
        if cfg.chroma > 0:
            x = _chroma(x, cfg.chroma * s)
        if cfg.saturation != 1.0:
            luma = x @ np.array([0.114, 0.587, 0.299], dtype=F32)
            x = luma[..., None] + (x - luma[..., None]) * F32(cfg.saturation)
        if cfg.vignette > 0:
            x = x * _vignette_mask(h, w, cfg.vignette, cfg.vignette_softness)
        bgr = np.clip(x * 255 + 0.5, 0, 255).astype(np.uint8)
    if (
        cfg.contrast != 1.0
        or cfg.gamma != 1.0
        or cfg.warmth != 0.0
        or any(cfg.lift)
        or cfg.gain != (1.0, 1.0, 1.0)
    ):
        lut = _grade_lut((cfg.contrast, cfg.gamma, cfg.warmth, cfg.lift, cfg.gain))
        bgr = np.stack([lut[0][bgr[..., 0]], lut[1][bgr[..., 1]], lut[2][bgr[..., 2]]], axis=-1)
    out = np.empty_like(frame)
    out[..., :3] = bgr
    out[..., 3] = 255
    return out


def _bloom(x: np.ndarray, cfg: FxConfig, s: float) -> np.ndarray:
    h, w = x.shape[:2]
    k = 4 if w >= 700 else 2
    hs, ws = h // k, w // k
    small = x[: hs * k, : ws * k].reshape(hs, k, ws, k, 3).mean(axis=(1, 3))
    lum = small @ np.array([0.114, 0.587, 0.299], dtype=F32)
    bright = np.clip((lum - cfg.bloom_threshold) / max(1e-3, 1 - cfg.bloom_threshold), 0, 1)
    src = small * bright[..., None]
    im = Image.fromarray(np.clip(src * 255, 0, 255).astype(np.uint8))
    sigma = max(1.0, cfg.bloom_radius * s / k)
    glow = np.asarray(
        im.filter(ImageFilter.GaussianBlur(sigma)).resize((w, h), Image.Resampling.BILINEAR),
        dtype=F32,
    ) * F32(1 / 255)
    # screen blend keeps highlights from clipping harshly
    g = glow * F32(cfg.bloom * 1.6)
    return 1.0 - (1.0 - np.clip(x, 0, 1)) * (1.0 - np.clip(g, 0, 1))


def _chroma(x: np.ndarray, px: float) -> np.ndarray:
    """Radial RGB fringing: red grows and blue shrinks about the centre by up to ``px`` at the edge."""
    h, w = x.shape[:2]
    eps = px / (w / 2)
    out = x.copy()
    for ch, sc in ((2, 1.0 + eps), (0, 1.0 - eps)):  # BGR: 2 = red, 0 = blue
        im = Image.fromarray(x[..., ch], mode="F")
        a = 1.0 / sc
        cx, cy = w / 2, h / 2
        out[..., ch] = np.asarray(
            im.transform(
                (w, h),
                Image.Transform.AFFINE,
                (a, 0, cx - cx * a, 0, a, cy - cy * a),
                resample=Image.Resampling.BILINEAR,
            )
        )
    # The shrinking channel samples *outside* the frame at the very edge and gets black there, which shows as a thin
    # coloured border. The fringe is under a pixel wide at the edge anyway, so keep the original values in that band.
    # The zoom is the same in both directions, so a tall frame is displaced further at its top and bottom than at its
    # sides (by px * h / w): the vertical band has to be that much deeper.
    mx = min(int(np.ceil(px)) + 1, w // 2)
    my = min(int(np.ceil(px * h / w)) + 1, h // 2)
    for ch in (0, 2):
        out[:my, :, ch] = x[:my, :, ch]
        out[h - my :, :, ch] = x[h - my :, :, ch]
        out[:, :mx, ch] = x[:, :mx, ch]
        out[:, w - mx :, ch] = x[:, w - mx :, ch]
    return out


# ------------------------------------------------------------------------------- grain
_NOISE: dict[tuple[int, int, float, int], np.ndarray] = {}


def _noise_tex(h: int, w: int, size_px: float, seed: int) -> np.ndarray:
    key = (h, w, round(size_px, 2), seed)
    tex = _NOISE.get(key)
    if tex is None:
        rng = np.random.default_rng(seed)
        th, tw = h + 96, w + 96
        if size_px > 1.2:
            lo = rng.standard_normal((int(th / size_px) + 2, int(tw / size_px) + 2)).astype(F32)
            im = Image.fromarray(lo, mode="F").resize((tw, th), Image.Resampling.BICUBIC)
            tex = np.asarray(im, dtype=F32)
            tex = tex / max(float(tex.std()), 1e-6)
        else:
            tex = rng.standard_normal((th, tw)).astype(F32)
        if len(_NOISE) > 6:
            _NOISE.clear()
        _NOISE[key] = tex
    return tex


def finish(frame: np.ndarray, cfg: FxConfig, ctx: Any, *, seed: int, key: Any) -> np.ndarray:
    """Per-frame finishing: film grain (animated, deterministic) and letterbox bars."""
    h, w = frame.shape[:2]
    out = frame
    if cfg.grain > 0:
        rng = derive_rng(seed, "grain", key)
        tex = _noise_tex(h, w, max(1.0, cfg.grain_size * ctx.scale), seed & 0xFFFF)
        oy, ox = int(rng.integers(0, 96)), int(rng.integers(0, 96))
        mono = tex[oy : oy + h, ox : ox + w]
        if rng.random() < 0.5:
            mono = mono[:, ::-1]
        amp = F32(cfg.grain * 11.0)
        out = frame.copy()
        px = out[..., :3].astype(np.int16)
        n = (mono * amp).astype(np.int16)
        px += n[..., None]
        if cfg.chroma_grain > 0:
            oy2, ox2 = int(rng.integers(0, 96)), int(rng.integers(0, 96))
            c = tex[oy2 : oy2 + h, ox2 : ox2 + w]
            px[..., 0] += (c * amp * F32(cfg.chroma_grain)).astype(np.int16)
            px[..., 2] -= (c * amp * F32(cfg.chroma_grain)).astype(np.int16)
        out[..., :3] = np.clip(px, 0, 255).astype(np.uint8)
    if cfg.letterbox > 0:
        bar = round(h * cfg.letterbox)
        if bar > 0:
            if out is frame:
                out = frame.copy()
            out[:bar, :, :3] = 0
            out[h - bar :, :, :3] = 0
    return out


def lerp_frames(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    k = round(max(0.0, min(1.0, t)) * 256)
    if k <= 0:
        return a
    if k >= 256:
        return b
    return ((a.astype(np.uint16) * (256 - k) + b.astype(np.uint16) * k) >> 8).astype(np.uint8)
