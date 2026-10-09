"""Procedural paper: a tileable fibre texture (FFT-filtered noise, periodic by construction)."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import skia


@lru_cache(maxsize=4)
def paper_tile(size: int = 512, seed: int = 5) -> skia.Image:
    """512px tileable grey texture: soft cloudiness + fine horizontal-ish fibres, mean 128."""
    rng = np.random.default_rng(seed)
    n = rng.standard_normal((size, size))
    fx = np.fft.fftfreq(size)[:, None]
    fy = np.fft.fftfreq(size)[None, :]
    r = np.sqrt(fx**2 + fy**2) + 1e-6
    cloud = np.exp(-((r / 0.035) ** 2)) * 1.0  # large soft blotches
    grain = (1.0 / r**0.55) * np.exp(-((r / 0.30) ** 2)) * 0.07  # paper tooth
    fibre = np.exp(-((fy / 0.012) ** 2)) * np.exp(-((fx / 0.16) ** 2)) * 0.9  # long fibres
    spec = cloud + grain + fibre
    img = np.real(np.fft.ifft2(np.fft.fft2(n) * spec))
    img = img / (3.2 * img.std() + 1e-9)
    g = np.clip(128 + img * 70, 0, 255).astype(np.uint8)
    rgba = np.stack([g, g, g, np.full_like(g, 255)], axis=-1)
    return skia.Image.fromarray(np.ascontiguousarray(rgba), colorType=skia.kRGBA_8888_ColorType)
