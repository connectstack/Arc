"""The workspace: a plain folder of files you own.

::

    <workspace>/
      projects/<id>.reel.json      the spec, exactly what `reel render` reads (so the CLI works on it too)
      projects/<id>.script.txt     the script it was made from (optional)
      renders/<id>.mp4 + .json     finished videos and what made them
      .thumbs/  .audio/            caches the UI can lose without harm

Nothing here is a database: a project is a spec file.  Writes are atomic (temp file + rename), and every project has an
*etag* (a hash of its bytes), so a save from a stale tab, or over an edit made in an external editor, is refused with a
conflict instead of silently overwriting.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_SPEC_BYTES = 8 * 1024 * 1024


class WorkspaceError(Exception):
    """A request the workspace refuses; ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


def slugify(text: str, fallback: str = "reel") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return s or fallback


def etag_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def spec_summary(spec: Any) -> dict[str, Any]:
    """Title, style, length and size of a raw spec, tolerant of malformed documents (a list card must never fail)."""
    d: dict[str, Any] = spec if isinstance(spec, dict) else {}
    raw_meta = d.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    raw_scenes = d.get("scenes")
    scenes = [s for s in raw_scenes if isinstance(s, dict)] if isinstance(raw_scenes, list) else []
    total = 0.0
    for i, sc in enumerate(scenes):
        dur = sc.get("duration_sec")
        total += float(dur) if isinstance(dur, (int, float)) else 0.0
        tr = sc.get("transition_out")
        if (
            i < len(scenes) - 1
            and isinstance(tr, dict)
            and isinstance(tr.get("duration"), (int, float))
        ):
            total -= float(tr["duration"])
    return {
        "title": str(meta.get("title") or "Untitled"),
        "style": str(meta.get("style") or ""),
        "scenes": len(scenes),
        "duration_sec": round(total, 3),
    }


@dataclass
class ProjectDoc:
    id: str
    spec: dict[str, Any]
    script: str
    etag: str
    updated_at: float

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "spec": self.spec,
            "script": self.script,
            "etag": self.etag,
            "updated_at": self.updated_at,
        }


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.projects_dir = self.root / "projects"
        self.renders_dir = self.root / "renders"
        self.thumbs_dir = self.root / ".thumbs"
        self.audio_dir = self.root / ".audio"
        for d in (self.projects_dir, self.renders_dir, self.thumbs_dir, self.audio_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- projects ---------------------------------------------------------------------------
    def _spec_path(self, pid: str) -> Path:
        if not SLUG.match(pid):
            raise WorkspaceError(f"{pid!r} is not a valid project id", 400)
        return self.projects_dir / f"{pid}.reel.json"

    def _script_path(self, pid: str) -> Path:
        return self.projects_dir / f"{pid}.script.txt"

    def project_ids(self) -> list[str]:
        return sorted(p.name[: -len(".reel.json")] for p in self.projects_dir.glob("*.reel.json"))

    def read_project(self, pid: str) -> ProjectDoc:
        path = self._spec_path(pid)
        try:
            raw = path.read_bytes()
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            raise WorkspaceError(f"no project {pid!r}", 404) from None
        try:
            spec = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError(
                f"project {pid!r} is not valid JSON ({exc}); fix the file in {path}",
                422,
                path=str(path),
            ) from exc
        script = ""
        with contextlib.suppress(OSError, UnicodeDecodeError):
            script = self._script_path(pid).read_text(encoding="utf-8")
        return ProjectDoc(pid, spec if isinstance(spec, dict) else {}, script, etag_of(raw), mtime)

    def list_projects(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for pid in self.project_ids():
            try:
                doc = self.read_project(pid)
            except WorkspaceError as exc:
                out.append({"id": pid, "title": pid, "broken": exc.message, "updated_at": 0.0})
                continue
            out.append(
                {
                    "id": pid,
                    "etag": doc.etag,
                    "updated_at": doc.updated_at,
                    **spec_summary(doc.spec),
                }
            )
        out.sort(key=lambda p: -float(p.get("updated_at") or 0))
        return out

    def _write_atomic(self, path: Path, data: bytes) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def _encode(self, spec: dict[str, Any]) -> bytes:
        raw = (json.dumps(spec, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if len(raw) > MAX_SPEC_BYTES:
            raise WorkspaceError("the spec is too large (8 MB limit)", 413)
        return raw

    def create_project(
        self, spec: dict[str, Any], script: str = "", slug_from: str | None = None
    ) -> ProjectDoc:
        if not isinstance(spec, dict):
            raise WorkspaceError("a spec must be a JSON object", 400)
        base = slugify(slug_from or spec_summary(spec)["title"])
        pid, n = base, 1
        while self._spec_path(pid).exists():
            n += 1
            pid = f"{base}-{n}"
        self._write_atomic(self._spec_path(pid), self._encode(spec))
        if script.strip():
            self._write_atomic(self._script_path(pid), script.encode("utf-8"))
        return self.read_project(pid)

    def write_project(
        self,
        pid: str,
        spec: dict[str, Any],
        script: str | None,
        if_match: str | None,
    ) -> ProjectDoc:
        path = self._spec_path(pid)
        if not path.exists():
            raise WorkspaceError(f"no project {pid!r}", 404)
        current = self.read_project_bytes_etag(pid)
        if if_match is not None and if_match != current:
            doc = None
            with contextlib.suppress(WorkspaceError):
                doc = self.read_project(pid)
            raise WorkspaceError(
                "the project changed on disk since you opened it",
                409,
                current_etag=current,
                current=doc.to_json() if doc else None,
            )
        self._write_atomic(path, self._encode(spec))
        if script is not None:
            sp = self._script_path(pid)
            if script.strip():
                self._write_atomic(sp, script.encode("utf-8"))
            else:
                sp.unlink(missing_ok=True)
        return self.read_project(pid)

    def read_project_bytes_etag(self, pid: str) -> str:
        return etag_of(self._spec_path(pid).read_bytes())

    def delete_project(self, pid: str) -> None:
        path = self._spec_path(pid)
        if not path.exists():
            raise WorkspaceError(f"no project {pid!r}", 404)
        path.unlink()
        self._script_path(pid).unlink(missing_ok=True)

    def rename_title(self, pid: str, title: str) -> None:
        doc = self.read_project(pid)
        meta = doc.spec.setdefault("meta", {})
        meta["title"] = title
        self.write_project(pid, doc.spec, None, None)

    # -- renders ----------------------------------------------------------------------------
    def new_render_path(self, project_id: str | None, preset: str) -> tuple[str, Path]:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        rid = f"{slugify(project_id or 'reel')}-{preset}-{stamp}"
        n = 1
        base = rid
        while (self.renders_dir / f"{rid}.mp4").exists():
            n += 1
            rid = f"{base}-{n}"
        return rid, self.renders_dir / f"{rid}.mp4"

    def render_path(self, rid: str) -> Path:
        if not SLUG.match(rid):
            raise WorkspaceError(f"{rid!r} is not a valid render id", 400)
        return self.renders_dir / f"{rid}.mp4"

    def list_renders(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for mp4 in sorted(self.renders_dir.glob("*.mp4"), key=lambda p: -p.stat().st_mtime):
            rid = mp4.stem
            meta: dict[str, Any] = {}
            with contextlib.suppress(OSError, json.JSONDecodeError):
                meta = json.loads(mp4.with_suffix(".json").read_text(encoding="utf-8"))
            out.append(
                {
                    **meta,
                    "id": rid,
                    "path": str(mp4),
                    "size_bytes": mp4.stat().st_size,
                    "created_at": meta.get("created_at", mp4.stat().st_mtime),
                    "video_url": f"/api/renders/{rid}/video",
                }
            )
        return out

    def write_render_meta(self, rid: str, meta: dict[str, Any]) -> None:
        self._write_atomic(
            self.renders_dir / f"{rid}.json",
            (json.dumps(meta, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )

    def delete_render(self, rid: str) -> None:
        mp4 = self.render_path(rid)
        if not mp4.exists():
            raise WorkspaceError(f"no render {rid!r}", 404)
        mp4.unlink()
        mp4.with_suffix(".json").unlink(missing_ok=True)

    # -- scratch ----------------------------------------------------------------------------
    def audio_path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{8,32}", job_id):
            raise WorkspaceError("bad audio id", 400)
        return self.audio_dir / f"{job_id}.wav"

    def prune_audio(self, keep: int = 12) -> None:
        files = sorted(self.audio_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)

    def clear_thumbs(self) -> None:
        shutil.rmtree(self.thumbs_dir, ignore_errors=True)
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
