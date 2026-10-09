"""Health, the catalog, the JSON Schema, examples, doctor and the cache."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from reel.core.cache import CACHE_KINDS, DiskCache
from reel.core.schema import build_schema
from reel.server import health as health_mod
from reel.server.catalog_api import build_catalog, list_examples
from reel.server.deps import ctx, fail

router = APIRouter(prefix="/api")


@router.get("/health")
def get_health(request: Request) -> dict[str, Any]:
    c = ctx(request)
    return {**health_mod.health(), "workspace": str(c.workspace.root)}


@router.get("/catalog")
def get_catalog() -> dict[str, Any]:
    return build_catalog()


@router.get("/schema")
def get_schema() -> dict[str, Any]:
    return build_schema(enums=True)


@router.get("/examples")
def get_examples(request: Request) -> dict[str, Any]:
    return list_examples(ctx(request).config.examples_dir)


@router.get("/doctor")
def get_doctor() -> dict[str, Any]:
    return health_mod.doctor()


class DoctorTest(BaseModel):
    kind: Literal["llm", "tts"]
    target: str


@router.post("/doctor/test")
def post_doctor_test(body: DoctorTest) -> dict[str, Any]:
    """ONE tiny, explicit request to a hosted service (the UI says what it costs before calling this)."""
    return (
        health_mod.test_llm(body.target) if body.kind == "llm" else health_mod.test_tts(body.target)
    )


@router.get("/cache")
def get_cache() -> dict[str, Any]:
    return DiskCache().stats()


@router.delete("/cache/{kind}")
def delete_cache(kind: str, request: Request) -> dict[str, Any]:
    if kind not in CACHE_KINDS:
        raise fail(
            400, f"unknown cache kind {kind!r}", hint=f"choose one of: {', '.join(CACHE_KINDS)}"
        )
    removed = DiskCache().clear(kind)
    if kind == "frames":
        ctx(request).workspace.clear_thumbs()
    return {"removed": removed, "stats": DiskCache().stats()}
