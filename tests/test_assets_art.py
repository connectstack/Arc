"""Operations on loaded art: recolouring by role, and the shades that follow a recoloured role."""

from __future__ import annotations

from pathlib import Path

from reel.assets.art import recolor, role_colors
from reel.assets.library import load_dirs
from reel.assets.preview import render_preview
from reel.assets.svg import parse_svg
from reel.core.catalog import CATALOG
from reel.core.geometry import hls

NS = 'xmlns="http://www.w3.org/2000/svg"'
CAR = f"""<svg {NS} viewBox="0 0 100 100">
  <rect data-role="body" x="0" y="40" width="100" height="40" fill="#e63946"/>
  <rect data-role="body_dark" x="0" y="70" width="100" height="10" fill="#b02a36"/>
  <rect data-role="body_light" x="0" y="40" width="100" height="6" fill="#ff7b7b"/>
  <rect data-role="glass" x="30" y="20" width="30" height="20" fill="#bfe3f2"/>
</svg>"""


def _fills(svg: str, palette: dict[str, str]) -> dict[str, str]:
    art = parse_svg(svg, height=100)
    out = {}
    for sh, name in zip(
        recolor(art.shapes, art.roles, palette), ("body", "dark", "light", "glass"), strict=True
    ):
        out[name] = str(sh.fill)
    return out


def test_roles_keep_the_drawings_colours_without_a_palette() -> None:
    assert _fills(CAR, {}) == {
        "body": "#e63946",
        "dark": "#b02a36",
        "light": "#ff7b7b",
        "glass": "#bfe3f2",
    }


def test_recolouring_a_role_recolours_the_palette_entry_and_nothing_else() -> None:
    fills = _fills(CAR, {"glass": "#222222"})
    assert fills["glass"] == "#222222"
    assert fills["body"] == "#e63946" and fills["dark"] == "#b02a36"


def test_shades_follow_a_recoloured_base() -> None:
    fills = _fills(CAR, {"body": "#2a6fdb"})
    assert fills["body"] == "#2a6fdb"
    hb, lb, _ = hls("#2a6fdb")
    hd, ld, _ = hls(fills["dark"])
    hl, ll, _ = hls(fills["light"])
    assert ld < lb < ll, "the dark shade is darker and the light one lighter, as drawn"
    for h in (hd, hl):  # they stay blue, not the red they were drawn in
        assert abs(h - hb) < 0.02
    # the step is the drawing's step: the same share of lightness
    _, lo_base, _ = hls("#e63946")
    _, lo_dark, _ = hls("#b02a36")
    assert abs(ld / lb - lo_dark / lo_base) < 0.02


def test_a_shade_named_in_the_palette_wins() -> None:
    fills = _fills(CAR, {"body": "#2a6fdb", "body_dark": "#000000"})
    assert fills["dark"] == "#000000"
    assert fills["light"] not in ("#ff7b7b", "#000000")


def test_a_shade_with_no_base_role_in_the_drawing_is_left_alone() -> None:
    svg = f'<svg {NS} viewBox="0 0 10 10"><rect data-role="leaves_dark" width="10" height="10" fill="#357a47"/></svg>'
    art = parse_svg(svg, height=10)
    assert role_colors(art.roles, {"leaves": "#ff0000"})["leaves_dark"] == "#357a47"


def test_a_grey_recolour_does_not_invent_a_hue() -> None:
    fills = _fills(CAR, {"body": "#808080"})
    _, _, s = hls(fills["dark"])
    assert s < 0.05


FUR = f"""<svg {NS} viewBox="0 0 100 100">
  <rect data-role="fur" x="0" y="0" width="50" height="100" fill="#e08a3c"/>
  <rect data-role="fur" x="50" y="0" width="50" height="100" fill="#d57a2c"/>
</svg>"""


def test_pieces_of_one_role_keep_their_own_tone_until_it_is_recoloured() -> None:
    art = parse_svg(FUR, height=100)
    plain = [s.fill for s in recolor(art.shapes, art.roles, {})]
    assert plain == ["#e08a3c", "#d57a2c"]  # the drawing's own two oranges, not one flat colour
    blue = [s.fill for s in recolor(art.shapes, art.roles, {"fur": "#2a6fdb"})]
    assert blue[0] == "#2a6fdb"
    assert blue[1] != blue[0]
    (_, l0, _), (_, l1, _) = hls(blue[0]), hls(blue[1])
    assert l1 < l0  # the darker piece stays darker after the recolour
    assert abs(hls(blue[1])[0] - hls("#2a6fdb")[0]) < 0.02  # and is still blue


def test_a_place_with_marked_roles_draws_its_own_colours(tmp_path: Path) -> None:
    """A place takes no palette: roles must show up as the drawing's own colours (they used to reach the painter as "@role")."""
    d = tmp_path / "places"
    d.mkdir()
    (d / "roomy.svg").write_text(
        f'<svg {NS} viewBox="0 0 1080 1920"><rect data-role="wall" width="1080" height="1920" fill="#336699"/></svg>',
        encoding="utf-8",
    )
    fork = CATALOG.fork()
    load_dirs([tmp_path], fork)
    img = render_preview(fork.assets.get("roomy"), "flat_vector", scale=0.1, catalog=fork)
    b, g, r = (int(v) for v in img[2, 2, :3])
    assert (
        b > g > r and b - r > 40
    )  # the wall's blue (the scene's lighting grade shifts it a little)


def test_the_outline_of_a_drawing_of_thousands_of_pieces_comes_quickly() -> None:
    """Uniting pieces one by one takes minutes for a few thousand: the biggest ones carry the outline."""
    import random
    import time

    from reel.assets.art import MAX_SILHOUETTE_PIECES, silhouette

    rng = random.Random(1)
    rects = "".join(
        f'<rect data-lod="0" x="{rng.randint(0, 950)}" y="{rng.randint(0, 950)}" '
        f'width="{rng.randint(10, 60)}" height="{rng.randint(10, 60)}" fill="#c33"/>'
        for _ in range(4000)
    )
    art = parse_svg(f'<svg {NS} viewBox="0 0 1000 1000">{rects}</svg>', height=500)
    t0 = time.perf_counter()
    outline = silhouette(art)
    assert time.perf_counter() - t0 < 5.0
    assert outline and MAX_SILHOUETTE_PIECES < 4000
    again = parse_svg(f'<svg {NS} viewBox="0 0 1000 1000">{rects}</svg>', height=500)
    assert silhouette(again) == outline  # the same drawing always gives the same outline
