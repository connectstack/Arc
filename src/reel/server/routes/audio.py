"""Voices and sound: engines, voices, auditions, the cost estimate and the soundtrack job."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from reel.audio.pipeline import estimate_speech
from reel.audio.tts import TTSUnavailableError, UnknownTTSEngineError
from reel.core.lint import schema_issues
from reel.core.spec import ReelSpec
from reel.server import audio_api
from reel.server.deps import ctx, fail

router = APIRouter(prefix="/api")

MAX_SAMPLE_CHARS = 240


@router.get("/tts/engines")
def get_engines() -> dict[str, Any]:
    return audio_api.engines_status()


@router.get("/tts/voices")
def get_voices(engine: str = "auto") -> list[dict[str, Any]]:
    try:
        return audio_api.list_voices(engine)
    except (TTSUnavailableError, UnknownTTSEngineError) as exc:
        raise fail(409, str(exc), hint="see Voice & Audio for what this engine needs") from exc


class SampleBody(BaseModel):
    engine: str = "auto"
    voice: str | None = None
    text: str = "Hello! This is how I sound."
    confirm_billing: bool = False


@router.post("/tts/sample")
def post_sample(body: SampleBody) -> Response:
    """A short audition clip.  An online engine is billed per character, so it only runs when the caller says it is allowed."""
    text = " ".join(body.text.split())[:MAX_SAMPLE_CHARS]
    if not text:
        raise fail(400, "there is nothing to say")
    try:
        engine = audio_api.get_engine(body.engine)
        if engine.name == "elevenlabs" and not body.confirm_billing:
            raise fail(
                409,
                "this audition would be billed by ElevenLabs",
                hint="confirm to spend the credits",
                billable_characters=len(text),
                confirm_required=True,
            )
        clip = engine.synthesize(text, body.voice)
    except (TTSUnavailableError, UnknownTTSEngineError) as exc:
        raise fail(409, str(exc)) from exc
    return Response(audio_api.wav_bytes(clip.samples, clip.sample_rate), media_type="audio/wav")


class SpecBody(BaseModel):
    spec: Any
    engine: str | None = None


def _model(spec: Any) -> ReelSpec:
    if not isinstance(spec, dict):
        raise fail(422, "the spec must be a JSON object")
    try:
        return ReelSpec.model_validate(spec)
    except ValidationError as exc:
        raise fail(
            422, "the spec is not valid yet", issues=[i.to_dict() for i in schema_issues(exc)]
        ) from exc


@router.post("/audio/estimate")
def post_estimate(body: SpecBody) -> dict[str, Any]:
    """How many spoken lines are new and (for an online engine) how many characters would be billed: nothing is synthesised."""
    model = _model(body.spec)
    try:
        engine = audio_api.get_engine(body.engine)
    except (TTSUnavailableError, UnknownTTSEngineError) as exc:
        return {"available": False, "reason": str(exc), "engine": body.engine or "auto"}
    est = estimate_speech(model, engine)
    destination = getattr(engine, "destination", None) if est.online else None
    return {
        "available": True,
        "engine": est.engine,
        "online": est.online,
        "lines": est.lines,
        "cached_lines": est.cached_lines,
        "new_lines": est.new_lines,
        "billable_characters": est.billable_characters,
        "destination": destination,
    }


@router.post("/audio/prepare")
def post_prepare(body: SpecBody, request: Request) -> dict[str, str]:
    c = ctx(request)
    _model(body.spec)
    jid = uuid.uuid4().hex[:12]
    c.workspace.prune_audio()
    job = c.jobs.submit(
        "audio",
        "Voice and sound",
        {
            "spec": body.spec,
            "tts": body.engine,
            "base_dir": str(c.workspace.projects_dir),
            "wav_path": str(c.workspace.audio_path(jid)),
            "plugins": list(c.plugins),
        },
    )
    return {"job_id": job.id}


@router.get("/audio/{audio_id}.wav")
def get_audio(audio_id: str, request: Request) -> FileResponse:
    path = ctx(request).workspace.audio_path(audio_id)
    if not path.is_file():
        raise fail(404, "that soundtrack is gone", hint="generate the voice again")
    return FileResponse(path, media_type="audio/wav")


_SFX_CACHE: dict[tuple[str, int], bytes] = {}


@router.get("/sfx/{name}.wav")
def get_sfx(name: str, seed: int = 0) -> Response:
    from reel.audio.sfx import SAMPLE_RATE, synth
    from reel.core.catalog import CATALOG

    if name not in CATALOG.sfx:
        raise fail(404, f"no sound effect named {name!r}")
    data = _SFX_CACHE.get((name, seed))
    if data is None:
        data = audio_api.wav_bytes(synth(name, SAMPLE_RATE, seed), SAMPLE_RATE)
        _SFX_CACHE[(name, seed)] = data
    return Response(
        data, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/music/{mood}.wav")
def get_music(mood: str, seconds: float = 10.0, seed: int = 0) -> Response:
    from reel.audio.mix import SAMPLE_RATE
    from reel.audio.music import MUSIC_MOODS, generate_music

    if mood not in MUSIC_MOODS:
        raise fail(404, f"no music mood {mood!r}", hint=f"moods: {', '.join(MUSIC_MOODS)}")
    seconds = min(30.0, max(2.0, seconds))
    data = audio_api.wav_bytes(generate_music(mood, seconds, seed), SAMPLE_RATE)
    return Response(
        data, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"}
    )
