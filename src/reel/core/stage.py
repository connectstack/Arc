"""SceneStage: everything needed to render one scene's frames.

Per scene it builds the background graph (a template), plans and bakes every character layer,
and sets up the camera.  `render(local_frame)` composites the depth planes back to front with
parallax, depth-of-field blur and characters in between, and returns a BGRA frame *before*
post-FX and captions.  `signature(local_frame)` is a cheap digest of everything that frame's
pixels depend on, which is what the frame cache keys on.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import skia

from reel.core.animation import BakedLayer, LayerPlan, bake_layer
from reel.core.archetypes import Archetype
from reel.core.camera import Camera, max_plane_scale, plane_xform
from reel.core.captions import CaptionRender
from reel.core.catalog import CATALOG, Catalog
from reel.core.figure import build_figure
from reel.core.fx import FxConfig
from reel.core.ir import Particles, Shape, apply_anim, expand_particles, shape_bbox
from reel.core.planner import PlanReport, SimpleWorld, plan_layer
from reel.core.rig import solve, squash_scale
from reel.core.rng import stable_int
from reel.core.spec import ReelSpec
from reel.styles.base import StyleContext, StylePack
from reel.templates.base import X0, X1, Y0, Y1, SceneGraph, build_graph

FRAME_W, FRAME_H = 1080.0, 1920.0
GROUPS = ("sky", "far", "back", "mid", "near")  # camera plane groups, back to front
PLANE_GROUP = {
    "sky": "sky",
    "far": "far",
    "back": "back",
    "mid_back": "mid",
    "mid_front": "mid",
    "near": "near",
}
DEPTH_GROUP = {"background": "back", "mid": "mid", "foreground": "near"}
FOCUS_DEPTH = {"background": 0.5, "mid": 1.0, "foreground": 2.0}
PLATE_PAD = 90.0


@dataclass(frozen=True)
class RenderConfig:
    size: tuple[int, int]  # device pixels
    fps: int
    seed: int
    style: str
    lenient: bool
    fx: FxConfig
    safe: tuple[float, float, float, float]  # top, bottom, left, right

    @property
    def scale(self) -> float:
        return self.size[0] / FRAME_W


@dataclass
class LayerRT:
    index: int
    char_id: str
    arch: Archetype
    palette: dict[str, str]
    props: list[Any]  # PropDef
    baked: BakedLayer
    plan: Any  # LayerPlan
    group: str
    depth: str


@dataclass
class _PlaneData:
    static: list[Shape] = field(default_factory=list)
    dynamic: list[Shape] = field(default_factory=list)
    particles: list[Particles] = field(default_factory=list)
    objects: list[Any] = field(
        default_factory=list
    )  # library objects that move or come and go: drawn every frame


def to_array(surface: skia.Surface) -> np.ndarray:
    return np.ascontiguousarray(
        surface.makeImageSnapshot().toarray(colorType=skia.kBGRA_8888_ColorType)
    )


def _blur_k(sigma: float) -> int:
    """How much smaller than the frame a depth-of-field layer is rasterised and blurred: it is blurred anyway, so a
    layer that will be blurred by a few pixels is drawn at 1/2 (or 1/4) size, which costs a fraction of the pixels."""
    return 4 if sigma >= 2.0 else 2 if sigma >= 0.9 else 1


def _blur_composite(
    canvas: skia.Canvas, layer: skia.Surface, sigma: float, k: int, size: tuple[int, int]
) -> None:
    """Draw ``layer`` (rasterised at 1/``k`` of ``size``) onto ``canvas`` blurred by ``sigma`` device px of the full frame.

    The blur runs on the small layer (sigma/k) and the result is scaled back up; the down/up-sampling itself
    softens by about 0.28 px per unit of ``k``, which is taken off the blur so the look matches a full-size blur."""
    img = layer.makeImageSnapshot()
    if k == 1:
        paint = skia.Paint(ImageFilter=skia.ImageFilters.Blur(sigma, sigma, skia.TileMode.kClamp))
        canvas.drawImage(img, 0, 0, skia.SamplingOptions(), paint)
        return
    lw, lh = img.width(), img.height()
    low = math.sqrt(max(sigma * sigma - (0.28 * k) ** 2, 0.1)) / k
    soft = skia.Surface(lw, lh)
    bp = skia.Paint(ImageFilter=skia.ImageFilters.Blur(low, low, skia.TileMode.kClamp))
    soft.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(), bp)
    canvas.drawImageRect(
        soft.makeImageSnapshot(),
        skia.Rect(0, 0, lw, lh),
        skia.Rect(0, 0, size[0], size[1]),
        skia.SamplingOptions(skia.FilterMode.kLinear),
        None,
    )


class SceneStage:
    def __init__(
        self,
        spec: ReelSpec,
        index: int,
        cfg: RenderConfig,
        *,
        catalog: Catalog | None = None,
        word_timings: dict[int, list[tuple[float, float, str]]] | None = None,
        report: PlanReport | None = None,
    ) -> None:
        self.cat = catalog or CATALOG
        self.spec = spec
        self.index = index
        self.scene = spec.scenes[index]
        self.cfg = cfg
        self.report = report if report is not None else PlanReport()
        self.word_timings = word_timings or {}
        sc = self.scene
        self.fps = cfg.fps
        self.n_frames = max(1, round(sc.duration_sec * cfg.fps))

        style_cls = self.cat.styles.get(cfg.style)
        self.style: StylePack = style_cls()

        # --- background -------------------------------------------------------------------------
        tpl = sc.background.template
        if tpl not in self.cat.backgrounds:
            if not cfg.lenient:
                raise KeyError(f"unknown background template {tpl!r}")
            self.report.warn(f"scene {sc.id!r}: unknown background {tpl!r}; using 'abstract'")
            tpl = "abstract"
        self.bg_def = self.cat.backgrounds.get(tpl)
        params = sc.background.params if tpl == sc.background.template else {}
        try:
            self.graph: SceneGraph = build_graph(
                self.bg_def, params, stable_int(spec.meta.seed, sc.id), sc.id
            )
        except Exception as exc:  # bad params: strict raises, lenient uses defaults
            if not cfg.lenient:
                raise
            self.report.warn(
                f"scene {sc.id!r}: background params rejected ({exc.__class__.__name__}); using defaults"
            )
            self.graph = build_graph(self.bg_def, {}, stable_int(spec.meta.seed, sc.id), sc.id)
        self.world = SimpleWorld(
            frame_size=(int(FRAME_W), int(FRAME_H)),
            ground_y=self.graph.ground_y,
            perspective=self.graph.perspective,
            extra_slots=dict(self.graph.slots),
            positions=self._position_of,
        )
        self._split_planes()
        self.objects = self._build_objects()

        # --- characters ---------------------------------------------------------------------------------
        self.layers: list[LayerRT] = []
        self._home: dict[str, tuple[float, float]] = {}
        self._baked_by_char: dict[str, LayerRT] = {}
        plans: list[tuple[int, LayerPlan]] = []
        for li, layer in enumerate(sc.layers):
            plan = plan_layer(
                spec,
                sc,
                layer,
                li,
                self.world,
                catalog=self.cat,
                lenient=cfg.lenient,
                report=self.report,
                word_timings=self.word_timings,
            )
            if plan is not None:
                plans.append((li, plan))
                self._home[layer.character] = plan.position
        for (
            li,
            plan,
        ) in plans:  # bake in order; later layers can look at where earlier ones actually walk
            layer = sc.layers[li]
            baked = bake_layer(plan, self.world, fps=cfg.fps, duration=sc.duration_sec)
            arch = self.cat.archetypes.get(plan.char.archetype)
            ch = spec.character(layer.character)
            palette = {**arch.palette, **(ch.palette if ch else {})}
            lr = LayerRT(
                li,
                layer.character,
                arch,
                palette,
                list(plan.char.prop_defs),
                baked,
                plan,
                DEPTH_GROUP[layer.depth],
                layer.depth,
            )
            self.layers.append(lr)
            self._baked_by_char[layer.character] = lr
        self._seed = stable_int(spec.meta.seed, sc.id)

        # --- camera -------------------------------------------------------------------------------------------
        self.camera = Camera(
            [m for m in sc.camera.moves if m.type in self.cat.camera_moves],
            stable_int(spec.meta.seed, sc.id, "cam"),
            base_shake=getattr(self.style, "base_shake", 0.0),
            focus_of=self._focus_of,
        )
        self.ov = max(
            1.0, math.ceil(max_plane_scale(self.camera, sc.duration_sec, cfg.fps) * 4) / 4
        )
        self._plates: dict[str, tuple[skia.Image, tuple[float, float, float, float]] | None] = {}

        # static part of every frame signature
        blob = json.dumps(
            {
                "bg": [
                    sc.background.template,
                    sc.background.params,
                    self._asset_hash(sc.background.template),
                ],
                "objects": [o.static_key() for o in self.objects],
                "layers": [
                    [
                        lr.char_id,
                        lr.arch.name,
                        sorted(lr.palette.items()),
                        [p.name for p in lr.props],
                        sc.layers[lr.index].scale,
                        lr.depth,
                        sc.layers[lr.index].facing,
                        self._arch_hash(lr.arch),
                    ]
                    for lr in self.layers
                ],
                "scene": sc.id,
                "seed": spec.meta.seed,
                "style": (cfg.style, self.style.version),
                "size": cfg.size,
                "fps": cfg.fps,
                "ov": self.ov,
                "dof": self.style.dof,
                "fx": cfg.fx.pre_key(),
                "cam": [(m.type, m.t0, m.t1, m.ease) for m in self.camera.moves],
            },
            sort_keys=True,
            default=str,
        )
        self._static_key = hashlib.sha256(blob.encode()).digest()[:16]

    # ------------------------------------------------------------------------------- helpers
    def _position_of(self, char_id: str, t: float) -> tuple[float, float] | None:
        lr = self._baked_by_char.get(char_id)
        if lr is not None:
            return lr.baked.root(lr.baked.frame_index(t))
        return self._home.get(char_id)

    def _focus_of(self, char_id: str) -> float | None:
        lr = self._baked_by_char.get(char_id)
        return FOCUS_DEPTH[lr.depth] if lr else None

    def _asset_hash(self, name: str) -> str:
        """Content hash of the library asset behind a background or archetype name ('' for the engine's own)."""
        return self.cat.assets.get(name).content_hash if name in self.cat.assets else ""

    @staticmethod
    def _arch_hash(arch: Archetype) -> str:
        a = arch.features.get("asset")
        return str(a.content_hash) if a is not None else ""

    def _build_objects(self) -> list[Any]:
        """The scene's library objects; static ones are drawn into the cached plates, moving ones every frame."""
        from reel.assets.objects import build_object

        out: list[Any] = []
        for i, spec in enumerate(self.scene.objects):
            if spec.asset not in self.cat.objects:
                msg = f"scene {self.scene.id!r}: object {spec.asset!r} is not in the asset library"
                if not self.cfg.lenient:
                    raise KeyError(msg)
                self.report.warn(msg + "; left out")
                continue
            try:
                inst = build_object(
                    i,
                    spec,
                    self.cat.objects.get(spec.asset),
                    slot=self.world.slot,
                    depth_scale=self.world.depth_scale,
                    lenient=self.cfg.lenient,
                    warn=self.report.warn,
                )
            except (
                Exception
            ) as exc:  # an asset that cannot be read: strict raises, lenient leaves the object out
                if not self.cfg.lenient:
                    raise
                self.report.warn(
                    f"scene {self.scene.id!r}: object {spec.asset!r} cannot be drawn ({exc}); left out"
                )
                continue
            if inst is None:
                continue
            if inst.is_static:
                pd = self.planes[inst.plane]
                pd.static.extend(inst.shapes_at(0.0))
                pd.static.sort(key=lambda x: x.z)
            else:
                self.planes[inst.plane].objects.append(inst)
            out.append(inst)
        return out

    def _split_planes(self) -> None:
        self.planes: dict[str, _PlaneData] = {p: _PlaneData() for p in self.graph.planes}
        for name, shapes in self.graph.planes.items():
            pd = self.planes[name]
            for s in sorted(shapes, key=lambda x: x.z):
                (pd.dynamic if s.anim is not None else pd.static).append(s)
        for plane, p in self.graph.particles:
            self.planes[plane].particles.append(p)

    def _ctx(self, t: float, frame: int, scale: float | None = None) -> StyleContext:
        return StyleContext(
            scale=self.cfg.scale if scale is None else scale,
            size=self.cfg.size,
            scheme=self.graph.scheme,
            seed=self._seed,
            t=t,
            frame=frame,
            fps=self.fps,
            safe=self.cfg.safe,
        )

    # ------------------------------------------------------------------------------- plates
    def _plate(self, plane: str) -> tuple[skia.Image, tuple[float, float, float, float]] | None:
        if plane in self._plates:
            return self._plates[plane]
        shapes = self.planes[plane].static
        if not shapes:
            self._plates[plane] = None
            return None
        x0, y0, x1, y1 = X1, Y1, X0, Y0
        for s in shapes:
            bx0, by0, bx1, by1 = shape_bbox(s)
            x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
        pad = PLATE_PAD
        x0, y0, x1, y1 = max(X0, x0 - pad), max(Y0, y0 - pad), min(X1, x1 + pad), min(Y1, y1 + pad)
        k = self.cfg.scale * self.ov
        surf = skia.Surface(max(1, math.ceil((x1 - x0) * k)), max(1, math.ceil((y1 - y0) * k)))
        cv = surf.getCanvas()
        cv.clear(skia.ColorTRANSPARENT)
        cv.scale(k, k)
        cv.translate(-x0, -y0)
        self.style.draw_background(cv, shapes, plane, self._ctx(0.0, 0, scale=k))
        img = surf.makeImageSnapshot()
        self._plates[plane] = (img, (x0, y0, x1, y1))
        return self._plates[plane]

    # ------------------------------------------------------------------------------- drawing
    def _draw_plane(self, canvas: skia.Canvas, plane: str, ctx: StyleContext, t: float) -> None:
        plate = self._plate(plane)
        if plate is not None:
            img, (x0, y0, x1, y1) = plate
            canvas.drawImageRect(
                img,
                skia.Rect(0, 0, img.width(), img.height()),
                skia.Rect(x0, y0, x1, y1),
                skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear),
                None,
            )
        pd = self.planes[plane]
        dyn = [apply_anim(s, t) for s in pd.dynamic]
        for o in pd.objects:
            dyn.extend(o.shapes_at(t))
        for p in pd.particles:
            dyn.extend(expand_particles(p, t))
        if dyn:
            dyn.sort(key=lambda s: s.z)
            self.style.draw_background(canvas, dyn, plane, ctx)

    def _draw_characters(
        self, canvas: skia.Canvas, group: str, ctx: StyleContext, t: float, frame: int
    ) -> None:
        items = [lr for lr in self.layers if lr.group == group]
        if not items:
            return
        qfps = self.style.character_fps
        drawn: list[tuple[float, LayerRT, Any, Any, Any, tuple[float, float], float]] = []
        for lr in items:
            i = lr.baked.frame_index(t, qfps)
            pose = lr.baked.pose(i)
            rx, ry = lr.baked.root(i)
            if rx < -0.58 or rx > 1.58:
                continue  # fully off-stage
            fig = solve(lr.arch.dims, pose)
            tq = (math.floor(t * qfps + 1e-9) / qfps) if qfps else t
            props = [(pd, float(pose.get(f"prop:{pd.name}", 1.0))) for pd in lr.props]
            build = build_figure(fig, lr.arch, lr.palette, props, tq)
            drawn.append((ry, lr, fig, build, pose, (rx, ry), lr.plan.px_scale_at(ry)))
        drawn.sort(key=lambda d: (d[0], d[1].index))
        for _y, _lr, _fig, build, pose, (rx, ry), sc in drawn:  # shadows first: never cover bodies
            face = pose["face"]
            canvas.save()
            canvas.translate(rx * FRAME_W, ry * FRAME_H)
            canvas.scale(sc * (face if abs(face) > 0.05 else 0.05), sc)
            self.style.draw_shadow(
                canvas, build.shadow, replace(ctx, flip=1.0 if face >= 0 else -1.0)
            )
            canvas.restore()
        for _y, _lr, fig, build, pose, (rx, ry), sc in drawn:
            face = pose["face"]
            sx, sy = squash_scale(pose["squash"])
            canvas.save()
            canvas.translate(rx * FRAME_W, ry * FRAME_H)
            canvas.rotate(pose["rot"] * (1.0 if face >= 0 else -1.0))
            canvas.scale(sc * sx * (face if abs(face) > 0.05 else 0.05), sc * sy)
            self.style.draw_character(
                canvas, build, fig, replace(ctx, flip=1.0 if face >= 0 else -1.0)
            )
            canvas.restore()

    def _plane_matrix(self, canvas: skia.Canvas, group: str, st: Any) -> Any:
        xf = plane_xform(st, group, FRAME_W, FRAME_H, self.style.dof)
        s = self.cfg.scale
        cx, cy = FRAME_W / 2, FRAME_H / 2
        canvas.scale(s, s)
        canvas.translate(cx + xf.tx, cy + xf.ty)
        if xf.rot:
            canvas.rotate(xf.rot)
        canvas.scale(xf.scale, xf.scale)
        canvas.translate(-cx, -cy)
        return xf

    def render(self, local_frame: int) -> np.ndarray:
        """World frame (BGRA uint8, device size) for scene-local frame ``local_frame``."""
        t = local_frame / self.fps
        st = self.camera.state(t)
        W, H = self.cfg.size
        surface = skia.Surface(W, H)
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorWHITE)
        ctx = replace(self._ctx(t, local_frame), tick=int(t * 12) + 1)
        for group in GROUPS:
            planes = [
                p
                for p in ("sky", "far", "back", "mid_back", "mid_front", "near")
                if PLANE_GROUP[p] == group
            ]
            xf = plane_xform(st, group, FRAME_W, FRAME_H, self.style.dof)
            has_chars = any(lr.group == group for lr in self.layers)
            if not has_chars and not any(
                self.planes[p].static
                or self.planes[p].dynamic
                or self.planes[p].particles
                or self.planes[p].objects
                for p in planes
            ):
                continue
            target = canvas
            layer_surface = None
            sigma = xf.blur * self.cfg.scale if xf.blur > 0 else 0.0
            k = _blur_k(sigma)
            if xf.blur > 0:
                lw, lh = (
                    -(-W // k),
                    -(-H // k),
                )  # a blurred layer is rasterised at 1/k size (see _blur_k)
                layer_surface = skia.Surface(lw, lh)
                target = layer_surface.getCanvas()
                target.clear(skia.ColorTRANSPARENT)
                if k > 1:
                    target.scale(lw / W, lh / H)
            target.save()
            self._plane_matrix(target, group, st)
            if group == "mid":
                self._draw_plane(target, "mid_back", ctx, t)
                self._draw_characters(target, group, ctx, t, local_frame)
                self._draw_plane(target, "mid_front", ctx, t)
            else:
                for p in planes:
                    self._draw_plane(target, p, ctx, t)
                if has_chars:
                    self._draw_characters(target, group, ctx, t, local_frame)
            target.restore()
            if layer_surface is not None:
                _blur_composite(canvas, layer_surface, sigma, k, (W, H))
        if self.graph.overlay:
            canvas.save()
            canvas.scale(self.cfg.scale, self.cfg.scale)
            self.style.draw_background(
                canvas, [apply_anim(s, t) for s in self.graph.overlay], "overlay", ctx
            )
            canvas.restore()
        return to_array(surface)

    # ------------------------------------------------------------------------------- cache key
    def signature(self, local_frame: int) -> bytes:
        """Digest of everything the pixels of this frame depend on (before FX/captions)."""
        t = local_frame / self.fps
        h = hashlib.blake2b(digest_size=16)
        h.update(self._static_key)
        h.update(f"{local_frame}".encode())
        h.update(repr(self.camera.state(t).key()).encode())
        qfps = self.style.character_fps
        for lr in self.layers:
            h.update(lr.baked.signature_bytes(lr.baked.frame_index(t, qfps)))
        for o in self.objects:
            if not o.is_static:
                h.update(o.key(t))
        return h.digest()

    # ------------------------------------------------------------------------------- captions
    def caption_renders(self, local_frame: int) -> list[CaptionRender]:
        t = local_frame / self.fps
        out: list[CaptionRender] = []
        for ci, cap in enumerate(self.scene.captions):
            if not (cap.t0 <= t < cap.t1):
                continue
            style = cap.style if cap.style in self.cat.caption_styles else "subtitle"
            words = self.word_timings.get(ci)
            windows = [(w0 - cap.t0, w1 - cap.t0) for (w0, w1, _w) in words] if words else None
            out.append(
                CaptionRender(
                    cap.text,
                    style,
                    t - cap.t0,
                    cap.t1 - t,
                    cap.t1 - cap.t0,
                    cap.anchor,
                    windows,
                    stable_int(self.spec.meta.seed, self.scene.id, ci),
                    cap.speaker,
                )
            )
        return out

    def caption_key(self, local_frame: int) -> bytes:
        t = local_frame / self.fps
        h = hashlib.blake2b(digest_size=12)
        for ci, cap in enumerate(self.scene.captions):
            if cap.t0 <= t < cap.t1:
                h.update(
                    json.dumps(
                        [ci, cap.model_dump(), self.word_timings.get(ci), local_frame],
                        sort_keys=True,
                        default=str,
                    ).encode()
                )
        return h.digest()
