"""Rendering, the job stream and finished videos."""

from __future__ import annotations

import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import anyio
from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from reel.audio.pipeline import estimate_speech, prepare_audio
from reel.audio.tts import TTSUnavailableError, UnknownTTSEngineError
from reel.cli.pipeline import wants_audio
from reel.core.lint import schema_issues
from reel.core.render import Renderer, RenderOptions
from reel.core.spec import ReelSpec
from reel.server import audio_api
from reel.server.deps import ctx, fail
from reel.server.workers import estimate_seconds, resolve_preset

router = APIRouter(prefix="/api")


class RenderBody(BaseModel):
    spec: dict[str, Any] | None = None
    project_id: str | None = None
    preset: Literal["draft", "standard", "full", "custom"] = "draft"
    scale: float | None = None
    range: tuple[float, float] | None = None  # seconds [start, end]
    crf: int | None = None
    maxrate: str | None = None
    workers: int | None = None
    lenient: bool | None = None
    no_audio: bool = False
    tts: str | None = None
    seed: int | None = None
    style: str | None = None


def _spec_for(c: Any, body: RenderBody) -> tuple[dict[str, Any], str | None]:
    if body.spec is not None:
        return body.spec, body.project_id
    if body.project_id:
        return c.workspace.read_project(body.project_id).spec, body.project_id
    raise fail(400, "give a spec or a project_id to render")


def _validate(spec: dict[str, Any]) -> ReelSpec:
    try:
        return ReelSpec.model_validate(spec)
    except ValidationError as exc:
        raise fail(
            422, "the spec is not valid yet", issues=[i.to_dict() for i in schema_issues(exc)]
        ) from exc


def _as_it_will_be_drawn(
    c: Any, model: ReelSpec, body: RenderBody
) -> tuple[ReelSpec, dict[int, dict[int, list[tuple[float, float, str]]]], str]:
    """The spec as the renderer will see it, and whether that is certain: ``(spec, word timings, voice)``.

    A spoken reel is retimed to its speech before it is drawn, and a different timing is a different chunk, so counting the
    cached chunks of the spec *as written* says "nearly none" for a reel whose render is in fact fully cached.  When every
    spoken line is already generated the retiming is cheap and exact (nothing is synthesised, nothing is sent anywhere):
    ``voice`` is ``"ready"``.  When a line is still to be generated it cannot be known (``"pending"``) and the chunks are
    counted against the spec as written.  ``"none"``: nothing is spoken, so there is nothing to retime.
    """
    if body.no_audio or not wants_audio(model) or model.audio.voiceover != "tts":
        return model, {}, "none"
    try:
        engine = audio_api.get_engine(body.tts)
    except (TTSUnavailableError, UnknownTTSEngineError):
        return model, {}, "pending"
    if estimate_speech(model, engine).new_lines:
        return model, {}, "pending"
    with tempfile.TemporaryDirectory(prefix="reel-plan-") as tmp:
        plan = prepare_audio(
            model, base_dir=c.workspace.projects_dir, work_dir=Path(tmp), engine=body.tts
        )
    return plan.spec, plan.word_timings, "ready"


@router.post("/render/plan")
def post_plan(body: RenderBody, request: Request) -> dict[str, Any]:
    """What a render would do: how many 2-second chunks are already cached and about how long the rest takes."""
    c = ctx(request)
    spec, _ = _spec_for(c, body)
    model = _validate(spec)
    cfg = resolve_preset(body.preset, body.model_dump())
    fps = model.meta.fps
    rng = None if not body.range else (round(body.range[0] * fps), round(body.range[1] * fps))
    model, word_timings, voice = _as_it_will_be_drawn(c, model, body)
    options = RenderOptions(
        style=body.style, scale=cfg["scale"], lenient=True, workers=1, cache=True,
        crf=cfg["crf"], preset=cfg["x264"], maxrate=cfg["maxrate"], frame_range=rng,
    )  # fmt: skip
    options.word_timings = word_timings
    renderer = Renderer(model, options)
    segments = renderer.plan_segments()
    cached = sum(1 for s in segments if renderer.cache.enabled and renderer.cache.has_seg(s.key))
    w, h = renderer.cfg.size
    return {
        "frames": sum(len(s.frames) for s in segments),
        "segments_total": len(segments),
        "segments_cached": cached,
        # a voice that is not generated yet will retime most chunks: do not promise a cache hit that will not come
        "est_seconds": estimate_seconds(
            body.style or model.meta.style,
            cfg["scale"],
            len(segments) if voice == "pending" else len(segments) - cached,
        ),
        "width": int(w),
        "height": int(h),
        "duration_sec": round(renderer.total_frames / fps, 3),
        "preset": body.preset,
        # "pending": the voice is not generated yet, so its retiming (and with it most chunks) is not known
        "voice": voice,
    }


@router.post("/render")
def post_render(body: RenderBody, request: Request) -> dict[str, str]:
    c = ctx(request)
    spec, project_id = _spec_for(c, body)
    _validate(spec)
    rid, out = c.workspace.new_render_path(
        project_id or str(spec.get("meta", {}).get("title", "reel")), body.preset
    )
    payload = {
        **body.model_dump(),
        "spec": spec,
        "project_id": project_id,
        "out_path": str(out),
        "base_dir": str(c.workspace.projects_dir),
        "plugins": list(c.plugins),
        "assets": c.assets.dirs() if c.assets else [],
    }
    job = c.jobs.submit("render", f"Render ({body.preset})", payload)
    return {"job_id": job.id, "render_id": rid}


# ------------------------------------------------------------------------------- jobs
@router.get("/jobs")
def list_jobs(request: Request) -> list[dict[str, Any]]:
    return [{**j.snapshot(after=len(j.events)), "events": []} for j in ctx(request).jobs.active()]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, after: int = 0) -> dict[str, Any]:
    job = ctx(request).jobs.get(job_id)
    if job is None:
        raise fail(404, "no such job", hint="jobs are forgotten when the server restarts")
    return job.snapshot(after)


@router.delete("/jobs/{job_id}")
def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    job = ctx(request).jobs.cancel(job_id)
    if job is None:
        raise fail(404, "no such job")
    return {"id": job.id, "status": job.status}


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Server-sent events of a job.  ``Last-Event-ID`` (sent by the browser on reconnect) or ``?after=`` resumes the stream."""
    job = ctx(request).jobs.get(job_id)
    if job is None:
        raise fail(404, "no such job", hint="jobs are forgotten when the server restarts")
    start = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else after

    async def stream() -> AsyncIterator[str]:
        idx = start
        yield "retry: 1500\n\n"
        while True:
            events, finished = await anyio.to_thread.run_sync(job.wait, idx, 15.0)
            for ev in events:
                yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                idx = ev["seq"] + 1
            if finished and not events:
                return
            if not events:
                yield ": keep-alive\n\n"
            if await request.is_disconnected():
                return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------------------- finished videos
@router.get("/renders")
def list_renders(request: Request) -> list[dict[str, Any]]:
    return ctx(request).workspace.list_renders()


@router.get("/renders/{rid}/video")
def get_video(rid: str, request: Request) -> FileResponse:
    path = ctx(request).workspace.render_path(rid)
    if not path.is_file():
        raise fail(404, "that render is gone")
    return FileResponse(
        path, media_type="video/mp4", filename=f"{rid}.mp4", content_disposition_type="inline"
    )


@router.delete("/renders/{rid}")
def delete_render(rid: str, request: Request) -> dict[str, bool]:
    ctx(request).workspace.delete_render(rid)
    return {"ok": True}
