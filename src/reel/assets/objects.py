"""Objects in a scene: a car, a tree, a cake, a dragon that flies by.

``scenes[].objects[]`` places a library object at a position (a slot or [x, y] screen fractions, the point it stands on),
at a size, on a depth plane, and gives it a few simple motions over time.  An object that never moves is drawn into the
scene's cached background plate; one that moves is drawn each frame.  Placement uses the same perspective as characters, so
an object lower on the screen is bigger, and the same camera parallax, so it sits in the scene rather than on top of it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from reel.assets.art import load_art, recolor
from reel.assets.model import Art, AssetDef
from reel.core.easing import ease
from reel.core.ir import Shape
from reel.core.planner import CHAR_UNIT
from reel.core.spec import ObjectMotionSpec, ObjectSpec

FRAME_W, FRAME_H = 1080.0, 1920.0
Z_BASE = 1_000_000.0  # objects draw above the template's own shapes of the plane

# the motions an object may be given (name -> what it needs / does); see `Motion`
MOTIONS: dict[str, str] = {
    "move": "travel from where it stands to `to` ([x, y] or a slot) over t0..t1, then stay there",
    "hop": "jump `count` times (default 1) by `amount` of its height (default 0.35) during t0..t1",
    "float": "drift gently up and down by `amount` of its height (default 0.05) during t0..t1",
    "spin": "turn `amount` full turns (default 1) over t0..t1; keeps the final angle",
    "pulse": "swell and shrink by `amount` (default 0.1) `count` times (default 3) during t0..t1",
    "fade": "change opacity from `from` (default 0) to `to` (default 1) over t0..t1",
    "grow": "change size from `from` (default 0) to `to` (default 1) times its size over t0..t1",
    "shake": "tremble by `amount` of its height (default 0.02) during t0..t1",
}


@dataclass(frozen=True)
class ObjState:
    visible: bool
    x: float  # anchor, frame fractions
    y: float
    rot: float  # degrees, clockwise
    size: float  # multiplier on top of the base size
    alpha: float
    dx: float = 0.0  # extra offset in design px (hops, floating, shaking)
    dy: float = 0.0

    def key(self) -> tuple[Any, ...]:
        return (
            self.visible,
            round(self.x, 4),
            round(self.y, 4),
            round(self.rot, 3),
            round(self.size, 4),
            round(self.alpha, 3),
            round(self.dx, 2),
            round(self.dy, 2),
        )


def _u(t: float, t0: float, t1: float) -> float:
    return 0.0 if t <= t0 else 1.0 if t >= t1 else (t - t0) / max(t1 - t0, 1e-6)


@dataclass
class ObjectInstance:
    """One ``objects[]`` entry resolved against the library, ready to be drawn at any time of the scene."""

    index: int
    spec: ObjectSpec
    asset: AssetDef
    art: Art
    shapes: list[Shape]
    plane: str
    start: tuple[float, float]  # the anchor, frame fractions
    flip: float  # -1 when the object must be mirrored (the spec asked for a side the art does not face)
    depth_scale: Callable[[float], float]
    slot: Callable[[str], tuple[float, float] | None]
    warnings: list[str] = field(default_factory=list)

    # -- what it does over time ----------------------------------------------------------------------
    @property
    def is_static(self) -> bool:
        s = self.spec
        return not s.motions and s.t0 <= 0.0 and s.t1 is None

    def _target(self, to: Any) -> tuple[float, float] | None:
        if isinstance(to, str):
            return self.slot(to)
        if isinstance(to, (list, tuple)) and len(to) == 2:
            return float(to[0]), float(to[1])
        return None

    def state(self, t: float) -> ObjState:
        s = self.spec
        visible = t >= s.t0 and (s.t1 is None or t < s.t1)
        x, y = self.start
        rot, size, alpha = float(s.rotation), 1.0, float(s.alpha)
        dx = dy = 0.0
        h = self.asset.height
        for m in s.motions:
            if t < m.t0 and m.type != "fade":
                if (
                    m.type == "grow"
                ):  # a grow that has not begun is where it starts: nothing, or the size it was given
                    size *= 0.0 if m.from_ is None else float(m.from_)
                continue
            u = _u(t, m.t0, m.t1)
            e = float(ease(m.ease, u))
            amount = m.amount
            if m.type == "move":
                dest = self._target(m.to)
                if dest is not None:
                    x, y = x + (dest[0] - x) * e, y + (dest[1] - y) * e
            elif m.type == "hop":
                n = max(1, int(m.count or 1))
                if m.t0 <= t <= m.t1:
                    k = (u * n) % 1.0
                    dy -= (amount if amount is not None else 0.35) * h * 4.0 * k * (1.0 - k)
            elif m.type == "float":
                if m.t0 <= t <= m.t1:
                    dy += (
                        (amount if amount is not None else 0.05)
                        * h
                        * math.sin(2 * math.pi * (t - m.t0) / 2.4)
                    )
            elif m.type == "spin":
                rot += 360.0 * (amount if amount is not None else 1.0) * e
            elif m.type == "pulse":
                if m.t0 <= t <= m.t1:
                    n = max(1, int(m.count or 3))
                    size *= 1.0 + (amount if amount is not None else 0.1) * math.sin(
                        2 * math.pi * n * u
                    )
            elif m.type == "fade":
                a0 = 0.0 if m.from_ is None else float(m.from_)
                a1 = 1.0 if not isinstance(m.to, (int, float)) else float(m.to)
                alpha *= max(0.0, min(1.0, a0 + (a1 - a0) * e))
            elif m.type == "grow":
                g0 = 0.0 if m.from_ is None else float(m.from_)
                g1 = 1.0 if not isinstance(m.to, (int, float)) else float(m.to)
                size *= g0 + (g1 - g0) * e
            elif m.type == "shake" and m.t0 <= t <= m.t1:
                a = (amount if amount is not None else 0.02) * h
                dx += a * math.sin(2 * math.pi * 11.0 * t)
                dy += a * 0.6 * math.sin(2 * math.pi * 7.3 * t + 1.3)
        return ObjState(visible, x, y, rot, size, alpha, dx, dy)

    def shapes_at(self, t: float) -> list[Shape]:
        st = self.state(t)
        return self.shapes_for(st)

    def shapes_for(self, st: ObjState) -> list[Shape]:
        if not st.visible or st.alpha <= 0.002 or st.size <= 0.0:
            return []
        sc = CHAR_UNIT * self.spec.scale * self.depth_scale(st.y) * st.size
        xf = (
            st.x * FRAME_W + st.dx,
            st.y * FRAME_H + st.dy,
            st.rot * self.flip,
            sc * self.flip,
            sc,
            0.0,
            0.0,
        )
        if st.alpha < 0.999:
            return [replace(s, xf=xf, alpha=s.alpha * st.alpha) for s in self.shapes]
        return [replace(s, xf=xf) for s in self.shapes]

    def key(self, t: float) -> bytes:
        return repr(self.state(t).key()).encode()

    def static_key(self) -> list[Any]:
        """What the scene's cache key needs of this object besides its state at one frame."""
        s = self.spec
        return [
            self.asset.name,
            self.asset.content_hash,
            self.plane,
            list(self.start),
            s.scale,
            self.flip,
            s.rotation,
            s.alpha,
            s.t0,
            s.t1,
            sorted(s.palette.items()),
            [m.model_dump(by_alias=True, mode="json") for m in s.motions],
        ]


def plane_for(spec: ObjectSpec) -> str:
    if spec.depth == "background":
        return "back"
    if spec.depth == "foreground":
        return "near"
    return "mid_front" if spec.layer == "front" else "mid_back"


def build_object(
    index: int,
    spec: ObjectSpec,
    asset: AssetDef,
    *,
    slot: Callable[[str], tuple[float, float] | None],
    depth_scale: Callable[[float], float],
    lenient: bool,
    warn: Callable[[str], None],
) -> ObjectInstance | None:
    """Resolve one object of a scene; None (with a warning) when it cannot be placed and ``lenient``."""
    art = load_art(asset)
    pos: tuple[float, float]
    if isinstance(spec.position, str):
        found = slot(spec.position)
        if found is None:
            if not lenient:
                raise KeyError(f"unknown position slot {spec.position!r} for object {spec.asset!r}")
            warn(f"object {spec.asset!r}: unknown slot {spec.position!r}; using center")
            found = slot("center") or (0.5, 0.8)
        pos = found
    else:
        pos = (float(spec.position[0]), float(spec.position[1]))
    want = spec.facing if spec.facing != "auto" else "right"
    flip = -1.0 if want == "left" else 1.0
    shapes = recolor(art.shapes, art.roles, spec.palette) if art.roles else list(art.shapes)
    if (
        art.fmt == "svg"
    ):  # one soft shadow from the whole outline instead of one per piece (none for a thing that floats)
        from reel.assets.art import silhouette
        from reel.core.ir import PathG

        shapes = [replace(s, shadow=False) for s in shapes]
        outline = silhouette(art) if asset.casts_shadow else ()
        if outline:
            caster = Shape(PathG(outline), None, elev=3.2, shadow=True, tag="shadow_caster", lod=0)
            shapes = [caster, *shapes]
    shapes = [replace(s, z=Z_BASE + index * 10_000 + i) for i, s in enumerate(shapes)]
    return ObjectInstance(
        index, spec, asset, art, shapes, plane_for(spec), pos, flip, depth_scale, slot
    )


def motion_problems(m: ObjectMotionSpec) -> list[tuple[str, str]]:
    """(lint code, message) for each thing wrong with one motion's fields; empty when it is fine."""
    out: list[tuple[str, str]] = []
    if m.type not in MOTIONS:
        return [
            ("OBJECT_MOTION", f"unknown motion {m.type!r}; known: {', '.join(sorted(MOTIONS))}")
        ]
    if m.t1 <= m.t0:
        out.append(("TIME_RANGE", f"motion t1 ({m.t1:g}) must be after t0 ({m.t0:g})"))
    if m.type == "move" and m.to is None:
        out.append(("OBJECT_MOTION", "a move needs `to`: [x, y] screen fractions, or a slot name"))
    if m.type == "move" and isinstance(m.to, (int, float)):
        out.append(
            ("OBJECT_MOTION", "`to` of a move is a position ([x, y] or a slot), not a number")
        )
    if m.type in ("fade", "grow") and m.to is not None and not isinstance(m.to, (int, float)):
        out.append(("OBJECT_MOTION", f"`to` of a {m.type} is a number"))
    return out
