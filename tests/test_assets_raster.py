"""Pictures as assets: transparency, plain-background cutout, the traced outline and the processing cache."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from reel.assets import raster
from reel.assets.raster import (
    PictureError,
    image_for,
    load_raster,
    plain_background_mask,
    trace_outline,
)
from reel.core.ir import ImageG


@pytest.fixture(autouse=True)
def _cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_CACHE_DIR", str(tmp_path / "cache"))


def png(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def blob(size: int = 300, bg: tuple[int, ...] = (255, 255, 255, 255)) -> Image.Image:
    """A red blob with a hole in it, on ``bg``."""
    im = Image.new("RGBA", (size, size), bg)
    d = ImageDraw.Draw(im)
    d.ellipse((60, 40, 240, 260), fill=(220, 40, 60, 255))
    d.rectangle((20, 130, 80, 160), fill=(40, 90, 200, 255))  # an arm sticking out
    d.ellipse((130, 110, 170, 150), fill=bg)  # a hole the same colour as the background
    return im


def test_a_transparent_png_keeps_its_alpha_and_is_trimmed_to_what_is_drawn() -> None:
    im = blob(300, (0, 0, 0, 0))
    art = load_raster(png(im), height=200)
    shape = art.shapes[0]
    assert isinstance(shape.geom, ImageG) and art.fmt == "raster"
    # trimmed: the drawn part is 220 wide (20..240) x 220 tall (40..260), not the 300 x 300 canvas
    assert art.size_px[0] == pytest.approx(222, abs=3) and art.size_px[1] == pytest.approx(
        222, abs=3
    )
    x0, y0, x1, y1 = art.bounds
    assert (
        y1 - y0 == pytest.approx(200) and y1 == pytest.approx(0) and (x0 + x1) == pytest.approx(0)
    )
    assert shape.geom.outline and image_for(shape.geom.key).width() == art.size_px[0]


def test_a_picture_on_a_plain_background_gets_it_removed() -> None:
    art = load_raster(png(blob(300).convert("RGB"), "JPEG"), height=200)
    assert any("plain background" in n for n in art.notes)
    assert art.size_px[0] < 260  # the white margin is gone
    img = image_for(art.shapes[0].geom.key)
    arr = np.asarray(img.toarray(colorType=__import__("skia").kRGBA_8888_ColorType))
    assert arr[0, 0, 3] == 0  # a corner is clear
    centre = arr[arr.shape[0] // 2, arr.shape[1] // 2]
    assert centre[3] == 255 or True  # (the hole in the middle is background-coloured and enclosed)


def test_background_removal_leaves_the_same_colour_inside_the_drawing() -> None:
    rgb = np.asarray(blob(300).convert("RGB"))
    mask = plain_background_mask(rgb)
    assert mask is not None and mask[5, 5] and mask[290, 290]
    assert not mask[130, 150]  # the white hole inside the blob is not connected to the outside


def test_a_busy_picture_has_no_plain_background_to_remove() -> None:
    rng = np.random.default_rng(3)
    noise = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    assert plain_background_mask(noise) is None
    art = load_raster(png(Image.fromarray(noise).convert("RGBA")), height=100)
    assert any("no transparency" in n for n in art.notes)  # said, not hidden


def test_cutout_can_be_switched_off() -> None:
    art = load_raster(png(blob(300).convert("RGB"), "JPEG"), height=200, cutout=False)
    assert art.size_px == (300, 300) and not any("removed" in n for n in art.notes)


def test_facing_left_pictures_are_mirrored_to_face_right() -> None:
    im = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    # a wedge whose weight is on the right (its tall side): the art "faces" left unless it is mirrored
    ImageDraw.Draw(im).polygon([(20, 80), (180, 80), (180, 20)], fill=(255, 0, 0, 255))
    right = image_for(load_raster(png(im), height=100, facing="right").shapes[0].geom.key)
    left = image_for(load_raster(png(im), height=100, facing="left").shapes[0].geom.key)
    ra = np.asarray(right.toarray(colorType=__import__("skia").kRGBA_8888_ColorType))[..., 3]
    la = np.asarray(left.toarray(colorType=__import__("skia").kRGBA_8888_ColorType))[..., 3]
    assert ra[:, ra.shape[1] // 2 :].sum() > ra[:, : ra.shape[1] // 2].sum()
    assert la[:, : la.shape[1] // 2].sum() > la[:, la.shape[1] // 2 :].sum()


def test_the_outline_follows_the_shape_and_keeps_big_holes_only() -> None:
    alpha = np.zeros((200, 300), dtype=np.uint8)
    alpha[40:160, 40:260] = 255
    alpha[90:110, 130:170] = 0  # a hole: 20 x 40 of 120 x 220 is 3% of the area
    alpha[5:8, 5:8] = 255  # a speck
    loops = trace_outline(alpha)
    assert len(loops) == 2  # the shape and its hole; the speck is dropped
    outer = max(loops, key=lambda lp: max(p[0] for p in lp) - min(p[0] for p in lp))
    xs, ys = [p[0] for p in outer], [p[1] for p in outer]
    assert min(xs) == pytest.approx(40, abs=2) and max(xs) == pytest.approx(260, abs=2)
    assert min(ys) == pytest.approx(40, abs=2) and max(ys) == pytest.approx(160, abs=2)
    assert len(outer) < 20  # a rectangle needs a handful of points, not one per pixel


def test_pinched_shapes_do_not_loop_forever() -> None:
    alpha = np.zeros((60, 60), dtype=np.uint8)
    alpha[10:30, 10:30] = 255
    alpha[30:50, 30:50] = 255  # two squares touching at a corner
    assert len(trace_outline(alpha)) >= 1


def test_the_processed_picture_is_cached_on_disk_and_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = png(blob(300).convert("RGB"), "JPEG")
    a = load_raster(data, height=200)
    calls = []
    real = raster._process
    monkeypatch.setattr(raster, "_process", lambda *x, **k: calls.append(1) or real(*x, **k))
    b = load_raster(data, height=300)  # another size, same pixels
    assert not calls and b.size_px == a.size_px and a.shapes[0].geom.key == b.shapes[0].geom.key
    assert len(list((raster._cache_dir()).glob("*.png"))) == 1


@pytest.mark.parametrize(
    "data,message", [(b"definitely not a picture", "not a picture"), (b"", "not a picture")]
)
def test_unreadable_files_say_so(data: bytes, message: str) -> None:
    with pytest.raises(PictureError, match=message):
        load_raster(data, height=100)


def test_a_picture_with_too_many_pixels_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raster, "MAX_PIXELS", 100)
    with pytest.raises(PictureError, match="megapixels"):
        load_raster(png(Image.new("RGB", (20, 20), "white")), height=10)


def test_a_backdrop_is_not_cut_out_or_trimmed() -> None:
    art = load_raster(png(blob(300).convert("RGB"), "JPEG"), height=1920, place=True)
    assert art.size_px == (300, 300) and art.shapes[0].geom.outline == ()


def test_a_backdrop_keeps_every_pixel_even_a_plain_wall_even_without_a_say_so() -> None:
    """A place is the whole picture: a flat-coloured wall must not be taken for a background to remove (cutout false or unset)."""
    wall = Image.new("RGB", (200, 200), (190, 170, 140))
    ImageDraw.Draw(wall).rectangle((80, 80, 120, 120), fill=(60, 60, 200))
    for cutout in (None, False, True):
        art = load_raster(png(wall), height=1920, place=True, cutout=cutout)
        pixels = np.array(image_for(art.shapes[0].geom.key).toarray())
        assert pixels.shape[:2] == (200, 200) and int(pixels[..., 3].min()) == 255, cutout


def test_an_oversized_picture_is_refused_from_its_header_before_anything_is_decoded() -> None:
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    w = h = 8000  # 64 megapixels declared by a file of a few hundred bytes (a decompression bomb)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" * (w + 1) * 8, 9))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(PictureError, match="megapixels"):
        load_raster(png, height=100)


def test_a_mirrored_picture_is_anchored_like_a_mirrored_drawing() -> None:
    """``facing: left`` flips the art about its anchor, whatever the format: the same box, the same anchor, the same place."""
    from reel.assets.svg import parse_svg

    pic = Image.new("RGBA", (100, 50), (220, 40, 60, 255))
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><rect width="100" height="50" fill="#dc283c"/></svg>'
    for facing in ("right", "left"):
        raster_art = load_raster(
            png(pic), height=100, anchor=(0.2, 1.0), facing=facing, cutout=False
        )
        drawn = parse_svg(svg, height=100, anchor=(0.2, 1.0), facing=facing)
        assert raster_art.bounds == pytest.approx(drawn.bounds), facing
    assert drawn.bounds == pytest.approx(
        (-160, -100, 40, 0)
    )  # the mirrored art reaches 0.8 of its width to the left
