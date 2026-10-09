"""Pictures (PNG, JPG, WebP) as assets.

A picture becomes one `ImageG` shape: the pixels (kept in this module, found by key) and a traced outline of what is
opaque in it, so the styles can give it a paper edge and a shadow, an ink line, or a clean cel border that follows the
shape of the thing rather than its rectangle.

Pictures without transparency are common (a clip-art JPG on white): when the border of such a picture is one plain colour,
that colour is removed (flood-filled from the border, so the same colour *inside* the drawing stays).  Characters and
objects are trimmed to what is drawn, facing is normalised by mirroring the pixels, and large pictures are scaled down.
The processed pixels and the outline are cached on disk by content, so a render's worker processes do not redo the work.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
import skia
from PIL import Image, ImageOps

from reel.assets.model import MAX_ART_BYTES, Art
from reel.core.cache import default_cache_dir
from reel.core.ir import ImageG, Shape

MAX_SIDE = 1024  # a picture is scaled down to this on its longer side: that is more than a 1080-wide frame can show
MAX_PIXELS = 40_000_000
TRACE_SIDE = 240
PROCESS_VERSION = "4"  # bump when processing changes (it is part of the disk-cache key)

_IMAGES: dict[str, skia.Image] = {}


class PictureError(ValueError):
    """The file is not a picture this importer can use (the message says why, in plain words)."""


def drop_images(shapes: Any) -> None:
    """Free the decoded pixels of the pictures among ``shapes`` (an asset that is gone must not keep its pixels in memory)."""
    for sh in shapes:
        if isinstance(getattr(sh, "geom", None), ImageG):
            _IMAGES.pop(sh.geom.key, None)


def image_for(key: str) -> skia.Image:
    """The decoded pixels of an `ImageG` (registered when its asset's art was loaded)."""
    try:
        return _IMAGES[key]
    except KeyError:
        raise KeyError(
            f"no picture is loaded under {key!r}: its asset's art must be loaded first"
        ) from None


# ------------------------------------------------------------------------------- cutout
def plain_background_mask(rgb: np.ndarray, tol: float = 34.0) -> np.ndarray | None:
    """Where a picture is its plain background colour, connected to the border; None if it has no plain background."""
    h, w, _ = rgb.shape
    ring = max(2, round(0.015 * max(h, w)))
    border = np.concatenate(
        [
            rgb[:ring].reshape(-1, 3),
            rgb[-ring:].reshape(-1, 3),
            rgb[ring:-ring, :ring].reshape(-1, 3),
            rgb[ring:-ring, -ring:].reshape(-1, 3),
        ]
    ).astype(np.float32)
    med = np.median(border, axis=0)
    near = np.abs(border - med).max(axis=1) <= tol
    if near.mean() < 0.82:
        return None
    dist = np.abs(rgb.astype(np.float32) - med).max(axis=2)
    cand = dist <= tol
    # connectivity on a coarse grid (a cell is background when most of it is), then back to pixels
    cell = max(1, round(max(h, w) / 220))
    gh, gw = math.ceil(h / cell), math.ceil(w / cell)
    padded = np.zeros((gh * cell, gw * cell), dtype=np.float32)
    padded[:h, :w] = cand
    coarse = padded.reshape(gh, cell, gw, cell).mean(axis=(1, 3)) >= 0.5
    seen = np.zeros_like(coarse)
    q: deque[tuple[int, int]] = deque()
    for y in range(gh):
        for x in (0, gw - 1):
            if coarse[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    for x in range(gw):
        for y in (0, gh - 1):
            if coarse[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < gh and 0 <= nx < gw and coarse[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    if not seen.any():
        return None
    reach = np.kron(seen, np.ones((cell, cell), dtype=bool))[:h, :w]
    out = reach & cand
    if out.mean() < 0.02:
        return None
    return out


def _soft_alpha(rgb: np.ndarray, bg: np.ndarray, med_tol: float = 34.0) -> np.ndarray:
    """Alpha 0..255 with a soft edge: 0 inside the background, rising over a few colour steps at its boundary."""
    med = np.median(rgb[bg].reshape(-1, 3), axis=0)
    dist = np.abs(rgb.astype(np.float32) - med).max(axis=2)
    soft = np.clip((dist - 0.45 * med_tol) / (0.55 * med_tol), 0.0, 1.0)
    return np.where(bg, soft * 255.0, 255.0).astype(np.uint8)


# ------------------------------------------------------------------------------- outline
def _simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker on a closed loop (split at the two points that are furthest apart)."""
    n = len(pts)
    if n < 5:
        return pts
    far = max(
        range(1, n), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2
    )

    def run(chain: list[tuple[float, float]]) -> list[tuple[float, float]]:
        keep = [False] * len(chain)
        keep[0] = keep[-1] = True
        stack = [(0, len(chain) - 1)]
        while stack:
            a, b = stack.pop()
            ax, ay = chain[a]
            bx, by = chain[b]
            dx, dy = bx - ax, by - ay
            norm = math.hypot(dx, dy) or 1.0
            best, bi = -1.0, -1
            for i in range(a + 1, b):
                d = abs((chain[i][0] - ax) * dy - (chain[i][1] - ay) * dx) / norm
                if d > best:
                    best, bi = d, i
            if best > tol and bi > 0:
                keep[bi] = True
                stack.append((a, bi))
                stack.append((bi, b))
        return [p for p, k in zip(chain, keep, strict=True) if k]

    first = run(pts[: far + 1])
    second = run([*pts[far:], pts[0]])
    return first[:-1] + second[:-1]


def trace_outline(
    alpha: np.ndarray, *, side: int = TRACE_SIDE, thresh: float = 0.5
) -> list[list[tuple[float, float]]]:
    """Closed outlines (in the picture's own pixel coordinates) of what is opaque in an alpha channel (0..255).

    The boundary between opaque and clear cells of a downscaled copy is followed as grid edges, simplified, and
    returned as polygons: the big outer shapes, plus the holes that are big enough to matter.
    """
    h, w = alpha.shape
    k = min(1.0, side / max(h, w))
    a = alpha
    if k < 1.0:
        a = np.asarray(
            Image.fromarray(alpha).resize(
                (max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.BOX
            )
        )
    mask = np.pad(a >= thresh * 255.0, 1)
    gh, gw = mask.shape
    out_edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(sx: np.ndarray, sy: np.ndarray, dx: int, dy: int, ex: int, ey: int) -> None:
        for x, y in zip(sx.tolist(), sy.tolist(), strict=True):
            out_edges.setdefault((x + dx, y + dy), []).append((x + ex, y + ey))

    ys, xs = np.nonzero(
        mask[1:-1, 1:-1] & ~mask[:-2, 1:-1]
    )  # top edges: pixel (x, y) is filled, the one above is not
    add(xs + 1, ys + 1, 0, 0, 1, 0)
    ys, xs = np.nonzero(mask[1:-1, 1:-1] & ~mask[1:-1, 2:])  # right
    add(xs + 1, ys + 1, 1, 0, 1, 1)
    ys, xs = np.nonzero(mask[1:-1, 1:-1] & ~mask[2:, 1:-1])  # bottom
    add(xs + 1, ys + 1, 1, 1, 0, 1)
    ys, xs = np.nonzero(mask[1:-1, 1:-1] & ~mask[1:-1, :-2])  # left
    add(xs + 1, ys + 1, 0, 1, 0, 0)
    loops: list[list[tuple[float, float]]] = []
    while out_edges:
        start = next(iter(out_edges))
        cur = start
        prev_dir = (0, 0)
        loop: list[tuple[int, int]] = []
        while True:
            options = out_edges.get(cur)
            if not options:
                break
            if len(options) > 1 and prev_dir != (0, 0):  # a pinch: take the sharpest right turn

                def turn(
                    nxt: tuple[int, int], pd: tuple[int, int] = prev_dir, c: tuple[int, int] = cur
                ) -> int:
                    d = (nxt[0] - c[0], nxt[1] - c[1])
                    return -(
                        pd[0] * d[1] - pd[1] * d[0]
                    )  # cross product: > 0 turns right on a y-down screen

                nxt = min(options, key=turn)
            else:
                nxt = options[0]
            options.remove(nxt)
            if not options:
                del out_edges[cur]
            loop.append(cur)
            prev_dir = (nxt[0] - cur[0], nxt[1] - cur[1])
            cur = nxt
            if cur == start:
                break
        if len(loop) >= 4:
            loops.append([(x - 1.0, y - 1.0) for x, y in loop])
    if not loops:
        return []
    total = gh * gw
    result: list[tuple[float, list[tuple[float, float]]]] = []
    for lp in loops:
        s = lp
        area = 0.5 * sum(
            s[i][0] * s[(i + 1) % len(s)][1] - s[(i + 1) % len(s)][0] * s[i][1]
            for i in range(len(s))
        )
        result.append((area, _simplify(lp, 0.7)))
    biggest = max(result, key=lambda r: abs(r[0]))[0]
    keep: list[list[tuple[float, float]]] = []
    for area, lp in result:
        outer = (area > 0) == (biggest > 0)
        if abs(area) < (0.002 if outer else 0.012) * total or len(lp) < 3:
            continue
        keep.append([(x / k, y / k) for x, y in lp])
    return keep


def loops_to_cmds(loops: list[list[tuple[float, float]]]) -> tuple[tuple[Any, ...], ...]:
    """Closed polygons as smooth path commands: quadratic curves through the midpoints of the sides."""
    cmds: list[tuple[Any, ...]] = []
    for lp in loops:
        n = len(lp)
        mids = [
            ((lp[i][0] + lp[(i + 1) % n][0]) / 2, (lp[i][1] + lp[(i + 1) % n][1]) / 2)
            for i in range(n)
        ]
        cmds.append(("M", *mids[-1]))
        for i in range(n):
            cmds.append(("Q", lp[i][0], lp[i][1], *mids[i]))
        cmds.append(("Z",))
    return tuple(cmds)


# ------------------------------------------------------------------------------- loading
def _cache_dir() -> Path:
    return default_cache_dir() / "assets"


def _process(
    data: bytes, cutout: bool | None, mirror: bool, trim: bool
) -> tuple[np.ndarray, list[str], bool]:
    try:
        im = Image.open(
            io.BytesIO(data), formats=("PNG", "JPEG", "WEBP")
        )  # reads the header only; no other format is tried
    except Exception as exc:
        raise PictureError(
            f"this is not a picture that can be read ({exc.__class__.__name__}: {exc})"
        ) from exc
    if (
        im.width * im.height > MAX_PIXELS
    ):  # refuse before decoding anything: a few KB of file can hold gigapixels
        raise PictureError(
            f"the picture is {im.width}x{im.height}: more than {MAX_PIXELS // 1_000_000} megapixels"
        )
    try:
        im.load()
    except Exception as exc:
        raise PictureError(
            f"this is not a picture that can be read ({exc.__class__.__name__}: {exc})"
        ) from exc
    rgba = (ImageOps.exif_transpose(im) or im).convert("RGBA")
    if max(rgba.size) > MAX_SIDE:
        rgba.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    arr = np.array(rgba)
    notes: list[str] = []
    has_alpha = bool((arr[..., 3] < 250).mean() > 0.004)
    if not has_alpha and cutout is not False:
        bg = plain_background_mask(arr[..., :3])
        if bg is not None:
            arr[..., 3] = _soft_alpha(arr[..., :3], bg)
            has_alpha = True
            notes.append("made the plain background transparent")
        elif cutout:
            notes.append("no plain background was found to remove")
    if not has_alpha:
        notes.append("the picture has no transparency: it is drawn as a rectangle")
    if trim and has_alpha:
        ys, xs = np.nonzero(arr[..., 3] > 12)
        if len(xs) == 0:
            raise PictureError("nothing is left of the picture after removing its background")
        top, bottom = int(np.min(ys)), int(np.max(ys))
        left, right = int(np.min(xs)), int(np.max(xs))
        arr = arr[max(0, top - 1) : bottom + 2, max(0, left - 1) : right + 2]
    if mirror:
        arr = np.ascontiguousarray(arr[:, ::-1])
    return np.ascontiguousarray(arr), notes, has_alpha


def load_raster(
    data: bytes,
    *,
    height: float,
    anchor: tuple[float, float] = (0.5, 1.0),
    facing: str = "right",
    cutout: bool | None = None,
    place: bool = False,
) -> Art:
    """A picture as an `Art`: one `ImageG` shape ``height`` design px tall with its anchor at the origin, facing +x.

    ``place`` loads a backdrop: nothing is cut out or trimmed and there is no outline (the art box is the whole picture).
    """
    if len(data) > MAX_ART_BYTES:
        raise PictureError(f"the file is larger than {MAX_ART_BYTES // (1024 * 1024)} MB")
    mirror = facing == "left"
    pid = hashlib.sha256(
        data + f"|{PROCESS_VERSION}|{cutout}|{mirror}|{place}".encode()
    ).hexdigest()[:24]
    cdir = _cache_dir()
    png, meta = cdir / f"{pid}.png", cdir / f"{pid}.json"
    arr: np.ndarray | None = None
    info: dict[str, Any] = {}
    if png.is_file() and meta.is_file():
        try:
            arr = np.array(Image.open(png).convert("RGBA"))
            info = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:  # a damaged cache entry is just recomputed
            arr = None
    if arr is None:
        arr, notes, has_alpha = _process(data, False if place else cutout, mirror, trim=not place)
        loops = [] if (place or not has_alpha) else trace_outline(arr[..., 3])
        info = {
            "notes": notes,
            "has_alpha": has_alpha,
            "outline": [[list(p) for p in lp] for lp in loops],
        }
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            tmp = cdir / f".{pid}.{os.getpid()}.{time.monotonic_ns()}"
            Image.fromarray(arr).save(str(tmp) + ".png", format="PNG")
            Path(str(tmp) + ".png").replace(png)
            Path(str(tmp) + ".json").write_text(json.dumps(info), encoding="utf-8")
            Path(str(tmp) + ".json").replace(meta)
        except OSError:  # a read-only cache folder must not stop an import
            pass
    h_px, w_px = arr.shape[:2]
    if pid not in _IMAGES:
        _IMAGES[pid] = skia.Image.fromarray(
            np.ascontiguousarray(arr),
            colorType=skia.kRGBA_8888_ColorType,
            alphaType=skia.kUnpremul_AlphaType,
        ).withDefaultMipmaps()  # a big picture drawn small must not shimmer
    k = height / h_px
    ax, ay = anchor
    # the anchor is a place in the picture as it was drawn: mirrored, it is measured from the other side (as for an SVG)
    ox, oy = -((1 - ax) if mirror else ax) * w_px * k, -ay * h_px * k
    loops = [[(float(x), float(y)) for x, y in lp] for lp in info.get("outline", [])]
    scaled = [[(ox + x * k, oy + y * k) for x, y in lp] for lp in loops]
    shape = Shape(
        ImageG(pid, ox, oy, w_px * k, h_px * k, loops_to_cmds(scaled)),
        None,
        tag="picture",
        elev=2.5,
        lod=0,
    )
    return Art(
        [shape],
        {},
        (ox, oy, ox + w_px * k, oy + h_px * k),
        {},
        [],
        "raster",
        size_px=(w_px, h_px),
        notes=list(info.get("notes", [])),
    )
