"""Virtual camera: easing moves, handheld micro-shake, dolly parallax and rack focus.

A spec lists *moves*; `Camera` evaluates them into a `CameraState` per frame:

    pan        [x, y] camera centre offset, in frame fractions (+x moves the view right)
    zoom       lens zoom factor (1 = none)
    dolly      0..1 push-in: near planes grow faster than far ones (real parallax)
    shake      0..1 handheld intensity (smooth band-limited noise, deterministic)
    rack_focus depth to focus on: 'background' | 'midground' | 'foreground' | 0..1
               (or a character id: focuses on that character's depth plane)

Cinematic defaults come from the style; `meta` never has to mention the camera.  Planes at
different depths respond differently (`PLANES`), which is what produces parallax.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from reel.core.catalog import CATALOG, Catalog
from reel.core.easing import ease
from reel.core.rng import derive_rng

#: depth planes, back to front: (name, parallax factor, dolly sensitivity, depth for focus: 0 far .. 2 near)
PLANES: tuple[tuple[str, float, float, float], ...] = (
    ("sky", 0.10, 0.04, 0.0),
    ("far", 0.40, 0.20, 0.0),
    ("back", 0.72, 0.55, 0.5),
    ("mid", 1.00, 1.00, 1.0),
    ("near", 1.55, 2.20, 2.0),
)
PLANE_INDEX = {p[0]: i for i, p in enumerate(PLANES)}
FOCUS_NAMES = {
    "background": 0.0,
    "far": 0.0,
    "midground": 1.0,
    "mid": 1.0,
    "foreground": 2.0,
    "near": 2.0,
}
DOLLY_GAIN = 0.35  # scale gained by the mid plane at dolly = 1
MAX_BLUR = 14.0  # design px of blur at 2 depth units out of focus

#: The set is drawn this far beyond the frame, as a fraction of the frame (``reel.templates.base``: X0..X1 = -540..1620 of
#: 1080, Y0..Y1 = -300..2220 of 1920).  A plane that is moved or shrunk further than that shows nothing behind it (a white
#: band), so :func:`plane_xform` never asks for more, whatever a spec says.
BLEED_X = 0.5
BLEED_Y = 300.0 / 1920.0
#: pulling the picture back (zoom / dolly below 1) shows the edges of the set sooner than anything else; this is as far as it goes
MIN_PLANE_SCALE = 0.8

#: What a move may ask for.  Anything inside these is a legitimate camera move; the lint refuses what is outside (it
#: moves the picture off the set) and :func:`clamp_move_values` brings a model's reply back inside.
PAN_LIMIT = (
    0.25,
    0.10,
)  # |x|, |y| in frame fractions (a drift is 0.02 .. 0.15 in x and 0 .. 0.05 in y)
ZOOM_RANGE = (0.85, 2.5)
DOLLY_RANGE = (-0.3, 1.5)
SHAKE_RANGE = (0.0, 2.0)


@dataclass
class CameraState:
    pan_x: float = 0.0  # frame fractions
    pan_y: float = 0.0
    zoom: float = 1.0
    dolly: float = 0.0
    shake_x: float = 0.0  # frame fractions
    shake_y: float = 0.0
    shake_rot: float = 0.0  # degrees
    focus: float = 1.0
    rack: bool = False  # a rack_focus move is active/has run: DoF is on

    def key(self) -> tuple[float, ...]:
        return tuple(
            round(v, 4)
            for v in (
                self.pan_x,
                self.pan_y,
                self.zoom,
                self.dolly,
                self.shake_x,
                self.shake_y,
                self.shake_rot,
                self.focus,
                float(self.rack),
            )
        )


@dataclass(frozen=True)
class CameraMoveDef:
    name: str
    summary: str
    from_to: str  # human description of the from/to values
    check: Callable[[dict[str, Any]], list[str]] | None = None


def register_camera_move(
    name: str,
    *,
    summary: str,
    from_to: str,
    check: Callable[[dict[str, Any]], list[str]] | None = None,
    catalog: Catalog | None = None,
) -> None:
    (catalog or CATALOG).camera_moves.register(name, CameraMoveDef(name, summary, from_to, check))


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_pan(m: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in ("from", "to"):
        v = m.get(k)
        if v is None:
            continue
        if not (isinstance(v, list) and len(v) == 2 and all(_num(x) for x in v)):
            out.append(f"pan `{k}` must be [x, y] (frame fractions), got {v!r}")
        elif abs(v[0]) > PAN_LIMIT[0] or abs(v[1]) > PAN_LIMIT[1]:
            out.append(
                f"pan `{k}` {v!r} moves the picture off the set: a pan is a small drift of the camera "
                f"(x within +-{PAN_LIMIT[0]:g}, y within +-{PAN_LIMIT[1]:g}; a typical one goes from [0, 0] to [0.06, 0]), "
                "not a position on the screen"
            )
    return out


def _check_num(kind: str, lo: float, hi: float) -> Callable[[dict[str, Any]], list[str]]:
    def check(m: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for k in ("from", "to"):
            v = m.get(k)
            if v is not None and not (_num(v) and lo <= v <= hi):
                out.append(f"{kind} `{k}` must be a number in [{lo:g}, {hi:g}], got {v!r}")
        return out

    return check


def _check_focus(m: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in ("from", "to"):
        v = m.get(k)
        if v is None:
            continue
        if isinstance(v, str) or (_num(v) and 0 <= v <= 2):
            continue
        out.append(
            f"rack_focus `{k}` must be background|midground|foreground, a depth 0..2 or a character id, got {v!r}"
        )
    return out


register_camera_move(
    "pan",
    summary="Slide the camera across the set (parallax: far layers move less)",
    from_to="[x, y] camera offsets in frame fractions: a small drift, e.g. [0, 0] -> [0.06, 0] (x within +-0.25, y within +-0.10); NOT screen positions",
    check=_check_pan,
)
register_camera_move(
    "zoom",
    summary="Lens zoom in/out",
    from_to="scale factor, 1 = none (0.85 .. 2.5)",
    check=_check_num("zoom", *ZOOM_RANGE),
)
register_camera_move(
    "dolly",
    summary="Push in/out with real parallax (near layers grow faster)",
    from_to="0 .. 1 (negative pulls back, down to -0.3)",
    check=_check_num("dolly", *DOLLY_RANGE),
)
register_camera_move(
    "shake",
    summary="Handheld shake; also use for impacts",
    from_to="intensity 0 .. 1",
    check=_check_num("shake", *SHAKE_RANGE),
)
register_camera_move(
    "rack_focus",
    summary="Shift focus between depth layers (blurs the others)",
    from_to="'background'|'midground'|'foreground', 0..2, or a character id",
    check=_check_focus,
)


def _vec(v: Any, default: tuple[float, float] = (0.0, 0.0)) -> tuple[float, float]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return float(v[0]), float(v[1])
    if _num(v):
        return float(v), 0.0
    return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _looks_like_screen_positions(vectors: list[Any]) -> bool:
    """A pan written as where the camera should *look*, in screen fractions: every number well inside 0..1."""
    flat = [x for v in vectors for x in v]
    return bool(flat) and all(0.15 <= x <= 0.95 for x in flat)


def clamp_move_values(move: dict[str, Any]) -> list[str]:
    """Bring the ``from`` / ``to`` of a camera move (a plain dict, as a model writes it) inside what the set allows.

    Returns what was done, in words (an empty list: nothing was wrong).  A model that reads "pan: [x, y]" as a
    *position* writes ``[0.8, 0.5]`` where ``[0.05, 0]`` was meant, and the picture leaves the set: such a pan is read as
    where the camera looks (an offset from the centre, halved: ``[0.8, 0.5]`` becomes ``[0.15, 0]``) and then kept inside the limit.  Values that are not
    numbers are dropped, numbers are clamped.
    """
    kind = move.get("type")
    done: list[str] = []
    if kind == "pan":
        vecs = [
            v
            for v in (move.get("from"), move.get("to"))
            if isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) for x in v)
        ]
        beyond = any(abs(v[0]) > PAN_LIMIT[0] or abs(v[1]) > PAN_LIMIT[1] for v in vecs)
        if beyond and _looks_like_screen_positions(vecs):
            for key in ("from", "to"):
                v = move.get(key)
                if isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) for x in v):
                    # half of the distance from the centre: a gentle version of the sweep that was meant
                    move[key] = [
                        round((float(v[0]) - 0.5) * 0.5, 4),
                        round((float(v[1]) - 0.5) * 0.5, 4),
                    ]
            done.append("read a pan written as screen positions as a gentle drift from the centre")
    for key in ("from", "to"):
        if key not in move or move[key] is None:
            continue
        v = move[key]
        new: Any
        if kind == "pan":
            if isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) for x in v):
                new = [
                    round(_clamp(float(v[0]), -PAN_LIMIT[0], PAN_LIMIT[0]), 4),
                    round(_clamp(float(v[1]), -PAN_LIMIT[1], PAN_LIMIT[1]), 4),
                ]
            elif _num(v):
                new = [round(_clamp(float(v), -PAN_LIMIT[0], PAN_LIMIT[0]), 4), 0.0]
            else:
                new = None
        elif kind in ("zoom", "dolly", "shake"):
            lo, hi = {"zoom": ZOOM_RANGE, "dolly": DOLLY_RANGE, "shake": SHAKE_RANGE}[kind]
            new = round(_clamp(float(v), lo, hi), 4) if _num(v) else None
        else:
            continue
        if new != v and list(new if isinstance(new, list) else [new]) != list(
            v if isinstance(v, (list, tuple)) else [v]
        ):
            done.append(f"kept the camera {kind} inside the range the set allows")
            if new is None:
                del move[key]
            else:
                move[key] = new
    return list(dict.fromkeys(done))


class Camera:
    """Evaluates a scene's camera moves (+ a style's handheld baseline) at any time."""

    def __init__(
        self,
        moves: list[Any],
        seed: int,
        *,
        base_shake: float = 0.0,
        focus_of: Callable[[str], float | None] | None = None,
        default_focus: float = 1.0,
    ) -> None:
        self.moves = sorted(moves, key=lambda m: m.t0)
        self.seed = seed
        self.base_shake = base_shake
        self.focus_of = focus_of or (lambda _cid: None)
        self.default_focus = default_focus
        rng = derive_rng(seed, "camera-shake")
        self._phases = rng.uniform(0, 2 * math.pi, size=(3, 4))
        self._freqs = np.array([[0.9, 1.7, 2.9, 4.3], [1.1, 1.9, 3.1, 4.9], [0.7, 1.3, 2.3, 3.7]])
        self.has_rack = any(m.type == "rack_focus" for m in moves)

    def _focus_value(self, v: Any) -> float:
        if _num(v):
            return float(v)
        if isinstance(v, str):
            if v in FOCUS_NAMES:
                return FOCUS_NAMES[v]
            f = self.focus_of(v)
            if f is not None:
                return f
        return self.default_focus

    def _noise(self, row: int, t: float) -> float:
        w = np.array([1.0, 0.55, 0.3, 0.16])
        return float(
            np.sum(w * np.sin(2 * math.pi * self._freqs[row] * t + self._phases[row])) / w.sum()
        )

    def state(self, t: float) -> CameraState:
        st = CameraState(focus=self.default_focus, rack=self.has_rack)
        shake = self.base_shake
        first_rack = True
        # moves apply in order; each one's value persists after it ends (holds its `to`)
        for m in self.moves:
            u = 0.0 if t <= m.t0 else 1.0 if t >= m.t1 else (t - m.t0) / max(m.t1 - m.t0, 1e-6)
            f0, f1 = m.from_, m.to
            if t < m.t0 and m.type != "shake":
                if m.type == "rack_focus" and first_rack and f0 is not None:
                    st.focus = self._focus_value(f0)  # the lens already sits where the move starts
                first_rack = first_rack and m.type != "rack_focus"
                continue
            first_rack = first_rack and m.type != "rack_focus"
            e = float(ease(m.ease, u))
            if m.type == "pan":
                va, vb = _vec(f0, (st.pan_x, st.pan_y)), _vec(f1, (st.pan_x, st.pan_y))
                st.pan_x, st.pan_y = va[0] + (vb[0] - va[0]) * e, va[1] + (vb[1] - va[1]) * e
            elif m.type == "zoom":
                a = float(f0) if _num(f0) else st.zoom
                b = float(f1) if _num(f1) else a
                st.zoom = a + (b - a) * e
            elif m.type == "dolly":
                a = float(f0) if _num(f0) else st.dolly
                b = float(f1) if _num(f1) else a
                st.dolly = a + (b - a) * e
            elif m.type == "shake":
                if m.t0 <= t <= m.t1:
                    a = float(f0) if _num(f0) else 0.0
                    b = float(f1) if _num(f1) else a
                    shake += (
                        (a + (b - a) * e)
                        * (1.0 if u > 0.04 else u / 0.04)
                        * (1.0 if u < 0.9 else (1 - u) / 0.1)
                    )
            elif m.type == "rack_focus":
                a = self._focus_value(f0) if f0 is not None else st.focus
                b = self._focus_value(f1)
                st.focus = a + (b - a) * e
        # whatever a spec says, the picture stays on the set: a move beyond the range is a lint error, and a lenient render or
        # the preview (which draw it anyway) take it at the limit instead of pushing the characters out of the frame
        st.pan_x = _clamp(st.pan_x, -PAN_LIMIT[0], PAN_LIMIT[0])
        st.pan_y = _clamp(st.pan_y, -PAN_LIMIT[1], PAN_LIMIT[1])
        st.zoom = _clamp(st.zoom, *ZOOM_RANGE)
        st.dolly = _clamp(st.dolly, *DOLLY_RANGE)
        if shake > 0:
            st.shake_x = 0.0045 * shake * self._noise(0, t)
            st.shake_y = 0.0035 * shake * self._noise(1, t)
            st.shake_rot = 0.45 * shake * self._noise(2, t)
        return st


@dataclass(frozen=True)
class PlaneXform:
    """Where a depth plane's content lands for one frame (design px, about the frame centre)."""

    scale: float
    tx: float
    ty: float
    rot: float
    blur: float


def plane_xform(
    st: CameraState, plane: str, frame_w: float, frame_h: float, dof: float
) -> PlaneXform:
    """Camera state -> the transform of one depth plane."""
    _, par, dk, depth = PLANES[PLANE_INDEX[plane]]
    scale = max(MIN_PLANE_SCALE, st.zoom * (1.0 + DOLLY_GAIN * st.dolly * dk))
    tx = -(st.pan_x * par) * frame_w + st.shake_x * (0.55 + 0.45 * par) * frame_w
    ty = -(st.pan_y * par) * frame_h + st.shake_y * (0.55 + 0.45 * par) * frame_h
    # the camera never leaves the set: what the frame can see of this plane stays inside the world it was drawn in
    lim_x = max(0.0, 0.5 * frame_w * (scale * (1.0 + 2.0 * BLEED_X) - 1.0))
    lim_y = max(0.0, 0.5 * frame_h * (scale * (1.0 + 2.0 * BLEED_Y) - 1.0))
    tx, ty = _clamp(tx, -lim_x, lim_x), _clamp(ty, -lim_y, lim_y)
    strength = dof if not st.rack else max(dof, 1.0)
    blur = MAX_BLUR * strength * min(2.0, abs(depth - st.focus)) / 2.0
    return PlaneXform(scale, tx, ty, st.shake_rot, 0.0 if blur < 0.35 else blur)


def max_plane_scale(camera: Camera, duration: float, fps: int) -> float:
    """Largest scale any plane reaches (so plates can be rendered at enough resolution)."""
    hi = 1.0
    for i in range(0, int(duration * fps) + 1, 3):
        st = camera.state(i / fps)
        for name, _par, _dk, _d in PLANES:
            hi = max(hi, plane_xform(st, name, 1080, 1920, 0.0).scale)
    return hi
