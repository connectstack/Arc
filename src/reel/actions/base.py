"""Action registration + shared helpers.

An *action* is a function ``fn(ctx, params) -> Clip``.  It describes motion as
per-channel curves and knows nothing about how a style draws the result::

    @register_action("wave", params_schema=WaveParams, category="gesture",
                     summary="Raise the front hand and wave")
    def wave(ctx: ActionContext, p: WaveParams) -> Clip:
        c = ctx.clip()
        c.key("arm_r_sh", [(0, 5), (0.3, 150, "overshoot"), (ctx.duration, 5)])
        return c

See docs/adding-an-action.md for the five-minute walkthrough.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict

from reel.core.animation import ActionContext, Clip, Keyframes, Oscillator
from reel.core.catalog import CATALOG, Catalog

ActionFn = Callable[[ActionContext, Any], Clip]


class NoParams(BaseModel):
    """Params model for actions that take none."""

    model_config = ConfigDict(extra="forbid")


class ParamsBase(BaseModel):
    """Convenience base: strict (typos are errors) and documented via Field(description=...)."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ActionDef:
    name: str
    fn: ActionFn
    params_model: type[BaseModel] | None
    summary: str
    category: str
    moves_root: bool
    min_duration: float
    default_duration: float
    tags: tuple[str, ...] = ()
    check: Callable[[dict[str, Any], Any], list[str]] | None = None

    def parse(self, raw: dict[str, Any] | None) -> BaseModel:
        model = self.params_model or NoParams
        return model.model_validate(raw or {})

    def __call__(self, ctx: ActionContext, params: Any) -> Clip:
        return self.fn(ctx, params)


def register_action(
    name: str,
    *,
    params_schema: type[BaseModel] | None = None,
    summary: str = "",
    category: str = "misc",
    moves_root: bool = False,
    min_duration: float = 0.2,
    default_duration: float = 1.5,
    tags: tuple[str, ...] = (),
    check: Callable[[dict[str, Any], Any], list[str]] | None = None,
    catalog: Catalog | None = None,
) -> Callable[[ActionFn], ActionFn]:
    """Decorator that registers an action in the catalog (default: the global one)."""

    def wrap(fn: ActionFn) -> ActionFn:
        doc = (fn.__doc__ or "").strip().splitlines()
        defn = ActionDef(
            name=name,
            fn=fn,
            params_model=params_schema,
            summary=summary or (doc[0] if doc else ""),
            category=category,
            moves_root=moves_root,
            min_duration=min_duration,
            default_duration=default_duration,
            tags=tags,
            check=check,
        )
        (catalog or CATALOG).actions.register(name, defn)
        return fn

    return wrap


# ------------------------------------------------------------------------------- helpers
_SIBILANT_ES = re.compile(r"(?:s|x|z|ch|sh|c|g)es$")  # boxes, wishes, places, changes, houses


def _non_latin_syllables(word: str) -> int:
    """Rough syllables of a word with no Latin letters: one per ideograph, kana or Hangul block; in Indic scripts one per
    consonant or vowel that is not joined to the next by a virama; about 0.45 per letter elsewhere (Cyrillic, Arabic ...)."""
    from reel.core.shaping import is_cjk

    chars = list(unicodedata.normalize("NFC", word))
    n = 0.0
    for i, c in enumerate(chars):
        if not unicodedata.category(c).startswith("L"):
            continue
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if is_cjk(c):
            n += 1.0
        elif 0x0900 <= ord(c) <= 0x0DFF:  # Devanagari ... Malayalam
            n += 0.0 if nxt and unicodedata.combining(nxt) == 9 else 1.0
        else:
            n += 0.45
    return max(1, round(n)) if n else 0


def count_syllables(word: str) -> int:
    """Syllables of a word (English: vowel groups minus the usual silent endings; other scripts: a rough count)."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return _non_latin_syllables(word)
    n = len(re.findall(r"[aeiouy]+", w))
    silent_ed = w.endswith("ed") and not w.endswith(("ted", "ded"))  # walked, loved (not wanted)
    silent_es = w.endswith("es") and not _SIBILANT_ES.search(w)  # makes, times (not boxes, wishes)
    silent_e = w.endswith("e") and not w.endswith(("le", "ee", "ie"))  # make, name
    if n > 1 and (silent_ed or silent_es or silent_e):
        n -= 1
    return max(1, n)


def blink_times(rng: np.random.Generator, duration: float) -> list[float]:
    times: list[float] = []
    t = float(rng.uniform(0.6, 2.2))
    while t < duration:
        times.append(t)
        t += float(rng.uniform(2.0, 5.2))
        if rng.random() < 0.12 and t < duration:  # occasional double blink
            times.append(t - 1.9 + 0.22)
    return sorted(times)


def idle_baseline(ctx: ActionContext) -> Clip:
    """Always-on life: breathing, weight shifts, gaze wander and blinks (additive, seeded)."""
    D = ctx.duration
    rng = np.random.default_rng([int(ctx.rng.integers(0, 2**31)), 7])
    c = Clip(D, blend_in=0.0, blend_out=0.0)

    def osc(ch: str, amp: float, freq: float, harm: tuple[tuple[float, float], ...] = ()) -> None:
        c.curve(
            ch,
            Oscillator(
                amp,
                freq,
                -10.0,
                D + 10.0,
                float(rng.uniform(0, 2 * math.pi)),
                fade=0.01,
                harmonics=harm,
            ),
            mode="add",
        )

    osc("breath", 0.7, 0.27)
    osc("head_dy", 1.4, 0.27)
    osc("hip_dx", 3.5, 0.11, ((2.3, 0.4),))
    osc("torso_lean", 1.1, 0.17)
    osc("head_tilt", 1.6, 0.13, ((1.9, 0.5),))
    osc("arm_r_sh", 1.8, 0.19)
    osc("arm_l_sh", 1.8, 0.16)
    osc("arm_r_el", 2.0, 0.23)
    osc("arm_l_el", 2.0, 0.21)
    osc("eye_dx", 0.10, 0.23, ((1.7, 0.7),))
    osc("eye_dy", 0.05, 0.31)

    pts: list[tuple[float, float, str]] = [(0.0, 0.0, "linear")]
    for bt in blink_times(rng, D):
        pts += [
            (bt - 0.001, 0.0, "linear"),
            (bt + 0.055, 1.0, "ease_out"),
            (bt + 0.17, 0.0, "ease_in"),
        ]
    c.curve("blink", Keyframes.build(pts, "linear"))
    return c


def speech_envelope(
    ctx: ActionContext,
    text: str | None,
    words: list[tuple[float, float, str]] | None,
    energy: float,
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Mouth-open envelope for speech.  Returns (times, mouth_open, stress_times).

    With word timings (from TTS alignment) syllables are spread across each word; with only
    text they are spread at ~4.4 syllables/s with small pauses at punctuation; with neither the
    mouth just babbles at a natural cadence.  Deterministic from the action's rng.
    """
    D = ctx.duration
    rng = ctx.rng
    syl: list[tuple[float, float]] = []  # (center, half_width)
    if words:
        for w0, w1, w in words:
            n = count_syllables(w) or 1
            span = max(w1 - w0, 0.05)
            for k in range(n):
                syl.append((w0 + span * (k + 0.5) / n, span / n * 0.5))
    else:
        toks = (text or "").split() or ["la"] * max(1, int(D * 2.2))
        t = 0.08
        rate = 4.4
        for tok in toks:
            n = count_syllables(tok) or 1
            for _ in range(n):
                hw = 0.5 / rate
                syl.append((t + hw, hw))
                t += 1.0 / rate
            if tok[-1:] in ".,!?;:":
                t += 0.22 if tok[-1:] in ".!?" else 0.12
        if syl and t > D:  # squeeze so the line always fits the action window
            shrink = (D - 0.1) / t
            syl = [(c * shrink, hw * shrink) for c, hw in syl]
    dt = 1.0 / (ctx.fps * 2)
    ts = np.arange(0, D + dt / 2, dt)
    env = np.zeros_like(ts)
    stress: list[float] = []
    for i, (c, hw) in enumerate(syl):
        amp = float(rng.uniform(0.38, 0.95)) * (0.7 + 0.5 * energy)
        shape = np.clip(1.0 - ((ts - c) / max(hw * 1.15, 0.03)) ** 2, 0, None)
        env = np.maximum(env, amp * shape)
        if i % 3 == 0:
            stress.append(c)
    return ts, np.clip(env, 0, 1), stress
