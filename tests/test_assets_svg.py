"""The SVG importer, checked against skia's own SVG renderer: the shapes it produces must draw what the file draws."""

from __future__ import annotations

import numpy as np
import pytest
import skia

from reel.assets.svg import (
    SvgError,
    mapply,
    parse_color,
    parse_path_d,
    parse_svg,
    parse_transform,
)
from reel.core.ir import ColorScheme, Ellipse, Line, PathG, Poly, Rect
from reel.styles.base import StyleContext
from reel.styles.flat_vector import FlatVector

NS = 'xmlns="http://www.w3.org/2000/svg"'
SIZE = 200


def _svg(body: str, vb: str = "0 0 100 100", extra: str = "") -> str:
    return f'<svg {NS} viewBox="{vb}" {extra}>{body}</svg>'


def reference(svg: str, w: int, h: int) -> np.ndarray:
    """What skia's SVG renderer draws for the file: the alpha channel as an array."""
    dom = skia.SVGDOM.MakeFromStream(skia.MemoryStream.MakeDirect(svg.encode()))
    surf = skia.Surface(w, h)
    surf.getCanvas().clear(skia.ColorTRANSPARENT)
    dom.setContainerSize(skia.Size(w, h))
    dom.render(surf.getCanvas())
    return np.asarray(surf.makeImageSnapshot().toarray(colorType=skia.kBGRA_8888_ColorType))


def ours(svg: str, w: int, h: int) -> np.ndarray:
    """The same file through the importer and the flat-vector brush (no shading), over the same box."""
    art = parse_svg(svg, height=h, anchor=(0.0, 0.0), fit="viewbox")
    surf = skia.Surface(w, h)
    cv = surf.getCanvas()
    cv.clear(skia.ColorTRANSPARENT)
    style = FlatVector()
    ctx = StyleContext(scale=1.0, size=(w, h), scheme=ColorScheme(), seed=1)
    for s in art.shapes:
        style.paint_shape(cv, s, ctx)
    return np.asarray(surf.makeImageSnapshot().toarray(colorType=skia.kBGRA_8888_ColorType))


def agreement(svg: str) -> float:
    """Share of pixels whose coverage (alpha) both renderers agree on (> 0.5 or not); drawing nothing scores 0."""
    a = reference(svg, SIZE, SIZE)[..., 3] > 127
    b = ours(svg, SIZE, SIZE)[..., 3] > 127
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


@pytest.mark.parametrize(
    "name,body",
    [
        ("rect", '<rect x="10" y="20" width="60" height="40" fill="#e63946"/>'),
        ("rounded rect", '<rect x="10" y="20" width="70" height="50" rx="12" fill="#264653"/>'),
        ("circle", '<circle cx="50" cy="50" r="30" fill="#2a9d8f"/>'),
        ("ellipse", '<ellipse cx="50" cy="55" rx="40" ry="22" fill="#e9c46a"/>'),
        ("polygon", '<polygon points="50,8 92,90 8,90" fill="#f4a261"/>'),
        (
            "path with arcs and relative moves",
            '<path d="M10 50 a40 25 0 0 1 80 0 l-10 30 h-60 z" fill="#457b9d"/>',
        ),
        (
            "smooth curves",
            '<path d="M10 80 C 30 10, 70 10, 90 80 S 60 95, 40 60 Q 20 40 10 80 T 60 90 Z" fill="#6a4c93"/>',
        ),
        (
            "stroke only",
            '<path d="M10 80 L50 20 L90 80" fill="none" stroke="#1d3557" stroke-width="8" stroke-linejoin="round"/>',
        ),
        ("line", '<line x1="10" y1="90" x2="90" y2="10" stroke="#d62828" stroke-width="10"/>'),
        (
            "rotated rect",
            '<rect x="30" y="30" width="40" height="40" fill="#bc4749" transform="rotate(30 50 50)"/>',
        ),
        (
            "scaled and moved group",
            '<g transform="translate(20 10) scale(0.5)"><circle cx="80" cy="80" r="50" fill="#588157"/></g>',
        ),
        (
            "matrix and skew",
            '<rect width="40" height="30" fill="#3a5a40" transform="matrix(1 0.2 -0.3 1 30 20) skewX(10)"/>',
        ),
        (
            "nested groups",
            '<g fill="#e76f51"><g transform="translate(10,10)"><rect width="30" height="30"/><rect x="40" y="40" width="30" height="30" fill="#264653"/></g></g>',
        ),
        (
            "evenodd hole",
            '<path fill-rule="evenodd" d="M10 10 H90 V90 H10 Z M30 30 V70 H70 V30 Z" fill="#023047"/>',
        ),
        (
            "css class",
            '<style>.a{fill:#ff006e}.b{fill:#3a86ff}</style><rect class="a" width="50" height="50"/><circle class="b" cx="70" cy="70" r="20"/>',
        ),
        (
            "opacity",
            '<rect width="60" height="60" fill="#000" opacity="0.4"/><rect x="40" y="40" width="50" height="50" fill="#e63946" fill-opacity="0.8"/>',
        ),
    ],
)
def test_the_importer_draws_what_skia_draws(name: str, body: str) -> None:
    score = agreement(_svg(body))
    assert score > 0.93, (
        f"{name}: the two renderers agree on only {score:.0%} of the covered pixels"
    )


def test_use_of_an_id_draws_what_skia_draws_and_also_reads_the_newer_plain_href() -> None:
    body = '<defs><circle id="c" r="15" fill="#fb8500"/></defs><use {h}="#c" x="30" y="30"/><use {h}="#c" x="70" y="60"/>'
    xl = _svg(body.format(h="xlink:href"), extra='xmlns:xlink="http://www.w3.org/1999/xlink"')
    assert agreement(xl) > 0.97  # skia only understands the xlink spelling
    plain = ours(_svg(body.format(h="href")), SIZE, SIZE)[..., 3] > 127
    assert (plain == (ours(xl, SIZE, SIZE)[..., 3] > 127)).all()


def test_the_colours_match_where_both_draw() -> None:
    svg = _svg(
        '<rect width="100" height="100" fill="#e63946"/><circle cx="50" cy="50" r="20" fill="rgb(42, 157, 143)"/>'
    )
    a, b = reference(svg, SIZE, SIZE), ours(svg, SIZE, SIZE)
    for xy in ((20, 20), (100, 100), (180, 30)):
        assert np.abs(a[xy[1], xy[0], :3].astype(int) - b[xy[1], xy[0], :3].astype(int)).max() <= 3


def test_primitives_stay_primitives_and_the_rest_become_paths() -> None:
    art = parse_svg(
        _svg(
            '<rect width="20" height="10"/><circle cx="50" cy="50" r="10"/><line x1="0" y1="0" x2="9" y2="9" stroke="red"/>'
            '<polygon points="0,0 10,0 5,9"/><path d="M0 0 L9 9 L0 9Z"/><rect width="10" height="10" transform="rotate(20)"/>'
        ),
        height=100,
    )
    kinds = [type(s.geom) for s in art.shapes]
    assert kinds == [Rect, Ellipse, Line, Poly, PathG, PathG]


def test_the_art_is_scaled_to_its_height_with_the_anchor_at_the_origin() -> None:
    art = parse_svg(
        _svg('<rect x="20" y="30" width="40" height="20" fill="#000"/>'),
        height=100,
        anchor=(0.5, 1.0),
    )
    x0, y0, x1, y1 = art.bounds
    assert (
        (y1 - y0) == pytest.approx(100) and y1 == pytest.approx(0) and (x0 + x1) == pytest.approx(0)
    )
    assert (x1 - x0) == pytest.approx(200)  # the box is 40 x 20 and is scaled 5x
    centre = parse_svg(
        _svg('<rect x="20" y="30" width="40" height="20" fill="#000"/>'),
        height=100,
        anchor=(0.5, 0.5),
    )
    assert centre.bounds[1] == pytest.approx(-50) and centre.bounds[3] == pytest.approx(50)


def test_facing_left_art_is_mirrored_so_it_faces_right() -> None:
    body = '<rect x="0" y="0" width="30" height="10" fill="#000"/><circle cx="25" cy="5" r="4" fill="#f00"/>'  # a head on the right
    right = parse_svg(_svg(body), height=10, anchor=(0.5, 1.0), facing="right")
    left = parse_svg(_svg(body), height=10, anchor=(0.5, 1.0), facing="left")
    r_head = next(s for s in right.shapes if isinstance(s.geom, Ellipse)).geom
    l_head = next(s for s in left.shapes if isinstance(s.geom, Ellipse)).geom
    assert r_head.cx > 0 > l_head.cx and abs(r_head.cx + l_head.cx) < 1e-6


def test_roles_collect_the_recolourable_parts() -> None:
    art = parse_svg(
        _svg(
            '<g data-role="body"><rect width="40" height="40" fill="#e63946"/></g><circle cx="5" cy="5" r="3" fill="#fff" data-role="eye"/>'
        ),
        height=40,
    )
    assert art.roles == {"body": "#e63946", "eye": "#ffffff"}
    # each piece remembers the colour the drawing gave it next to its role
    assert [s.fill for s in art.shapes] == ["@body=#e63946", "@eye=#ffffff"]


def test_hints_set_detail_level_elevation_and_shadow() -> None:
    art = parse_svg(
        _svg(
            '<rect width="100" height="100" fill="#ccc"/><rect width="4" height="4" fill="#000" data-lod="1" data-elev="3" data-shadow="false"/>'
        ),
        height=100,
    )
    big, small = art.shapes
    assert big.lod == 0 and small.lod == 1 and small.elev == 3 and small.shadow is False


def test_small_pieces_are_marked_as_fine_detail_but_the_big_ones_never_are() -> None:
    boxes = "".join(
        f'<rect x="{i * 6}" y="0" width="5" height="5" fill="#000"/>' for i in range(30)
    )
    art = parse_svg(_svg('<rect width="100" height="100" fill="#ccc"/>' + boxes), height=100)
    assert art.shapes[0].lod == 0
    assert (
        sum(1 for s in art.shapes if s.lod == 1) >= 20
        and sum(1 for s in art.shapes if s.lod == 0) >= 6
    )


def test_layered_places_group_shapes_by_plane() -> None:
    art = parse_svg(
        _svg(
            '<g data-plane="far"><rect width="100" height="50" fill="#9cf"/></g><g data-plane="near"><rect y="80" width="100" height="20" fill="#3a3"/></g>'
        ),
        height=100,
        fit="viewbox",
    )
    assert sorted(art.planes) == ["far", "near"] and len(art.shapes) == 2


def test_what_cannot_be_drawn_is_reported_not_silently_dropped() -> None:
    art = parse_svg(
        _svg(
            '<defs><filter id="f"><feGaussianBlur stdDeviation="3"/></filter><radialGradient id="g"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#f00"/></radialGradient></defs>'
            '<text x="5" y="20">hello</text><rect width="50" height="50" fill="url(#g)" filter="url(#f)"/>'
        ),
        height=50,
    )
    joined = " ".join(art.warnings)
    assert "text" in joined and "filters" in joined and "radial" in joined
    assert len(art.shapes) == 1


def test_a_linear_gradient_becomes_a_two_colour_gradient() -> None:
    art = parse_svg(
        _svg(
            '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff0000"/><stop offset="1" stop-color="#0000ff"/></linearGradient></defs>'
            '<rect width="100" height="100" fill="url(#g)"/>'
        ),
        height=100,
    )
    assert art.shapes[0].gradient == ("#ff0000", "#0000ff", pytest.approx(90.0))


@pytest.mark.parametrize(
    "bad,message",
    [
        (b"not xml at all", "readable"),
        (b"<html><body/></html>", "not an SVG"),
        (
            b'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg"/>',
            "entity",
        ),
        (_svg("<text>only text</text>").encode(), "nothing"),
    ],
)
def test_files_that_are_not_usable_say_why(bad: bytes, message: str) -> None:
    with pytest.raises(SvgError, match=message):
        parse_svg(bad, height=100)


def test_a_huge_file_is_refused() -> None:
    with pytest.raises(SvgError, match="larger"):
        parse_svg(b"<svg>" + b" " * (13 * 1024 * 1024) + b"</svg>", height=10)


def test_path_data_is_read_in_every_form() -> None:
    cmds = parse_path_d("M10,10 20,10 20,20 H5 v-5 z m 3 3 l 1 1")
    assert cmds[0] == ("M", 10, 10) and cmds[1] == (
        "L",
        20,
        10,
    )  # extra pairs after a moveto are linetos
    assert ("Z",) in cmds and cmds[-1][0] == "L"
    assert cmds[-2] == ("M", 13, 13)  # a relative moveto after close starts from the subpath start
    arc = parse_path_d("M0 0 a5 5 0 1 1 10 0")  # a half circle is one or two cubic pieces
    assert (all(c[0] in "MC" for c in arc) and arc[-1][-2:] == (10, 0)) or abs(
        arc[-1][-2] - 10
    ) < 1e-6
    packed = parse_path_d("M0 0a2 2 0 00.5.5")  # arc flags written without separators
    assert packed[-1][0] == "C"
    with pytest.raises(SvgError):
        parse_path_d("M0 0 L 5")


def test_transforms_compose_left_to_right() -> None:
    m = parse_transform("translate(10 0) scale(2)")
    assert mapply(m, 1, 1) == (12, 2)
    r = parse_transform("rotate(90)")
    x, y = mapply(r, 1, 0)
    assert (round(x, 6), round(y, 6)) == (0, 1)
    about = parse_transform("rotate(180 5 5)")
    x, y = mapply(about, 0, 0)
    assert (round(x, 6), round(y, 6)) == (10, 10)


def test_colours() -> None:
    assert parse_color("#abc") == ("#aabbcc", 1.0)
    assert parse_color("rgb(10, 20, 30)") == ("#0a141e", 1.0)
    assert parse_color("rgba(255,0,0,0.5)") == ("#ff0000", 0.5)
    assert parse_color("CornflowerBlue") == ("#6495ed", 1.0)
    assert (
        parse_color("none") is None
        and parse_color("#12") is None
        and parse_color("nonsense") is None
    )


# ======================================================================= hostile and odd files
def test_a_hostile_style_block_cannot_make_the_importer_slow() -> None:
    import time

    for style in ("a{" * 40_000, "/*" * 40_000, "a{b:c}" * 20_000, "x," * 50_000 + "{fill:red}"):
        t0 = time.perf_counter()
        parse_svg(_svg(f"<style>{style}</style><rect width='10' height='10'/>"), height=100)
        assert time.perf_counter() - t0 < 2.0, style[:12]


def test_many_rules_and_many_elements_do_not_multiply() -> None:
    import time

    rules = "".join(f".c{i}{{fill:#f00}}" for i in range(8000))
    rects = "".join(
        f"<rect class='c{i}' x='{i % 90}' y='{i // 90}' width='5' height='5'/>" for i in range(3000)
    )
    t0 = time.perf_counter()
    art = parse_svg(_svg(f"<style>{rules}</style>{rects}"), height=100)
    assert time.perf_counter() - t0 < 6.0
    assert len(art.shapes) == 3000


def test_css_still_styles_by_tag_class_and_id_and_the_later_rule_wins() -> None:
    svg = _svg(
        "<style>rect{fill:#111111} .a{fill:#222222} #z{fill:#333333} rect.b{fill:#444444} .a{fill:#666666}</style>"
        "<rect width='10' height='10'/><rect class='a' x='10' width='10' height='10'/>"
        "<rect id='z' x='20' width='10' height='10'/><rect class='b' x='30' width='10' height='10'/>"
    )
    assert [str(s.fill) for s in parse_svg(svg, height=100).shapes] == [
        "#111111",
        "#666666",  # .a is written twice: the later one wins
        "#333333",
        "#444444",
    ]


@pytest.mark.parametrize(
    "svg",
    [
        '<?xml version="1.0" encoding="gb2312"?><svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>',
        '<?xml version="1.0" encoding="x-mac-roman"?><svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>',
    ],
)
def test_a_character_set_python_cannot_read_is_a_plain_error(svg: str) -> None:
    with pytest.raises(SvgError):
        parse_svg(svg.encode(), height=100)


def test_numbers_that_are_not_numbers_never_escape_as_other_errors() -> None:
    ok = "<rect width='10' height='10' fill='#f00'/>"
    # a transform, a colour and a length that overflow are ignored; a shape at infinity is skipped
    art = parse_svg(
        _svg(
            f"{ok}<rect width='5' height='5' transform='rotate(1e999)' fill='rgb(1e999,0,0)'/>"
            "<circle cx='1e999' cy='0' r='3'/><path d='M0 0 L1e999 5 L3 3 Z'/>"
        ),
        height=100,
    )
    assert art.shapes and all(abs(v) < 1e9 for v in art.bounds)
    with pytest.raises(SvgError):
        parse_svg(_svg("<rect width='1e999' height='1e999'/>"), height=100)
    with pytest.raises(SvgError):
        parse_svg(
            f'<svg {NS} viewBox="0 0 1e999 1e999"><rect width="5" height="5"/></svg>', height=100
        )


def test_percentages_are_of_the_viewbox() -> None:
    art = parse_svg(
        _svg("<rect width='100%' height='100%' fill='#336699'/>", vb="0 0 200 400"), height=400
    )
    assert len(art.shapes) == 1
    x0, y0, x1, y1 = art.bounds
    assert (x1 - x0, y1 - y0) == pytest.approx((200, 400))
    half = parse_svg(
        _svg(
            "<rect x='25%' y='25%' width='50%' height='50%' fill='#336699'/><circle cx='50%' cy='50%' r='10%'/>"
        ),
        height=100,
    )
    assert len(half.shapes) == 2
