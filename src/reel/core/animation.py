"""Keyframes, clips, and the animator that bakes a whole scene's motion.

Actions are authored as *clips*: per-channel curves over local time (``0..duration``).
The :func:`bake_layer` animator then lays all of a layer's actions on the timeline,
cross-blends them, chains root motion, adds secondary motion (follow-through,
sway), and returns dense per-frame arrays.  Because everything is baked up front a
frame is a pure lookup - which is what makes rendering deterministic, parallel and
cacheable.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from reel.core.archetypes import PropDef
from reel.core.easing import ease, smoothstep
from reel.core.rig import CHANNELS, PROP_PREFIX, Rig, is_channel

F = NDArray[np.float64]
Point = tuple[float, float]


# ------------------------------------------------------------------------------- curves
class Curve(Protocol):
    def sample(self, t: F) -> F: ...


@dataclass(frozen=True)
class Const:
    value: float

    def sample(self, t: F) -> F:
        return np.full_like(np.asarray(t, dtype=np.float64), self.value)


@dataclass(frozen=True)
class Keyframes:
    """Piecewise eased curve. ``eases[i]`` shapes the segment *arriving* at key ``i``."""

    times: F
    values: F
    eases: tuple[str, ...]

    @staticmethod
    def build(points: Sequence[Sequence[Any]], default_ease: str = "ease_in_out") -> Keyframes:
        if not points:
            raise ValueError("a curve needs at least one keyframe")
        pts = sorted(
            ((float(p[0]), float(p[1]), str(p[2]) if len(p) > 2 else default_ease) for p in points),
            key=lambda p: p[0],
        )
        times: list[float] = []
        for t, _v, _e in pts:  # strictly increasing times (instant jumps get a 1 µs ramp)
            times.append(t if not times or t > times[-1] else times[-1] + 1e-6)
        return Keyframes(
            np.array(times, dtype=np.float64),
            np.array([p[1] for p in pts], dtype=np.float64),
            tuple(p[2] for p in pts),
        )

    def sample(self, t: F) -> F:
        t = np.asarray(t, dtype=np.float64)
        n = len(self.times)
        out = np.empty_like(t)
        idx = np.searchsorted(self.times, t, side="right") - 1
        before = idx < 0
        after = idx >= n - 1
        out[before] = self.values[0]
        out[after] = self.values[-1]
        mid = ~(before | after)
        if mid.any():
            i = idx[mid]
            t0, t1 = self.times[i], self.times[i + 1]
            u = (t[mid] - t0) / np.maximum(t1 - t0, 1e-9)
            eased = np.empty_like(u)
            seg_ease = np.array([self.eases[k + 1] for k in i], dtype=object)
            for name in set(seg_ease.tolist()):
                sel = seg_ease == name
                eased[sel] = ease(name, u[sel])
            out[mid] = self.values[i] + (self.values[i + 1] - self.values[i]) * eased
        return out

    @property
    def end(self) -> float:
        return float(self.values[-1])

    @property
    def start(self) -> float:
        return float(self.values[0])


@dataclass(frozen=True)
class Sampled:
    """Densely sampled curve (for actions that compute arrays, e.g. walk cycles)."""

    t0: float
    dt: float
    values: F

    def sample(self, t: F) -> F:
        t = np.asarray(t, dtype=np.float64)
        xs = self.t0 + np.arange(len(self.values)) * self.dt
        return np.interp(t, xs, self.values)


@dataclass(frozen=True)
class Oscillator:
    """center + amp*sin(2*pi*freq*(t-t0)+phase) inside [t0,t1], faded in/out over ``fade``."""

    amp: float
    freq: float
    t0: float = 0.0
    t1: float = math.inf
    phase: float = 0.0
    center: float = 0.0
    fade: float = 0.12
    harmonics: tuple[tuple[float, float], ...] = ()  # (freq multiple, amp multiple)

    def sample(self, t: F) -> F:
        t = np.asarray(t, dtype=np.float64)
        x = t - self.t0
        wave = np.sin(2 * math.pi * self.freq * x + self.phase)
        for fm, am in self.harmonics:
            wave = wave + am * np.sin(2 * math.pi * self.freq * fm * x + self.phase * fm)
        f = max(self.fade, 1e-6)
        env = np.clip(x / f, 0, 1) * np.clip((self.t1 - t) / f, 0, 1)
        env = env * env * (3 - 2 * env)
        return self.center + self.amp * wave * env


@dataclass(frozen=True)
class SumCurve:
    parts: tuple[Curve, ...]

    def sample(self, t: F) -> F:
        out = self.parts[0].sample(t)
        for p in self.parts[1:]:
            out = out + p.sample(t)
        return out


@dataclass(frozen=True)
class FuncCurve:
    fn: Callable[[F], F]

    def sample(self, t: F) -> F:
        return np.asarray(self.fn(np.asarray(t, dtype=np.float64)), dtype=np.float64)


# ------------------------------------------------------------------------------- clips
@dataclass
class ChannelTrack:
    base: Curve | None = None  # "set": blended towards this value
    adds: list[Curve] = field(default_factory=list)  # "add": offsets on top of whatever is below
    persist: bool = False  # keep the final value after the action ends (facing, held props)

    def sample_base(self, t: F) -> F | None:
        return None if self.base is None else self.base.sample(t)

    def sample_add(self, t: F) -> F | None:
        if not self.adds:
            return None
        out = self.adds[0].sample(t)
        for c in self.adds[1:]:
            out = out + c.sample(t)
        return out


@dataclass(frozen=True)
class RootTrack:
    """Absolute stage position (screen fractions) over local time."""

    x: Curve
    y: Curve
    hold_before: bool = True


@dataclass(frozen=True)
class Event:
    t: float
    name: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Clip:
    """What an action returns: per-channel curves over local time ``0..duration``."""

    duration: float
    tracks: dict[str, ChannelTrack] = field(default_factory=dict)
    root: RootTrack | None = None
    events: list[Event] = field(default_factory=list)
    blend_in: float = 0.12
    blend_out: float = 0.15
    default_ease: str = "ease_in_out"
    pre: dict[str, float] = field(
        default_factory=dict
    )  # persistent channels' value BEFORE the action

    # -- authoring helpers -------------------------------------------------------------
    def _track(self, channel: str) -> ChannelTrack:
        if not is_channel(channel):
            from reel.core.registry import UnknownEntryError  # lazy: tiny, avoids cycle noise

            close = [c for c in CHANNELS if channel in c or c in channel][:4]
            raise UnknownEntryError("rig channel", channel, close, list(CHANNELS))
        return self.tracks.setdefault(channel, ChannelTrack())

    def key(
        self,
        channel: str,
        points: Sequence[Sequence[Any]],
        *,
        ease: str | None = None,
        mode: str = "set",
        persist: bool = False,
    ) -> Clip:
        """Keyframes ``[(t, value[, ease]), ...]`` (t in seconds from the action start)."""
        curve = Keyframes.build(points, ease or self.default_ease)
        return self.curve(channel, curve, mode=mode, persist=persist)

    def curve(
        self, channel: str, curve: Curve, *, mode: str = "set", persist: bool = False
    ) -> Clip:
        tr = self._track(channel)
        if mode == "set":
            if tr.base is not None:
                raise ValueError(
                    f"channel {channel!r} already has a base curve in this clip; use mode='add' "
                    "to layer another curve on top"
                )
            tr.base = curve
        elif mode == "add":
            tr.adds.append(curve)
        else:
            raise ValueError("mode must be 'set' or 'add'")
        tr.persist = tr.persist or persist
        return self

    def rekey(
        self, channel: str, points: Sequence[Sequence[Any]], *, ease: str | None = None
    ) -> Clip:
        """Replace the base curve of a channel that a helper (e.g. a gait generator) already keyed."""
        self._track(channel).base = Keyframes.build(points, ease or self.default_ease)
        return self

    def hold(self, channel: str, value: float, *, persist: bool = False) -> Clip:
        return self.curve(channel, Const(value), persist=persist)

    def osc(
        self,
        channel: str,
        *,
        amp: float,
        freq: float,
        t0: float = 0.0,
        t1: float | None = None,
        phase: float = 0.0,
        center: float = 0.0,
        fade: float = 0.12,
        harmonics: tuple[tuple[float, float], ...] = (),
        mode: str = "add",
    ) -> Clip:
        """Sinusoidal motion in [t0, t1]; additive by default (rides on top of the pose)."""
        c = Oscillator(
            amp, freq, t0, self.duration if t1 is None else t1, phase, center, fade, harmonics
        )
        return self.curve(channel, c, mode=mode)

    def event(self, t: float, name: str, **data: Any) -> Clip:
        self.events.append(Event(float(t), name, dict(data)))
        return self

    def prop(
        self, name: str, points: Sequence[Sequence[Any]], *, before: float | None = None
    ) -> Clip:
        """Persistent prop attachment weight (1 = carried/worn at its home, 0 = on the ground).
        ``before`` is the value until this action starts (e.g. 0 = it lies on the floor)."""
        ch = PROP_PREFIX + name
        self.key(ch, points, ease="hold", persist=True)
        if before is not None:
            self.pre[ch] = before
        return self

    def move_root(self, x: Curve, y: Curve, *, hold_before: bool = True) -> Clip:
        self.root = RootTrack(x, y, hold_before)
        return self


# ------------------------------------------------------------------------------- context
class StageWorld(Protocol):
    """What an action may ask about the stage it is performing on."""

    frame_size: tuple[int, int]

    def slot(self, name: str) -> Point | None: ...

    def position_of(self, char_id: str, t: float) -> Point | None: ...

    def depth_scale(self, y: float) -> float: ...


@dataclass(frozen=True)
class CharacterInfo:
    id: str
    archetype: str
    props: tuple[str, ...] = ()
    prop_defs: tuple[PropDef, ...] = ()


OFFSCREEN_X = 0.62  # how far outside the frame (in screen fractions) enter_from/exit_to use


@dataclass
class ActionContext:
    """Everything an action function may use. Treat as read-only."""

    name: str
    duration: float
    fps: int
    t0: float  # scene-local start of this action
    rng: np.random.Generator  # deterministic: seeded from (spec seed, scene, layer, action)
    rig: Rig
    char: CharacterInfo
    px_scale: float  # screen px per design px for this character here (layer scale x depth)
    pos0: Point  # root position at the start of this action (screen fractions)
    home: Point  # the layer's configured position
    face0: float  # facing at the start (+1 right / -1 left)
    world: StageWorld
    scene_duration: float = 0.0

    # -- geometry helpers --------------------------------------------------------------------
    @property
    def dims(self) -> Any:
        return self.rig.dims

    @property
    def body_px(self) -> float:
        """Character height on screen in pixels."""
        return float(self.rig.dims.height * self.px_scale)

    @property
    def frame_w(self) -> int:
        return self.world.frame_size[0]

    @property
    def frame_h(self) -> int:
        return self.world.frame_size[1]

    def clip(self, **kw: Any) -> Clip:
        return Clip(self.duration, **kw)

    def free_hand(self, prefer: str = "r") -> str:
        """The hand ('r'/'l') that is not holding a prop, so gestures never wave the briefcase around."""
        held = {
            "r" if d.home == "hand_r" else "l"
            for d in self.char.prop_defs
            if d.home in ("hand_r", "hand_l")
        }
        other = "l" if prefer == "r" else "r"
        return prefer if prefer not in held else (other if other not in held else prefer)

    def resolve_point(self, spec: Any, default: Point | None = None) -> Point:
        """Turn a param into a screen-fraction point.

        Accepts ``[x, y]``, a slot name (``left`` ... ``off_right`` or a background slot), or a
        character id (where that character is when this action starts).
        """
        if spec is None:
            if default is None:
                raise ValueError("a target point is required")
            return default
        if isinstance(spec, (list, tuple)) and len(spec) == 2:
            return float(spec[0]), float(spec[1])
        if isinstance(spec, str):
            if spec in ("off_left", "off_right"):
                x = -OFFSCREEN_X if spec == "off_left" else 1.0 + OFFSCREEN_X
                return (x, self.pos0[1])
            p = self.world.slot(spec)
            if p is not None:
                return p
            other = self.world.position_of(spec, self.t0)
            if other is not None:
                return other
        raise ValueError(
            f"cannot resolve target {spec!r} (use [x,y], a slot name or a character id)"
        )

    def px(self, dx_frac: float, dy_frac: float = 0.0) -> float:
        """Screen distance in px of a displacement given in screen fractions."""
        return math.hypot(dx_frac * self.frame_w, dy_frac * self.frame_h)

    def pose(self, **kw: float) -> dict[str, float]:
        p = self.rig.rest_pose()
        p.update(kw)
        return p

    def seeded(self, *salt: Any) -> np.random.Generator:
        from reel.core.rng import derive_rng

        return derive_rng(int(self.rng.integers(0, 2**31)), *salt)


# ------------------------------------------------------------------------------- baking
@dataclass
class PlacedAction:
    index: int
    name: str
    t0: float
    t1: float
    params: Any  # validated params model instance
    fn: Callable[[ActionContext, Any], Clip]
    moves_root: bool = False


@dataclass
class LayerPlan:
    char: CharacterInfo
    rig: Rig
    position: Point
    px_scale_at: Callable[[float], float]  # depth y -> px scale (layer scale x perspective)
    facing: str = "auto"  # auto | left | right
    actions: list[PlacedAction] = field(default_factory=list)
    seed: int = 0


class BakedLayer:
    """Dense per-frame animation arrays for one character layer."""

    def __init__(
        self,
        fps: int,
        n: int,
        channels: dict[str, F],
        root_x: F,
        root_y: F,
        events: list[Event],
        prop_names: tuple[str, ...],
    ) -> None:
        self.fps = fps
        self.n = n
        self.names = tuple(channels)
        self.matrix = np.stack([channels[k] for k in self.names], axis=1)  # (n, C)
        self.root_x = root_x
        self.root_y = root_y
        self.events = events
        self.prop_names = prop_names
        self._index = {k: i for i, k in enumerate(self.names)}

    def frame_index(self, t: float, quantize_fps: float | None = None) -> int:
        if quantize_fps:
            t = math.floor(t * quantize_fps + 1e-9) / quantize_fps
        return int(min(self.n - 1, max(0, math.floor(t * self.fps + 1e-6))))

    def pose(self, i: int) -> dict[str, float]:
        row = self.matrix[min(max(i, 0), self.n - 1)]
        return {k: float(row[j]) for j, k in enumerate(self.names)}

    def root(self, i: int) -> Point:
        i = min(max(i, 0), self.n - 1)
        return float(self.root_x[i]), float(self.root_y[i])

    def channel(self, name: str) -> F:
        return self.matrix[:, self._index[name]]

    def signature_bytes(self, i: int) -> bytes:
        i = min(max(i, 0), self.n - 1)
        row = np.round(self.matrix[i], 4).astype(np.float32)
        return (
            row.tobytes()
            + np.round(np.array([self.root_x[i], self.root_y[i]]), 5).astype(np.float32).tobytes()
        )


def spring_follow(x: F, freq: float, zeta: float, fps: int, substeps: int = 4) -> F:
    """Critically-underdamped follower: ``y`` chases ``x`` with overshoot (follow-through)."""
    w = 2 * math.pi * freq
    dt = 1.0 / (fps * substeps)
    y = float(x[0])
    v = 0.0
    out = np.empty_like(x)
    for i, target in enumerate(x):
        for _ in range(substeps):
            a = w * w * (target - y) - 2 * zeta * w * v
            v += a * dt
            y += v * dt
        out[i] = y
    return out


def _blend_weights(t: F, a0: float, a1: float, b_in: float, b_out: float, *, persist: bool) -> F:
    w_in = smoothstep((t - a0) / max(b_in, 1e-6)) if b_in > 0 else (t >= a0).astype(np.float64)
    if persist:
        return np.asarray(w_in)
    w_out = (
        1.0 - smoothstep((t - a1) / max(b_out, 1e-6)) if b_out > 0 else (t < a1).astype(np.float64)
    )
    return np.asarray(w_in * w_out)


def apply_clip(
    arr: dict[str, F],
    clip: Clip,
    t: F,
    a0: float,
    a1: float,
    *,
    fps: int,
    blend: bool = True,
    touched: set[str] | None = None,
) -> None:
    """Blend one clip into the baked arrays over its [a0, a1] window (+ fade-out tail)."""
    n = len(t)
    i0 = max(0, math.floor(a0 * fps))
    tail = clip.blend_out if blend else 0.0
    i1 = min(n, math.ceil((a1 + tail) * fps) + 1)
    if i0 >= n:
        return
    for ch, tr in clip.tracks.items():
        if ch not in arr:  # prop channel the layer doesn't own: nothing to animate
            continue
        if tr.persist and touched is not None:
            if ch not in touched and ch in clip.pre:
                arr[ch][:i0] = clip.pre[ch]  # e.g. a prop lying on the ground until picked up
            touched.add(ch)
        end = n if tr.persist else i1
        seg = slice(i0, end)
        tt = t[seg] - a0
        w = _blend_weights(
            t[seg], a0, a1, clip.blend_in if blend else 0.0, tail, persist=tr.persist
        )
        cur = arr[ch][seg]
        base = tr.sample_base(tt)
        if base is not None:
            cur = cur + (base - cur) * w
        add = tr.sample_add(tt)
        if add is not None:
            cur = cur + add * w
        arr[ch][seg] = cur


def bake_layer(plan: LayerPlan, world: StageWorld, *, fps: int, duration: float) -> BakedLayer:
    """Bake every action of a layer into dense arrays (see module docstring)."""
    from reel.actions.base import idle_baseline  # late import: actions register themselves

    n = max(1, math.ceil(duration * fps - 1e-9))
    t = np.arange(n, dtype=np.float64) / fps
    rig = plan.rig
    arr: dict[str, F] = {k: np.full(n, v) for k, v in rig.rest_pose().items()}
    prop_names = tuple(p for p in plan.char.props)
    for p in prop_names:
        arr[PROP_PREFIX + p] = np.ones(n)

    x0, y0 = plan.position
    face0 = {"left": -1.0, "right": 1.0}.get(plan.facing, 1.0 if x0 < 0.5 else -1.0)
    arr["face"][:] = face0
    root_x = np.full(n, x0)
    root_y = np.full(n, y0)
    events: list[Event] = []

    # baseline: breathing, weight shifts, blinks (always on, never blended out)
    base_ctx = ActionContext(
        "baseline",
        duration,
        fps,
        0.0,
        np.random.default_rng(plan.seed),
        rig,
        plan.char,
        plan.px_scale_at(y0),
        plan.position,
        plan.position,
        face0,
        world,
        duration,
    )
    apply_clip(arr, idle_baseline(base_ctx), t, 0.0, duration + 1.0, fps=fps, blend=False)

    pos: Point = plan.position
    face = face0
    first_root_done = False
    touched: set[str] = set()
    for act in sorted(plan.actions, key=lambda a: (a.t0, a.index)):
        dur = act.t1 - act.t0
        if dur <= 0:
            continue
        ctx = ActionContext(
            name=act.name,
            duration=dur,
            fps=fps,
            t0=act.t0,
            rng=np.random.default_rng([plan.seed, act.index, 1]),
            rig=rig,
            char=plan.char,
            px_scale=plan.px_scale_at(pos[1]),
            pos0=pos,
            home=plan.position,
            face0=face,
            world=world,
            scene_duration=duration,
        )
        clip = act.fn(ctx, act.params)
        apply_clip(arr, clip, t, act.t0, act.t1, fps=fps, touched=touched)
        for ev in clip.events:
            if 0 <= ev.t <= dur:
                events.append(Event(act.t0 + ev.t, ev.name, ev.data))
        if clip.root is not None:
            i0 = max(0, math.floor(act.t0 * fps))
            i1 = min(n, math.ceil(act.t1 * fps))
            if i0 < n:
                rel = t[i0:i1] - act.t0
                rx = clip.root.x.sample(rel)
                ry = clip.root.y.sample(rel)
                if not first_root_done and clip.root.hold_before:
                    root_x[:i0] = rx[0] if len(rx) else root_x[0]
                    root_y[:i0] = ry[0] if len(ry) else root_y[0]
                root_x[i0:i1] = rx
                root_y[i0:i1] = ry
                end = np.array([dur])
                ex = float(clip.root.x.sample(end)[0])
                ey = float(clip.root.y.sample(end)[0])
                root_x[i1:] = ex
                root_y[i1:] = ey
                pos = (ex, ey)
            first_root_done = True
        if "face" in clip.tracks:  # the next action starts facing wherever this one left us
            end_i = min(n - 1, max(0, math.ceil(act.t1 * fps) + 2))
            face = float(np.sign(arr["face"][end_i])) or face

    _secondary_motion(arr, root_x, fps)
    for k, v in arr.items():
        if k in CHANNELS:
            c = CHANNELS[k]
            np.clip(v, c.lo, c.hi, out=v)
    events.sort(key=lambda e: e.t)
    return BakedLayer(fps, n, arr, root_x, root_y, events, prop_names)


def _secondary_motion(arr: dict[str, F], root_x: F, fps: int) -> None:
    """Follow-through and sway derived from the primary motion."""
    for s in ("r", "l"):
        fore_world = arr["rot"] + arr["torso_lean"] + arr[f"arm_{s}_sh"] + arr[f"arm_{s}_el"]
        lag = spring_follow(fore_world, 3.4, 0.30, fps)
        arr[f"hand_lag_{s}"] = np.clip((lag - fore_world) * 0.85, -45, 45)
    head_world = arr["rot"] + arr["torso_lean"] + arr["head_tilt"]
    lag_h = spring_follow(head_world, 2.8, 0.40, fps)
    arr["head_lag"] = np.clip((lag_h - head_world) * 0.55, -14, 14)
    vx = np.gradient(root_x, 1.0 / fps) if len(root_x) > 1 else np.zeros_like(root_x)
    (np.gradient(arr["lift"] + arr["bob"], 1.0 / fps) if len(root_x) > 1 else np.zeros_like(root_x))
    # figure-space sway: negative = trailing behind (against the facing direction)
    target = -np.clip(vx * np.sign(arr["face"]) / 0.30, -1.6, 1.6)
    sway = spring_follow(target, 1.9, 0.28, fps)
    # a little idle flutter keeps hair/cloth alive even when standing still
    tt = np.arange(len(root_x)) / fps
    arr["sway"] = np.clip(sway + 0.06 * np.sin(2 * math.pi * 0.35 * tt), -2, 2)
