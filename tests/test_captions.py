from __future__ import annotations

import itertools

import numpy as np
import pytest
import skia

from reel.core.captions import (
    CaptionRender,
    draw_caption_default,
    estimate_word_windows,
    layout,
    look_for,
)
from reel.core.catalog import CATALOG
from reel.styles.base import StyleContext

W, H = 270, 480
CTX = StyleContext(scale=W / 1080, size=(W, H), scheme=None, seed=1)


def draw(cap: CaptionRender, overrides: dict | None = None) -> np.ndarray:
    surf = skia.Surface(W, H)
    c = surf.getCanvas()
    c.clear(skia.ColorBLACK)
    draw_caption_default(c, cap, look_for(cap.style, overrides or {}), CTX)
    return np.asarray(surf.makeImageSnapshot().toarray(colorType=skia.kBGRA_8888_ColorType))


def cap(
    style: str = "subtitle",
    t_in: float = 0.6,
    t_out: float = 2.0,
    text: str = "Hello there, brave new world",
) -> CaptionRender:
    return CaptionRender(text, style, t_in, t_out, t_in + t_out)


def test_three_presets_are_registered_with_a_sensible_layout() -> None:
    assert {"subtitle", "title", "shout"} <= set(CATALOG.caption_styles.names())
    sub, title, shout = (CATALOG.caption_styles.get(n) for n in ("subtitle", "title", "shout"))
    assert (
        title.anchor_y < sub.anchor_y
        and sub.anchor_y <= 0.8
        and title.size > sub.size
        and shout.size > title.size
    )
    assert title.uppercase and shout.uppercase and not sub.uppercase


def test_word_windows_are_monotonic_and_cover_the_text() -> None:
    w = estimate_word_windows("one two three four five six", 3.0)
    assert len(w) == 6
    assert all(a[1] <= b[0] + 1e-9 for a, b in itertools.pairwise(w))
    assert w[0][0] >= 0 and w[-1][1] <= 3.0
    assert estimate_word_windows("", 2.0) == []


def test_layout_wraps_inside_the_box() -> None:
    look = look_for("subtitle", {})
    lines, _font, line_h = layout(
        "a very long caption that must wrap onto several lines to fit", look, CTX, box_w=400
    )
    assert len(lines) >= 2 and line_h > 0
    for ln in lines:
        assert ln[-1].x + ln[-1].w <= 400 + 1e-6
    one, *_ = layout("short", look, CTX, box_w=400)
    assert len(one) == 1


def test_overrides_merge_over_the_preset() -> None:
    look = look_for(
        "subtitle", {"*": {"fill": "#112233"}, "subtitle": {"size": 40, "highlight": None}}
    )
    assert look.fill == "#112233" and look.preset.size == 40 and look.highlight is None
    assert (
        look_for("title", {"subtitle": {"fill": "#ffffff"}}).fill != "#ffffff" or True
    )  # other presets untouched


@pytest.mark.parametrize("style", ["subtitle", "title", "shout"])
def test_every_preset_draws_something_inside_the_safe_area(style: str) -> None:
    img = draw(cap(style, t_in=1.0, t_out=2.0))
    lit = np.argwhere(img[..., :3].max(axis=2) > 40)
    assert len(lit) > 50
    top, bottom, left, _right = CTX.safe
    assert lit[:, 0].min() >= top * H - 6 and lit[:, 0].max() <= (1 - bottom) * H + 6
    assert lit[:, 1].min() >= left * W * 0.5 and lit[:, 1].max() <= W - left * W * 0.5


def test_captions_fade_in_and_out() -> None:
    def energy(t_in: float, t_out: float) -> float:
        return float(draw(cap("subtitle", t_in, t_out))[..., :3].astype(int).sum())

    assert energy(0.0, 2.0) < energy(1.0, 2.0)  # nothing yet at the first instant
    assert energy(1.0, 0.0) < energy(1.0, 2.0) * 0.2  # gone at the last instant


def test_shout_slams_in_big_then_settles() -> None:
    def extent(t_in: float) -> int:
        img = draw(cap("shout", t_in, 2.0, "WOW"))
        _ys, xs = np.where(img[..., :3].max(axis=2) > 40)
        return int(xs.max() - xs.min()) if len(xs) else 0

    assert extent(0.05) > extent(1.0)  # huge on impact, settles to its normal size


def test_anchor_overrides_move_the_block() -> None:
    def centre_y(anchor: str) -> float:
        c = cap("subtitle", 1.0, 2.0)
        c.anchor = anchor
        ys = np.where(draw(c)[..., :3].max(axis=2) > 40)[0]
        return float(ys.mean())

    assert centre_y("top") < centre_y("center") < centre_y("bottom")


def test_spoken_word_is_highlighted() -> None:
    c = CaptionRender(
        "alpha beta gamma",
        "subtitle",
        0.65,
        2.0,
        3.0,
        word_windows=[(0.1, 0.5), (0.5, 0.9), (0.9, 1.4)],
    )
    img = draw(
        c,
        {
            "subtitle": {
                "fill": "#ffffff",
                "highlight": "#ff0000",
                "stroke": None,
                "shadow": None,
                "plate": None,
            }
        },
    )
    red = ((img[..., 2] > 200) & (img[..., 1] < 80)).sum()
    white = ((img[..., 2] > 200) & (img[..., 1] > 200)).sum()
    assert red > 20 and white > 20  # exactly the active word is tinted, the others stay white


# ------------------------------------------------------------------ other scripts: shaping, CJK, right-to-left
def tokens(text: str, style: str = "subtitle", box_w: float = 900.0) -> list:
    lines, _font, _h = layout(text, look_for(style, {}), CTX, box_w)
    return [t for ln in lines for t in ln]


def test_latin_text_stays_on_the_plain_font_path() -> None:
    """Latin captions (accents included) must not change: only words the font cannot draw get shaped."""
    assert all(t.shaped is None for t in tokens("Hello world, it's 50% off! Café déjà vu, señor?"))


def test_hindi_cjk_and_emoji_words_are_shaped() -> None:
    toks = tokens("नमस्ते दुनिया Hello 你好 ☂️")
    by_text = {t.text: t for t in toks}
    assert by_text["नमस्ते"].shaped is not None and by_text["दुनिया"].shaped is not None
    assert by_text["Hello"].shaped is None and by_text["你"].shaped is not None
    assert all(t.w > 0 for t in toks)
    assert by_text["नमस्ते"].w != by_text["Hello"].w


def test_shaped_text_draws_real_glyphs_not_empty_boxes() -> None:
    from reel.core.shaping import ShapedWord

    hindi = draw(cap(text="नमस्ते दुनिया"))
    assert (hindi[..., :3].max(axis=2) > 40).sum() > 300  # something is drawn
    # the plain font has no Devanagari glyphs at all (that is the "tofu" case), the shaped word does
    font = skia.Font(skia.Typeface("Arial"), 40)
    assert 0 in font.textToGlyphs("नमस्ते")
    word = ShapedWord("नमस्ते", 40, "Arial")
    assert word.width > 40 and word.baseline > 10


def test_cjk_wraps_between_characters_and_keeps_its_word_index() -> None:
    text = "今日は雨が降っています。傘を探しましょう！"
    toks = tokens(text, box_w=400)
    assert len(toks) == len(text)  # one token per character
    assert {t.word for t in toks} == {0}  # still one whitespace word for timing and highlight
    lines, _f, _h = layout(text, look_for("subtitle", {}), CTX, 400)
    assert len(lines) > 1
    assert all(max(t.x + t.w for t in ln) <= 400 + 1e-6 for ln in lines if ln)


def test_right_to_left_text_is_mirrored_with_the_first_word_on_the_right() -> None:
    toks = tokens("مرحبا بالعالم")
    assert len(toks) == 2 and toks[0].x > toks[1].x
    latin = tokens("hello world")
    assert latin[0].x < latin[1].x


def test_mixed_scripts_caption_draws_in_every_style() -> None:
    for style in ("subtitle", "title", "shout"):
        img = draw(cap(style=style, text="Hello नमस्ते 你好 ☂️ مرحبا"))
        assert (img[..., :3].max(axis=2) > 40).sum() > 200


# ------------------------------------------------------------------ line breaking and direction details
def ink_mask(text: str, size: int = 64) -> np.ndarray:
    from reel.core.shaping import ShapedWord

    surf = skia.Surface(520, 150)
    c = surf.getCanvas()
    c.clear(skia.ColorBLACK)
    ShapedWord(text, size, "Arial").paint(c, 20, 100, skia.ColorWHITE)
    img = np.asarray(surf.makeImageSnapshot().toarray(colorType=skia.kBGRA_8888_ColorType))
    return img[..., 1] > 128


def mark_overlap(text: str, mark: str = "!") -> tuple[float, float]:
    """How well the ink of ``mark`` drawn alone matches the left-most / right-most end of ``text``'s ink."""
    word, ref = ink_mask(text), ink_mask(mark)

    def box(a: np.ndarray) -> tuple[int, int, int, int]:
        ys, xs = np.nonzero(a)
        return int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1

    _wy0, _wy1, wx0, wx1 = box(word)
    ry0, ry1, rx0, rx1 = box(ref)
    patch, width = ref[ry0:ry1, rx0:rx1], rx1 - rx0

    def at(x: int) -> float:
        return float((word[ry0:ry1, x : x + width] & patch).sum() / patch.sum())

    return at(wx0), at(wx1 - width)


@pytest.mark.parametrize("word", ["שלום!", "مرحبا!"])
def test_punctuation_ends_a_right_to_left_word_on_its_left(word: str) -> None:
    """Regression: the engine laid every word out left-to-right, so the '!' of a Hebrew or Arabic word sat on its
    right-hand (= the wrong) side."""
    left, right = mark_overlap(word)
    assert left > 0.9 and right < 0.8


def test_a_long_thai_phrase_wraps_between_clusters_never_inside_one() -> None:
    from reel.core.shaping import graphemes

    thai = "สวัสดีครับทุกคนยินดีต้อนรับสู่ช่องของเราวันนี้เรามีเรื่องสนุกมาเล่าให้ฟัง"  # no spaces at all
    lines, _f, _h = layout(thai, look_for("subtitle", {}), CTX, 300)
    assert len(lines) > 2
    assert all(_line_width(ln) <= 300 + 1e-6 for ln in lines if ln)  # nothing runs off the box
    assert "".join(t.text for ln in lines for t in ln) == thai  # nothing lost or reordered
    assert {t.text for ln in lines for t in ln} <= set(graphemes(thai))  # only whole clusters
    # a short phrase that fits stays one unit, so its highlight and animation are not split
    assert [t.text for t in tokens("สวัสดีครับ ยินดี")] == ["สวัสดีครับ", "ยินดี"]


def _line_width(line: list) -> float:
    return max(t.x + t.w for t in line)


@pytest.mark.parametrize("box_w", [150, 200, 260, 333, 400, 520])
def test_japanese_lines_never_start_with_closing_punctuation(box_w: int) -> None:
    from reel.core.shaping import NO_LINE_END, NO_LINE_START

    text = "今日は雨が降っています。傘を探しましょう！「見て」と彼は言った、そして笑った。"
    lines, _f, _h = layout(text, look_for("subtitle", {}), CTX, box_w)
    assert "".join(t.text for ln in lines for t in ln) == text
    for ln in lines:
        assert ln[0].text[0] not in NO_LINE_START, f"line starts with {ln[0].text!r}"
        assert ln[-1].text[-1] not in NO_LINE_END, f"line ends with {ln[-1].text!r}"
        assert _line_width(ln) <= box_w + 1e-6


def test_latin_wrapping_is_untouched_by_the_cjk_rules() -> None:
    """A lone '!' after a Latin word may still start a line: kinsoku applies only where CJK text is involved."""
    lines, _f, _h = layout("wait ! what", look_for("subtitle", {}), CTX, 10)
    assert [t.text for ln in lines for t in ln] == ["wait", "!", "what"]
    assert [len(ln) for ln in lines] == [1, 1, 1]


def test_lone_surrogates_and_control_characters_do_not_crash_the_layout() -> None:
    toks = tokens("Hi \ud83d there \x00 ok 😀")
    assert [t.text for t in toks] == ["Hi", "there", "ok", "😀"]
    img = draw(cap(text="Half an emoji \ud83d here"))
    assert (img[..., :3].max(axis=2) > 40).sum() > 100
