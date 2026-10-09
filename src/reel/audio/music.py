"""Generative music beds: ``generate_music(mood, duration, seed)`` -> stereo float32, no samples.

A tiny arranger: it picks a key, tempo and chord progression from the (mood, seed) pair, voice-leads
the chords, and writes a pad, a bass line, a plucked/bell arpeggio, a short hook and light drums
with an energy curve (intro -> build -> body with a "chorus" every second phrase -> outro).
Instruments are cheap vectorised synths (band-limited wavetables, FM, modal bells, noise drums);
notes are cached and dropped on the timeline by slice-adds, so a 60 s bed takes about a second.
A stereo noise-convolution reverb and a ping-pong delay glue it together.

Deterministic: the result depends only on ``(mood, duration, seed, sr)``.  The first bars do not
depend on the duration, so a short preview and the full bed start the same way.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from reel.audio.mix import SAMPLE_RATE, fast_fft_len, pan_gains
from reel.audio.sfx import (
    add_at,
    bandpass,
    fft_convolve,
    highpass,
    lowpass,
    modal,
    raised_attack,
)
from reel.core.rng import stable_int

MUSIC_MOODS: tuple[str, ...] = (
    "upbeat",
    "calm",
    "playful",
    "tense",
    "epic",
    "mysterious",
    "sad",
    "corporate",
)
DEFAULT_MOOD = "upbeat"
PROCEDURAL = "procedural"

_SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
}
_MINOR_FAMILY = frozenset({"minor", "dorian", "phrygian", "harmonic_minor"})


def parse_music_spec(spec: str | None, default: str = DEFAULT_MOOD) -> str | None:
    """``'procedural'`` -> ``default`` mood, ``'procedural:calm'`` -> ``'calm'``, anything else -> None.

    Raises ``ValueError`` for ``procedural:<unknown mood>`` (listing the valid moods).
    """
    if not spec:
        return None
    head, _, mood = spec.strip().partition(":")
    if head.strip().lower() != PROCEDURAL:
        return None
    mood = mood.strip().lower()
    if not mood:
        return default
    if mood not in MUSIC_MOODS:
        raise ValueError(f"unknown music mood {mood!r}; choose one of: {', '.join(MUSIC_MOODS)}")
    return mood


# =============================================================================== mood table
@dataclass(frozen=True)
class _Levels:
    """Mix level (linear) of each layer before the master stage."""

    pad: float
    bass: float
    arp: float
    lead: float
    drums: float


@dataclass(frozen=True)
class _Gates:
    """Energy (0..1) a bar needs before a layer joins in; 9 means never."""

    arp: float = 0.28
    bass: float = 0.5
    drums: float = 0.62
    lead: float = 0.9


@dataclass(frozen=True)
class _Mood:
    bpm: tuple[int, int]
    modes: tuple[str, ...]
    progressions: tuple[tuple[int, ...], ...]  # scale-degree roots, one chord per bar
    levels: _Levels
    pad: str  # warm | bright | string | glass
    bass: str  # pump | steady | bounce | sustain | pedal | epic | drone
    arp: str  # none | eighth | synco | slow | ostinato
    arp_voice: str  # pluck | epiano | marimba | bell | strings
    lead: str  # none | pluck | marimba | epiano | bell
    lead_rhythms: tuple[tuple[tuple[int, int], ...], ...]
    drums: str  # none | pop | soft | shuffle | tick | tom
    reverb: float  # wet level
    rt60: float  # reverb tail (s)
    cutoff: float  # master low-pass (Hz)
    seventh: bool = False  # stack 7ths (lush) instead of plain triads
    swing: float = 0.0  # 0 straight .. 0.33 triplet feel
    gates: _Gates = _Gates()
    delay: float = 0.0  # ping-pong echo spacing in beats (0 = none)


_R_POP = (
    ((0, 2), (2, 1), (4, 3), (8, 2), (10, 2), (12, 4)),
    ((0, 3), (3, 3), (6, 2), (8, 4), (12, 4)),
    ((2, 2), (4, 2), (6, 2), (8, 2), (10, 2), (12, 2), (14, 2)),
    ((0, 4), (4, 2), (6, 2), (8, 8)),
)
_R_SPARSE = (((0, 6), (8, 8)), ((0, 4), (6, 4), (12, 4)), ((2, 6), (10, 6)))
_R_BOUNCY = (
    ((0, 1), (2, 1), (4, 2), (8, 1), (10, 1), (12, 3)),
    ((0, 2), (3, 1), (4, 1), (6, 2), (8, 2), (12, 2)),
    ((0, 1), (1, 1), (2, 2), (4, 2), (8, 1), (9, 1), (10, 2), (12, 4)),
)
_R_STAB = (((0, 2), (6, 2)), ((2, 2), (8, 2), (11, 2)), ((0, 3), (10, 3)))

_MOODS: dict[str, _Mood] = {
    "upbeat": _Mood(
        bpm=(116, 128),
        modes=("major", "major", "mixolydian"),
        progressions=((0, 4, 5, 3), (0, 5, 3, 4), (5, 3, 0, 4), (0, 3, 5, 4)),
        levels=_Levels(pad=0.5, bass=0.6, arp=0.34, lead=0.36, drums=0.9),
        pad="bright", bass="pump", arp="eighth", arp_voice="pluck", lead="pluck", lead_rhythms=_R_POP,
        drums="pop", reverb=0.22, rt60=1.3, cutoff=11000.0, delay=0.75,
    ),
    "calm": _Mood(
        bpm=(64, 76),
        modes=("major", "lydian", "major"),
        progressions=((0, 3, 5, 4), (0, 4, 3, 4), (0, 5, 3, 4), (3, 0, 4, 5)),
        levels=_Levels(pad=0.6, bass=0.45, arp=0.36, lead=0.3, drums=0.0),
        pad="warm", bass="sustain", arp="slow", arp_voice="epiano", lead="epiano", lead_rhythms=_R_SPARSE,
        drums="none", reverb=0.42, rt60=2.2, cutoff=7500.0, seventh=True,
        gates=_Gates(arp=0.2, bass=0.45, drums=9.0, lead=0.8), delay=1.5,
    ),
    "playful": _Mood(
        bpm=(108, 122),
        modes=("major", "major", "lydian"),
        progressions=((0, 3, 0, 4), (0, 5, 1, 4), (0, 3, 4, 3), (0, 2, 3, 4)),
        levels=_Levels(pad=0.3, bass=0.55, arp=0.4, lead=0.42, drums=0.7),
        pad="bright", bass="bounce", arp="eighth", arp_voice="marimba", lead="marimba", lead_rhythms=_R_BOUNCY,
        drums="shuffle", reverb=0.16, rt60=0.9, cutoff=10000.0, swing=0.2, delay=0.75,
    ),
    "tense": _Mood(
        bpm=(92, 106),
        modes=("phrygian", "minor", "harmonic_minor"),
        progressions=((0, 1, 0, 6), (0, 0, 5, 6), (0, 6, 5, 6), (0, 3, 1, 0)),
        levels=_Levels(pad=0.55, bass=0.6, arp=0.3, lead=0.28, drums=0.6),
        pad="string", bass="pedal", arp="ostinato", arp_voice="pluck", lead="bell", lead_rhythms=_R_STAB,
        drums="tick", reverb=0.3, rt60=1.8, cutoff=6500.0,
        gates=_Gates(arp=0.2, bass=0.35, drums=0.5, lead=0.82), delay=0.75,
    ),
    "epic": _Mood(
        bpm=(84, 98),
        modes=("minor", "minor", "dorian"),
        progressions=((0, 5, 2, 6), (0, 6, 5, 6), (0, 3, 6, 2), (0, 5, 3, 4)),
        levels=_Levels(pad=0.75, bass=0.6, arp=0.32, lead=0.0, drums=0.95),
        pad="string", bass="epic", arp="ostinato", arp_voice="strings", lead="none", lead_rhythms=_R_SPARSE,
        drums="tom", reverb=0.34, rt60=2.0, cutoff=9500.0,
        gates=_Gates(arp=0.18, bass=0.34, drums=0.45, lead=0.9),
    ),
    "mysterious": _Mood(
        bpm=(68, 84),
        modes=("dorian", "phrygian", "harmonic_minor"),
        progressions=((0, 3, 0, 6), (0, 6, 5, 6), (0, 1, 0, 6), (0, 5, 3, 6)),
        levels=_Levels(pad=0.7, bass=0.45, arp=0.0, lead=0.38, drums=0.0),
        pad="glass", bass="drone", arp="none", arp_voice="bell", lead="bell", lead_rhythms=_R_STAB,
        drums="none", reverb=0.55, rt60=2.6, cutoff=8000.0, seventh=True,
        gates=_Gates(arp=9.0, bass=0.2, drums=9.0, lead=0.35), delay=1.5,
    ),
    "sad": _Mood(
        bpm=(58, 70),
        modes=("minor", "minor", "dorian"),
        progressions=((0, 5, 2, 6), (0, 3, 6, 2), (0, 5, 3, 4), (0, 6, 5, 4)),
        levels=_Levels(pad=0.6, bass=0.45, arp=0.34, lead=0.34, drums=0.0),
        pad="warm", bass="sustain", arp="slow", arp_voice="epiano", lead="epiano", lead_rhythms=_R_SPARSE,
        drums="none", reverb=0.4, rt60=2.2, cutoff=7000.0, seventh=True,
        gates=_Gates(arp=0.2, bass=0.45, drums=9.0, lead=0.7), delay=1.5,
    ),
    "corporate": _Mood(
        bpm=(100, 112),
        modes=("major",),
        progressions=((0, 4, 5, 3), (0, 3, 4, 3), (0, 5, 3, 4), (0, 3, 5, 4)),
        levels=_Levels(pad=0.5, bass=0.5, arp=0.32, lead=0.3, drums=0.6),
        pad="warm", bass="steady", arp="synco", arp_voice="pluck", lead="bell", lead_rhythms=_R_POP,
        drums="soft", reverb=0.2, rt60=1.2, cutoff=10000.0, delay=0.75,
    ),
}  # fmt: skip


# =============================================================================== synth voices
_TABLE_N = 2048
_F_MIN = 16.3516


@lru_cache(maxsize=16)
def _tables(shape: str, sr: int) -> tuple[np.ndarray, ...]:
    """Band-limited single-cycle tables, one per octave band, so no oscillator ever aliases."""
    out = []
    for b in range(10):
        f_hi = _F_MIN * 2.0 ** (b + 1)
        kmax = max(1, int(0.45 * sr / f_hi))
        k = np.arange(1, kmax + 1, dtype=np.float64)
        if shape == "saw":
            a = 1.0 / k
        elif shape == "saw2":
            a = 1.0 / k**1.4
        elif shape == "soft":
            a = 1.0 / k**1.8
        elif shape == "square":
            a = np.where(k % 2 == 1, 1.0 / k, 0.0)
        elif shape == "tri":
            a = np.where(k % 2 == 1, ((-1.0) ** ((k - 1) // 2)) / k**2, 0.0)
        else:
            raise ValueError(shape)
        spec = np.zeros(_TABLE_N // 2 + 1, dtype=np.complex128)
        spec[1 : kmax + 1] = -1j * a * (_TABLE_N / 2)
        tab = np.fft.irfft(spec, _TABLE_N)
        out.append((tab / np.max(np.abs(tab))).astype(np.float32))
    return tuple(out)


def _osc(freq: float | np.ndarray, n: int, shape: str, sr: int, phase0: float = 0.0) -> np.ndarray:
    """Wavetable oscillator (linear interpolation); ``freq`` may be a per-sample array."""
    f_top = float(np.max(freq))
    band = int(np.clip(math.floor(math.log2(max(f_top, _F_MIN) / _F_MIN)), 0, 9))
    tab = _tables(shape, sr)[band]
    if np.isscalar(freq):
        ph = (phase0 + np.arange(n) * (float(freq) / sr)) % 1.0  # type: ignore[arg-type]
    else:
        ph = (phase0 + np.cumsum(np.asarray(freq, dtype=np.float64)) / sr) % 1.0
    pos = ph * _TABLE_N
    i0 = pos.astype(np.int64)
    fr = (pos - i0).astype(np.float32)
    return tab[i0] * (1.0 - fr) + tab[(i0 + 1) % _TABLE_N] * fr


def _hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def _env(t: np.ndarray, attack: float, release: float, total: float) -> np.ndarray:
    """Raised-cosine attack, flat, raised-cosine release ending at ``total`` seconds."""
    up = raised_attack(t, attack)
    down = np.where(
        t > total - release,
        0.5 + 0.5 * np.cos(np.pi * np.clip((t - (total - release)) / max(release, 1e-6), 0, 1)),
        1.0,
    )
    return np.asarray(up * down)


class _Bank:
    """Note renderers with a per-piece cache: (instrument, midi, length, brightness) -> samples."""

    def __init__(self, sr: int) -> None:
        self.sr = sr
        self._cache: dict[tuple, np.ndarray] = {}

    def _get(self, key: tuple, make: Callable[[], np.ndarray]) -> np.ndarray:
        hit = self._cache.get(key)
        if hit is None:
            hit = make().astype(np.float32)
            self._cache[key] = hit
        return hit

    # -- sustained -----------------------------------------------------------------------
    def pad_chord(self, notes: tuple[int, ...], dur: float, style: str) -> np.ndarray:
        """A chord as a stereo ensemble pad of ``dur`` seconds (+ release), shape (n, 2)."""
        attack, release = {
            "warm": (0.5, 1.0),
            "bright": (0.12, 0.5),
            "string": (0.7, 1.1),
            "glass": (0.9, 1.4),
        }[style]
        shape = {"warm": "soft", "bright": "saw2", "string": "saw2", "glass": "tri"}[style]
        cents = {"warm": 7.0, "bright": 9.0, "string": 12.0, "glass": 5.0}[style]

        def make() -> np.ndarray:
            total = dur + release
            n = int(total * self.sr)
            t = np.arange(n) / self.sr
            left = np.zeros(n)
            right = np.zeros(n)
            for i, m in enumerate(notes):
                f = _hz(m)
                for det, wl, wr in ((-cents, 0.9, 0.35), (0.0, 0.6, 0.6), (cents, 0.35, 0.9)):
                    o = _osc(
                        f * 2.0 ** (det / 1200.0), n, shape, self.sr, (i * 0.37 + det * 0.013) % 1.0
                    )
                    left += wl * o
                    right += wr * o
                if style == "string":  # octave shimmer
                    o = _osc(f * 2.0, n, "soft", self.sr, 0.2 * i) * 0.25
                    left += o
                    right += o
            if style == "glass":
                trem = 1.0 + 0.12 * np.sin(2 * np.pi * 0.23 * t)
                left *= trem
                right *= trem[::-1]
            env = _env(t, attack, release, total)
            stereo = np.stack([left, right], axis=1) * env[:, None] / (len(notes) * 1.6)
            return stereo

        return self._get(("pad", notes, round(dur, 3), style), make)

    def bass(self, midi: int, dur: float, style: str) -> np.ndarray:
        def make() -> np.ndarray:
            n = int((dur + 0.12) * self.sr)
            t = np.arange(n) / self.sr
            f = _hz(midi)
            if style in ("sustain", "drone", "epic"):
                y = (
                    np.sin(2 * np.pi * f * t)
                    + 0.35 * np.sin(2 * np.pi * 2 * f * t + 0.5)
                    + 0.12 * np.sin(2 * np.pi * 3 * f * t)
                )
                if style == "epic":
                    y = y + 0.5 * _osc(f, n, "soft", self.sr)
                e = _env(
                    t,
                    0.02 if style != "drone" else 0.4,
                    0.18 if style != "drone" else 0.6,
                    dur + 0.12,
                )
            else:
                y = 0.8 * _osc(f, n, "soft" if style != "pump" else "saw2", self.sr) + 0.7 * np.sin(
                    2 * np.pi * f * t
                )
                tau = {"pump": 0.2, "steady": 0.16, "bounce": 0.07, "pedal": 0.14}[style]
                e = raised_attack(t, 0.004) * np.exp(-t / tau) * _env(t, 0.001, 0.03, dur + 0.12)
                y = np.tanh(1.5 * y) / 1.5
            return y * e

        return self._get(("bass", midi, round(dur, 3), style), make)

    def strings_stab(self, midi: int, dur: float) -> np.ndarray:
        def make() -> np.ndarray:
            n = int((dur + 0.1) * self.sr)
            t = np.arange(n) / self.sr
            y = sum(
                _osc(_hz(midi) * 2.0 ** (d / 1200.0), n, "soft", self.sr, 0.3 * i)
                for i, d in enumerate((-9.0, 0.0, 9.0))
            )
            return np.asarray(y) * _env(t, 0.03, 0.08, dur + 0.1) / 3.0

        return self._get(("str", midi, round(dur, 3)), make)

    # -- struck / plucked -------------------------------------------------------------------
    def pluck(self, midi: int, dur: float, vel: float = 1.0) -> np.ndarray:
        def make() -> np.ndarray:
            n = int((dur + 0.25) * self.sr)
            t = np.arange(n) / self.sr
            f = _hz(midi)
            bright = np.exp(-t / 0.09)
            y = _osc(f, n, "saw2", self.sr) * bright * 0.8 + (
                _osc(f, n, "tri", self.sr) * 0.9 + 0.3 * np.sin(2 * np.pi * 2 * f * t)
            ) * (1.0 - bright * 0.7)
            e = (
                raised_attack(t, 0.002)
                * np.exp(-t / (0.12 + 0.3 * min(dur, 1.0)))
                * _env(t, 0.001, 0.07, dur + 0.25)
            )
            return y * e

        return self._get(("pluck", midi, round(dur, 3), round(vel, 1)), make)

    def epiano(self, midi: int, dur: float) -> np.ndarray:
        def make() -> np.ndarray:
            n = int((dur + 0.5) * self.sr)
            t = np.arange(n) / self.sr
            f = _hz(midi)
            idx = 2.2 * np.exp(-t / 0.4) + 0.2
            y = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
            y += 0.3 * np.sin(2 * np.pi * 2 * f * t + 0.5 * idx * np.sin(2 * np.pi * 2 * f * t))
            if f * 14 < 0.45 * self.sr:
                y += 0.1 * np.sin(2 * np.pi * 14 * f * t) * np.exp(-t / 0.015)
            e = (
                raised_attack(t, 0.003)
                * np.exp(-t / (0.55 + 0.9 * min(dur, 1.5)))
                * _env(t, 0.001, 0.12, dur + 0.5)
            )
            return y * e

        return self._get(("ep", midi, round(dur, 3)), make)

    def marimba(self, midi: int, dur: float) -> np.ndarray:
        def make() -> np.ndarray:
            n = int((dur + 0.2) * self.sr)
            t = np.arange(n) / self.sr
            f = _hz(midi)
            y = modal(t, [f, f * 4.0, f * 10.0], (1.0, 0.45, 0.14), (0.28, 0.09, 0.035))
            y += np.sin(2 * np.pi * f * 0.5 * t) * np.exp(-t / 0.1) * 0.25
            return y * raised_attack(t, 0.0015) * _env(t, 0.001, 0.05, dur + 0.2)

        return self._get(("mar", midi, round(dur, 3)), make)

    def bell(self, midi: int, dur: float) -> np.ndarray:
        def make() -> np.ndarray:
            n = int((max(dur, 0.6) + 1.2) * self.sr)
            t = np.arange(n) / self.sr
            f = _hz(midi)
            y = modal(
                t, [f, f * 2.76, f * 5.4, f * 2.0], (1.0, 0.3, 0.1, 0.25), (1.5, 0.7, 0.3, 0.9)
            )
            return y * raised_attack(t, 0.001) * _env(t, 0.001, 0.4, n / self.sr)

        return self._get(("bell", midi, round(dur, 1)), make)

    def voice(self, kind: str, midi: int, dur: float) -> np.ndarray:
        if kind == "pluck":
            return self.pluck(midi, dur)
        if kind == "epiano":
            return self.epiano(midi, dur)
        if kind == "marimba":
            return self.marimba(midi, dur)
        if kind == "bell":
            return self.bell(midi, dur)
        return self.strings_stab(midi, dur)


class _Drums:
    """One-shot drum voices rendered once per piece (a few round-robin variants each)."""

    def __init__(self, sr: int, key: tuple) -> None:
        self.sr = sr
        self.key = key
        self.rng = np.random.default_rng(0)
        self._shots: dict[str, list[np.ndarray]] = {}

    def shot(self, name: str, k: int = 0) -> np.ndarray:
        """Variant ``k`` (round-robin of 3) of drum ``name``; each is seeded by (piece, name, variant)."""
        if name not in self._shots:
            variants = []
            for v in range(3):
                self.rng = np.random.default_rng(stable_int("music-drum", *self.key, name, v))
                variants.append(getattr(self, f"_{name}")().astype(np.float32))
            self._shots[name] = variants
        variants = self._shots[name]
        return variants[k % len(variants)]

    def _noise(self, n: int) -> np.ndarray:
        return self.rng.standard_normal(n)

    def _kick(self) -> np.ndarray:
        n = int(0.4 * self.sr)
        t = np.arange(n) / self.sr
        f = 50.0 + 115.0 * np.exp(-t / 0.032)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t / 0.15)
        click = bandpass(self._noise(n), self.sr, 1500, 5000) * np.exp(-t / 0.003) * 0.18
        return np.tanh(1.3 * (body + click)) * raised_attack(t, 0.0008)

    def _soft_kick(self) -> np.ndarray:
        n = int(0.3 * self.sr)
        t = np.arange(n) / self.sr
        f = 50.0 + 80.0 * np.exp(-t / 0.03)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t / 0.1)
        return body * raised_attack(t, 0.003)

    def _snare(self) -> np.ndarray:
        n = int(0.3 * self.sr)
        t = np.arange(n) / self.sr
        noise = bandpass(self._noise(n), self.sr, 1400, 9000) * np.exp(-t / 0.085)
        tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.06) * 0.5
        return (noise + tone) * raised_attack(t, 0.0008)

    def _clap(self) -> np.ndarray:
        n = int(0.32 * self.sr)
        t = np.arange(n) / self.sr
        out = np.zeros(n)
        for k, off in enumerate((0.0, 0.011, 0.023)):
            m = int(0.05 * self.sr)
            b = bandpass(self._noise(m), self.sr, 900, 3600) * np.exp(
                -np.arange(m) / self.sr / 0.007
            )
            add_at(out, b, int(off * self.sr), 1.0 - 0.15 * k)
        out += (
            bandpass(self._noise(n), self.sr, 1000, 3200)
            * np.exp(-t / 0.09)
            * 0.5
            * raised_attack(t, 0.02)
        )
        return out

    def _rim(self) -> np.ndarray:
        n = int(0.1 * self.sr)
        t = np.arange(n) / self.sr
        return (
            np.sin(2 * np.pi * 1750 * t) * np.exp(-t / 0.012)
            + bandpass(self._noise(n), self.sr, 1500, 6000) * np.exp(-t / 0.006) * 0.5
        ) * 0.9

    def _hat(self) -> np.ndarray:
        n = int(0.12 * self.sr)
        t = np.arange(n) / self.sr
        return (
            highpass(self._noise(n), self.sr, 8000) * np.exp(-t / 0.02) * raised_attack(t, 0.0005)
        )

    def _open_hat(self) -> np.ndarray:
        n = int(0.5 * self.sr)
        t = np.arange(n) / self.sr
        return (
            highpass(self._noise(n), self.sr, 7500)
            * np.exp(-t / 0.15)
            * raised_attack(t, 0.0008)
            * 0.6
        )

    def _shaker(self) -> np.ndarray:
        n = int(0.14 * self.sr)
        t = np.arange(n) / self.sr
        return (
            bandpass(self._noise(n), self.sr, 5000, 11000)
            * np.exp(-t / 0.045)
            * raised_attack(t, 0.008)
        )

    def _tom(self) -> np.ndarray:
        n = int(0.6 * self.sr)
        t = np.arange(n) / self.sr
        f = 62.0 + 70.0 * np.exp(-t / 0.06)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t / 0.28)
        thump = lowpass(self._noise(n), self.sr, 900) * np.exp(-t / 0.035) * 0.5
        return np.tanh(1.2 * (body + thump)) * raised_attack(t, 0.001)

    def _crash(self) -> np.ndarray:
        n = int(2.2 * self.sr)
        t = np.arange(n) / self.sr
        src = highpass(self._noise(n), self.sr, 3000, order=3)
        return (src * np.exp(-t / 0.55)) * raised_attack(t, 0.002) * 0.6


# =============================================================================== arranger
class _Bus:
    """Dry stereo bus + a mono reverb send."""

    def __init__(self, n: int, sr: int) -> None:
        self.n, self.sr = n, sr
        self.l = np.zeros(n, dtype=np.float32)
        self.r = np.zeros(n, dtype=np.float32)
        self.wet = np.zeros(n, dtype=np.float32)
        self.dly = np.zeros(n, dtype=np.float32)

    def _span(self, length: int, t: float) -> tuple[int, int, int] | None:
        start = round(t * self.sr)
        lo, hi = max(start, 0), min(start + length, self.n)
        return None if hi <= lo else (start, lo, hi)

    def place(
        self,
        x: np.ndarray,
        t: float,
        gain: float = 1.0,
        pan: float = 0.0,
        send: float = 0.0,
        delay: float = 0.0,
    ) -> None:
        sp = self._span(x.shape[0], t)
        if sp is None:
            return
        start, lo, hi = sp
        seg = x[lo - start : hi - start] * gain
        gl, gr = pan_gains(pan)
        self.l[lo:hi] += seg * gl
        self.r[lo:hi] += seg * gr
        if send:
            self.wet[lo:hi] += seg * send
        if delay:
            self.dly[lo:hi] += seg * delay

    def place_stereo(self, x: np.ndarray, t: float, gain: float = 1.0, send: float = 0.0) -> None:
        sp = self._span(x.shape[0], t)
        if sp is None:
            return
        start, lo, hi = sp
        seg = x[lo - start : hi - start] * gain
        self.l[lo:hi] += seg[:, 0]
        self.r[lo:hi] += seg[:, 1]
        if send:
            self.wet[lo:hi] += 0.5 * (seg[:, 0] + seg[:, 1]) * send


def _scale_note(scale: tuple[int, ...], root: int, idx: int) -> int:
    return root + 12 * (idx // len(scale)) + scale[idx % len(scale)]


def _chord(scale: tuple[int, ...], root: int, degree: int, seventh: bool, mood: str) -> list[int]:
    idxs = [degree, degree + 2, degree + 4] + ([degree + 6] if seventh else [])
    notes = [_scale_note(scale, root, i) for i in idxs]
    if mood == "epic":  # open fifths + octave under a triad
        notes = [notes[0], notes[2], notes[0] + 12, notes[1] + 12]
    return notes


def _voice_lead(prev: list[int] | None, chord: list[int], lo: int = 53, hi: int = 79) -> list[int]:
    """Choose the inversion/octave of ``chord`` that moves least from ``prev`` and stays in [lo, hi]."""
    pcs_sorted = sorted(chord)
    cands: list[list[int]] = []
    for inv in range(len(pcs_sorted)):
        rot = pcs_sorted[inv:] + [n + 12 for n in pcs_sorted[:inv]]
        for shift in range(-48, 49, 12):
            cands.append([n + shift for n in rot])
    target = prev if prev is not None else [64 - 4, 64, 64 + 4, 64 + 8][: len(chord)]

    def cost(c: list[int]) -> float:
        if len(c) == len(target):
            move = float(sum(abs(a - b) for a, b in zip(c, target)))
        else:
            move = abs(sum(c) / len(c) - sum(target) / len(target)) * len(c)
        spread = max(c) - min(c)
        out = max(0, lo - min(c)) + max(0, max(c) - hi)
        return move + 0.3 * max(0, spread - 17) + 6.0 * out

    return min(cands, key=cost)


def _energy(bar: int, n_bars: int) -> tuple[float, bool]:
    """(intensity 0..1, is_chorus) of a bar: ramp in over ~3 bars, ramp out over the last ~2.5,
    and every second 4-bar phrase is a "chorus" (the hook and the fullest drums play there)."""
    scale = min(1.0, n_bars / 8.0)
    ramp_in = min(1.0, (bar + 1) / (3.0 * scale))
    ramp_out = min(1.0, (n_bars - bar - 0.4) / (2.5 * scale))
    e = max(0.0, min(ramp_in, ramp_out))
    return e, (bar // 4) % 2 == 1 and e >= 0.9


def _arp_pattern(kind: str, rng: np.random.Generator) -> list[tuple[int, int, int]]:
    """Per-bar arpeggio as (step, pool index, length in steps) on a 16-step bar."""
    if kind == "eighth":
        seq = ([0, 1, 2, 3, 2, 1, 2, 3], [0, 2, 1, 3, 2, 3, 1, 2], [0, 1, 2, 1, 3, 2, 1, 2])[
            int(rng.integers(3))
        ]
        return [(2 * i, p, 2) for i, p in enumerate(seq)]
    if kind == "synco":
        seq = [0, 2, 1, 3, 2, 1]
        return [(s, seq[i], 2) for i, s in enumerate((0, 3, 6, 8, 11, 14))]
    if kind == "slow":
        seq = ([0, 2, 1, 3], [0, 1, 2, 1], [0, 2, 3, 2])[int(rng.integers(3))]
        return [(4 * i, p, 6) for i, p in enumerate(seq)]
    if kind == "sixteenth":
        return [(i, (0, 1, 2, 3, 2, 1)[i % 6], 1) for i in range(16)]
    if kind == "ostinato":
        return [(2 * i, (0, 0, 2, 0, 1, 0, 2, 1)[i % 8], 2) for i in range(8)]
    return []


def _lead_bar(
    mood: _Mood,
    scale: tuple[int, ...],
    root: int,
    degree: int,
    family_minor: bool,
    rng: np.random.Generator,
    rhythm: tuple[tuple[int, int], ...],
    prev: int,
) -> tuple[list[tuple[int, int, int]], int]:
    """A bar of melody: strong beats on chord tones, weak beats on the pentatonic scale."""
    pent = [i for i in range(7) if i not in ((1, 5) if family_minor else (3, 6))]
    chord_idx = {degree % 7, (degree + 2) % 7, (degree + 4) % 7}
    lo, hi = (76, 93) if mood.lead == "bell" else (72, 88)
    notes: list[tuple[int, int, int]] = []
    cur = prev
    for step, length in rhythm:
        pool = sorted(
            (
                m
                for o in range(-2, 4)
                for i in pent
                for m in [_scale_note(scale, root, i + 7 * o)]
                if lo <= m <= hi and (step % 8 != 0 or (i in chord_idx))
            ),
            key=lambda m: abs(m - cur) + float(rng.uniform(0, 2.2)),
        )
        if not pool:
            continue
        nxt = pool[0]
        if nxt == cur and len(pool) > 1 and rng.random() < 0.7:  # avoid droning on one note
            nxt = pool[1]
        notes.append((step, nxt, length))
        cur = nxt
    return notes, cur


@dataclass
class _Plan:
    root_midi: int
    scale: tuple[int, ...]
    minor: bool
    bpm: float
    progression: tuple[int, ...]
    chords: list[list[int]] = field(default_factory=list)


def _make_plan(name: str, seed: int) -> _Plan:
    mood = _MOODS[name]
    rng = np.random.default_rng(stable_int("music-plan", name, seed))
    mode = mood.modes[int(rng.integers(len(mood.modes)))]
    scale = _SCALES[mode]
    key = int(
        rng.choice([0, 2, 3, 5, 7, 9, 10])
    )  # keys whose bass sits comfortably (C D Eb F G A Bb)
    prog = mood.progressions[int(rng.integers(len(mood.progressions)))]
    bpm = float(rng.integers(mood.bpm[0], mood.bpm[1] + 1))
    root = 48 + key  # tonic in octave 3
    plan = _Plan(root, scale, mode in _MINOR_FAMILY, bpm, prog)
    prev: list[int] | None = None
    for d in prog:
        v = _voice_lead(prev, _chord(scale, root, d, mood.seventh, name))
        plan.chords.append(v)
        prev = v
    return plan


_DRUM_GAIN = {
    "kick": 1.0,
    "soft_kick": 0.9,
    "snare": 0.6,
    "clap": 0.55,
    "rim": 0.55,
    "hat": 0.28,
    "open_hat": 0.3,
    "shaker": 0.3,
    "tom": 0.6,
}


def _drum_hits(style: str, bar: int, e: float, chorus: bool) -> list[tuple[int, str, float]]:
    """(step, drum, velocity) for one 16-step bar."""
    hits: list[tuple[int, str, float]] = []
    if style == "pop":
        for s in (0, 8) + ((10,) if bar % 2 else ()):
            hits.append((s, "kick", 1.0 if s == 0 else 0.85))
        hits += [(4, "snare", 0.9), (12, "snare", 0.95), (12, "clap", 0.5 if chorus else 0.0)]
        for s in range(0, 16, 2):
            hits.append((s, "hat", 0.55 if s % 4 == 0 else 0.35))
        if chorus:
            hits.append((14, "open_hat", 0.5))
        if bar % 4 == 3:
            hits += [(13, "snare", 0.5), (14, "snare", 0.6), (15, "snare", 0.75)]
    elif style == "soft":
        hits += [(0, "soft_kick", 0.9), (8, "soft_kick", 0.75), (4, "rim", 0.6), (12, "rim", 0.65)]
        hits += [(s, "hat", 0.22 if s % 4 == 0 else 0.14) for s in range(0, 16, 2)]
        if bar % 4 == 3:
            hits.append((15, "rim", 0.4))
    elif style == "shuffle":
        hits += [(0, "soft_kick", 0.9), (8, "soft_kick", 0.7), (4, "rim", 0.75), (12, "rim", 0.8)]
        hits += [(s, "shaker", 0.5 if s % 4 == 0 else 0.3) for s in range(0, 16, 2)]
        if chorus:
            hits += [(s, "shaker", 0.2) for s in range(1, 16, 2)]
    elif style == "tick":
        hits += [(0, "soft_kick", 0.9), (3, "soft_kick", 0.55)]
        hits += [(s, "hat", 0.12 + 0.2 * e) for s in range(16)]
        if bar % 2 == 1:
            hits.append((12, "tom", 0.45))
    elif style == "tom":
        hits += [(0, "kick", 1.0), (8, "kick", 0.9), (12, "snare", 0.75)]
        hits += [(s, "tom", 0.75 if s in (0, 8) else 0.5) for s in (0, 3, 6, 8, 11, 14)]
        if bar % 4 == 3:
            hits += [(14, "snare", 0.6), (15, "snare", 0.8)]
    return [h for h in hits if h[2] > 0]


def generate_music(mood: str, duration: float, seed: int = 0, sr: int = SAMPLE_RATE) -> np.ndarray:
    """A generative music bed: float32 stereo ``(n, 2)``, ``n = round(duration * sr)``, peak <= 0.9.

    ``mood`` is one of :data:`MUSIC_MOODS`.  Fades in over 0.5 s and out over 1.5 s.
    """
    mood = mood.strip().lower()
    if mood not in _MOODS:
        raise ValueError(f"unknown music mood {mood!r}; choose one of: {', '.join(MUSIC_MOODS)}")
    n = round(duration * sr)
    if n < 2:
        return np.zeros((max(n, 0), 2), dtype=np.float32)
    spec = _MOODS[mood]
    plan = _make_plan(mood, seed)
    beat = 60.0 / plan.bpm
    bar_sec = 4.0 * beat
    step = beat / 4.0
    n_bars = math.ceil(duration / bar_sec)
    bank = _Bank(sr)
    drums = _Drums(sr, (mood, seed))
    bus = _Bus(n, sr)
    pad_lv, bass_lv, arp_lv, lead_lv, drum_lv = (
        spec.levels.pad,
        spec.levels.bass,
        spec.levels.arp,
        spec.levels.lead,
        spec.levels.drums,
    )
    th_arp, th_bass, th_drum, th_lead = (
        spec.gates.arp,
        spec.gates.bass,
        spec.gates.drums,
        spec.gates.lead,
    )
    prog_len = len(plan.progression)
    lead_prev = _scale_note(plan.scale, plan.root_midi + 12, 4)

    def swing(s: int) -> float:
        if spec.swing <= 0:
            return 0.0
        if s % 4 == 2:
            return spec.swing * 0.5 * beat
        if s % 2 == 1:
            return spec.swing * 0.25 * beat
        return 0.0

    for bar in range(n_bars):
        t0 = bar * bar_sec
        e, chorus = _energy(bar, n_bars)
        brng = np.random.default_rng(stable_int("music-bar", mood, seed, bar))
        ci = bar % prog_len
        degree = plan.progression[ci]
        chord = plan.chords[ci]
        root_note = _scale_note(plan.scale, plan.root_midi, degree)
        phrase_bar = bar % 4

        # ---- pad ------------------------------------------------------------------------
        if e > 0.05:
            bus.place_stereo(
                bank.pad_chord(tuple(chord), bar_sec * 1.0, spec.pad),
                t0,
                pad_lv * min(1.0, 0.45 + e),
                send=0.5,
            )

        # ---- bass -----------------------------------------------------------------------
        bass_midi = root_note
        while bass_midi > 47:
            bass_midi -= 12
        while bass_midi < 33:
            bass_midi += 12
        if e >= th_bass and spec.bass != "none":
            fifth = bass_midi + 7
            if spec.bass == "pump":
                for s, ln, m, v in (
                    (0, 3, bass_midi, 1.0),
                    (3, 1, bass_midi, 0.6),
                    (6, 2, fifth, 0.7),
                    (8, 3, bass_midi, 0.9),
                    (11, 1, bass_midi, 0.6),
                    (14, 2, bass_midi + 12, 0.65),
                ):
                    bus.place(bank.bass(m, ln * step * 0.95, "pump"), t0 + s * step, bass_lv * v)
            elif spec.bass == "steady":
                for s in range(0, 16, 2):
                    bus.place(
                        bank.bass(bass_midi, step * 1.6, "steady"),
                        t0 + s * step,
                        bass_lv * (0.95 if s % 4 == 0 else 0.6),
                    )
            elif spec.bass == "bounce":
                for s, m in (
                    (0, bass_midi),
                    (4, fifth),
                    (8, bass_midi),
                    (12, fifth),
                    (14, bass_midi + 12),
                ):
                    bus.place(
                        bank.bass(m, step * 1.2, "bounce"),
                        t0 + (s * step + swing(s)),
                        bass_lv * (0.95 if s % 8 == 0 else 0.7),
                    )
            elif spec.bass == "pedal":
                root_low = plan.root_midi - 12
                while root_low > 47:
                    root_low -= 12
                for s in range(0, 16, 2):
                    bus.place(
                        bank.bass(max(root_low, 33), step * 1.7, "pedal"),
                        t0 + s * step,
                        bass_lv * (0.9 if s % 8 == 0 else 0.55),
                    )
            elif spec.bass == "epic":
                for s, ln in ((0, 6), (8, 6)):
                    bus.place(bank.bass(bass_midi, ln * step, "epic"), t0 + s * step, bass_lv)
                    bus.place(
                        bank.bass(bass_midi + 12, ln * step, "sustain"),
                        t0 + s * step,
                        bass_lv * 0.35,
                    )
            elif spec.bass == "sustain":
                bus.place(bank.bass(bass_midi, bar_sec * 0.5, "sustain"), t0, bass_lv)
                bus.place(
                    bank.bass(bass_midi if bar % 2 == 0 else fifth, bar_sec * 0.5, "sustain"),
                    t0 + bar_sec * 0.5,
                    bass_lv * 0.85,
                )
            elif spec.bass == "drone":
                bus.place(bank.bass(bass_midi, bar_sec * 0.98, "drone"), t0, bass_lv)

        # ---- arpeggio / ostinato ---------------------------------------------------------
        if e >= th_arp and spec.arp != "none":
            if spec.arp == "ostinato":  # low-mid pulse on root / fifth / octave / colour tone
                r = root_note
                while r > 59:
                    r -= 12
                while r < 48:
                    r += 12
                pool = [r, r + 7, r + 12, r + (3 if plan.minor else 4)]
            else:
                pool = sorted({*chord, *(c + 12 for c in chord)})
                pool = [p for p in pool if 55 <= p <= 91] or pool
            pat = _arp_pattern(
                spec.arp, np.random.default_rng(stable_int("music-arp", mood, seed, bar % 4))
            )
            for k, (s_, pi, ln) in enumerate(pat):
                m = pool[min(pi, len(pool) - 1)]
                if spec.arp_voice == "strings":
                    snip = bank.strings_stab(m, ln * step * 0.9)
                else:
                    snip = bank.voice(
                        spec.arp_voice,
                        m,
                        ln * step * (1.4 if spec.arp_voice in ("epiano", "bell") else 0.95),
                    )
                vel = (
                    arp_lv
                    * (0.9 + 0.15 * float(brng.uniform(-1, 1)))
                    * (1.0 if s_ % 4 == 0 else 0.78)
                )
                pan = -0.35 if k % 2 == 0 else 0.35
                bus.place(
                    snip,
                    t0 + s_ * step + swing(s_) + float(brng.normal(0, 0.002)),
                    vel,
                    pan,
                    send=0.3,
                    delay=0.35 if spec.delay else 0.0,
                )

        # ---- lead hook -------------------------------------------------------------------------
        if spec.lead != "none" and e >= th_lead and (chorus or th_lead < 0.9):
            variant = (bar // 4) % 2
            lrng = np.random.default_rng(stable_int("music-lead", mood, seed, variant, phrase_bar))
            rhythm = spec.lead_rhythms[
                stable_int("music-rhythm", mood, seed, variant, phrase_bar % 2)
                % len(spec.lead_rhythms)
            ]
            if phrase_bar == 0:  # every phrase starts from the same anchor so the hook repeats
                lead_prev = _scale_note(plan.scale, plan.root_midi + 12, 4)
            hook, lead_prev = _lead_bar(
                spec, plan.scale, plan.root_midi + 12, degree, plan.minor, lrng, rhythm, lead_prev
            )
            for s_, m, ln in hook:
                dur_n = ln * step * (1.0 if spec.lead in ("bell", "epiano") else 0.9)
                bus.place(
                    bank.voice(spec.lead, m, dur_n),
                    t0 + s_ * step + swing(s_),
                    lead_lv * (1.0 if s_ % 8 == 0 else 0.8),
                    pan=0.12,
                    send=0.4,
                    delay=0.5 if spec.delay else 0.0,
                )

        # ---- drums -------------------------------------------------------------------------------
        if e >= th_drum and spec.drums != "none":
            if (
                bar % 8 == 0
                and bar > 0
                and spec.drums in ("pop", "tom")
                and chorus is False
                and e >= 0.9
            ):
                bus.place(drums.shot("crash"), t0, drum_lv * 0.45, pan=0.15, send=0.2)
            for k, (s_, name, vel) in enumerate(_drum_hits(spec.drums, bar, e, chorus)):
                pan = {
                    "hat": 0.25,
                    "shaker": -0.2,
                    "open_hat": 0.25,
                    "rim": 0.1,
                    "tom": -0.15 if s_ % 2 else 0.15,
                }.get(name, 0.0)
                gain = drum_lv * vel * (0.92 + 0.16 * float(brng.random())) * _DRUM_GAIN[name]
                t = t0 + s_ * step + swing(s_) + float(brng.normal(0, 0.0015))
                bus.place(
                    drums.shot(name, k + bar),
                    t,
                    gain,
                    pan,
                    send=0.12 if name in ("snare", "clap", "rim", "tom") else 0.0,
                )

    # ---- reverb / delay / master -------------------------------------------------------------
    rng_fx = np.random.default_rng(stable_int("music-fx", mood, seed))
    wet_l, wet_r = _stereo_reverb(bus.wet, sr, spec.rt60, rng_fx)
    left = bus.l + wet_l * spec.reverb * 2.2
    right = bus.r + wet_r * spec.reverb * 2.2
    if spec.delay > 0:  # ping-pong echoes of the arp/lead (dotted-eighth or half-note spacing)
        d = int(spec.delay * beat * sr)
        for k, g in enumerate((0.34, 0.2, 0.1), start=1):
            tgt = left if k % 2 else right
            if d * k < n:
                tgt[d * k :] += bus.dly[: n - d * k] * g
    stereo = np.stack([left, right], axis=1).astype(np.float64)
    stereo = _master(stereo, sr, spec.cutoff)
    fi = min(int(0.5 * sr), n // 4)
    fo = min(int(1.5 * sr), n // 2)
    if fi > 1:
        stereo[:fi] *= (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, fi)))[:, None]
    if fo > 1:
        stereo[-fo:] *= (0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, fo)))[:, None]
    peak = float(np.max(np.abs(stereo)))
    if peak > 0:
        stereo *= 0.9 / peak
    return stereo.astype(np.float32)


def _stereo_reverb(
    send: np.ndarray, sr: int, rt60: float, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Wet-only stereo reverb of a mono send (two decorrelated noise-burst impulse responses)."""
    m = int(rt60 * 1.0 * sr)
    t = np.arange(m) / sr
    outs = []
    for _ in range(2):
        ir = rng.standard_normal(m) * np.exp(-6.9078 * t / rt60)
        ir[: int(0.014 * sr)] = 0.0
        ir = lowpass(ir, sr, 5500.0, order=2)
        ir /= np.sqrt(np.sum(ir * ir)) + 1e-12
        outs.append(fft_convolve(send.astype(np.float64), ir)[: send.shape[0]].astype(np.float32))
    return outs[0], outs[1]


def _master(stereo: np.ndarray, sr: int, cutoff: float) -> np.ndarray:
    """Glue with gentle saturation, then high-pass the sub rumble and round off the top."""
    peak = float(np.max(np.abs(stereo))) or 1.0
    sat = np.tanh(1.2 * stereo / peak) / np.tanh(1.2)
    out = np.empty_like(sat)
    n = sat.shape[0]
    total = fast_fft_len(n + 4096)
    f = np.fft.rfftfreq(total, 1.0 / sr)
    resp = 1.0 / np.sqrt(1.0 + (f / cutoff) ** 6) / np.sqrt(1.0 + (38.0 / np.maximum(f, 1e-3)) ** 4)
    for ch in range(2):
        buf = np.zeros(total)
        buf[2048 : 2048 + n] = sat[:, ch]
        out[:, ch] = np.fft.irfft(np.fft.rfft(buf) * resp, total)[2048 : 2048 + n]
    return out
