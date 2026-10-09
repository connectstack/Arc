"""Scene transitions: cut, crossfade, wipe, page_flip.

A transition blends two *finished* frames (BGRA uint8) for the frames where two scenes overlap.
Styles may override `StylePack.transition` for a custom look; these are the shared defaults and
they are registered, so specs name them and the linter knows them.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from reel.core.catalog import CATALOG, Catalog
from reel.core.easing import ease
from reel.core.fx import lerp_frames

TransitionFn = Callable[[np.ndarray, np.ndarray, float, Any, Any], np.ndarray]


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class TransitionDef:
    name: str
    fn: TransitionFn
    params_model: type[BaseModel] | None
    summary: str

    def parse(self, raw: dict[str, Any] | None) -> BaseModel:
        return (self.params_model or _P).model_validate(raw or {})


def register_transition(
    name: str,
    *,
    params_schema: type[BaseModel] | None = None,
    summary: str = "",
    catalog: Catalog | None = None,
) -> Callable[[TransitionFn], TransitionFn]:
    def wrap(fn: TransitionFn) -> TransitionFn:
        (catalog or CATALOG).transitions.register(
            name,
            TransitionDef(
                name, fn, params_schema, summary or (fn.__doc__ or "").strip().splitlines()[0]
            ),
        )
        return fn

    return wrap


def run_transition(
    kind: str,
    a: np.ndarray,
    b: np.ndarray,
    progress: float,
    ctx: Any,
    params: dict[str, Any] | None,
    style: Any = None,
) -> np.ndarray:
    d: TransitionDef = CATALOG.transitions.get(kind)
    return d.fn(a, b, progress, ctx, d.parse(params))


# ------------------------------------------------------------------------------- cut
@register_transition("cut", summary="Hard cut: the next scene starts immediately")
def _cut(a: np.ndarray, b: np.ndarray, p: float, ctx: Any, prm: Any) -> np.ndarray:
    return b if p >= 0.5 else a


# ------------------------------------------------------------------------------- crossfade
class CrossfadeParams(_P):
    curve: str = Field("ease_in_out", description="easing of the dissolve")


@register_transition(
    "crossfade", params_schema=CrossfadeParams, summary="Dissolve from one scene into the next"
)
def _crossfade(
    a: np.ndarray, b: np.ndarray, p: float, ctx: Any, prm: CrossfadeParams
) -> np.ndarray:
    return lerp_frames(a, b, float(ease(prm.curve, p)))


# ------------------------------------------------------------------------------- wipe
class WipeParams(_P):
    direction: Literal["left", "right", "up", "down"] = Field(
        "left", description="direction the wipe edge travels"
    )
    softness: float = Field(
        0.07, ge=0.0, le=0.5, description="width of the soft edge as a fraction of the frame"
    )
    angle: float = Field(0.0, ge=-45.0, le=45.0, description="tilt of the wipe edge in degrees")


def wipe_mask(
    h: int, w: int, p: float, direction: str, softness: float, angle: float
) -> np.ndarray:
    """(h, w) or (1, w)/(h, 1) float32 mask: 0 = still the old scene, 1 = the new scene."""
    pe = float(ease("ease_in_out", p))
    xs = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    ang = math.radians(angle)
    if direction in ("left", "right"):
        coord = xs * math.cos(ang) + ys * math.sin(ang) if angle else xs
        coord = (1 - coord) if direction == "right" else coord
    else:
        coord = ys * math.cos(ang) + xs * math.sin(ang) if angle else ys
        coord = (1 - coord) if direction == "down" else coord
    soft = max(softness, 1e-4)
    edge = 1 + soft / 2 - pe * (1 + soft)
    u = np.clip((coord - edge) / soft + 0.5, 0, 1)
    return (u * u * (3 - 2 * u)).astype(np.float32)


def blend_masked(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> np.ndarray:
    mq = (m * 256).astype(np.uint16)
    if mq.ndim == 2:
        mq = mq[:, :, None]
    return ((a.astype(np.uint16) * (256 - mq) + b.astype(np.uint16) * mq) >> 8).astype(np.uint8)


@register_transition(
    "wipe",
    params_schema=WipeParams,
    summary="A soft-edged line sweeps across the frame revealing the next scene",
)
def _wipe(a: np.ndarray, b: np.ndarray, p: float, ctx: Any, prm: WipeParams) -> np.ndarray:
    h, w = a.shape[:2]
    return blend_masked(a, b, wipe_mask(h, w, p, prm.direction, prm.softness, prm.angle))


# ------------------------------------------------------------------------------- page flip
class PageFlipParams(_P):
    direction: Literal["left", "right"] = Field(
        "left", description="the page peels towards this side"
    )
    radius: float = Field(
        0.11, ge=0.03, le=0.3, description="curl radius as a fraction of the frame width"
    )


@register_transition(
    "page_flip",
    params_schema=PageFlipParams,
    summary="The outgoing page curls away like a turning page, revealing the next scene",
)
def _page_flip(a: np.ndarray, b: np.ndarray, p: float, ctx: Any, prm: PageFlipParams) -> np.ndarray:
    _h, w = a.shape[:2]
    mirror = prm.direction == "right"
    if mirror:
        a, b = a[:, ::-1], b[:, ::-1]
    r = prm.radius * w
    pe = float(ease("ease_in_out", p))
    xc = w - pe * (w + r + 2.0)  # axis of the roll, travelling right -> left
    x = np.arange(w, dtype=np.float32)
    d = x - xc
    in_roll = (d >= 0) & (d < r)
    dd = np.clip(d / r, 0.0, 1.0)
    src = np.where(d < 0, x, xc + r * np.arcsin(dd))
    src_i = np.clip(np.rint(src), 0, w - 1).astype(np.int64)
    normal = np.sqrt(np.clip(1 - dd * dd, 0, 1))
    shade_roll = 0.42 + 0.58 * normal**0.8 + 0.14 * np.sin(np.pi * dd)
    shade_a = np.where(in_roll, shade_roll, 1.0).astype(np.float32)
    # the paper casts a soft shadow on the page underneath
    beyond = d >= r
    fall = np.clip(1.0 - (d - r) / (r * 1.7), 0.0, 1.0)
    shadow_b = np.where(beyond, 1.0 - 0.42 * fall * fall, 1.0).astype(np.float32)
    from_a = d < r
    out_a = a[:, src_i].astype(np.float32) * shade_a[None, :, None]
    out_b = b.astype(np.float32) * shadow_b[None, :, None]
    out = np.where(from_a[None, :, None], out_a, out_b)
    res = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    res[..., 3] = 255
    return res[:, ::-1].copy() if mirror else res
