"""The ASGI application: API routes, the built web app, and the launch-link handshake."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from reel.server.assets_api import AssetStore
from reel.server.config import ServerConfig
from reel.server.deps import Context
from reel.server.jobs import JobManager
from reel.server.preview import PreviewCache, Thumbs
from reel.server.routes import assets, audio, projects, render, studio, system
from reel.server.security import COOKIE, SecurityMiddleware, token_from_request, token_ok
from reel.server.workspace import Workspace, WorkspaceError

log = logging.getLogger("reel.server")

_LOCKED = """<!doctype html><meta charset="utf-8"><title>Reel Studio</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:15px/1.5 system-ui,sans-serif;background:#0b0d12;color:#e8ebf2;display:grid;place-items:center;min-height:100vh;margin:0}
main{max-width:30rem;padding:2rem}code{background:#181c25;padding:.1em .4em;border-radius:6px}h1{font-size:1.25rem}</style>
<main><h1>Reel Studio is running, but this window has no access token.</h1>
<p>Open the link that <code>reel serve</code> printed in your terminal. It contains a one-time access token, so only you can use this server.</p></main>"""

_NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Reel Studio</title>
<style>body{font:15px/1.5 system-ui,sans-serif;background:#0b0d12;color:#e8ebf2;display:grid;place-items:center;min-height:100vh;margin:0}
main{max-width:34rem;padding:2rem}code{background:#181c25;padding:.1em .4em;border-radius:6px}h1{font-size:1.25rem}</style>
<main><h1>The server is up, but the web app has not been built.</h1>
<p>From the repository run <code>make ui-build</code> (it needs Node 20+), then restart <code>reel serve</code>.
For development run <code>make ui-dev</code> next to this server; the API is already available under <code>/api</code>.</p></main>"""


def seed_examples(workspace: Workspace, examples_dir: Path | None) -> None:
    """A new workspace starts with the two example reels (copied once; they are yours to edit)."""
    if workspace.project_ids() or examples_dir is None:
        return
    for name, script in (("story_50s", "script_story"), ("explainer_45s", "script_explainer")):
        path = examples_dir / f"{name}.json"
        if not path.is_file():
            continue
        text = examples_dir / f"{script}.txt"
        workspace.create_project(
            json.loads(path.read_text(encoding="utf-8")),
            text.read_text(encoding="utf-8") if text.is_file() else "",
            slug_from=name.replace("_", "-"),
        )


def create_app(
    config: ServerConfig,
    *,
    plugins: tuple[str, ...] = (),
    asset_dirs: tuple[str, ...] = (),
    inline_jobs: bool = False,
) -> FastAPI:
    workspace = Workspace(config.workspace)
    seed_examples(workspace, config.examples_dir)
    jobs = JobManager(inline=inline_jobs)
    store = AssetStore(
        workspace, asset_dirs
    )  # the workspace's assets/ folder is read now and after every change

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        jobs.shutdown()

    app = FastAPI(
        title="Reel Studio",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.ctx = Context(
        config=config,
        workspace=workspace,
        jobs=jobs,
        previews=PreviewCache(),
        thumbs=Thumbs(workspace.thumbs_dir),
        plugins=plugins,
        assets=store,
    )
    app.add_middleware(SecurityMiddleware, token=config.token, allowed_hosts=config.allowed_hosts)

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        body: Any = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return JSONResponse(
            body, status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(WorkspaceError)
    async def _workspace_error(_request: Request, exc: WorkspaceError) -> JSONResponse:
        return JSONResponse({"detail": exc.message, **exc.extra}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            {
                "detail": f"internal error: {type(exc).__name__}",
                "hint": "see the terminal running `reel serve`",
            },
            status_code=500,
        )

    for module in (system, projects, studio, audio, render, assets):
        app.include_router(module.router)

    static = config.static_dir

    def authorised(request: Request) -> bool:
        return token_ok(config.token, token_from_request(request.scope))

    def entry(request: Request, token: str | None) -> Response:
        """The app's front door: a launch link (``/?token=...``) sets the cookie and redirects to the clean URL."""
        if token is not None:
            if not token_ok(config.token, token):
                return HTMLResponse(_LOCKED, status_code=401)
            resp = RedirectResponse(request.url.path, status_code=303)
            resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
            return resp
        if not authorised(request):
            return HTMLResponse(_LOCKED, status_code=401)
        if static is None:
            return HTMLResponse(_NOT_BUILT)
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request, token: str | None = None) -> Response:
        if path.startswith("api/"):
            raise HTTPException(404, {"detail": "no such API route"})
        if static is not None and path:
            candidate = (static / path).resolve()
            if candidate.is_file() and static.resolve() in candidate.parents:
                return FileResponse(
                    candidate,
                    headers={
                        "Cache-Control": "public, max-age=31536000, immutable"
                        if path.startswith("assets/")
                        else "no-cache"
                    },
                )
        return entry(request, token)

    return app
