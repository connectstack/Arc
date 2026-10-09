"""Easing curves.  Every curve maps u in [0,1] -> value (may overshoot [0,1]).

All functions are vectorised over numpy arrays so whole animation timelines are
baked in one call.  Names are public API (specs refer to them), so they are
registered in the catalog; `ease()` is the one function the rest of the code uses.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from numpy.typing import ArrayLike, NDArray

from reel.core.catalog import CATALOG

F = NDArray[np.float64]
EaseFn = Callable[[F], F]

_C1 = 1.70158
_C2 = _C1 * 1.525
_C3 = _C1 + 1.0


def register_easing(name: str, *, summary: str = "") -> Callable[[EaseFn], EaseFn]:
    """Decorator: add an easing curve to the catalog (usable as `"ease": "<name>"` in specs)."""

    def wrap(fn: EaseFn) -> EaseFn:
        CATALOG.easings.register(name, fn, summary=summary or (fn.__doc__ or "").strip())
        return fn

    return wrap


@register_easing("linear", summary="constant speed")
def _linear(u: F) -> F:
    return u


@register_easing("ease_in", summary="start slow, accelerate (quadratic)")
def _ease_in(u: F) -> F:
    return u * u


@register_easing("ease_out", summary="start fast, decelerate (quadratic)")
def _ease_out(u: F) -> F:
    return 1.0 - (1.0 - u) ** 2


@register_easing("ease_in_out", summary="smooth start and stop (cubic) - the default")
def _ease_in_out(u: F) -> F:
    return np.where(u < 0.5, 4.0 * u**3, 1.0 - (-2.0 * u + 2.0) ** 3 / 2.0)


@register_easing("ease_in_cubic", summary="slow start, strong acceleration")
def _ease_in_cubic(u: F) -> F:
    return u**3


@register_easing("ease_out_cubic", summary="fast start, long soft landing")
def _ease_out_cubic(u: F) -> F:
    return 1.0 - (1.0 - u) ** 3


@register_easing("ease_in_out_quint", summary="very pronounced slow-fast-slow")
def _ease_in_out_quint(u: F) -> F:
    return np.where(u < 0.5, 16.0 * u**5, 1.0 - (-2.0 * u + 2.0) ** 5 / 2.0)


@register_easing("sine_in_out", summary="gentle sinusoidal in/out")
def _sine_in_out(u: F) -> F:
    return -(np.cos(np.pi * u) - 1.0) / 2.0


@register_easing("expo_out", summary="very fast start with a long tail (whip-pans, slams)")
def _expo_out(u: F) -> F:
    return np.where(u >= 1.0, 1.0, 1.0 - 2.0 ** (-10.0 * u))


@register_easing("overshoot", summary="passes the target then settles back (back-out)")
def _overshoot(u: F) -> F:
    return 1.0 + _C3 * (u - 1.0) ** 3 + _C1 * (u - 1.0) ** 2


@register_easing("anticipate", summary="pulls back first, then accelerates (back-in)")
def _anticipate(u: F) -> F:
    return _C3 * u**3 - _C1 * u**2


@register_easing("anticipate_overshoot", summary="wind-up before and overshoot after (back in-out)")
def _anticipate_overshoot(u: F) -> F:
    lo = ((2.0 * u) ** 2 * ((_C2 + 1.0) * 2.0 * u - _C2)) / 2.0
    hi = ((2.0 * u - 2.0) ** 2 * ((_C2 + 1.0) * (u * 2.0 - 2.0) + _C2) + 2.0) / 2.0
    return np.where(u < 0.5, lo, hi)


@register_easing("bounce", summary="lands and bounces to rest (bounce-out)")
def _bounce(u: F) -> F:
    n1, d1 = 7.5625, 2.75
    out = np.empty_like(u)
    a = u < 1.0 / d1
    b = (u >= 1.0 / d1) & (u < 2.0 / d1)
    c = (u >= 2.0 / d1) & (u < 2.5 / d1)
    d = u >= 2.5 / d1
    out[a] = n1 * u[a] ** 2
    ub = u[b] - 1.5 / d1
    out[b] = n1 * ub**2 + 0.75
    uc = u[c] - 2.25 / d1
    out[c] = n1 * uc**2 + 0.9375
    ud = u[d] - 2.625 / d1
    out[d] = n1 * ud**2 + 0.984375
    return out


@register_easing("bounce_in", summary="bounces away from the start value")
def _bounce_in(u: F) -> F:
    return 1.0 - _bounce(1.0 - u)


@register_easing("elastic", summary="rubber-band wobble around the target (elastic-out)")
def _elastic(u: F) -> F:
    c4 = (2.0 * np.pi) / 3.0
    out = 2.0 ** (-10.0 * u) * np.sin((u * 10.0 - 0.75) * c4) + 1.0
    return np.where(u <= 0.0, 0.0, np.where(u >= 1.0, 1.0, out))


@register_easing("spring", summary="damped spring settling on the target")
def _spring(u: F) -> F:
    s = 1.0 - np.exp(-7.0 * u) * np.cos(2.0 * np.pi * 1.6 * u)
    residual = np.exp(-7.0) * np.cos(2.0 * np.pi * 1.6)  # s(1) - 1; blend it away so u=1 lands on 1
    return s + residual * u**4


@register_easing("hold", summary="step: hold the old value, jump at the end (comedy hold frames)")
def _hold(u: F) -> F:
    return np.where(u >= 1.0, 1.0, 0.0)


def ease(name: str, u: ArrayLike) -> F:
    """Evaluate easing ``name`` at ``u`` (clipped to [0,1]); returns float64 array/0-d array."""
    arr = np.clip(np.asarray(u, dtype=np.float64), 0.0, 1.0)
    fn = CATALOG.easings.get(name)
    out = fn(arr.reshape(-1) if arr.ndim == 0 else arr)
    return np.asarray(out, dtype=np.float64).reshape(arr.shape)


def smoothstep(u: ArrayLike) -> F:
    a = np.clip(np.asarray(u, dtype=np.float64), 0.0, 1.0)
    return a * a * (3.0 - 2.0 * a)
