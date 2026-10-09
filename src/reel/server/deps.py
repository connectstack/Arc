"""Shared plumbing for the route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from reel.server.assets_api import AssetStore
from reel.server.config import ServerConfig
from reel.server.jobs import JobManager
from reel.server.preview import PreviewCache, Thumbs
from reel.server.workspace import Workspace


@dataclass
class Context:
    config: ServerConfig
    workspace: Workspace
    jobs: JobManager
    previews: PreviewCache
    thumbs: Thumbs
    plugins: tuple[str, ...] = ()
    assets: AssetStore | None = None


def ctx(request: Request) -> Context:
    c: Context = request.app.state.ctx
    return c


def asset_store(request: Request) -> AssetStore:
    store = ctx(request).assets
    if store is None:  # create_app always makes one; a hand-built Context may not
        raise fail(500, "the asset library is not available in this server")
    return store


def fail(status: int, detail: str, **extra: Any) -> HTTPException:
    """An HTTP error whose body is ``{"detail": ..., ...extra}`` (the UI reads ``detail`` and ``hint``)."""
    return HTTPException(status_code=status, detail={"detail": detail, **extra})
