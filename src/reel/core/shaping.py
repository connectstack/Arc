"""Shaped text for captions: Indic and Arabic scripts, CJK and emoji.

The caption fonts (Impact, Arial Black, Marker Felt ...) are Latin display faces.  Drawing a Devanagari
word with ``canvas.drawString`` shows empty boxes (no glyphs) and, even with a fallback font, loses the
shaping that makes the script readable (conjuncts, vowel signs, joining).  Skia's *Paragraph* engine
(HarfBuzz + ICU, with system font fallback) does both, so a word that the plain path cannot draw is laid
out and painted through it instead.  Latin words never come here, so Latin captions are unchanged.

A :class:`ShapedWord` is one word: it knows its advance width (for line wrapping and plates) and paints
itself with a fill, a stroke or a blurred shadow, like the plain path does.  Paragraphs are cached per
(word, size, paint) because captions repaint the same words every frame.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Any

import skia
from skia import textlayout as tl

from reel.core.text import clean_text

_state: dict[str, Any] = {}


def _engine() -> tuple[Any, Any]:
    """The font collection and ICU unicode support (created on first use, once per process)."""
    if not _state:
        fc = tl.FontCollection()
        fc.setDefaultFontManager(skia.FontMgr.RefDefault())  # system fonts, with per-glyph fallback
        _state["fc"], _state["uni"] = fc, skia.Unicodes.ICU.Make()
    return _state["fc"], _state["uni"]


# ------------------------------------------------------------------------------- script detection
def _needs_complex_layout(ch: str) -> bool:
    o = ord(ch)
    return (
        0x0590
        <= o
        < 0x2000  # Hebrew, Arabic, Indic, Thai, Lao, Tibetan, Myanmar, Georgian, Hangul jamo ...
        or 0x2E80 <= o < 0xA000  # CJK radicals, kana, ideographs
        or 0xA000 <= o < 0xD800  # Yi, Indic extended, Hangul syllables
        or 0xF900 <= o < 0x10000  # compatibility ideographs, presentation forms, fullwidth
        or o >= 0x1F000  # emoji and symbols
        or unicodedata.combining(ch) != 0
        or o in (0x200D, 0xFE0F)  # joiner, emoji variation selector
    )


def needs_shaping(text: str, font: skia.Font) -> bool:
    """True when ``canvas.drawString`` cannot draw ``text`` properly with ``font``.

    ASCII never needs it; a word in a script that needs shaping always does; any other word does only
    if the font lacks one of its glyphs (``ł`` or ``→`` in a face without them).
    """
    text = clean_text(text)
    if text.isascii():
        return False
    if any(_needs_complex_layout(c) for c in text):
        return True
    return 0 in font.textToGlyphs(text)


def is_cjk(ch: str) -> bool:
    """Ideographs, kana, Hangul and CJK punctuation: written without spaces, so wrapped per character."""
    o = ord(ch)
    return (
        0x3000 <= o <= 0x303F
        or 0x3040 <= o <= 0x30FF
        or 0x3400 <= o <= 0x4DBF
        or 0x4E00 <= o <= 0x9FFF
        or 0xAC00 <= o <= 0xD7AF
        or 0xF900 <= o <= 0xFAFF
        or 0xFF00 <= o <= 0xFFEF
        or 0x20000 <= o <= 0x2FFFF
    )


def is_rtl(text: str) -> bool:
    """True when the strong characters of ``text`` are mostly right-to-left (Arabic, Hebrew, ...)."""
    strong = [unicodedata.bidirectional(c) for c in text]
    right = sum(1 for b in strong if b in ("R", "AL"))
    left = sum(1 for b in strong if b == "L")
    return right > left


def is_unspaced(ch: str) -> bool:
    """Thai, Lao, Khmer and Myanmar: written without spaces between words, so a too-long run wraps per cluster."""
    o = ord(ch)
    return (
        0x0E00 <= o <= 0x0EFF  # Thai, Lao
        or 0x1000 <= o <= 0x109F  # Myanmar
        or 0x1780 <= o <= 0x17FF  # Khmer
        or 0x19E0 <= o <= 0x19FF  # Khmer symbols
        or 0xA9E0 <= o <= 0xA9FF  # Myanmar extended-B
        or 0xAA60 <= o <= 0xAA7F  # Myanmar extended-A
    )


# a Thai/Lao vowel is written *before* the consonant it follows in speech: it must stay with the next character
_PREFIX_VOWELS = frozenset("\u0e40\u0e41\u0e42\u0e43\u0e44\u0ec0\u0ec1\u0ec2\u0ec3\u0ec4")
_SARA_AM = frozenset(
    "\u0e33\u0eb3"
)  # a letter (not a mark) that nevertheless belongs to the previous consonant
_NOT_JOINING = frozenset(
    "\u103a\u0e3a"
)  # Myanmar asat and Thai phinthu: "killers", they do not join the next letter


def _joins_previous(prev: str, ch: str, flag_run: int) -> bool:
    o = ord(ch)
    return (
        unicodedata.category(ch) in ("Mn", "Mc", "Me")  # vowel signs, tone marks, accents, viramas
        or o in (0x200C, 0x200D)  # joiners
        or prev == "\u200d"  # whatever follows a ZWJ is part of the sequence (emoji, conjuncts)
        or (
            unicodedata.combining(prev) == 9 and prev not in _NOT_JOINING
        )  # virama: the next consonant is a conjunct
        or ch in _SARA_AM
        or prev in _PREFIX_VOWELS
        or 0x1F3FB <= o <= 0x1F3FF  # skin tone
        or 0xE0020 <= o <= 0xE007F  # emoji tag sequences (subdivision flags)
        or 0x1160 <= o <= 0x11FF  # Hangul vowel / final jamo
        or (0x1F1E6 <= o <= 0x1F1FF and flag_run % 2 == 1)  # second regional indicator of a flag
    )


def graphemes(text: str) -> list[str]:
    """``text`` split into user-perceived characters: a base with its marks, conjuncts, emoji sequences, flags.

    An approximation of Unicode's extended grapheme clusters that needs no extra library; it is what keeps a line break
    from landing between a Devanagari consonant and its vowel sign, or in the middle of a Thai syllable or a family emoji.
    """
    out: list[str] = []
    prev = ""
    flag_run = 0
    for ch in text:
        if out and _joins_previous(prev, ch, flag_run):
            out[-1] += ch
        else:
            out.append(ch)
        flag_run = flag_run + 1 if 0x1F1E6 <= ord(ch) <= 0x1F1FF else 0
        prev = ch
    return out


#: closing punctuation, small kana and the prolonged sound mark never start a line; opening brackets never end one
NO_LINE_START = frozenset(
    "、。，．・：；！？）］｝〕〉》」』】〙〗〟’”〜～ー々ゝゞヽヾ"
    "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ…‥％"
    ")]},.;:!?%"
)
NO_LINE_END = frozenset("（［｛〔〈《「『【〘〖〝‘“([{")


def break_allowed(before: str, after: str) -> bool:
    """False when a line may not break between the text ``before`` and the text ``after`` (CJK line-start/line-end rules)."""
    return after[:1] not in NO_LINE_START and before[-1:] not in NO_LINE_END


# ------------------------------------------------------------------------------- shaped words
_RLE, _PDF = "\u202b", "\u202c"  # right-to-left embedding / pop directional formatting


@lru_cache(maxsize=4096)
def _paragraph(
    text: str, size: float, family: str, bold: bool, color: int, stroke: float, blur: float
) -> Any:
    fc, uni = _engine()
    paint = skia.Paint(AntiAlias=True, Color=color)
    if stroke > 0:
        paint.setStyle(skia.Paint.kStroke_Style)
        paint.setStrokeWidth(stroke)
        paint.setStrokeJoin(skia.Paint.kRound_Join)
    if blur > 0:
        paint.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    style = tl.TextStyle()
    style.setFontFamilies([family])
    style.setFontSize(size)
    style.setFontStyle(skia.FontStyle.Bold() if bold else skia.FontStyle.Normal())
    style.setForegroundPaint(paint)
    pstyle = tl.ParagraphStyle()
    pstyle.setTextStyle(style)
    builder = tl.ParagraphBuilder.make(pstyle, fc, uni)
    builder.pushStyle(style)
    # The engine's base direction is left-to-right, which puts the "!" of a Hebrew or Arabic word on its right-hand
    # (= wrong) side; an RTL embedding makes it the word's own end.  The marks are invisible and add no width.
    builder.addText(f"{_RLE}{text}{_PDF}" if is_rtl(text) else text)
    builder.pop()
    para = builder.Build()
    para.layout(1.0e6)  # one line: wrapping is the caption layout's job
    return para


class ShapedWord:
    """One word laid out by the Paragraph engine: ``width`` to wrap with, ``paint`` to draw."""

    __slots__ = ("baseline", "bold", "family", "size", "text", "width")

    def __init__(self, text: str, size: float, family: str, bold: bool = True) -> None:
        self.text, self.size, self.family, self.bold = clean_text(text), float(size), family, bold
        para = _paragraph(self.text, self.size, family, bold, 0xFFFFFFFF, 0.0, 0.0)
        self.width = float(para.MaxIntrinsicWidth)
        self.baseline = float(para.AlphabeticBaseline)

    def paint(
        self,
        canvas: skia.Canvas,
        x: float,
        baseline_y: float,
        color: int,
        *,
        stroke: float = 0.0,
        blur: float = 0.0,
    ) -> None:
        """Draw with its baseline at ``baseline_y``: a fill, or (``stroke`` > 0) an outline, or (``blur`` > 0) a shadow."""
        para = _paragraph(
            self.text,
            self.size,
            self.family,
            self.bold,
            int(color),
            round(stroke, 2),
            round(blur, 2),
        )
        para.paint(canvas, x, baseline_y - self.baseline)
