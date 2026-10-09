"""Projects: spec files in the workspace."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel

from reel.core.lint import LintOptions, lint_data
from reel.server.deps import ctx, fail
from reel.server.workspace import ProjectDoc, WorkspaceError

router = APIRouter(prefix="/api")

_LINT_COUNTS: dict[
    str, dict[str, int]
] = {}  # etag -> {"errors", "warnings"} (a project list must stay fast)


def _lint_counts(doc: dict[str, Any], spec: dict[str, Any] | None = None) -> dict[str, int]:
    etag = str(doc.get("etag"))
    hit = _LINT_COUNTS.get(etag)
    if hit is None and spec is not None:
        c = lint_data(spec, options=LintOptions(check_files=False)).to_dict()["counts"]
        hit = {"errors": c["errors"], "warnings": c["warnings"]}
        if len(_LINT_COUNTS) > 500:
            _LINT_COUNTS.clear()
        _LINT_COUNTS[etag] = hit
    return hit or {"errors": 0, "warnings": 0}


def blank_spec(title: str, style: str) -> dict[str, Any]:
    """A new, lint-clean reel: two 25 s scenes (a scene is at most 30 s) with one character; the editor grows it from here."""

    def scene(sid: str, captions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": sid,
            "duration_sec": 25,
            "background": {"template": "abstract", "params": {}},
            "layers": [
                {
                    "character": "hero",
                    "position": "center",
                    "actions": [{"name": "idle", "t0": 0, "t1": 25}],
                }
            ],
            "captions": captions,
        }

    return {
        "version": "1.0",
        "meta": {
            "title": title,
            "style": style,
            "fps": 30,
            "resolution": [1080, 1920],
            "seed": 1,
            "target_duration_sec": 50,
        },
        "characters": [{"id": "hero", "archetype": "everyman", "name": "Hero"}],
        "scenes": [
            scene("scene_1", [{"text": title, "t0": 0.5, "t1": 4, "style": "title"}]),
            scene("scene_2", []),
        ],
        "audio": {"music": "procedural", "voiceover": "none"},
    }


class CreateProject(BaseModel):
    spec: dict[str, Any] | None = None
    title: str | None = None
    script: str = ""
    style: str = "paper_cutout"
    from_example: str | None = None


class SaveProject(BaseModel):
    spec: dict[str, Any]
    script: str | None = None


def _public(doc: ProjectDoc) -> dict[str, Any]:
    return {**doc.to_json(), "thumb_url": f"/api/projects/{doc.id}/thumb?v={doc.etag}"}


@router.get("/projects")
def list_projects(request: Request) -> list[dict[str, Any]]:
    ws = ctx(request).workspace
    out = []
    for item in ws.list_projects():
        if "broken" in item:
            out.append(item)
            continue
        try:
            doc = ws.read_project(item["id"])
            counts = _lint_counts(item, doc.spec)
        except WorkspaceError:
            counts = {"errors": 0, "warnings": 0}
        out.append(
            {
                **item,
                "lint": counts,
                "thumb_url": f"/api/projects/{item['id']}/thumb?v={item['etag']}",
            }
        )
    return out


@router.post("/projects")
def create_project(body: CreateProject, request: Request) -> dict[str, Any]:
    c = ctx(request)
    spec = body.spec
    script = body.script
    if body.from_example:
        examples = c.config.examples_dir
        name = body.from_example
        path = None if examples is None else examples / f"{name}.json"
        if examples is None or path is None or "/" in name or ".." in name or not path.is_file():
            raise fail(404, f"no example {name!r}")
        spec = json.loads(path.read_text(encoding="utf-8"))
        sidecar = {"story_50s": "script_story", "explainer_45s": "script_explainer"}.get(name)
        if sidecar and (examples / f"{sidecar}.txt").is_file() and not script:
            script = (examples / f"{sidecar}.txt").read_text(encoding="utf-8")
    if spec is None:
        spec = blank_spec(body.title or "Untitled reel", body.style)
    elif body.title:
        spec.setdefault("meta", {})["title"] = body.title
    doc = c.workspace.create_project(spec, script, slug_from=body.title)
    return _public(doc)


@router.get("/projects/{pid}")
def get_project(pid: str, request: Request, response: Response) -> dict[str, Any]:
    doc = ctx(request).workspace.read_project(pid)
    response.headers["ETag"] = f'"{doc.etag}"'
    return _public(doc)


@router.put("/projects/{pid}")
def save_project(
    pid: str,
    body: SaveProject,
    request: Request,
    response: Response,
    if_match: str | None = Header(default=None),
) -> dict[str, Any]:
    etag = if_match.strip().strip('"') if if_match else None
    doc = ctx(request).workspace.write_project(pid, body.spec, body.script, etag)
    response.headers["ETag"] = f'"{doc.etag}"'
    return _public(doc)


@router.delete("/projects/{pid}")
def delete_project(pid: str, request: Request) -> dict[str, bool]:
    ctx(request).workspace.delete_project(pid)
    return {"ok": True}


@router.get("/projects/{pid}/thumb")
def project_thumb(pid: str, request: Request) -> Response:
    c = ctx(request)
    doc = c.workspace.read_project(pid)
    try:
        data = c.thumbs.project(doc.spec)
    except Exception as exc:  # an invalid spec has no picture: the card shows its placeholder
        raise fail(422, "this project cannot be drawn yet", hint=str(exc)[:200]) from exc
    return Response(
        data, media_type="image/webp", headers={"Cache-Control": "private, max-age=3600"}
    )
