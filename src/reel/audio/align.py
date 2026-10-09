"""Word timing estimation from audio alone (pure numpy, no forced-alignment model).

Given the text and the synthesised/recorded speech, :func:`estimate_word_timings` returns one
``(t0, t1, word)`` per whitespace token, relative to the start of the audio:

1. a 5 ms RMS envelope finds the voiced span (leading/trailing silence is stripped);
2. internal energy dips of at least ~60 ms are candidate word/phrase boundaries;
3. a small dynamic programme matches those dips to word boundaries (by syllable proportion, with a
   bonus for punctuation) so a stop closure inside a word is not mistaken for a pause;
4. words between matched dips are spread proportionally to syllable count (plus a pause weight
   after punctuation), and the remaining boundaries snap to the nearest local energy minimum.

The result is monotonic, never overlapping, and covers the voiced span exactly.
"""

from __future__ import annotations

import math
import re
import unicodedata

import numpy as np

from reel.actions.base import count_syllables

#: pause (in syllable-equivalents, ~0.23 s each) that follows a token ending in this punctuation
_PAUSE_AFTER = (
    ("...", 0.9),
    ("…", 0.9),
    (".", 0.9),
    ("!", 0.9),
    ("?", 0.9),
    (";", 0.6),
    (":", 0.6),
    (",", 0.45),
)
_TRAIL = re.compile(r"[\"'”’)\]}]+$")


def fold_accents(token: str) -> str:
    """ASCII-fold accents so ``count_syllables`` (a-z only) sees the letters.

    Only the marks on *Latin* letters are dropped.  In Devanagari, Thai, Khmer ... the same kind of mark (virama,
    nukta, tone and vowel signs) is part of the word and decides its syllable count: ``क्ष`` is one syllable
    with its virama and two without it.
    """
    out: list[str] = []
    on_latin = False
    for c in unicodedata.normalize("NFKD", token):
        if unicodedata.combining(c):
            if on_latin:
                continue
        else:
            on_latin = c.isascii() or unicodedata.name(c, "").startswith("LATIN")
        out.append(c)
    return "".join(out)


def word_weight(token: str) -> float:
    """Speech weight of a token in syllables (digits ~0.9 syllable each; pure punctuation ~0)."""
    folded = fold_accents(token)
    n = count_syllables(folded)
    if n:
        return float(n)
    digits = sum(c.isdigit() for c in folded)
    if digits:
        return float(max(1, math.ceil(0.9 * digits)))
    return 0.0


def pause_weight(token: str) -> float:
    """Silence expected after ``token`` in syllable-equivalents, from its trailing punctuation."""
    core = _TRAIL.sub("", token)
    for mark, weight in _PAUSE_AFTER:
        if core.endswith(mark):
            return weight
    if token and not any(c.isalnum() for c in token):  # a lone dash or symbol
        return 0.3
    return 0.0


def spread_words(tokens: list[str], t0: float, t1: float) -> list[tuple[float, float, str]]:
    """Distribute ``tokens`` over ``[t0, t1]`` by syllable weight (+ punctuation pauses)."""
    if not tokens:
        return []
    speech = [max(word_weight(t), 0.25) for t in tokens]
    pause = [pause_weight(t) for t in tokens]
    pause[-1] = 0.0  # no pause after the last word: the span ends with it
    total = sum(speech) + sum(pause)
    span = max(t1 - t0, 0.0)
    out: list[tuple[float, float, str]] = []
    cur = t0
    for tok, w, p in zip(tokens, speech, pause):
        a = cur
        b = a + span * w / total
        out.append((a, b, tok))
        cur = b + span * p / total
    return out


# ----------------------------------------------------------------------------- envelope
def _envelope_db(x: np.ndarray, hop: int) -> np.ndarray:
    """Power (dB) of a centred ~20 ms window every ``hop`` samples."""
    n = x.shape[0]
    half = 2 * hop
    cs = np.concatenate([[0.0], np.cumsum(x * x)])
    centres = np.arange(0, n, hop) + hop // 2
    lo = np.clip(centres - half, 0, n)
    hi = np.clip(centres + half, 0, n)
    power = (cs[hi] - cs[lo]) / np.maximum(hi - lo, 1)
    return np.asarray(10.0 * np.log10(power + 1e-12))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal ``True`` runs of ``mask`` as ``[start, end)`` index pairs."""
    if mask.size == 0:
        return []
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def _voiced_span(env: np.ndarray) -> tuple[int, int] | None:
    """First/last voiced frame (inclusive) of the envelope, or None when it is all silence."""
    ref = float(np.percentile(env, 98))
    floor = float(np.percentile(env, 5))
    thr = max(ref - 38.0, floor + 10.0, -80.0)
    voiced = env > thr
    runs = [(a, b) for a, b in _runs(voiced) if b - a >= 2]  # drop 5-10 ms blips
    if not runs:
        return None
    return runs[0][0], runs[-1][1] - 1


# ----------------------------------------------------------------------------- matching
def _match_dips(
    dips: list[tuple[float, float]],
    bounds_syl: np.ndarray,
    punct: list[bool],
    voiced_before: list[float],
    total_voiced: float,
    total_syl: float,
) -> list[tuple[int, int]]:
    """Monotonic assignment of dips -> word boundaries maximising a closeness score.

    ``bounds_syl[k]`` is the cumulative syllable weight at the boundary after word ``k``; a dip
    is described by the voiced time before it.  Returns ``(dip_index, boundary_index)`` pairs.
    """
    n_dip, n_bound = len(dips), len(bounds_syl)
    if n_dip == 0 or n_bound == 0 or total_voiced <= 0:
        return []
    tol = max(1.6, 0.12 * total_syl)
    score = np.zeros((n_dip, n_bound))
    for i, (a, b) in enumerate(dips):
        pos_syl = voiced_before[i] / total_voiced * total_syl
        long_dip = (b - a) >= 0.12
        for k in range(n_bound):
            dist = abs(pos_syl - bounds_syl[k])
            lim = tol * (1.8 if (punct[k] and long_dip) else 1.0)
            if dist <= lim:
                score[i, k] = (1.0 - dist / lim) + (1.0 if (punct[k] and long_dip) else 0.0) + 0.05
    best = np.zeros((n_dip + 1, n_bound + 1))
    for i in range(1, n_dip + 1):
        for k in range(1, n_bound + 1):
            best[i, k] = max(
                best[i - 1, k], best[i, k - 1], best[i - 1, k - 1] + score[i - 1, k - 1]
            )
    pairs: list[tuple[int, int]] = []
    i, k = n_dip, n_bound
    while i > 0 and k > 0:
        if score[i - 1, k - 1] > 0 and best[i, k] == best[i - 1, k - 1] + score[i - 1, k - 1]:
            pairs.append((i - 1, k - 1))
            i -= 1
            k -= 1
        elif best[i, k] == best[i - 1, k]:
            i -= 1
        else:
            k -= 1
    return pairs[::-1]


def voiced_span(samples: np.ndarray, sr: int) -> tuple[float, float] | None:
    """``(start, end)`` seconds of the audible speech (leading/trailing silence excluded), or None."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1 if x.shape[1] <= 8 else 0)
    if x.shape[0] < int(sr * 0.03):
        return None
    hop = max(1, round(sr * 0.005))
    span = _voiced_span(_envelope_db(x, hop))
    if span is None:
        return None
    return span[0] * hop / sr, min(x.shape[0] / sr, (span[1] + 1) * hop / sr)


def estimate_word_timings(
    text: str, samples: np.ndarray, sr: int, *, min_pause: float = 0.06
) -> list[tuple[float, float, str]]:
    """Estimate ``(t0, t1, word)`` seconds (relative to the clip start) for each token of ``text``."""
    tokens = text.split()
    if not tokens:
        return []
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1 if x.shape[1] <= 8 else 0)
    n = x.shape[0]
    dur = n / sr if sr else 0.0
    if n < int(sr * 0.03):
        return spread_words(tokens, 0.0, dur)

    hop = max(1, round(sr * 0.005))
    hop_s = hop / sr
    env = _envelope_db(x, hop)
    span = _voiced_span(env)
    if span is None:
        return spread_words(tokens, 0.0, dur)
    f_first, f_last = span
    t_start = f_first * hop_s
    t_end = min(dur, (f_last + 1) * hop_s)
    if t_end - t_start < 0.02 or len(tokens) == 1:
        return spread_words(tokens, t_start, max(t_end, t_start + 0.02))

    speech_db = float(np.percentile(env[f_first : f_last + 1], 85))
    quiet = np.zeros(env.shape[0], dtype=bool)
    quiet[f_first : f_last + 1] = env[f_first : f_last + 1] < speech_db - 20.0
    need = max(2, math.ceil(min_pause / hop_s))
    dips = [
        (a * hop_s, b * hop_s)
        for a, b in _runs(quiet)
        if b - a >= need and a > f_first and b - 1 < f_last
    ]

    weights = [max(word_weight(t), 0.25) for t in tokens]
    pauses = [pause_weight(t) for t in tokens]
    cum = np.cumsum(weights)
    bounds_syl = cum[:-1]
    punct = [p > 0 for p in pauses[:-1]]
    total_syl = float(cum[-1])
    dip_total = sum(b - a for a, b in dips)
    total_voiced = (t_end - t_start) - dip_total
    voiced_before: list[float] = []
    acc = 0.0
    for a, b in dips:
        voiced_before.append(a - t_start - acc)
        acc += b - a
    pairs = _match_dips(dips, bounds_syl, punct, voiced_before, max(total_voiced, 1e-6), total_syl)
    anchor_at = {k: dips[i] for i, k in pairs}  # boundary index -> (dip_start, dip_end)

    # ---- spread words between anchors -------------------------------------------------
    out: list[list[float]] = [[0.0, 0.0] for _ in tokens]
    group_start = 0
    seg_start = t_start
    for k in [*sorted(anchor_at), len(tokens) - 1]:
        last = k == len(tokens) - 1
        seg_end = t_end if last else anchor_at[k][0]
        idx = list(range(group_start, k + 1))
        spans_w = [weights[j] for j in idx]
        gaps_w = [pauses[j] for j in idx]
        gaps_w[-1] = 0.0
        tot = sum(spans_w) + sum(gaps_w)
        cur = seg_start
        length = max(seg_end - seg_start, 0.0)
        for j, w, g in zip(idx, spans_w, gaps_w):
            out[j][0] = cur
            out[j][1] = cur + length * w / tot
            cur = out[j][1] + length * g / tot
        if not last:
            seg_start = anchor_at[k][1]
            group_start = k + 1

    # ---- snap the unanchored boundaries to the nearest energy minimum -------------------
    smooth = np.convolve(env, np.ones(3) / 3.0, mode="same")
    anchored = set(anchor_at)
    for k in range(len(tokens) - 1):
        if k in anchored:
            continue
        a, b = out[k][1], out[k + 1][0]
        centre = 0.5 * (a + b)
        gap = b - a
        reach = min(0.1, 0.4 * min(out[k][1] - out[k][0], out[k + 1][1] - out[k + 1][0]))
        lo_f = max(f_first, math.floor((centre - reach) / hop_s))
        hi_f = min(f_last, math.ceil((centre + reach) / hop_s))
        if hi_f <= lo_f:
            continue
        frames = np.arange(lo_f, hi_f + 1)
        cost = smooth[frames] + 4.0 * np.abs(frames * hop_s + hop_s / 2 - centre) / max(
            reach, hop_s
        )
        c_new = float(frames[int(np.argmin(cost))] * hop_s + hop_s / 2)
        out[k][1] = c_new - gap / 2
        out[k + 1][0] = c_new + gap / 2

    # ---- enforce monotonic, non-overlapping, minimum word length ---------------------
    min_len = 0.03
    res: list[tuple[float, float, str]] = []
    prev_end = t_start
    for j, (a, b) in enumerate(out):
        a = max(a, prev_end)
        b = max(b, a + min_len)
        res.append((a, b, tokens[j]))
        prev_end = b
    # minimum-length padding may have pushed the last word past the voiced end: rescale to fit
    if res[-1][1] > t_end + 1e-9:
        scale = (t_end - t_start) / (res[-1][1] - t_start)
        res = [
            (t_start + (a - t_start) * scale, t_start + (b - t_start) * scale, w) for a, b, w in res
        ]
    return res
