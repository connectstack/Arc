"""The asset library API: list, add (a draft first, then keep it), change, remove, thumbnails; what a script needs from it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from reel.assets.coverage import analyze_script
from reel.assets.gaps import apply_library_gap, resolve_gap
from reel.assets.model import KINDS, MAX_ART_BYTES
from reel.core.catalog import CATALOG
from reel.core.lint import lint_data
from reel.server.deps import asset_store, ctx, fail

router = APIRouter(prefix="/api")

_TIMES = ("dawn", "day", "dusk", "night")
_MEDIA = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
#: an SVG shown as an image runs no script, but served on its own it could: lock it down anyway
_SVG_HEADERS = {
    "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
    "Cache-Control": "private, max-age=86400",
}


@router.get("/assets")
def list_assets(request: Request) -> dict[str, Any]:
    """Every character, object and place of the library (built in and yours), plus problems found in your files."""
    return asset_store(request).listing()


@router.post("/assets/draft")
async def create_draft(
    request: Request, filename: str = Query("asset", max_length=200), kind: str | None = None
) -> dict[str, Any]:
    """Start adding an asset: the request body is the file itself (SVG, PNG, JPG or WebP).  Nothing is saved yet."""
    store = asset_store(request)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_ART_BYTES + 4096:
        raise fail(
            413,
            f"this file is larger than {MAX_ART_BYTES // (1024 * 1024)} MB",
            hint="shrink the picture first",
        )
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_ART_BYTES + 4096:
            raise fail(
                413,
                f"this file is larger than {MAX_ART_BYTES // (1024 * 1024)} MB",
                hint="shrink the picture first",
            )
    if not body:
        raise fail(400, "the upload is empty")
    # reading a file can take a while (a big picture is cut out, a drawing is parsed): not on the thread that serves everyone
    return await run_in_threadpool(store.create_draft, filename, bytes(body), kind)


def _fields(
    kind: str | None,
    height: float | None,
    anchor_x: float | None,
    anchor_y: float | None,
    facing: str | None,
    cutout: bool | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "height": height, "facing": facing, "cutout": cutout}
    if anchor_x is not None and anchor_y is not None:
        out["anchor"] = [anchor_x, anchor_y]
    return out


@router.get("/assets/draft/{draft_id}/thumb")
def draft_thumb(
    draft_id: str,
    request: Request,
    style: str = "flat_vector",
    time_of_day: str = "day",
    true_scale: bool = False,
    kind: str | None = None,
    height: float | None = None,
    anchor_x: float | None = None,
    anchor_y: float | None = None,
    facing: str | None = None,
    cutout: bool | None = None,
) -> Response:
    """The upload drawn the way a scene would show it, with the settings chosen so far (not cached: they change)."""
    if style not in CATALOG.styles:
        raise fail(400, f"unknown style {style!r}")
    if time_of_day not in _TIMES:
        raise fail(400, f"time_of_day must be one of {', '.join(_TIMES)}")
    data = asset_store(request).draft_thumb(
        draft_id,
        _fields(kind, height, anchor_x, anchor_y, facing, cutout),
        style,
        time_of_day,
        true_scale,
    )
    return Response(data, media_type="image/webp", headers={"Cache-Control": "no-store"})


class CommitBody(BaseModel):
    name: str
    kind: str
    summary: str = ""
    tags: list[str] = []
    height: float | None = None
    anchor: list[float] | None = None
    facing: str = "right"
    cutout: bool | None = None
    credit: str = ""
    place: dict[str, Any] | None = None
    replace: bool = False


@router.post("/assets/draft/{draft_id}/commit")
def commit_draft(draft_id: str, body: CommitBody, request: Request) -> dict[str, Any]:
    """Keep the upload: it is written into the workspace's assets folder (art + a small sidecar) and usable at once."""
    if body.kind not in KINDS:
        raise fail(422, f"kind must be one of {', '.join(KINDS)}")
    fields = body.model_dump(exclude={"replace"}, exclude_none=True)
    return asset_store(request).commit(draft_id, fields, replace=body.replace)


@router.delete("/assets/draft/{draft_id}")
def discard_draft(draft_id: str, request: Request) -> dict[str, bool]:
    asset_store(request).discard_draft(draft_id)
    return {"ok": True}


@router.get("/assets/{name}/thumb")
def asset_thumb(
    name: str,
    request: Request,
    style: str = "flat_vector",
    time_of_day: str = "day",
    true_scale: bool = False,
    v: str | None = None,
) -> Response:
    store = asset_store(request)
    a = store.get(name)
    if style not in CATALOG.styles:
        raise fail(400, f"unknown style {style!r}")
    if time_of_day not in _TIMES:
        raise fail(400, f"time_of_day must be one of {', '.join(_TIMES)}")
    data = ctx(request).thumbs.asset(a, style, time_of_day, true_scale)
    return Response(
        data, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"}
    )


@router.get("/assets/{name}/art")
def asset_art(name: str, request: Request) -> Response:
    """The asset's own file (to look at or download)."""
    a = asset_store(request).get(name)
    data = a.path.read_bytes()
    ext = a.path.suffix.lower()
    headers = dict(_SVG_HEADERS) if ext == ".svg" else {"Cache-Control": "private, max-age=86400"}
    headers["Content-Disposition"] = (
        f'inline; filename="{a.name}{ext}"'  # the asset's name is plain ASCII, the file's may not be
    )
    return Response(data, media_type=_MEDIA.get(ext, "application/octet-stream"), headers=headers)


class UpdateBody(BaseModel):
    name: str | None = None
    kind: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    height: float | None = None
    anchor: list[float] | None = None
    facing: str | None = None
    cutout: bool | None = None
    credit: str | None = None
    place: dict[str, Any] | None = None


@router.patch("/assets/{name}")
def update_asset(name: str, body: UpdateBody, request: Request) -> dict[str, Any]:
    """Change an asset of your own library (the picture stays; its words, size, anchor, facing, kind or name change)."""
    changes = body.model_dump(exclude_none=True)
    if "kind" in changes and changes["kind"] not in KINDS:
        raise fail(422, f"kind must be one of {', '.join(KINDS)}")
    return asset_store(request).update(name, changes)


@router.delete("/assets/{name}")
def delete_asset(name: str, request: Request) -> dict[str, bool]:
    asset_store(request).remove(name)
    return {"ok": True}


# ------------------------------------------------------------------------------- what a script needs
class ScriptOnly(BaseModel):
    script: str


@router.post("/script/assets")
def script_assets(body: ScriptOnly, request: Request) -> dict[str, Any]:
    """The characters, places and objects a script mentions: which the library can draw, and which it lacks."""
    if len(body.script) > 200_000:
        raise fail(413, "the script is too long")
    return analyze_script(body.script).to_dict()


class FillBody(BaseModel):
    spec: dict[str, Any]
    #: only these gaps (``{"kind": "object", "name": "dragon"}``); default: every gap the library can fill now
    only: list[dict[str, str]] | None = None


@router.post("/spec/fill-gaps")
def fill_gaps(body: FillBody) -> dict[str, Any]:
    """Make a spec use library assets it was missing when it was planned (its ``meta.library_gaps``), as `reel assets fill`."""
    spec = body.spec
    raw_meta = spec.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    gaps = [g for g in (meta.get("library_gaps") or []) if isinstance(g, dict)]
    wanted = {(o.get("kind"), str(o.get("name", "")).lower()) for o in body.only or []}
    filled: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for g in gaps:
        if body.only is not None and (g.get("kind"), str(g.get("name", "")).lower()) not in wanted:
            pending.append(g)
            continue
        have = resolve_gap(g)
        if have is not None and apply_library_gap(spec, g, have):
            filled.append({**g, "asset": have.name})
        else:
            pending.append(g)
    return {"spec": spec, "filled": filled, "pending": pending, "lint": lint_data(spec).to_dict()}
