"""The editing loop: lint, script -> spec, live preview frames, catalog thumbnails."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ValidationError

from reel.core.catalog import CATALOG
from reel.core.lint import LintOptions, lint_data, schema_issues
from reel.llm.fidelity import ScriptLock
from reel.llm.generator import normalize_spec
from reel.server.deps import ctx, fail
from reel.server.preview import parse_word_timings

router = APIRouter(prefix="/api")

IMMUTABLE = {"Cache-Control": "private, max-age=86400, immutable"}


class LintBody(BaseModel):
    spec: Any
    style_override: str | None = None
    check_files: bool = False


@router.post("/lint")
def post_lint(body: LintBody, request: Request) -> dict[str, Any]:
    """The linter's report for a spec in the editor (never raises: problems are the answer)."""
    opts = LintOptions(style_override=body.style_override, check_files=body.check_files)
    return lint_data(body.spec, options=opts).to_dict()


class GenerateBody(BaseModel):
    script: str
    style: str = "paper_cutout"
    target_duration: float = 50.0
    seed: int = 0
    planner: str = (
        "offline"  # offline | ollama:<model> | openai[:model] | anthropic[:model] | claude | gpt
    )
    enrich: bool = True
    repairs: int = 3
    #: a model's captions are the script's own words, in order (what it rewords, drops or invents is put right)
    verbatim: bool = True


@router.post("/generate")
def post_generate(body: GenerateBody, request: Request) -> dict[str, str]:
    c = ctx(request)
    if not body.script.strip():
        raise fail(400, "the script is empty", hint="paste or write a script first")
    if len(body.script) > 60_000:
        raise fail(413, "the script is too long", hint="a 45-60 s reel needs about 130-160 words")
    job = c.jobs.submit(
        "generate",
        "Script to reel",
        {**body.model_dump(), "plugins": list(c.plugins)},
        mode="thread",
    )
    return {"job_id": job.id}


class ScriptBody(BaseModel):
    script: str
    spec: dict[str, Any]


@router.post("/script/check")
def post_script_check(body: ScriptBody) -> dict[str, Any]:
    """How closely the captions of a spec follow its script: the share of the script's words they show, the lines that no
    caption shows, and the captions that are speaker labels or words the script does not have.  Nothing is changed."""
    lock = ScriptLock(body.script)
    if not lock.units:
        return {"empty": True}
    try:
        return {
            "empty": False,
            **lock.coverage(body.spec),
            "reading_sec": round(lock.reading_sec, 1),
        }
    except Exception as exc:  # a spec in the middle of an edit: say so instead of failing
        raise fail(
            422, "the spec cannot be compared with the script yet", hint=str(exc)[:200]
        ) from exc


@router.post("/script/lock")
def post_script_lock(body: ScriptBody) -> dict[str, Any]:
    """The spec with its captions put back to the script's own words, in order (what is missing is added as scenes, what is
    not in the script goes), refitted to the 45-60 s budget and linted.  The caller decides whether to keep it."""
    lock = ScriptLock(body.script)
    if not lock.units:
        raise fail(400, "the script is empty", hint="there is nothing to follow")
    if not lock.fits():
        raise fail(
            409,
            "this script is too long to show word for word",
            hint=f"it takes about {round(lock.reading_sec)} s to read and a reel holds about 55 s: shorten it, or let a planner condense it",
        )
    try:
        locked, report = lock.apply(body.spec)
        meta: dict[str, Any] = locked["meta"] if isinstance(locked.get("meta"), dict) else {}
        again: list[str] = []
        fixed = normalize_spec(
            locked,
            style=str(meta.get("style") or "paper_cutout"),
            seed=int(meta.get("seed") or 0),
            target_duration=float(meta.get("target_duration_sec") or 50.0),
            audio=locked.get("audio") if isinstance(locked.get("audio"), dict) else None,
            notes=again,
        )
    except Exception as exc:
        raise fail(
            422, "the spec cannot be put back to the script yet", hint=str(exc)[:200]
        ) from exc
    return {
        "spec": fixed,
        "changed": report.changed(),
        "notes": [*report.notes(), *again],
        "lint": lint_data(fixed).to_dict(),
        "coverage": lock.coverage(fixed),
    }


class PreviewBody(BaseModel):
    spec: Any
    scale: float = 0.5
    style: str | None = None
    #: from the audio job: when each spoken word is said, so the mouths follow the voice (optional)
    word_timings: Any = None


@router.post("/preview")
def post_preview(body: PreviewBody, request: Request) -> dict[str, Any]:
    c = ctx(request)
    if not isinstance(body.spec, dict):
        raise fail(422, "the spec must be a JSON object")
    try:
        timings = parse_word_timings(body.word_timings)
        return c.previews.create(body.spec, body.scale, body.style, timings).info()
    except ValidationError as exc:
        raise fail(
            422,
            "the spec is not valid yet, so there is nothing to draw",
            issues=[i.to_dict() for i in schema_issues(exc)],
        ) from exc
    except (
        Exception
    ) as exc:  # planning problems (an impossible spec): the editor keeps its last good frame
        raise fail(422, "the spec cannot be drawn yet", hint=str(exc)[:300]) from exc


@router.get("/preview/{preview_id}/frame/{n}")
def get_frame(preview_id: str, n: int, request: Request) -> Response:
    p = ctx(request).previews.get(preview_id)
    if p is None:
        raise fail(
            404, "this preview has expired", hint="request a new preview for the current spec"
        )
    data = p.frame_webp(n)
    return Response(
        data,
        media_type="image/webp",
        headers={
            **IMMUTABLE,
            "X-Total-Frames": str(p.total_frames),
            "X-Frame": str(max(0, min(p.total_frames - 1, n))),
        },
    )


_KINDS = {"style": "styles", "background": "backgrounds", "archetype": "archetypes"}
_TIMES = ("dawn", "day", "dusk", "night")


@router.get("/library/thumb/{kind}/{name}")
def library_thumb(
    kind: str, name: str, request: Request, time_of_day: str = "day", style: str = "flat_vector"
) -> Response:
    if kind not in _KINDS:
        raise fail(
            400, f"unknown thumbnail kind {kind!r}", hint="use style, background or archetype"
        )
    registry = getattr(CATALOG, _KINDS[kind])
    if name not in registry:
        raise fail(404, f"no {kind} named {name!r}")
    if time_of_day not in _TIMES:
        raise fail(400, f"time_of_day must be one of {', '.join(_TIMES)}")
    if style not in CATALOG.styles:
        style = "flat_vector"
    data = ctx(request).thumbs.library(kind, name, time_of_day, style)
    return Response(
        data, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"}
    )
