"""Live preview frames and thumbnails.

A *preview* is a :class:`~reel.core.render.Renderer` kept alive for a spec: asking for frame ``n`` costs 20-140 ms
(and nothing at all when the engine's frame cache already has it), so the UI can scrub the whole reel live.  Previews are
identified by a hash of the spec, so an unchanged spec re-uses its renderer and the browser may cache every frame URL forever.

The renderer is not thread-safe (it builds scene stages lazily), so each preview has a lock; a few previews are kept (an
LRU), because every edit makes a new spec and the old ones are never asked for again.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from reel.core.cache import engine_fingerprint
from reel.core.render import Renderer, RenderOptions
from reel.core.spec import ReelSpec

WEBP_QUALITY = 80


def encode_webp(frame: np.ndarray, quality: int = WEBP_QUALITY) -> bytes:
    """A BGRA frame from the engine as a WebP file (a 540x960 frame is about 20-50 KB)."""
    rgb = np.ascontiguousarray(frame[..., [2, 1, 0]])
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="WEBP", quality=quality, method=2)
    return buf.getvalue()


#: scene index -> caption index -> (start, end, word): when each word of a spoken caption is said (lip-sync)
WordTimings = dict[int, dict[int, list[tuple[float, float, str]]]]


def parse_word_timings(raw: Any) -> WordTimings:
    """Word timings as the audio job reports them (JSON: string keys, lists) -> what the renderer takes.  A preview must
    never fail over a malformed entry, so whatever cannot be read is left out."""
    out: WordTimings = {}
    if not isinstance(raw, dict):
        return out
    for si, caps in raw.items():
        if not isinstance(caps, dict):
            continue
        for ci, words in caps.items():
            if not isinstance(words, list):
                continue
            try:
                found = [(float(w[0]), float(w[1]), str(w[2])) for w in words]
                out.setdefault(int(si), {})[int(ci)] = found
            except (TypeError, ValueError, IndexError):
                continue
    return out


def preview_id_for(
    spec: dict[str, Any],
    scale: float,
    style: str | None,
    timings: WordTimings | None = None,
) -> str:
    from reel.assets.library import library_fingerprint

    raw = json.dumps(
        [spec, round(scale, 4), style, sorted((timings or {}).items()), library_fingerprint()],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=list,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Preview:
    id: str
    renderer: Renderer
    fps: int
    total_frames: int
    width: int
    height: int
    lock: threading.Lock = field(default_factory=threading.Lock)

    def info(self) -> dict[str, Any]:
        return {
            "preview_id": self.id,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "width": self.width,
            "height": self.height,
        }

    def frame_webp(self, n: int) -> bytes:
        n = max(0, min(self.total_frames - 1, int(n)))
        with self.lock:
            return encode_webp(self.renderer.frame(n))


class PreviewCache:
    def __init__(self, capacity: int = 4) -> None:
        self._items: OrderedDict[str, Preview] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    def create(
        self,
        spec: dict[str, Any],
        scale: float = 0.5,
        style: str | None = None,
        word_timings: WordTimings | None = None,
    ) -> Preview:
        """A preview of ``spec`` (raises pydantic's ``ValidationError`` when it is not a valid spec at all).

        ``word_timings`` (from the audio job) make the mouths follow the generated voice instead of an even rhythm."""
        scale = min(1.0, max(0.1, float(scale)))
        pid = preview_id_for(spec, scale, style, word_timings)
        with self._lock:
            hit = self._items.get(pid)
            if hit is not None:
                self._items.move_to_end(pid)
                return hit
        model = ReelSpec.model_validate(spec)
        options = RenderOptions(style=style, scale=scale, lenient=True, workers=1, cache=True)
        if word_timings:
            options.word_timings = word_timings
        renderer = Renderer(model, options)
        w, h = renderer.cfg.size
        preview = Preview(pid, renderer, model.meta.fps, renderer.total_frames, int(w), int(h))
        with self._lock:
            self._items[pid] = preview
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)
        return preview

    def get(self, preview_id: str) -> Preview | None:
        with self._lock:
            hit = self._items.get(preview_id)
            if hit is not None:
                self._items.move_to_end(preview_id)
            return hit


# ------------------------------------------------------------------------------- thumbnails
_THUMB_SCALE = 0.25  # 270x480


def library_spec(kind: str, name: str, time_of_day: str, style: str) -> dict[str, Any]:
    """A one-scene spec that shows a catalog item: a style, a background with two characters on it, or one character."""
    two: list[dict[str, Any]] = [
        {"id": "a", "archetype": "everyman", "props": ["hat"]},
        {"id": "b", "archetype": "kid", "props": ["backpack"]},
    ]
    if kind == "archetype":
        chars: list[dict[str, Any]] = [{"id": "a", "archetype": name}]
        layers = [
            {
                "character": "a",
                "position": "center",
                "scale": 1.15,
                "actions": [{"name": "idle", "t0": 0, "t1": 4}],
            }
        ]
        bg = {
            "template": "abstract",
            "params": {"time_of_day": time_of_day, "mood": "neutral", "density": 0.35},
        }
    else:
        chars = two
        layers = [
            {"character": "a", "position": "left", "actions": [{"name": "idle", "t0": 0, "t1": 4}]},
            {
                "character": "b",
                "position": "right",
                "actions": [{"name": "wave", "t0": 0.4, "t1": 3.6}],
            },
        ]
        template = name if kind == "background" else "street"
        bg = {"template": template, "params": {"time_of_day": time_of_day, "mood": "neutral"}}
    return {
        "version": "1.0",
        "meta": {"title": "thumb", "style": name if kind == "style" else style, "seed": 11},
        "characters": chars,
        "scenes": [{"id": "s", "duration_sec": 4, "background": bg, "layers": layers}],
    }


class Thumbs:
    """Thumbnail renders cached on disk (a thumbnail is re-made when the engine's code or the installed fonts change)."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _cached(self, key: str, make: Any) -> bytes:
        path = self.dir / f"{key}.webp"
        try:
            return path.read_bytes()
        except OSError:
            pass
        with (
            self._lock
        ):  # one thumbnail at a time: each builds a renderer, and the page asks for many at once
            try:
                return path.read_bytes()
            except OSError:
                pass
            data: bytes = make()
            tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            return data

    @staticmethod
    def _render(spec: dict[str, Any], at: float, style: str | None = None) -> bytes:
        model = ReelSpec.model_validate(spec)
        r = Renderer(
            model,
            RenderOptions(style=style, scale=_THUMB_SCALE, lenient=True, workers=1, cache=True),
        )
        n = min(r.total_frames - 1, max(0, round(at * model.meta.fps)))
        return encode_webp(r.frame(n), quality=78)

    def _key(self, *parts: object) -> str:
        from reel.assets.library import library_fingerprint
        from reel.core import fonts

        raw = "|".join(
            str(p)
            for p in (
                *parts,
                engine_fingerprint(()),
                fonts.inventory_fingerprint(),
                library_fingerprint(),
            )
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def project(self, spec: dict[str, Any]) -> bytes:
        scenes = spec.get("scenes") if isinstance(spec.get("scenes"), list) else []
        first = scenes[0] if scenes and isinstance(scenes[0], dict) else {}
        dur = (
            float(first.get("duration_sec") or 3.0)
            if isinstance(first.get("duration_sec"), (int, float))
            else 3.0
        )
        at = max(0.2, 0.4 * dur)
        key = self._key("project", json.dumps(spec, sort_keys=True, ensure_ascii=False), at)
        return self._cached(key, lambda: self._render(spec, at))

    def library(self, kind: str, name: str, time_of_day: str, style: str) -> bytes:
        key = self._key("library", kind, name, time_of_day, style)
        spec = library_spec(kind, name, time_of_day, style)
        return self._cached(key, lambda: self._render(spec, 1.6))

    def asset(
        self, asset: Any, style: str, time_of_day: str = "day", true_scale: bool = False
    ) -> bytes:
        """A library asset drawn as a scene would show it (the key holds the art's own hash: new art, new picture)."""
        from reel.assets.preview import preview_spec

        key = self._key("asset", asset.name, asset.content_hash, style, time_of_day, true_scale)
        spec = preview_spec(asset, style, time_of_day=time_of_day, true_scale=true_scale)
        return self._cached(key, lambda: self._render(spec, 0.5))
