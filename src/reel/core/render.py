"""The renderer: timeline -> frames -> cached segments -> MP4.

One frame is a pure function of (spec, seed, frame number):

    stage.render(world) -> [transition blend] -> style.post_process (cacheable) -> grain/letterbox -> captions

`Renderer.frame(f)` does that in-process (and is what the golden tests call).  `render_video`
splits the timeline into segments of ~2 s that never straddle a scene/transition boundary,
checks the segment cache, renders only the missing segments in parallel worker processes (each
encodes its own H.264 chunk) and then concatenates + remuxes - so editing one caption
re-renders and re-encodes a couple of seconds, not the whole reel.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import multiprocessing
import os
import signal
import tempfile
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import skia

from reel.core import fonts
from reel.core.cache import DiskCache, engine_fingerprint
from reel.core.catalog import CATALOG, Catalog
from reel.core.ffmpeg import H264SegmentEncoder, concat_h264, mux_mp4
from reel.core.fx import finish
from reel.core.planner import PlanReport
from reel.core.spec import ReelSpec, SceneSpec
from reel.core.stage import RenderConfig as StageConfig
from reel.core.stage import SceneStage
from reel.core.timeline import Active, Timeline, compute_timeline


@dataclass
class RenderOptions:
    style: str | None = None  # override meta.style
    scale: float = 1.0  # output size relative to the spec's resolution (preview = 1/3)
    lenient: bool = False  # unknown action/background/... -> fall back with a warning
    workers: int | None = None
    cache: bool = True
    cache_dir: str | None = None
    cache_max_bytes: int = 8 << 30
    crf: int = 20
    preset: str = "medium"
    maxrate: str | None = "10M"  # x264 VBV cap (None = uncapped); previews pass None
    frame_range: tuple[int, int] | None = None  # global frames [a, b)
    plugins: tuple[str, ...] = ()
    seed: int | None = None
    segment_frames: int = 60
    audio: bool = True
    word_timings: dict[int, dict[int, list[tuple[float, float, str]]]] = field(
        default_factory=dict
    )  # scene -> caption -> words

    def for_worker(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Segment:
    index: int
    frames: tuple[int, ...]
    key: str


@dataclass
class RenderResult:
    path: Path | None
    frames: int
    seconds: float
    segments_total: int = 0
    segments_encoded: int = 0
    segments_reused: int = 0
    warnings: list[str] = field(default_factory=list)
    size: tuple[int, int] = (0, 0)
    duration_sec: float = 0.0


def _even(v: float) -> int:
    n = round(v)
    return n + (n % 2)


class Renderer:
    def __init__(
        self, spec: ReelSpec, opts: RenderOptions | None = None, catalog: Catalog | None = None
    ) -> None:
        self.opts = opts or RenderOptions()
        self.cat = catalog or CATALOG
        if self.opts.seed is not None:
            spec = spec.model_copy(
                update={"meta": spec.meta.model_copy(update={"seed": self.opts.seed})}
            )
        self.spec = spec
        self.report = PlanReport()
        style_name = self.opts.style or spec.meta.style
        if style_name not in self.cat.styles:
            if not self.opts.lenient:
                self.cat.styles.get(style_name)  # raises UnknownEntryError with suggestions
            self.report.warn(f"unknown style {style_name!r}; using 'flat_vector'")
            style_name = "flat_vector"
        self.style_name = style_name
        style_cls = self.cat.styles.get(style_name)
        self.style = style_cls()
        w0, h0 = spec.meta.resolution
        size = (_even(w0 * self.opts.scale), _even(h0 * self.opts.scale))
        fx = style_cls.fx.with_spec(spec.meta.fx)
        sa = spec.meta.safe_area
        safe = (sa.top, sa.bottom, sa.left, sa.right) if sa else (0.10, 0.20, 0.07, 0.07)
        self.cfg = StageConfig(
            size=size,
            fps=spec.meta.fps,
            seed=spec.meta.seed,
            style=style_name,
            lenient=self.opts.lenient,
            fx=fx,
            safe=safe,
        )
        self.timeline: Timeline = compute_timeline(spec)
        self.cache = DiskCache(
            Path(self.opts.cache_dir) if self.opts.cache_dir else None,
            enabled=self.opts.cache,
            max_bytes=self.opts.cache_max_bytes,
        )
        if self.cache.enabled and not self.cache.ensure_writable():
            self.report.warn(
                f"cache folder {self.cache.root} is not writable; rendering without the cache"
            )
        self._stages: OrderedDict[int, SceneStage] = OrderedDict()
        plugin_files = tuple(getattr(self.cat, "plugin_files", ()))
        self.fingerprint = hashlib.sha256(
            f"{engine_fingerprint(plugin_files)}|{fonts.inventory_fingerprint()}".encode()
        ).hexdigest()[
            :20
        ]  # source code + plugins + installed fonts: any of them changes what a frame looks like
        self._cfg_key = hashlib.sha256(
            json.dumps(
                [
                    self.fingerprint,
                    size,
                    spec.meta.fps,
                    style_name,
                    style_cls.version,
                    fx.pre_key(),
                    fx.post_key(),
                    list(safe),
                ],
                default=str,
            ).encode()
        ).digest()[:16]

    # ------------------------------------------------------------------------------- stages
    @property
    def total_frames(self) -> int:
        return self.timeline.total_frames

    def stage(self, index: int) -> SceneStage:
        st = self._stages.get(index)
        if st is None:
            st = SceneStage(
                self.spec,
                index,
                self.cfg,
                catalog=self.cat,
                word_timings=self.opts.word_timings.get(index),
                report=self.report,
            )
            self._stages[index] = st
            while len(self._stages) > 3:
                self._stages.popitem(last=False)
        else:
            self._stages.move_to_end(index)
        return st

    def scene(self, index: int) -> SceneSpec:
        return self.spec.scenes[index]

    # ------------------------------------------------------------------------------- keys
    def pre_key(self, f: int) -> bytes:
        acts = self.timeline.active(f)
        h = hashlib.blake2b(digest_size=16)
        h.update(self._cfg_key)
        if len(acts) == 1:
            h.update(b"S")
            h.update(self.stage(acts[0].slot).signature(acts[0].local_frame))
        else:
            a, b = acts[0], acts[1]
            tr = self.scene(a.slot).transition_out
            h.update(b"T")
            h.update(self.stage(a.slot).signature(a.local_frame))
            h.update(self.stage(b.slot).signature(b.local_frame))
            h.update(
                json.dumps(
                    [tr.type, tr.params, round(self.timeline.transition_progress(f) or 0.0, 5)],
                    sort_keys=True,
                ).encode()
            )
        return h.digest()

    def final_key(self, f: int) -> bytes:
        acts = self.timeline.active(f)
        h = hashlib.blake2b(digest_size=16)
        h.update(self.pre_key(f))
        for a in acts:
            h.update(self.stage(a.slot).caption_key(a.local_frame))
        last = acts[-1]
        h.update(
            f"{self.cfg.fx.post_key()}|{self.spec.meta.seed}|{self.scene(last.slot).id}|{last.local_frame}".encode()
        )
        return h.digest()

    # ------------------------------------------------------------------------------- frames
    def _compute_pre(self, f: int, acts: list[Active]) -> np.ndarray:
        if len(acts) == 1:
            st = self.stage(acts[0].slot)
            world = st.render(acts[0].local_frame)
            ctx = st._ctx(acts[0].local_frame / self.cfg.fps, acts[0].local_frame)
        else:
            a, b = acts[0], acts[1]
            sa, sb = self.stage(a.slot), self.stage(b.slot)
            tr = self.scene(a.slot).transition_out
            kind = tr.type
            if kind not in self.cat.transitions:
                if not self.cfg.lenient:
                    self.cat.transitions.get(kind)
                self.report.warn(f"unknown transition {kind!r}; using 'crossfade'")
                kind, params = "crossfade", {}
            else:
                params = tr.params
            ctx = sb._ctx(b.local_frame / self.cfg.fps, b.local_frame)
            p = self.timeline.transition_progress(f) or 0.5
            world = self.style.transition(
                kind, sa.render(a.local_frame), sb.render(b.local_frame), p, ctx, params
            )
        return np.ascontiguousarray(self.style.post_process(world, ctx, self.cfg.fx))

    def frame(self, f: int) -> np.ndarray:
        """The finished BGRA frame ``f`` (cache-aware, deterministic)."""
        acts = self.timeline.active(f)
        key = self.pre_key(f)
        pre = self.cache.get_frame(key)
        if pre is None:
            pre = self._compute_pre(f, acts)
            self.cache.put_frame(key, pre)
        last = acts[-1]
        st_last = self.stage(last.slot)
        ctx = st_last._ctx(last.local_frame / self.cfg.fps, last.local_frame)
        out = finish(
            pre,
            self.cfg.fx,
            ctx,
            seed=self.spec.meta.seed,
            key=(self.scene(last.slot).id, last.local_frame),
        )
        if out is pre or not out.flags.writeable:
            out = out.copy()
        out = np.ascontiguousarray(out)
        surf = skia.Surface(out, colorType=skia.kBGRA_8888_ColorType)
        canvas = surf.getCanvas()
        for a in acts:
            st = self.stage(a.slot)
            c2 = st._ctx(a.local_frame / self.cfg.fps, a.local_frame)
            for cap in st.caption_renders(a.local_frame):
                self.style.draw_caption(canvas, cap, c2)
        return out

    def frames(self, a: int, b: int) -> Iterator[np.ndarray]:
        for f in range(a, b):
            yield self.frame(f)

    # ------------------------------------------------------------------------------- segments
    def frame_range(self) -> tuple[int, int]:
        lo, hi = self.opts.frame_range or (0, self.total_frames)
        return max(0, lo), min(self.total_frames, hi)

    def plan_segments(self) -> list[Segment]:
        lo, hi = self.frame_range()
        runs: list[list[int]] = []
        last_unit: tuple[str, int] | None = None
        for f in range(lo, hi):
            acts = self.timeline.active(f)
            unit = ("S", acts[0].slot) if len(acts) == 1 else ("T", acts[0].slot)
            if unit != last_unit or len(runs[-1]) >= self.opts.segment_frames:
                runs.append([])
                last_unit = unit
            runs[-1].append(f)
        segs: list[Segment] = []
        enc = (
            f"{self.opts.crf}|{self.opts.preset}|{self.opts.maxrate}|{self.cfg.size}|{self.cfg.fps}"
        )
        for i, fr in enumerate(runs):
            h = hashlib.sha256()
            h.update(enc.encode())
            for f in fr:
                h.update(self.final_key(f))
            segs.append(Segment(i, tuple(fr), h.hexdigest()[:32]))
        return segs

    def _seg_file(self, key: str, workdir: Path) -> Path:
        return self.cache.seg_path(key) if self.cache.enabled else workdir / f"{key}.h264"

    def encode_segment(self, seg: Segment, workdir: Path) -> Path:
        dst = self._seg_file(seg.key, workdir)
        tmp_dir = (self.cache.root / "tmp") if self.cache.enabled else workdir
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"{seg.key}.{os.getpid()}.h264"
        try:
            with H264SegmentEncoder(
                tmp,
                self.cfg.size,
                self.cfg.fps,
                crf=self.opts.crf,
                preset=self.opts.preset,
                maxrate=self.opts.maxrate,
            ) as enc:
                for f in seg.frames:
                    enc.write(self.frame(f))
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)  # a failed or interrupted chunk must not stay behind
        return dst

    # ------------------------------------------------------------------------------- video
    def render_video(
        self,
        out: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
        audio: Path | None = None,
        segments: list[Segment] | None = None,
    ) -> RenderResult:
        t0 = time.perf_counter()
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        segs = segments if segments is not None else self.plan_segments()
        if self.cache.enabled:
            self.cache.purge_tmp()  # half-written chunks of earlier killed runs
        total = sum(len(s.frames) for s in segs)
        done = 0
        with tempfile.TemporaryDirectory(prefix="reel-") as td:
            workdir = Path(td)
            todo = [s for s in segs if not (self.cache.enabled and self.cache.has_seg(s.key))]
            reused = len(segs) - len(todo)
            for s in segs:
                if s not in todo:
                    done += len(s.frames)
            if progress:
                progress(done, total)
            workers = (
                self.opts.workers
                if self.opts.workers is not None
                else max(1, min((os.cpu_count() or 2) - 2, 10))
            )
            workers = max(1, min(workers, len(todo))) if todo else 1
            if todo and workers == 1:
                for s in todo:
                    self.encode_segment(s, workdir)
                    done += len(s.frames)
                    if progress:
                        progress(done, total)
            elif todo:
                ctxm = multiprocessing.get_context("spawn")
                spec_json = self.spec.model_dump_json(by_alias=True)
                opts = self.opts.for_worker()
                ex = ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=ctxm,
                    initializer=_worker_init,
                    initargs=(spec_json, opts, str(workdir)),
                )
                try:
                    futs = {ex.submit(_worker_segment, s.index, s.frames, s.key): s for s in todo}
                    for fut in as_completed(futs):
                        fut.result()
                        done += len(futs[fut].frames)
                        if progress:
                            progress(done, total)
                except BaseException:  # Ctrl-C, a failed segment: stop NOW, do not drain the queue
                    _abort_pool(ex)
                    raise
                ex.shutdown(wait=True)
            parts = [self._seg_file(s.key, workdir) for s in segs]
            concat = workdir / "all.h264"
            concat_h264(parts, concat)
            mux_mp4(
                concat, out, self.cfg.fps, audio=audio, title=self.spec.meta.title, frames=total
            )
        if self.cache.enabled:
            self.cache.prune()
        return RenderResult(
            out,
            total,
            time.perf_counter() - t0,
            len(segs),
            len(todo),
            reused,
            list(self.report.warnings),
            self.cfg.size,
            total / self.cfg.fps,
        )

    def action_events(self) -> list[tuple[float, str]]:
        """(global time, event name) for footsteps/landings/... that the actions themselves emit."""
        out: list[tuple[float, str]] = []
        for slot in self.timeline.slots:
            st = self.stage(slot.index)
            for lr in st.layers:
                for ev in lr.baked.events:
                    out.append((self.timeline.global_time(slot.index, ev.t), ev.name))
        out.sort()
        return out

    def dump_frames(
        self, directory: Path, a: int | None = None, b: int | None = None
    ) -> list[Path]:
        lo, hi = self.frame_range()
        a = lo if a is None else a
        b = hi if b is None else b
        directory.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for f in range(a, b):
            p = directory / f"frame_{f:05d}.png"
            skia.Image.fromarray(self.frame(f), colorType=skia.kBGRA_8888_ColorType).save(
                str(p), skia.kPNG
            )
            paths.append(p)
        return paths


# ------------------------------------------------------------------------------- worker side
_W: dict[str, Any] = {}


def _abort_pool(ex: ProcessPoolExecutor) -> None:
    """Stop a pool immediately: drop the queued work and kill the workers (and so their ffmpeg children)."""
    procs = list(getattr(ex, "_processes", {}).values())  # CPython detail; harmless if it changes
    ex.shutdown(wait=False, cancel_futures=True)
    for p in procs:
        with contextlib.suppress(Exception):
            p.terminate()
    for p in procs:
        with contextlib.suppress(Exception):
            p.join(timeout=3)


def _worker_init(spec_json: str, opts: dict[str, Any], workdir: str) -> None:
    # Ctrl-C reaches the whole process group; only the parent should react to it (it terminates the workers)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    opts = dict(opts)
    opts["frame_range"] = tuple(opts["frame_range"]) if opts.get("frame_range") else None
    opts["plugins"] = tuple(opts.get("plugins", ()))
    opts["word_timings"] = {
        int(k): {int(ck): [tuple(w) for w in cv] for ck, cv in v.items()}
        for k, v in (opts.get("word_timings") or {}).items()
    }
    if opts["plugins"]:
        CATALOG.load_plugins(list(opts["plugins"]))
    spec = ReelSpec.model_validate_json(spec_json)
    _W["r"] = Renderer(spec, RenderOptions(**opts))
    _W["workdir"] = Path(workdir)


def _worker_segment(index: int, frames: tuple[int, ...], key: str) -> str:
    r: Renderer = _W["r"]
    seg = Segment(index, tuple(frames), key)
    trace = os.environ.get(
        "REEL_TRACE"
    )  # dev aid: append "pid segment start end" lines (seconds, wall clock)
    t0 = time.time()
    out = str(r.encode_segment(seg, _W["workdir"]))
    if trace:
        with open(trace, "a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {index} {t0:.3f} {time.time():.3f}\n")
    return out


def render_spec(
    spec: ReelSpec, out: Path, opts: RenderOptions | None = None, **kw: Any
) -> RenderResult:
    """Convenience: render ``spec`` to ``out``."""
    return Renderer(spec, opts).render_video(out, **kw)
