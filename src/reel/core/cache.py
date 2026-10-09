"""On-disk caches: rendered frames and encoded video segments.

* **frame cache**  - the world+FX frame (before grain and captions), lossless (zlib level 1 of the
  BGR bytes), keyed by a digest of everything that frame's pixels depend on.  A small spec edit
  therefore re-renders only the frames it actually changes.
* **segment cache** - encoded H.264 elementary-stream chunks of ~2 s, keyed by the digests of
  their frames + encoder settings.  Unchanged chunks are concatenated without re-encoding.

Keys include a fingerprint of reel's own source (and of loaded plugin files), so changing any
code invalidates stale pixels automatically.  All writes are atomic, so parallel workers and
interrupted runs are safe.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import time
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

_MAGIC = b"RFC1"


#: sub-packages that cannot change a pixel: editing the web server, the command line or the LLM clients must not throw away
#: every cached frame and video chunk
_NOT_RENDERING = frozenset({"server", "cli", "llm"})


@lru_cache(maxsize=1)
def _package_files() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parents[1]
    return tuple(
        sorted(
            p
            for p in root.rglob("*")
            if p.suffix
            in (".py", ".json", ".md", ".svg")  # the built-in library's art changes a pixel too
            and "__pycache__" not in p.parts
            and p.relative_to(root).parts[0] not in _NOT_RENDERING
        )
    )


def engine_fingerprint(extra_files: tuple[str, ...] = ()) -> str:
    """Digest of reel's source plus any plugin files: changes whenever rendering code changes."""
    h = hashlib.sha256()
    for p in _package_files():
        h.update(p.name.encode())
        h.update(p.read_bytes())
    for f in sorted(extra_files):
        try:
            h.update(Path(f).read_bytes())
        except OSError:
            h.update(f.encode())
    return h.hexdigest()[:20]


def default_cache_dir() -> Path:
    env = os.environ.get("REEL_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "reel"


#: sub-folders of the cache root: finished world frames, encoded video chunks, synthesised speech, half-written chunks
CACHE_KINDS = ("frames", "segments", "tts", "tmp")

#: a compressed world frame above this size (per 1080x1920 frame) is not worth storing, see DiskCache.put_frame
FRAME_BUDGET_BYTES = 1_800_000
FRAME_GIVE_UP = 3
FRAME_PROBE_EVERY = (
    30  # once given up, test one frame in this many to notice that frames became cheap again
)
FRAME_BUDGET_FLOOR = 64_000
FULL_HD_PIXELS = 1080 * 1920


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:  # removed between listing and measuring
        return 0


def _atime(p: Path) -> float:
    try:
        return p.stat().st_atime
    except OSError:
        return 0.0


def _files(directory: Path) -> list[Path]:
    """Every file below ``directory``; folders that vanish or cannot be read while listing are skipped.

    ``Path.rglob`` + ``is_file`` raise when a parallel worker or a second `reel` process removes something
    mid-walk; ``os.walk`` ignores such errors, and a cache listing must never be the thing that fails.
    """
    return [Path(root, name) for root, _dirs, names in os.walk(directory) for name in names]


class DiskCache:
    def __init__(
        self, root: Path | None = None, *, enabled: bool = True, max_bytes: int = 8 << 30
    ) -> None:
        self.root = (root or default_cache_dir()).expanduser()
        self.enabled = enabled
        self.max_bytes = max_bytes
        self.hits = 0
        self.misses = 0
        self._over_budget = 0  # consecutive frames too big to be worth storing
        self._skipped = 0  # frames not even tried since giving up

    # -- paths -------------------------------------------------------------------------------
    def _path(self, kind: str, key: str, ext: str) -> Path:
        return self.root / kind / key[:2] / f"{key}{ext}"

    @staticmethod
    def hex(key: bytes | str) -> str:
        return key if isinstance(key, str) else key.hex()

    # -- frames ---------------------------------------------------------------------------------
    def get_frame(self, key: bytes | str) -> np.ndarray | None:
        if not self.enabled:
            return None
        p = self._path("frames", self.hex(key), ".rfc")
        try:
            data = p.read_bytes()
        except OSError:
            self.misses += 1
            return None
        if data[:4] != _MAGIC:
            self.misses += 1
            return None
        h = int.from_bytes(data[4:6], "big")
        w = int.from_bytes(data[6:8], "big")
        try:
            bgr = np.frombuffer(zlib.decompress(data[8:]), dtype=np.uint8).reshape(h, w, 3)
        except (
            zlib.error,
            ValueError,
        ):  # truncated or damaged (a killed writer, a full disk): drop it, re-render
            self.misses += 1
            with contextlib.suppress(OSError):
                p.unlink()
            return None
        out = np.empty((h, w, 4), dtype=np.uint8)
        out[..., :3] = bgr
        out[..., 3] = 255
        with contextlib.suppress(OSError):
            os.utime(p, None)  # LRU touch
        self.hits += 1
        return out

    def put_frame(self, key: bytes | str, frame: np.ndarray) -> None:
        """Store a finished world frame, unless storing it costs more than it can save.

        Frames full of texture (paper fibre) compress to ~3 MB and 60 ms each, more than a cold render of a typical
        reel would ever win back (and one reel would fill the cache), so after `FRAME_GIVE_UP` consecutive
        over-budget frames this cache stops compressing them; cheap-to-store frames keep being cached.
        """
        if not self.enabled:
            return
        if (
            self._over_budget >= FRAME_GIVE_UP
        ):  # given up: only look again every FRAME_PROBE_EVERY frames
            self._skipped += 1
            if self._skipped % FRAME_PROBE_EVERY:
                return
        h, w = frame.shape[:2]
        packed = zlib.compress(np.ascontiguousarray(frame[..., :3]).tobytes(), 1)
        if len(packed) > max(FRAME_BUDGET_FLOOR, FRAME_BUDGET_BYTES * h * w / FULL_HD_PIXELS):
            self._over_budget += 1
            return
        self._over_budget = 0
        payload = _MAGIC + h.to_bytes(2, "big") + w.to_bytes(2, "big") + packed
        self._atomic_write(self._path("frames", self.hex(key), ".rfc"), payload)

    # -- segments ------------------------------------------------------------------------------------
    def seg_path(self, key: bytes | str) -> Path:
        return self._path("segments", self.hex(key), ".h264")

    def has_seg(self, key: bytes | str) -> bool:
        p = self.seg_path(key)
        if not self.enabled or _size(p) == 0:  # missing, or a zero-byte leftover
            return False
        with contextlib.suppress(OSError):
            os.utime(p, None)
        return True

    def put_seg(self, key: bytes | str, src: Path) -> Path:
        dst = self.seg_path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        return dst

    # -- housekeeping ------------------------------------------------------------------------------------
    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, path)

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {"root": str(self.root)}
        total = 0
        for kind in CACHE_KINDS:
            files = _files(self.root / kind)
            size = sum(_size(p) for p in files)
            out[kind] = {"files": len(files), "bytes": size}
            total += size
        out["total_bytes"] = total
        return out

    def clear(self, kind: str | None = None) -> int:
        if (
            kind is not None and kind not in CACHE_KINDS
        ):  # never build a path from an arbitrary string
            raise ValueError(f"unknown cache kind {kind!r}; choose one of {', '.join(CACHE_KINDS)}")
        n = 0
        for k in [kind] if kind else CACHE_KINDS:
            for p in _files(self.root / k):
                try:
                    p.unlink()
                    n += 1
                except OSError:  # already gone: another process cleared it first
                    pass
        return n

    def prune(self) -> int:
        """Delete least-recently-used frames and chunks until under ``max_bytes`` (speech clips are kept). Returns files removed."""
        files = [p for kind in CACHE_KINDS for p in _files(self.root / kind)]
        total = sum(_size(p) for p in files)
        removed = 0
        if total <= self.max_bytes:
            return 0
        speech = (
            self.root / "tts"
        )  # tiny, and an online voice (ElevenLabs) charged for every clip: never pruned
        for p in sorted((f for f in files if speech not in f.parents), key=_atime):
            if total <= self.max_bytes * 0.85:
                break
            total -= _size(p)
            try:
                p.unlink()
                removed += 1
            except OSError:  # already gone: someone else pruned it
                pass
        return removed

    def ensure_writable(self) -> bool:
        """True if the cache folder can be written to; otherwise the cache is switched off (a read-only home must not stop a render)."""
        if not self.enabled:
            return False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / f".write-test.{os.getpid()}"
            probe.write_bytes(b"x")
            probe.unlink()
            return True
        except OSError:
            self.enabled = False
            return False

    def purge_tmp(self, older_than_sec: float = 6 * 3600) -> int:
        """Remove half-written chunks that a killed or failed run left behind."""
        n = 0
        cutoff = time.time() - older_than_sec
        for p in _files(self.root / "tmp"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
            except (
                OSError
            ):  # removed (or still being written by a live run) between listing and looking
                pass
        return n
