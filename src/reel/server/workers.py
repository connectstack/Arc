"""What the jobs do: render a video, prepare the soundtrack, turn a script into a spec.

Every worker is ``fn(payload, emit)`` and must end with a terminal event (``done`` / ``error``).  They run in a job process
(see :mod:`reel.server.jobs`), call the same library functions as the command line, and report through ``emit`` instead of
printing, so the browser can show progress.  Payloads are plain dicts: they cross a process boundary.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from reel.server.jobs import Emit

#: render presets (a ``custom`` preset starts from ``full`` and takes the request's own scale / crf / maxrate / lenient)
PRESETS: dict[str, dict[str, Any]] = {
    "draft": {"scale": 1 / 3, "crf": 26, "x264": "veryfast", "maxrate": None, "lenient": True},
    "standard": {"scale": 0.5, "crf": 22, "x264": "fast", "maxrate": None, "lenient": False},
    "full": {"scale": 1.0, "crf": 20, "x264": "medium", "maxrate": "10M", "lenient": False},
}

#: measured seconds per ~2 s chunk of a 1080x1920 cold render on an Apple-silicon laptop (12 cores); a rough guide only
SECONDS_PER_CHUNK = {"paper_cutout": 1.75, "flat_vector": 1.1, "stickman": 0.65}


def resolve_preset(preset: str, request: dict[str, Any]) -> dict[str, Any]:
    """The encoder settings for ``preset`` with the request's overrides applied (maxrate is normalised for ffmpeg)."""
    from reel.cli.pipeline import parse_rate

    cfg = dict(PRESETS.get(preset, PRESETS["full"]))
    for key in ("scale", "crf", "lenient"):
        if request.get(key) is not None:
            cfg[key] = request[key]
    if request.get("maxrate") is not None:
        cfg["maxrate"] = parse_rate(str(request["maxrate"]))
    cfg["scale"] = min(1.0, max(0.1, float(cfg["scale"])))
    cfg["crf"] = int(min(40, max(10, int(cfg["crf"]))))
    return cfg


def estimate_seconds(style: str, scale: float, uncached_chunks: int) -> float:
    """About how long ``uncached_chunks`` cold chunks take to encode (audio and start-up not included)."""
    per = SECONDS_PER_CHUNK.get(style, 1.2) * scale**1.7
    return round(max(0.0, uncached_chunks) * max(per, 0.15), 1)


def _load_plugins(payload: dict[str, Any]) -> None:
    plugins = payload.get("plugins") or []
    if plugins:
        from reel.core.catalog import CATALOG

        CATALOG.load_plugins(list(plugins))


def _frame_range(
    payload: dict[str, Any], fps: int
) -> tuple[tuple[int, int] | None, tuple[float, float] | None]:
    rng = payload.get("range")
    if not rng:
        return None, None
    a, b = float(rng[0]), float(rng[1])
    return (round(a * fps), round(b * fps)), (a, b)


def _progress_emitter(emit: Emit, phase: str) -> Any:
    """A ``(done, total)`` callback that sends at most ~10 events a second, with an ETA."""
    start = time.perf_counter()
    last = 0.0

    def cb(done: int, total: int) -> None:
        nonlocal last
        now = time.perf_counter()
        if done < total and now - last < 0.1:
            return
        last = now
        elapsed = now - start
        eta = round(elapsed * (total - done) / done, 1) if done > 0 and elapsed > 0.5 else None
        emit(
            {
                "type": "progress",
                "phase": phase,
                "done": int(done),
                "total": int(total),
                "eta_sec": eta,
            }
        )

    return cb


# ------------------------------------------------------------------------------- render
def render_job(payload: dict[str, Any], emit: Emit) -> None:
    from reel.cli.pipeline import wants_audio
    from reel.core.lint import LintOptions, lint_data
    from reel.core.render import Renderer, RenderOptions
    from reel.core.spec import ReelSpec

    _load_plugins(payload)
    if payload.get("cache_dir"):
        os.environ["REEL_CACHE_DIR"] = str(payload["cache_dir"])
    spec = payload["spec"]
    cfg = resolve_preset(payload.get("preset", "full"), payload)
    base_dir = Path(payload.get("base_dir") or ".")
    out = Path(payload["out_path"])
    started = time.perf_counter()

    emit({"type": "progress", "phase": "plan", "done": 0, "total": 1})
    partial = bool(payload.get("range"))
    report = lint_data(
        spec,
        options=LintOptions(
            check_duration=not partial, base_dir=base_dir, style_override=payload.get("style")
        ),
    )
    if not report.ok and not cfg["lenient"]:
        emit(
            {
                "type": "error",
                "message": f"the spec has {len(report.errors)} error(s), so it was not rendered",
                "hint": "fix them in Problems, or render anyway with the lenient option (unknown names fall back)",
                "lint": report.to_dict(),
            }
        )
        return
    if report.errors:
        emit(
            {
                "type": "warning",
                "message": f"{len(report.errors)} lint error(s): rendering leniently, with fallbacks",
            }
        )
    model = ReelSpec.model_validate(spec)
    fps = model.meta.fps
    frame_range, audio_window = _frame_range(payload, fps)
    opts = RenderOptions(
        style=payload.get("style"),
        scale=cfg["scale"],
        lenient=bool(cfg["lenient"]),
        workers=payload.get("workers"),
        cache=True,
        cache_dir=str(payload["cache_dir"]) if payload.get("cache_dir") else None,
        crf=cfg["crf"],
        preset=cfg["x264"],
        frame_range=frame_range,
        plugins=tuple(payload.get("plugins") or ()),
        seed=payload.get("seed"),
        audio=not payload.get("no_audio"),
        maxrate=cfg["maxrate"],
    )
    notes: list[str] = []
    warnings: list[str] = []
    wav: Path | None = None
    tmp = Path(tempfile.mkdtemp(prefix="reel-audio-"))
    try:
        if not payload.get("no_audio") and wants_audio(model):
            from reel.audio.pipeline import prepare_audio

            emit({"type": "progress", "phase": "audio", "done": 0, "total": 1})
            events: list[tuple[float, str]] = []
            if model.audio.auto_sfx:
                events = Renderer(model, opts).action_events()
            plan = prepare_audio(
                model,
                base_dir=base_dir,
                work_dir=tmp,
                engine=payload.get("tts"),
                action_events=events,
                frame_range_sec=audio_window,
            )
            model, wav = plan.spec, plan.wav
            opts.word_timings = plan.word_timings
            warnings += plan.warnings
            notes += plan.notes
            for w in plan.warnings:
                emit({"type": "warning", "message": w})
            for n in plan.notes:
                emit({"type": "note", "message": n})
            emit({"type": "progress", "phase": "audio", "done": 1, "total": 1})
        renderer = Renderer(model, opts)
        segments = renderer.plan_segments()
        cached = sum(
            1 for s in segments if renderer.cache.enabled and renderer.cache.has_seg(s.key)
        )
        emit({"type": "note", "message": f"{cached} of {len(segments)} chunks are already cached"})
        frames_cb = _progress_emitter(emit, "frames")

        def progress(done: int, total: int) -> None:
            frames_cb(done, total)

        res = renderer.render_video(out, progress=progress, segments=segments, audio=wav)
        emit({"type": "progress", "phase": "mux", "done": 1, "total": 1})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    warnings += list(res.warnings)
    for w in res.warnings:
        emit({"type": "warning", "message": w})
    size = out.stat().st_size if out.exists() else 0
    rid = out.stem
    result: dict[str, Any] = {
        "id": rid,
        "video_url": f"/api/renders/{rid}/video",
        "path": str(out),
        "size_bytes": size,
        "width": res.size[0],
        "height": res.size[1],
        "duration_sec": round(res.duration_sec, 3),
        "frames": res.frames,
        "seconds": round(time.perf_counter() - started, 2),
        "bitrate_bps": int(size * 8 / res.duration_sec) if res.duration_sec else 0,
        "segments": {
            "total": res.segments_total,
            "encoded": res.segments_encoded,
            "reused": res.segments_reused,
        },
        "warnings": warnings,
        "notes": notes,
        "preset": payload.get("preset", "full"),
        "project_id": payload.get("project_id"),
        "title": model.meta.title,
        "style": payload.get("style") or model.meta.style,
        "has_audio": wav is not None,
        "created_at": time.time(),
    }
    sidecar = out.with_suffix(".json")
    import json

    sidecar.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    emit({"type": "done", "result": result})


# ------------------------------------------------------------------------------- audio
def lane_peaks(clips: list[Any], total: float, bins: int = 1000) -> dict[str, list[float]]:
    """Per-track waveform envelopes (max |sample| per bin, gain applied) for ``voice``, ``music`` and ``sfx``."""
    lanes = {k: np.zeros(bins) for k in ("voice", "music", "sfx")}
    if total <= 0:
        return {k: v.tolist() for k, v in lanes.items()}
    for clip in clips:
        arr = lanes.get(str(clip.kind))
        if arr is None:
            continue
        x = np.asarray(clip.samples, dtype=np.float32)
        if x.ndim == 2:
            x = np.abs(x).max(axis=1)
        x = np.abs(x) * float(10 ** (clip.gain_db / 20.0))
        win = max(1, round(clip.sr * total / bins))
        n = -(-x.shape[0] // win)
        padded = np.zeros(n * win, dtype=np.float32)
        padded[: x.shape[0]] = x
        env = padded.reshape(n, win).max(axis=1)
        b0 = int(clip.start_sec / total * bins)
        if b0 >= bins:
            continue
        seg = env[: bins - b0]
        arr[b0 : b0 + seg.shape[0]] = np.maximum(arr[b0 : b0 + seg.shape[0]], seg)
    return {k: [round(float(v), 3) for v in lanes[k]] for k in lanes}


def duck_curve(
    clips: list[Any], total: float, *, depth_db: float | None = None, per_sec: int = 40
) -> dict[str, Any] | None:
    """The gain the mixer puts on the music while somebody speaks, ``per_sec`` samples a second (1 = untouched).

    It is the mixer's own :func:`~reel.audio.mix.duck_envelope` run on the voice track at 400 Hz, then the minimum of
    each block so a short dip is not lost; ``None`` when nothing is spoken.
    """
    from reel.audio.mix import MixPlan, duck_envelope

    depth = MixPlan.duck_depth_db if depth_db is None else depth_db
    rate = 400
    n = int(np.ceil(total * rate)) if total > 0 else 0
    if n <= 0:
        return None
    amp = np.zeros(n, dtype=np.float32)
    for clip in clips:
        if str(clip.kind) != "voice":
            continue
        x = np.asarray(clip.samples, dtype=np.float32)
        if x.ndim == 2:
            x = np.abs(x).max(axis=1)
        x = np.abs(x) * float(10 ** (clip.gain_db / 20.0))
        win = max(1, round(clip.sr / rate))
        m = -(-x.shape[0] // win)
        padded = np.zeros(m * win, dtype=np.float32)
        padded[: x.shape[0]] = x
        env = padded.reshape(m, win).max(axis=1)
        b0 = int(clip.start_sec * rate)
        if b0 >= n:
            continue
        seg = env[: n - b0]
        amp[b0 : b0 + seg.shape[0]] = np.maximum(amp[b0 : b0 + seg.shape[0]], seg)
    if float(np.max(amp)) <= 1e-5:
        return None
    gain = duck_envelope(amp, rate, depth_db=depth, attack=0.08, release=0.45)
    block = max(1, rate // per_sec)
    m = -(-gain.shape[0] // block)
    padded = np.ones(m * block, dtype=np.float32)
    padded[: gain.shape[0]] = gain
    pooled = padded.reshape(m, block).min(axis=1)
    return {
        "per_sec": per_sec,
        "depth_db": depth,
        "gain": [round(float(v), 3) for v in pooled],
    }


def audio_job(payload: dict[str, Any], emit: Emit) -> None:
    from reel.audio.mix import (
        SAMPLE_RATE,  # noqa: F401  (imported to fail early if the audio stack is broken)
    )
    from reel.audio.pipeline import prepare_audio
    from reel.core.render import Renderer, RenderOptions
    from reel.core.spec import ReelSpec
    from reel.core.timeline import compute_timeline

    _load_plugins(payload)
    if payload.get("cache_dir"):
        os.environ["REEL_CACHE_DIR"] = str(payload["cache_dir"])
    model = ReelSpec.model_validate(payload["spec"])
    base_dir = Path(payload.get("base_dir") or ".")
    emit({"type": "progress", "phase": "audio", "done": 0, "total": 1})
    events: list[tuple[float, str]] = []
    if model.audio.auto_sfx:
        events = Renderer(model, RenderOptions(scale=0.1, lenient=True, cache=True)).action_events()
    tmp = Path(tempfile.mkdtemp(prefix="reel-audio-"))
    try:
        plan = prepare_audio(
            model, base_dir=base_dir, work_dir=tmp, engine=payload.get("tts"), action_events=events
        )
        for w in plan.warnings:
            emit({"type": "warning", "message": w})
        for n in plan.notes:
            emit({"type": "note", "message": n})
        wav_url = None
        if plan.wav is not None:
            dst = Path(payload["wav_path"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(plan.wav, dst)
            wav_url = f"/api/audio/{dst.stem}.wav"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tl = compute_timeline(plan.spec)
    total = tl.total_frames / tl.fps
    changed: list[dict[str, Any]] = []
    for si, (a, b) in enumerate(zip(model.scenes, plan.spec.scenes, strict=True)):
        for ci, (ca, cb) in enumerate(zip(a.captions, b.captions, strict=True)):
            if abs(ca.t1 - cb.t1) > 1e-6 or abs(ca.t0 - cb.t0) > 1e-6:
                changed.append(
                    {
                        "scene": si,
                        "caption": ci,
                        "t0": cb.t0,
                        "t1": cb.t1,
                        "was_t0": ca.t0,
                        "was_t1": ca.t1,
                    }
                )
    rep = plan.report
    emit({"type": "progress", "phase": "audio", "done": 1, "total": 1})
    emit(
        {
            "type": "done",
            "result": {
                "wav_url": wav_url,
                "spec": plan.spec.model_dump(mode="json", by_alias=True, exclude_none=True),
                "retimed": changed,
                "word_timings": {
                    str(si): {
                        str(ci): [[a, b, w] for a, b, w in words] for ci, words in caps.items()
                    }
                    for si, caps in plan.word_timings.items()
                },
                "warnings": plan.warnings,
                "notes": plan.notes,
                "report": None
                if rep is None
                else {
                    "duration": round(rep.duration, 3),
                    "peak": round(rep.peak, 4),
                    "approx_lufs": round(rep.approx_lufs, 2),
                    "ducked_seconds": round(rep.ducked_seconds, 2),
                    "n_clips": rep.n_clips,
                },
                "peaks": lane_peaks(plan.clips, total),
                "duck": duck_curve(plan.clips, total) if model.audio.ducking else None,
                "total_sec": round(total, 3),
            },
        }
    )


# ------------------------------------------------------------------------------- script -> spec
def generate_job(payload: dict[str, Any], emit: Emit) -> None:
    from reel.llm import generate_spec
    from reel.llm.base import SpecGenerationError
    from reel.llm.client import LLMError, get_client, llm_env

    _load_plugins(payload)
    planner = str(payload.get("planner") or "offline")
    label = "the offline planner" if planner == "offline" else planner
    emit({"type": "note", "message": f"Planning with {label}"})
    try:
        client = None if planner == "offline" else get_client(planner, env=llm_env())
        result = generate_spec(
            str(payload["script"]),
            str(payload.get("style") or "paper_cutout"),
            client,
            seed=int(payload.get("seed") or 0),
            target_duration=float(payload.get("target_duration") or 50),
            max_repairs=int(payload.get("repairs", 3)),
            enrich=bool(payload.get("enrich", True)),
            verbatim=bool(payload.get("verbatim", True)),
            fallback=False,
        )
    except SpecGenerationError as exc:
        emit(
            {
                "type": "error",
                "message": str(exc).splitlines()[0]
                if str(exc)
                else "no valid spec could be produced",
                "hint": "open the draft in the JSON editor and fix what the problems list names",
                "lint": exc.lint.to_dict() if exc.lint else None,
                "spec": exc.spec,
            }
        )
        return
    except LLMError as exc:
        emit(
            {
                "type": "error",
                "message": str(exc),
                "hint": "check the planner in Health, or use the offline planner",
            }
        )
        return
    for note in result.notes:
        emit({"type": "note", "message": note})
    emit(
        {
            "type": "done",
            "result": {
                "spec": result.spec,
                "lint": result.lint.to_dict(),
                "attempts": result.attempts,
                "notes": result.notes,
                "generator": result.generator,
            },
        }
    )


WORKERS = {"render": render_job, "audio": audio_job, "generate": generate_job}
