"""The asset library as the app manages it: the workspace's own ``assets/`` folder, files being added, thumbnails.

::

    <workspace>/assets/characters|objects|places/<name>.svg|png|jpg|webp  +  <name>.json   the user's assets
    <workspace>/.drafts/<id>/art.<ext>                                                      a file being added, not saved yet

Adding a file is two steps so the person sees what they get before it is kept: ``create_draft`` reads the upload and says what
it found (notes, how it was cut out), a draft can be drawn in the three styles with any settings, and ``commit`` writes the
art and its sidecar into the library.  Everything is a plain file the command line reads too (``reel assets list``).
"""

from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from reel.assets.art import ArtError, forget, load_art
from reel.assets.library import (
    Problem,
    asset_dirs_from_env,
    asset_from_files,
    register_asset,
    reset_assets,
)
from reel.assets.model import (
    ART_EXT,
    KIND_HEIGHT,
    KINDS,
    MAX_ART_BYTES,
    NAME_RE,
    AssetDef,
    AssetKind,
    AssetManifest,
    PlaceSpec,
    slug,
)
from reel.core.catalog import CATALOG
from reel.server.workspace import Workspace, WorkspaceError

FOLDER = {"character": "characters", "object": "objects", "place": "places"}
_KIND_BY_NAME: dict[str, AssetKind] = {k: k for k in KINDS}
DRAFT_TTL_SEC = 3600.0
MAX_DRAFTS = 12
_PNG, _JPEG, _RIFF = b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF"


def sniff_extension(data: bytes) -> str | None:
    """The file type of art bytes by what they contain (never by the file's name): ``.png`` ``.jpg`` ``.webp`` ``.svg``."""
    if data.startswith(_PNG):
        return ".png"
    if data.startswith(_JPEG):
        return ".jpg"
    if data.startswith(_RIFF) and data[8:12] == b"WEBP":
        return ".webp"
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<") and b"<svg" in data[:4096].lower():
        return ".svg"
    return None


@dataclass
class Draft:
    id: str
    dir: Path
    art: Path
    ext: str
    filename: str
    created: float


class AssetStore:
    """Owns the user's asset folders for one server: reloads the catalog when they change, holds drafts, draws thumbnails."""

    def __init__(self, workspace: Workspace, extra_dirs: tuple[str, ...] = ()) -> None:
        self.workspace = workspace
        self.root = (
            workspace.root / "assets"
        ).resolve()  # resolved: a symlinked folder is still the workspace's own
        self.root.mkdir(parents=True, exist_ok=True)
        self.drafts_dir = workspace.root / ".drafts"
        shutil.rmtree(self.drafts_dir, ignore_errors=True)
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.extra = tuple(
            str(Path(d).expanduser().resolve()) for d in extra_dirs if str(d).strip()
        )
        self._lock = threading.RLock()
        self._drafts: dict[str, Draft] = {}
        self.problems: list[Problem] = []
        self.reload()

    @property
    def lock(self) -> threading.RLock:
        """Held while the library changes: a reader that wants a consistent view of the catalog takes it too."""
        return self._lock

    # -- the folders --------------------------------------------------------------------------
    def dirs(self) -> list[str]:
        """Every user asset folder, in the order they are read (the workspace's own last, so it wins)."""
        out: list[str] = []
        for d in (*asset_dirs_from_env(), *self.extra, str(self.root)):
            r = str(Path(d).expanduser().resolve())
            if r not in out:
                out.append(r)
        return out

    def reload(self) -> None:
        """Read the folders again into the catalog (after a file was added, changed or removed, here or by hand)."""
        with self._lock:
            self.problems = reset_assets(CATALOG, self.dirs(), validate=True).problems

    # -- the library --------------------------------------------------------------------------
    def editable(self, a: AssetDef) -> bool:
        """Is this a file the app may change or delete: one in the workspace's own assets folder."""
        return a.origin == "user" and self.root in a.path.resolve().parents

    def describe(self, a: AssetDef) -> dict[str, Any]:
        d = a.to_dict()
        d["editable"] = self.editable(a)
        d["version"] = a.content_hash[
            :10
        ]  # in thumbnail URLs, so a changed picture is fetched again
        d["file"] = a.path.name
        return d

    def listing(self) -> dict[str, Any]:
        with self._lock:
            rows = [self.describe(e.obj) for e in CATALOG.assets.entries()]
            problems = list(self.problems)
        return {
            "assets": rows,
            "problems": [{"file": p.file, "message": p.message} for p in problems],
            "folder": str(self.root),
            "other_folders": [d for d in self.dirs() if d != str(self.root)],
            "max_bytes": MAX_ART_BYTES,
        }

    def get(self, name: str) -> AssetDef:
        if name not in CATALOG.assets:
            raise WorkspaceError(f"there is no asset named {name!r}", 404)
        a: AssetDef = CATALOG.assets.get(name)
        return a

    # -- drafts -------------------------------------------------------------------------------
    def _sweep(self) -> None:
        now = time.time()
        for did in [d for d, dr in self._drafts.items() if now - dr.created > DRAFT_TTL_SEC]:
            self.discard_draft(did)
        while len(self._drafts) > MAX_DRAFTS:
            self.discard_draft(min(self._drafts, key=lambda k: self._drafts[k].created))

    def _draft(self, did: str) -> Draft:
        dr = self._drafts.get(did)
        if dr is None:
            raise WorkspaceError(
                "this upload has expired", 404, hint="choose the file again to continue"
            )
        return dr

    def _unique_name(self, wanted: str) -> str:
        name, i = wanted, 2
        while name in CATALOG.assets or name in CATALOG.registry("background"):
            name = f"{wanted[:36]}_{i}"
            i += 1
        return name

    def _draft_asset(self, dr: Draft, m: AssetManifest, name: str | None = None) -> AssetDef:
        kind: AssetKind = m.kind or "object"
        return AssetDef(
            name=name or f"draft_{dr.id}",
            kind=kind,
            summary=m.summary,
            tags=tuple(m.tags),
            path=dr.art,
            fmt="svg" if dr.ext == ".svg" else "raster",
            height=float(m.height or KIND_HEIGHT[kind]),
            anchor=(float(m.anchor[0]), float(m.anchor[1])),
            facing=m.facing,
            fit=m.fit,
            cutout=m.cutout,
            credit=m.credit,
            origin="user",
            place=(m.place or PlaceSpec()) if kind == "place" else None,
        )

    def create_draft(self, filename: str, data: bytes, kind: str | None = None) -> dict[str, Any]:
        """Read an uploaded file and say what it is; nothing enters the library yet."""
        if len(data) > MAX_ART_BYTES:
            raise WorkspaceError(
                f"this file is {len(data) // (1024 * 1024)} MB: the limit is {MAX_ART_BYTES // (1024 * 1024)} MB",
                413,
            )
        ext = sniff_extension(data)
        if ext is None:
            raise WorkspaceError(
                "this is not a picture or drawing Reel can read",
                415,
                hint="use an SVG, PNG, JPG or WebP file",
            )
        did = secrets.token_hex(6)
        ddir = self.drafts_dir / did
        wanted_kind = _KIND_BY_NAME.get(kind or "", "object")
        with (
            self._lock
        ):  # only the bookkeeping is under the lock: reading the file below can take a while
            self._sweep()
            ddir.mkdir(parents=True)
            art = ddir / f"art{ext}"
            art.write_bytes(data)
            dr = Draft(did, ddir, art, ext, filename, time.time())
            suggested = AssetManifest(
                name=self._unique_name(slug(Path(filename).stem)), kind=wanted_kind
            )
        try:
            info = self._art_info(self._draft_asset(dr, suggested))
        except (ArtError, ValueError) as exc:
            shutil.rmtree(ddir, ignore_errors=True)
            raise WorkspaceError(f"cannot use this file: {exc}", 422) from exc
        except Exception as exc:  # whatever else a hostile file does to the importer: a refusal, and nothing is left behind
            shutil.rmtree(ddir, ignore_errors=True)
            raise WorkspaceError(f"cannot use this file ({type(exc).__name__})", 422) from exc
        with self._lock:
            self._drafts[did] = dr
        return {
            "id": did,
            "filename": filename,
            "format": "svg" if ext == ".svg" else "picture",
            "bytes": len(data),
            "suggested": {
                "name": suggested.name,
                "kind": wanted_kind,
                "summary": "",
                "tags": [],
                "height": KIND_HEIGHT[wanted_kind],
                "anchor": [0.5, 1.0],
                "facing": "right",
            },
            **info,
        }

    @staticmethod
    def _art_info(a: AssetDef) -> dict[str, Any]:
        art = load_art(a)
        forget(a)
        return {
            "notes": [*art.notes, *art.warnings],
            "roles": list(a.roles),
            "aspect": round(art.width / max(art.height, 1e-6), 3),
        }

    def draft_manifest(self, did: str, fields: dict[str, Any]) -> AssetManifest:
        try:
            return AssetManifest.model_validate({k: v for k, v in fields.items() if v is not None})
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"]) or "the settings"
            raise WorkspaceError(f"{loc}: {first['msg']}", 422) from exc

    def draft_thumb(
        self, did: str, fields: dict[str, Any], style: str, time_of_day: str, true_scale: bool
    ) -> bytes:
        """The draft drawn as a scene would show it, with the settings the person has chosen so far."""
        from reel.assets.preview import render_preview
        from reel.server.preview import encode_webp

        with self._lock:
            dr = self._draft(did)
            dr.created = time.time()
        m = self.draft_manifest(did, fields)
        a = self._draft_asset(dr, m)
        fork = CATALOG.fork()
        try:
            register_asset(a, fork, replace=True)
            arr = render_preview(
                a, style, scale=0.25, catalog=fork, time_of_day=time_of_day, true_scale=true_scale
            )
        except (ValueError, ArtError) as exc:
            raise WorkspaceError(f"cannot draw this: {exc}", 422) from exc
        finally:
            forget(a)
        return encode_webp(arr, quality=80)

    def discard_draft(self, did: str) -> None:
        with self._lock:
            dr = self._drafts.pop(did, None)
        if dr is not None:
            shutil.rmtree(dr.dir, ignore_errors=True)

    # -- adding, changing, removing -------------------------------------------------------------
    def _write_asset(
        self, folder: Path, name: str, art_bytes: bytes, ext: str, manifest: dict[str, Any]
    ) -> tuple[Path, Path]:
        folder.mkdir(parents=True, exist_ok=True)
        art = folder / f"{name}{ext}"
        sidecar = folder / f"{name}.json"
        for path, data in (
            (art, art_bytes),
            (sidecar, (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")),
        ):
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return art, sidecar

    def commit(self, did: str, fields: dict[str, Any], replace: bool = False) -> dict[str, Any]:
        """Keep a draft: write its art and sidecar into the workspace library and make it available at once."""
        with self._lock:
            dr = self._draft(did)
            m = self.draft_manifest(did, fields)
            name = m.name or ""
            if not NAME_RE.match(name):
                raise WorkspaceError(
                    "give it a name: lower-case letters, digits and underscores, starting with a letter",
                    422,
                )
            kind: AssetKind = m.kind or "object"
            if name in CATALOG.assets:
                have = CATALOG.assets.get(name)
                if not replace:
                    raise WorkspaceError(
                        f"there is already {'a built-in' if have.origin == 'builtin' else 'an'} asset named {name!r}",
                        409,
                        hint="choose another name, or replace it with yours",
                        conflict=have.origin,
                    )
            manifest = m.model_dump(exclude_none=True, mode="json")
            manifest["name"], manifest["kind"] = name, kind
            if not manifest.get("summary"):
                manifest["summary"] = name.replace("_", " ")
            if kind != "place":
                manifest.pop("place", None)
            # try it exactly as it will be read, against a copy of the catalog, before anything is written
            check = self.drafts_dir / f".check-{dr.id}"
            shutil.rmtree(check, ignore_errors=True)
            check.mkdir()
            try:
                art, side = self._write_asset(check, name, dr.art.read_bytes(), dr.ext, manifest)
                trial = asset_from_files(
                    art, root=check, origin="user", manifest_path=side, kind_hint=kind
                )
                register_asset(trial, CATALOG.fork(), replace=True)
                load_art(trial)
                forget(trial)
            except (ValueError, ArtError) as exc:
                raise WorkspaceError(f"cannot add this: {exc}", 422) from exc
            finally:
                shutil.rmtree(check, ignore_errors=True)
            for old in self._stale_files(name):  # the same name in another folder or format goes
                old.unlink(missing_ok=True)
            self._write_asset(self.root / FOLDER[kind], name, dr.art.read_bytes(), dr.ext, manifest)
            self.discard_draft(did)
            self.reload()
            return self.describe(self.get(name))

    def _stale_files(self, name: str) -> list[Path]:
        """What an asset of the workspace library with this name has on disk: its art and sidecar (never any other file that
        happens to share the name), in the library's folders or loose in the assets folder."""
        out: list[Path] = []
        if name in CATALOG.assets:
            have = CATALOG.assets.get(name)
            if self.editable(have):
                out += [f for f in have.files() if self.root in f.resolve().parents]
        for folder in (self.root, *(self.root / sub for sub in FOLDER.values())):
            if folder.is_dir():
                out += [
                    p
                    for p in folder.iterdir()
                    if p.is_file() and p.stem == name and p.suffix.lower() in (*ART_EXT, ".json")
                ]
        return list(dict.fromkeys(out))

    def update(self, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Change an asset of the workspace: its words, summary, size, anchor, facing, kind or name (the art stays)."""
        with self._lock:
            a = self.get(name)
            if not self.editable(a):
                raise WorkspaceError(
                    "this asset is not in your workspace library, so it cannot be changed here",
                    403,
                    hint="built-in assets are read-only: add your own with the same name to replace one",
                )
            current: dict[str, Any] = {}
            if a.manifest and a.manifest.is_file():
                try:
                    current = json.loads(a.manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    current = {}
            merged = {**current, **{k: v for k, v in changes.items() if k != "replace"}}
            merged.setdefault("kind", a.kind)
            try:
                m = AssetManifest.model_validate({k: v for k, v in merged.items() if v is not None})
            except ValidationError as exc:
                first = exc.errors()[0]
                loc = ".".join(str(p) for p in first["loc"]) or "the settings"
                raise WorkspaceError(f"{loc}: {first['msg']}", 422) from exc
            new_name = m.name or a.name
            kind: AssetKind = m.kind or a.kind
            if new_name != a.name and new_name in CATALOG.assets:
                raise WorkspaceError(f"there is already an asset named {new_name!r}", 409)
            manifest = m.model_dump(exclude_none=True, mode="json")
            manifest["name"], manifest["kind"] = new_name, kind
            if kind != "place":
                manifest.pop("place", None)
            ext = a.path.suffix.lower()
            data = a.path.read_bytes()
            try:
                check = self.drafts_dir / f".check-{secrets.token_hex(4)}"
                check.mkdir()
                try:
                    art, side = self._write_asset(check, new_name, data, ext, manifest)
                    trial = asset_from_files(
                        art, root=check, origin="user", manifest_path=side, kind_hint=kind
                    )
                    fork = CATALOG.fork()
                    if a.name in fork.assets:
                        from reel.assets.library import unregister_asset

                        unregister_asset(fork, a.name)
                    register_asset(trial, fork, replace=True)
                    forget(trial)
                finally:
                    shutil.rmtree(check, ignore_errors=True)
            except (ValueError, ArtError) as exc:
                raise WorkspaceError(f"cannot save this: {exc}", 422) from exc
            for old in a.files():
                old.unlink(missing_ok=True)
            self._write_asset(self.root / FOLDER[kind], new_name, data, ext, manifest)
            self.reload()
            return self.describe(self.get(new_name))

    def remove(self, name: str) -> None:
        with self._lock:
            a = self.get(name)
            if not self.editable(a):
                raise WorkspaceError(
                    "this asset is not in your workspace library, so it cannot be deleted here",
                    403,
                    hint="built-in assets cannot be deleted",
                )
            for f in a.files():
                f.unlink(missing_ok=True)
            self.reload()


__all__ = ["AssetStore", "Draft", "sniff_extension"]
