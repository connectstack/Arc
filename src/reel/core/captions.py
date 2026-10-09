"""Captions: presets, layout and kinetic typography.

A *preset* (`subtitle`, `title`, `shout`) fixes what a caption does - where it sits, how big it
is and how it animates in and out.  A style pack supplies the *look* (font, colours, outline,
backing plate) through a `CaptionLook`.  Layout respects the reel safe area so nothing hides
under platform UI.
"""

from __future__ import annotations

import math
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import skia

from reel.core import fonts
from reel.core.catalog import CATALOG
from reel.core.easing import ease
from reel.core.geometry import clamp, skcolor
from reel.core.shaping import (
    ShapedWord,
    break_allowed,
    graphemes,
    is_cjk,
    is_rtl,
    is_unspaced,
    needs_shaping,
)
from reel.core.text import clean_text


@dataclass(frozen=True)
class CaptionPreset:
    name: str
    summary: str
    size: float  # design px (1080-wide frame)
    anchor_y: float  # block centre as a fraction of frame height
    anim: str  # pop_words | drop_letters | slam | fade_up
    uppercase: bool = False
    max_width: float = 1.0  # fraction of the safe width
    in_dur: float = 0.18
    out_dur: float = 0.22
    line_spacing: float = 1.08
    letter_spacing: float = 0.0
    stagger: float = 0.06  # seconds between words/letters


def register_caption_style(preset: CaptionPreset) -> CaptionPreset:
    CATALOG.caption_styles.register(preset.name, preset, summary=preset.summary)
    return preset


SUBTITLE = register_caption_style(
    CaptionPreset(
        "subtitle",
        "Reading text on the ground line, inside the bottom safe margin; words pop in, the spoken word is highlighted",
        size=64,
        anchor_y=0.73,
        anim="pop_words",
        max_width=0.94,
        stagger=0.055,
    )
)
TITLE = register_caption_style(
    CaptionPreset(
        "title",
        "Large headline in the upper third; letters drop in with a bounce",
        size=118,
        anchor_y=0.2,
        anim="drop_letters",
        uppercase=True,
        max_width=0.94,
        in_dur=0.5,
        out_dur=0.3,
        line_spacing=1.0,
        stagger=0.03,
    )
)
SHOUT = register_caption_style(
    CaptionPreset(
        "shout",
        "Huge slam-in text with a camera-shake feel, for punchlines; sits above the characters' heads",
        size=170,
        anchor_y=0.30,
        anim="slam",
        uppercase=True,
        max_width=0.96,
        in_dur=0.16,
        out_dur=0.14,
        line_spacing=0.98,
        stagger=0.0,
    )
)


@dataclass(frozen=True)
class CaptionLook:
    """How a style draws a preset."""

    preset: CaptionPreset
    fonts: tuple[str, ...] = fonts.SANS_BOLD
    fill: str = "#ffffff"
    stroke: str | None = "#14141c"
    stroke_w: float = 0.12  # fraction of font size
    shadow: tuple[float, float, float, str] | None = (
        0.0,
        5.0,
        8.0,
        "#00000080",
    )  # dx, dy, blur, colour
    highlight: str | None = "#ffd23f"
    plate: tuple[str, float, float] | None = None  # (colour, alpha, corner radius as font fraction)
    plate_pad: tuple[float, float] = (0.5, 0.22)  # horizontal/vertical padding in font-size units
    plate_jitter: float = 0.0  # torn / hand-cut plate edges: deviation in design px
    plate_shadow: bool = False
    plate_rotate: float = 0.0  # degrees, tilt of each plate
    rotate: float = 0.0  # degrees, whole block
    size_scale: float = 1.0
    italic: bool = False


def look_for(name: str, overrides: dict[str, Any]) -> CaptionLook:
    """Preset ``name`` merged with a style's `caption_look` overrides for that preset."""
    preset: CaptionPreset = CATALOG.caption_styles.get(name)
    base = overrides.get("*", {})
    mine = overrides.get(name, {})
    kw = {**base, **mine}
    pk = {
        k: kw.pop(k)
        for k in (
            "size",
            "anchor_y",
            "anim",
            "uppercase",
            "line_spacing",
            "letter_spacing",
            "stagger",
        )
        if k in kw
    }
    if pk:
        preset = replace(preset, **pk)
    return CaptionLook(preset=preset, **kw)


@dataclass
class CaptionRender:
    """One caption at one moment."""

    text: str
    style: str
    t_in: float  # seconds since it appeared
    t_out: float  # seconds until it disappears
    duration: float
    anchor: str = "auto"
    word_windows: list[tuple[float, float]] | None = None  # per word, seconds since appearing
    seed: int = 0
    speaker: str | None = None


def estimate_word_windows(
    text: str, duration: float, lead: float = 0.05, tail: float = 0.12
) -> list[tuple[float, float]]:
    """Spread words over the caption by character count (used when no TTS timings exist)."""
    words = text.split()
    if not words:
        return []
    weights = np.array(
        [max(2, len(re.sub(r"\W", "", w))) + (2 if w[-1:] in ".,!?;:" else 0) for w in words],
        dtype=float,
    )
    span = max(0.2, duration - lead - tail)
    edges = np.concatenate([[0.0], np.cumsum(weights)]) / weights.sum() * span + lead
    return [(float(edges[i]), float(edges[i + 1])) for i in range(len(words))]


# ------------------------------------------------------------------------------- layout
@dataclass
class _Token:
    text: str
    x: float = 0.0  # left of the token in block space
    y: float = 0.0  # baseline
    w: float = 0.0
    word: int = 0  # index of the whitespace word this token belongs to (CJK words give one token per character)
    index: int = 0  # order within the caption (words or letters)
    shaped: ShapedWord | None = (
        None  # set when the plain font cannot draw it (Hindi, Arabic, CJK, emoji ...)
    )


def _typeface(look: CaptionLook) -> skia.Typeface:
    return fonts.typeface(look.fonts, bold=True, italic=look.italic)


def _pieces(txt: str) -> list[tuple[str, int, bool]]:
    """Whitespace words as ``(text, word index, joined to the previous piece)``; a CJK word is broken into one piece per
    character (it has no spaces, so it can only wrap between characters)."""
    out: list[tuple[str, int, bool]] = []
    for wi, word in enumerate(txt.split()):
        if not any(is_cjk(c) for c in word):
            out.append((word, wi, False))
            continue
        run = ""
        first = True
        for g in graphemes(word):  # a mark stays with its base
            if is_cjk(g[0]):
                if run:
                    out.append((run, wi, not first))
                    run, first = "", False
                out.append((g, wi, not first))
                first = False
            else:
                run += g
        if run:
            out.append((run, wi, not first))
    return out


def _split_unspaced(piece: str) -> list[str]:
    """A run of Thai / Lao / Khmer / Myanmar broken into clusters (the finest place a line may wrap), Latin runs kept whole."""
    out: list[str] = []
    run = ""
    for g in graphemes(piece):
        if is_unspaced(g[0]):
            if run:
                out.append(run)
                run = ""
            out.append(g)
        else:
            run += g
    if run:
        out.append(run)
    return out


def _may_break(before: str, after: str) -> bool:
    """Line-start / line-end rules (kinsoku) apply where CJK text is involved; Latin captions wrap exactly as before."""
    if not (is_cjk(before[-1:] or " ") or is_cjk(after[:1] or " ")):
        return True
    return break_allowed(before, after)


def _break_line(lines: list[list[_Token]], nxt: str) -> float:
    """Start a new line before a piece reading ``nxt``; returns the width of what the new line already holds.

    Closing punctuation may not start a line and an opening bracket may not end one, so the last token(s) of the
    old line move down with the piece when the break would be illegal.
    """
    old = lines[-1]
    keep = len(old)
    while keep > 1 and not _may_break(old[keep - 1].text, nxt):
        keep -= 1
        nxt = old[keep].text
    carried = old[keep:]
    del old[keep:]
    if carried:
        shift = carried[0].x
        for t in carried:
            t.x -= shift
    lines.append(carried)
    return max((t.x + t.w for t in carried), default=0.0)


def _line_w(line: list[_Token]) -> float:
    return max(t.x + t.w for t in line)


def layout(
    text: str, look: CaptionLook, ctx: Any, box_w: float
) -> tuple[list[list[_Token]], skia.Font, float]:
    """Wrap ``text`` into lines (design px). Returns (lines of word tokens, font, line height)."""
    p = look.preset
    size = p.size * look.size_scale
    typeface = _typeface(look)
    font = skia.Font(typeface, size)
    font.setSubpixel(True)
    text = clean_text(text)
    txt = text.upper() if p.uppercase else text
    space = font.measureText(" ") + p.letter_spacing * size
    lines: list[list[_Token]] = [[]]
    cur = 0.0
    queue = deque(_pieces(txt))
    while queue:
        text_piece, wi, joined = queue.popleft()
        shaped = (
            ShapedWord(text_piece, size, typeface.getFamilyName())
            if needs_shaping(text_piece, font)
            else None
        )
        ww = (
            shaped.width
            if shaped
            else font.measureText(text_piece)
            + p.letter_spacing * size * max(0, len(text_piece) - 1)
        )
        if ww > box_w and any(is_unspaced(c) for c in text_piece):
            cells = _split_unspaced(text_piece)  # wider than a whole line: wrap inside it
            if len(cells) > 1:
                queue.extendleft(
                    reversed([(cells[0], wi, joined), *((c, wi, True) for c in cells[1:])])
                )
                continue
        gap = space if lines[-1] and not joined else 0.0
        if lines[-1] and cur + gap + ww > box_w:
            cur = _break_line(lines, text_piece)
            gap = space if lines[-1] and not joined else 0.0
        tok = _Token(text_piece, cur + gap, 0.0, ww, wi, shaped=shaped)
        lines[-1].append(tok)
        cur = tok.x + ww
    if is_rtl(
        txt
    ):  # words keep their reading order; each line is mirrored so the first word is on the right
        for ln in lines:
            if ln:
                lw = _line_w(ln)
                for tok in ln:
                    tok.x = lw - tok.x - tok.w
    return lines, font, size * p.line_spacing


def _block_size(lines: list[list[_Token]], line_h: float) -> tuple[float, float]:
    w = max(_line_w(ln) for ln in lines if ln) if any(lines) else 0.0
    return w, line_h * len(lines)


# ------------------------------------------------------------------------------- animation
def _word_anim(
    look: CaptionLook, c: CaptionRender, idx: int, n: int
) -> tuple[float, float, float, float, float]:
    """(alpha, dy, scale, rot_deg, extra_dx) of word/letter ``idx`` of ``n`` at this moment."""
    p = look.preset
    t = c.t_in - idx * p.stagger
    if p.anim == "slam":
        u = clamp(c.t_in / max(p.in_dur, 1e-3), 0, 1)
        k = 1.0 + 1.5 * (1 - float(ease("expo_out", u)))
        dx = 9.0 * math.sin(c.t_in * 62) * max(0.0, 1 - c.t_in / 0.35)
        dy = 7.0 * math.cos(c.t_in * 55) * max(0.0, 1 - c.t_in / 0.35)
        rot = -4.5 * (1 - float(ease("overshoot", u))) - 1.5
        a = clamp(u * 3.0, 0, 1)
        out = clamp(c.t_out / max(p.out_dur, 1e-3), 0, 1)
        return a * out, dy, k * (0.94 + 0.06 * out), rot, dx
    if p.anim == "drop_letters":
        u = clamp(t / max(p.in_dur, 1e-3), 0, 1)
        e = float(ease("bounce", u))
        dy = -95.0 * (1 - e)
        a = clamp(t / 0.08, 0, 1)
        out = clamp(c.t_out / max(p.out_dur, 1e-3), 0, 1)
        return a * out, dy - 14 * (1 - out), 1.0 + 0.0 * e, 0.0, 0.0
    if p.anim == "fade_up":
        u = clamp(t / max(p.in_dur, 1e-3), 0, 1)
        out = clamp(c.t_out / max(p.out_dur, 1e-3), 0, 1)
        return u * out, 26.0 * (1 - float(ease("ease_out", u))), 1.0, 0.0, 0.0
    # pop_words
    u = clamp(t / max(p.in_dur, 1e-3), 0, 1)
    sc = 0.55 + 0.45 * float(ease("overshoot", u))
    out = clamp(c.t_out / max(p.out_dur, 1e-3), 0, 1)
    return clamp(t / 0.09, 0, 1) * out, 14.0 * (1 - float(ease("ease_out", u))), sc, 0.0, 0.0


# ------------------------------------------------------------------------------- drawing
def draw_caption_default(
    canvas: skia.Canvas, cap: CaptionRender, look: CaptionLook, ctx: Any
) -> None:
    """Draw a caption in screen space (canvas is in device pixels; we work in design px)."""
    p = look.preset
    s = ctx.scale
    W, H = ctx.size[0] / s, ctx.size[1] / s
    top, bottom, left, right = ctx.safe
    safe_w = W * (1 - left - right)
    box_w = safe_w * p.max_width
    lines, font, line_h = layout(cap.text, look, ctx, box_w)
    if not any(lines):
        return
    bw, bh = _block_size(lines, line_h)
    # block centre, clamped inside the safe area
    ay = {"top": 0.17, "center": 0.5, "bottom": 0.73}.get(cap.anchor, p.anchor_y)
    cy = clamp(ay * H, top * H + bh / 2, (1 - bottom) * H - bh / 2)
    cx = W / 2
    cx - bw / 2
    y_origin = cy - bh / 2
    size = p.size * look.size_scale

    n_words = sum(len(ln) for ln in lines)
    windows = cap.word_windows or estimate_word_windows(cap.text, cap.duration)
    letters = p.anim == "drop_letters"

    canvas.save()
    canvas.scale(s, s)
    if look.rotate:
        canvas.rotate(look.rotate, cx, cy)

    # backing plates (one pill per line) ---------------------------------------------------------
    if look.plate is not None:
        col, pa, rad = look.plate
        outa = clamp(cap.t_out / max(p.out_dur, 1e-3), 0, 1)
        ina = clamp(cap.t_in / 0.12, 0, 1)
        for li, ln in enumerate(lines):
            if not ln:
                continue
            lw = _line_w(ln)
            lx = cx - lw / 2
            rect = skia.Rect(
                lx - look.plate_pad[0] * size,
                y_origin + li * line_h + line_h * 0.04 - look.plate_pad[1] * size * 0.2,
                lx + lw + look.plate_pad[0] * size,
                y_origin + (li + 1) * line_h + look.plate_pad[1] * size * 0.55,
            )
            plate = skia.Path()
            plate.addRoundRect(rect, rad * size, rad * size)
            canvas.save()
            if look.plate_rotate:
                canvas.rotate(
                    look.plate_rotate * (1 if li % 2 == 0 else -0.6), rect.centerX(), rect.centerY()
                )
            if look.plate_shadow:
                sp = skia.Paint(AntiAlias=True, Color=skcolor("#000000", 0.30 * pa * ina * outa))
                sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 5.0))
                canvas.translate(4, 7)
                canvas.drawPath(plate, sp)
                canvas.translate(-4, -7)
            paint = skia.Paint(AntiAlias=True, Color=skcolor(col, pa * ina * outa))
            if look.plate_jitter:
                paint.setPathEffect(skia.DiscretePathEffect.Make(16.0, look.plate_jitter, 7 + li))
            canvas.drawPath(plate, paint)
            canvas.restore()

    idx = 0
    for li, ln in enumerate(lines):
        if not ln:
            continue
        lw = _line_w(ln)
        line_x = cx - lw / 2
        base_y = y_origin + li * line_h + size * 0.82
        for tok in ln:
            wi = tok.word
            # word highlight (subtitle): the word currently being spoken
            active = False
            if look.highlight and p.anim == "pop_words" and wi < len(windows):
                w0, w1 = windows[wi]
                active = w0 <= cap.t_in <= w1
            if letters and tok.shaped is None:
                advance = 0.0
                for ch in tok.text:
                    chw = font.measureText(ch)
                    _draw_token(
                        canvas,
                        ch,
                        line_x + tok.x + advance,
                        base_y,
                        chw,
                        font,
                        look,
                        cap,
                        idx,
                        max(n_words, sum(len(t.text) for t in ln)),
                        size,
                        False,
                    )
                    advance += chw + p.letter_spacing * size
                    idx += 1
            else:
                _draw_token(
                    canvas,
                    tok.text,
                    line_x + tok.x,
                    base_y,
                    tok.w,
                    font,
                    look,
                    cap,
                    idx,
                    n_words,
                    size,
                    active,
                    tok.shaped,
                )
                idx += 1
    canvas.restore()


def _draw_token(
    canvas: skia.Canvas,
    text: str,
    x: float,
    y: float,
    w: float,
    font: skia.Font,
    look: CaptionLook,
    cap: CaptionRender,
    idx: int,
    n: int,
    size: float,
    active: bool,
    shaped: ShapedWord | None = None,
) -> None:
    alpha, dy, sc, rot, dx = _word_anim(look, cap, idx, n)
    if alpha <= 0.002:
        return
    if active:
        sc *= 1.07
    canvas.save()
    cx, cy = x + w / 2, y - size * 0.32
    canvas.translate(dx, dy)
    if rot:
        canvas.rotate(rot, cx, cy)
    if sc != 1.0:
        canvas.translate(cx, cy)
        canvas.scale(sc, sc)
        canvas.translate(-cx, -cy)
    fill = look.highlight if (active and look.highlight) else look.fill
    if look.shadow:
        sdx, sdy, blur, scol = look.shadow
        if shaped is not None:
            shaped.paint(canvas, x + sdx, y + sdy, skcolor(scol, alpha), blur=blur)
        else:
            sp = skia.Paint(AntiAlias=True, Color=skcolor(scol, alpha))
            if blur > 0:
                sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
            canvas.drawString(text, x + sdx, y + sdy, font, sp)
    if look.stroke and look.stroke_w > 0:
        if shaped is not None:
            shaped.paint(canvas, x, y, skcolor(look.stroke, alpha), stroke=look.stroke_w * size)
        else:
            stp = skia.Paint(AntiAlias=True, Color=skcolor(look.stroke, alpha))
            stp.setStyle(skia.Paint.kStroke_Style)
            stp.setStrokeWidth(look.stroke_w * size)
            stp.setStrokeJoin(skia.Paint.kRound_Join)
            canvas.drawString(text, x, y, font, stp)
    if cap.style == "shout":  # white-hot flash settling to the fill colour
        k = clamp(cap.t_in / 0.22, 0, 1)
        from reel.core.geometry import mix

        fill = mix("#ffffff", fill, k)
    if shaped is not None:
        shaped.paint(canvas, x, y, skcolor(fill, alpha))
    else:
        canvas.drawString(text, x, y, font, skia.Paint(AntiAlias=True, Color=skcolor(fill, alpha)))
    canvas.restore()


CaptionDrawer = Callable[[skia.Canvas, CaptionRender, CaptionLook, Any], None]
