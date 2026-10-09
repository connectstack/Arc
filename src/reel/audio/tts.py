"""Offline text-to-speech behind one small interface.

* :class:`PiperTTS`  - the ``piper`` CLI (or the in-process ``piper`` package) with a local ``.onnx`` voice.
* :class:`MacSayTTS` - macOS ``say`` (AIFF -> mono float32 via ffmpeg).
* :class:`BabbleTTS` - always available: a deterministic syllable-timed formant babble ("Animalese")
  so the pipeline, the lip-sync and the tests work on any machine.

:func:`get_tts_engine` picks one (``auto`` = piper if usable, else ``say`` on macOS, else babble).
Every engine caches its clips on disk under ``<cache dir>/tts`` keyed by
``sha256(engine, version, voice, speed, text)``, so re-renders never re-synthesise.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from reel.actions.base import count_syllables
from reel.audio.align import fold_accents, pause_weight, word_weight
from reel.audio.mix import AudioDecodeError, decode_audio, ffmpeg_exe, read_wav, to_mono
from reel.audio.sfx import (
    add_at,
    bandpass,
    formant_response,
    smooth_bump,
    spectral_filter,
    vocal_tract,
)
from reel.core.cache import default_cache_dir
from reel.core.rng import stable_int

if TYPE_CHECKING:
    from reel.core.spec import CharacterSpec


class TTSUnavailableError(RuntimeError):
    """The requested engine cannot run on this machine."""


class UnknownTTSEngineError(ValueError):
    """``get_tts_engine`` was given a name that is not an engine."""


@dataclass
class TTSClip:
    """Mono float32 speech.  ``words`` optionally carries exact ``(t0, t1, word)`` timings."""

    samples: np.ndarray
    sample_rate: int
    words: list[tuple[float, float, str]] | None = field(default=None)

    @property
    def duration(self) -> float:
        return float(self.samples.shape[0]) / self.sample_rate if self.sample_rate else 0.0


# ----------------------------------------------------------------------------- disk cache
def tts_cache_dir() -> Path:
    return default_cache_dir() / "tts"


def tts_cache_key(engine: str, version: str, voice: str | None, speed: float, text: str) -> str:
    raw = "\x1f".join([engine, version, voice or "", f"{speed:.4f}", text])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_paths(key: str) -> tuple[Path, Path]:
    base = tts_cache_dir() / key[:2]
    return base / f"{key}.npy", base / f"{key}.json"


def _atomic_write(path: Path, writer: Callable[[Path], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_cached_clip(key: str) -> TTSClip | None:
    npy, meta = _cache_paths(key)
    try:
        samples = np.load(npy, allow_pickle=False)
        info = json.loads(meta.read_text(encoding="utf-8"))
        words = info.get("words")
        return TTSClip(
            samples.astype(np.float32, copy=False),
            int(info["sample_rate"]),
            [(float(a), float(b), str(w)) for a, b, w in words] if words else None,
        )
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        for damaged in (
            npy,
            meta,
        ):  # a truncated or empty file would otherwise fail on every render
            with contextlib.suppress(OSError):
                damaged.unlink()
        return None


def store_cached_clip(key: str, clip: TTSClip) -> None:
    npy, meta = _cache_paths(key)
    info: dict[str, Any] = {"sample_rate": clip.sample_rate}
    if clip.words:
        info["words"] = [[a, b, w] for a, b, w in clip.words]

    def write_npy(p: Path) -> None:
        with p.open("wb") as fh:
            np.save(fh, np.asarray(clip.samples, dtype=np.float32), allow_pickle=False)

    def write_meta(p: Path) -> None:
        p.write_text(json.dumps(info), encoding="utf-8")

    try:
        _atomic_write(npy, write_npy)
        _atomic_write(meta, write_meta)
    except OSError:
        pass  # a read-only cache must never break a render


# ----------------------------------------------------------------------------- language of a text
#: (first code point, last code point, language) for the scripts that tell us which voice can read a text
_SCRIPT_LANG: tuple[tuple[int, int, str], ...] = (
    (0x0900, 0x097F, "hi"),  # Devanagari (Hindi; also Marathi and Nepali)
    (0x0980, 0x09FF, "bn"), (0x0A00, 0x0A7F, "pa"), (0x0A80, 0x0AFF, "gu"), (0x0B00, 0x0B7F, "or"),
    (0x0B80, 0x0BFF, "ta"), (0x0C00, 0x0C7F, "te"), (0x0C80, 0x0CFF, "kn"), (0x0D00, 0x0D7F, "ml"),
    (0x0D80, 0x0DFF, "si"),
    (0x0590, 0x05FF, "he"), (0xFB1D, 0xFB4F, "he"),
    (0x0600, 0x06FF, "ar"), (0x0750, 0x077F, "ar"), (0x08A0, 0x08FF, "ar"), (0xFB50, 0xFDFF, "ar"), (0xFE70, 0xFEFF, "ar"),
    (0x0E00, 0x0E7F, "th"), (0x0E80, 0x0EFF, "lo"), (0x1000, 0x109F, "my"), (0x1780, 0x17FF, "km"),
    (0x0F00, 0x0FFF, "bo"), (0x1200, 0x137F, "am"), (0x0530, 0x058F, "hy"), (0x10A0, 0x10FF, "ka"),
    (0x3040, 0x30FF, "ja"),  # kana (kanji alone would read as Chinese)
    (0x31F0, 0x31FF, "ja"), (0xFF66, 0xFF9F, "ja"),  # small kana extensions, half-width katakana
    (0xAC00, 0xD7AF, "ko"), (0x1100, 0x11FF, "ko"), (0x3130, 0x318F, "ko"),
    (0x4E00, 0x9FFF, "zh"), (0x3400, 0x4DBF, "zh"), (0x3100, 0x312F, "zh"),
    (0x0400, 0x052F, "ru"), (0x0370, 0x03FF, "el"), (0x1F00, 0x1FFF, "el"),
)  # fmt: skip


def text_language(text: str) -> str | None:
    """The language a text implies through its dominant non-Latin script (``'hi'`` for Devanagari ...).

    ``None`` for Latin text, and for text that merely *contains* some other script: "Buy 3 coffees ☕ 咖啡" is English,
    so it must not be handed to a Chinese voice; the script has to account for at least half of the letters.
    """
    counts: dict[str, int] = {}
    latin = 0
    for ch in text:
        if not unicodedata.category(ch).startswith(("L", "M")):  # letters and their marks only
            continue
        o = ord(ch)
        for lo, hi, lang in _SCRIPT_LANG:
            if lo <= o <= hi:
                counts[lang] = counts.get(lang, 0) + 1
                break
        else:
            if not unicodedata.combining(ch):
                latin += 1
    script = sum(counts.values())
    if not script or script < latin:
        return None
    if counts.get("ja"):  # Japanese mixes kanji with kana; kana settles it
        return "ja"
    return max(counts, key=lambda k: counts[k])


# ----------------------------------------------------------------------------- interface
class TTSEngine(ABC):
    """What the pipeline needs from a speech engine."""

    name: str = "engine"

    @abstractmethod
    def available(self) -> bool:
        """True when this engine can synthesise on this machine."""

    @abstractmethod
    def synthesize(self, text: str, voice: str | None = None, speed: float = 1.0) -> TTSClip:
        """Speak ``text`` (``speed`` 1.0 = the voice's natural rate)."""

    @abstractmethod
    def list_voices(self) -> list[str]:
        """Names usable as ``voice``."""

    # optional hooks the pipeline uses --------------------------------------------------
    def resolve_voice(self, voice: str | None) -> str | None:
        """The voice that will really be used for ``voice`` (``None`` = engine default)."""
        return voice

    def voice_warning(self, voice: str | None) -> str | None:
        """A message when ``voice`` is not known to this engine (it falls back to a default)."""
        return None

    def speaker_voice(
        self, speaker: str | None, character: CharacterSpec | None = None
    ) -> str | None:
        """Voice for a speaker that has no explicit voice configured (``None`` = engine default).

        ``character`` is the spec's character (archetype, display name) when there is one.
        """
        return None

    def voice_language(self, voice: str | None) -> str | None:
        """Two-letter language of ``voice`` when the engine knows it (``None`` = unknown / any)."""
        return None

    def voice_for_text(self, text: str, voice: str | None) -> str | None:
        """The voice that can actually read ``text``: ``voice`` unless the text is in another script and the
        engine has a voice for that language."""
        return voice

    def take_warnings(self) -> list[str]:
        """Problems the engine noticed while synthesising (a voice it did not know ...), returned once and forgotten."""
        return []

    def usage(self) -> str | None:
        """One line on what this run cost (online engines: characters sent and billed), ``None`` when it is free."""
        return None


class CachedEngine(TTSEngine):
    """Base for engines that implement ``_render`` and get the on-disk clip cache for free."""

    version = "1"

    @abstractmethod
    def _render(self, text: str, voice: str | None, speed: float) -> TTSClip: ...

    def synthesize(self, text: str, voice: str | None = None, speed: float = 1.0) -> TTSClip:
        clean = " ".join(text.split())
        key = tts_cache_key(self.name, self.version, self.resolve_voice(voice), speed, clean)
        hit = load_cached_clip(key)
        if hit is not None:
            return hit
        clip = self._render(clean, voice, speed)
        samples = np.nan_to_num(np.asarray(clip.samples, dtype=np.float32).reshape(-1))
        clip = TTSClip(samples, int(clip.sample_rate), clip.words)
        store_cached_clip(key, clip)
        return clip


# ============================================================================== babble
#: vowel formants F1..F3 (Hz) for a ~140 Hz male-ish voice
_VOWELS: dict[str, tuple[float, float, float]] = {
    "a": (730.0, 1090.0, 2440.0),
    "e": (530.0, 1840.0, 2480.0),
    "i": (270.0, 2290.0, 3010.0),
    "o": (570.0, 840.0, 2410.0),
    "u": (300.0, 870.0, 2240.0),
    "@": (500.0, 1500.0, 2500.0),
}
_VOWEL_GROUPS = {
    "ee": "i", "ea": "i", "ie": "i", "ei": "e", "oo": "u", "ou": "a", "ow": "o", "oi": "o", "oa": "o",
    "ai": "e", "ay": "e", "au": "a", "aw": "a", "ue": "u", "ui": "u", "eu": "u", "ey": "e", "io": "o",
}  # fmt: skip
#: formant loci of the sonorant consonants (the vowel glides away from / towards them)
_LOCUS: dict[str, tuple[float, float, float]] = {
    "l": (360.0, 1300.0, 2500.0),
    "r": (310.0, 1060.0, 1380.0),
    "w": (290.0, 610.0, 2150.0),
    "y": (270.0, 2100.0, 3000.0),
    "m": (250.0, 1100.0, 2200.0),
}
_SONORANT = frozenset("mlrwy")
_ONSET_SEC = {"": 0.0, "p": 0.040, "t": 0.040, "k": 0.045, "b": 0.040, "d": 0.040, "g": 0.045, "s": 0.075, "z": 0.065, "sh": 0.075, "f": 0.06, "v": 0.05, "h": 0.045, "m": 0.055, "l": 0.05, "r": 0.05, "w": 0.055, "y": 0.045}  # fmt: skip
_CODA_SEC = {"": 0.0, "p": 0.035, "t": 0.035, "k": 0.04, "b": 0.03, "d": 0.03, "g": 0.035, "s": 0.055, "z": 0.05, "sh": 0.06, "f": 0.05, "v": 0.04, "h": 0.0, "m": 0.05, "l": 0.045, "r": 0.045, "w": 0.0, "y": 0.0}  # fmt: skip
_F2_SHIFT = {
    "p": 0.88,
    "b": 0.88,
    "f": 0.92,
    "v": 0.92,
    "t": 1.08,
    "d": 1.08,
    "s": 1.05,
    "z": 1.05,
    "k": 0.96,
    "g": 0.96,
}
_FUNCTION_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "is",
        "it",
        "and",
        "or",
        "but",
        "at",
        "on",
        "for",
        "with",
        "as",
        "by",
        "be",
        "am",
        "are",
        "was",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "my",
        "your",
    ]
)
_PHRASE_PAUSE = (
    ("...", 0.38),
    ("…", 0.38),
    (".", 0.30),
    ("!", 0.30),
    ("?", 0.30),
    (";", 0.20),
    (":", 0.20),
    (",", 0.14),
)
#: name -> (f0 Hz, formant scale, monotone)
_VOICE_PRESETS: dict[str, tuple[float, float, bool]] = {
    "babble": (150.0, 1.0, False),
    "low": (105.0, 0.93, False),
    "deep": (88.0, 0.88, False),
    "high": (215.0, 1.1, False),
    "child": (275.0, 1.22, False),
    "robot": (130.0, 1.0, True),
}


@dataclass
class _Syl:
    onset: str  # '' | p t k b d g s z sh f v h | m l r w y
    vowel: tuple[float, float, float]
    coda: str = ""
    first: bool = False
    last: bool = False
    stressed: bool = False


@dataclass
class _Word:
    token: str
    syls: list[_Syl]
    start: float
    end: float


@dataclass
class _Job:
    syl: _Syl
    t0: float
    t1: float
    f0: float
    amp: float


def _consonant_class(cluster: str) -> str:
    """Representative consonant of a letter cluster (its last sound)."""
    if not cluster:
        return ""
    if cluster.endswith(("sh", "ch", "zh")):
        return "sh"
    if cluster.endswith(("th", "ph")):
        return "f"
    c = cluster[-1]
    if c in "ptkbdgfvhlrwy":
        return c
    if c in "cq":
        return "k"
    if c in "sx":
        return "s"
    if c in "zj":
        return "z"
    if c in "mn":
        return "m"
    return ""


_PSEUDO_VOWELS = "aeiou"
_PSEUDO_ONSETS = ("", "k", "t", "m", "s", "r", "p", "l", "b", "d", "h", "z")


def _pseudo_syllables(word: str) -> list[_Syl]:
    """Babble for a word without Latin letters (Hindi, Arabic, 你好, 2024): ``word_weight`` syllables whose vowels and
    onsets come from the characters' code points, so the same word always sounds the same and different words differ.
    (A voice that said nothing for every non-Latin word would leave the cartoon character mute.)"""
    n = round(word_weight(word))
    codes = [ord(c) for c in unicodedata.normalize("NFC", word) if c.isalnum()]
    if n <= 0 or not codes:
        return []
    sylls: list[_Syl] = []
    for i in range(n):
        c = codes[i * len(codes) // n]
        vowel = _VOWELS[_PSEUDO_VOWELS[c % len(_PSEUDO_VOWELS)]]
        onset = _PSEUDO_ONSETS[(c // len(_PSEUDO_VOWELS)) % len(_PSEUDO_ONSETS)]
        sylls.append(_Syl(onset, vowel))
    sylls[0].first = True
    sylls[-1].last = True
    return sylls


def split_syllables(word: str) -> list[_Syl]:
    """Rough letter-to-sound: exactly ``count_syllables(word)`` syllables, vowels from the letters."""
    w = re.sub(r"[^a-z]", "", fold_accents(word).lower())
    if not w:
        return _pseudo_syllables(word)
    n = count_syllables(w)
    groups = list(re.finditer(r"[aeiouy]+", w))[:n]
    sylls: list[_Syl] = []
    prev_end = 0
    for gi in range(n):
        if gi < len(groups):
            g = groups[gi]
            key = g.group()
            letter = "i" if key[0] == "y" else key[0]
            vowel = _VOWELS[_VOWEL_GROUPS.get(key[:2], letter if letter in _VOWELS else "@")]
            onset = _consonant_class(w[prev_end : g.start()])
            prev_end = g.end()
        else:  # no vowel letters at all ("hmm", "shh")
            vowel, onset, prev_end = _VOWELS["@"], _consonant_class(w), len(w)
        sylls.append(_Syl(onset, vowel))
    tail = w[prev_end:]
    if w.endswith("e") and len(w) > 2:  # silent final e
        tail = tail[:-1] if tail.endswith("e") else tail
    if tail:
        sylls[-1].coda = _consonant_class(tail if tail.endswith(("sh", "ch", "th")) else tail[-1])
    sylls[0].first = True
    sylls[-1].last = True
    return sylls


def _ramp(t: np.ndarray, t0: float, t1: float) -> np.ndarray:
    u = np.clip((t - t0) / max(t1 - t0, 1e-6), 0.0, 1.0)
    return np.asarray(u * u * (3.0 - 2.0 * u))


class BabbleTTS(CachedEngine):
    """Deterministic syllable-timed formant babble (~4.3 syllables/s, pauses at punctuation).

    Each word becomes as many syllables as ``count_syllables`` says, with vowels picked from its
    letters and consonant onsets/codas synthesised as noise bursts, hiss or murmur, so it reads as
    cartoon speech.  Because every timing is known, the clip carries exact word timings.
    """

    name = "babble"
    version = "1"
    SYLLABLE_SEC = 1.0 / 4.3
    LEAD_SEC = 0.08
    TAIL_SEC = 0.12
    WORD_GAP = 0.045

    def __init__(self, sample_rate: int = 22_050) -> None:
        self.sample_rate = sample_rate

    def available(self) -> bool:
        return True

    def list_voices(self) -> list[str]:
        return sorted(_VOICE_PRESETS)

    def speaker_voice(
        self, speaker: str | None, character: CharacterSpec | None = None
    ) -> str | None:
        return f"babble:{speaker}" if speaker else None

    def resolve_voice(self, voice: str | None) -> str | None:
        return voice or "babble"

    @staticmethod
    def voice_params(voice: str | None) -> tuple[float, float, bool]:
        """``(f0 Hz, formant scale, monotone)``: presets, else a stable hash of the name."""
        name = voice or "babble"
        if name in _VOICE_PRESETS:
            return _VOICE_PRESETS[name]
        f0 = 100.0 + (stable_int("babble-voice", name) % 1200) / 10.0  # 100 .. 220 Hz
        return f0, float(np.clip((f0 / 140.0) ** 0.35, 0.88, 1.2)), False

    # -- layout -------------------------------------------------------------------------
    def _layout(self, tokens: list[str], speed: float) -> tuple[list[_Word], float]:
        slot = self.SYLLABLE_SEC / speed
        gap = self.WORD_GAP / speed
        words: list[_Word] = []
        t = self.LEAD_SEC
        for tok in tokens:
            syls = split_syllables(tok) if word_weight(tok) > 0 else []
            start = t
            t += slot * len(syls)
            words.append(_Word(tok, syls, start, t - gap if syls else t))
            core = tok.rstrip("\"')]}”’")
            pause = next((p for mark, p in _PHRASE_PAUSE if core.endswith(mark)), 0.0)
            if not syls and pause == 0.0:
                pause = 0.1
            t += pause / speed
        return words, t + self.TAIL_SEC

    # -- prosody ------------------------------------------------------------------------
    def _prosody(
        self, words: list[_Word], f0_base: float, mono: bool, speed: float, rng: np.random.Generator
    ) -> list[_Job]:
        """Phrase declination, stress accents, rising questions -> one :class:`_Job` per syllable."""
        slot = self.SYLLABLE_SEC / speed
        gap = self.WORD_GAP / speed
        phrases: list[list[_Word]] = [[]]
        for w in words:
            phrases[-1].append(w)
            if pause_weight(w.token) > 0:
                phrases.append([])
        jobs: list[_Job] = []
        for ph in phrases:
            count = sum(len(w.syls) for w in ph)
            if count == 0:
                continue
            tail = ph[-1].token.rstrip("\"')]}”’")
            question, shout, comma = (
                tail.endswith("?"),
                tail.endswith("!"),
                tail.endswith((",", ";", ":")),
            )
            done = 0
            for w in ph:
                bare = re.sub(r"[^a-z]", "", w.token.lower())
                content = len(bare) >= 4 or bare not in _FUNCTION_WORDS
                for k, s in enumerate(w.syls):
                    r = done / max(count - 1, 1)
                    decl = (1.07 - 0.07 * r) if comma else (1.1 - 0.22 * r)
                    if question:
                        decl *= 1.0 + 0.38 * float(_ramp(np.array([r]), 0.7, 1.0)[0])
                    s.stressed = s.first and content
                    f0 = (
                        f0_base
                        if mono
                        else f0_base
                        * decl
                        * (1.07 if s.stressed else 1.0)
                        * (1.0 + float(rng.uniform(-0.025, 0.025)))
                    )
                    if shout and not mono:
                        f0 *= 1.06
                    amp = (
                        (1.0 if s.stressed else 0.82)
                        * (1.15 if shout else 1.0)
                        * (0.85 if done == count - 1 else 1.0)
                    )
                    t0 = w.start + k * slot
                    jobs.append(_Job(s, t0, t0 + slot - (gap if s.last else 0.0), f0, amp))
                    done += 1
        return jobs

    # -- synthesis ----------------------------------------------------------------------
    def _render(self, text: str, voice: str | None, speed: float) -> TTSClip:
        sr = self.sample_rate
        speed = float(np.clip(speed, 0.25, 4.0))
        f0_base, fscale, mono = self.voice_params(voice)
        rng = np.random.default_rng(stable_int("babble", voice or "", text))
        words, total = self._layout(text.split(), speed)
        out = np.zeros(round(total * sr))
        for job in self._prosody(words, f0_base, mono, speed, rng):
            seg = self._syllable(job, fscale, mono, speed, rng)
            add_at(out, seg * job.amp, round(job.t0 * sr))
        peak = float(np.max(np.abs(out))) if out.size else 0.0
        if peak > 0.0:
            out = np.tanh(out / peak * 1.25) / np.tanh(1.25) * 0.85
        timings = [(w.start, max(w.end, w.start + 0.02), w.token) for w in words]
        return TTSClip(out.astype(np.float32), sr, timings)

    def _syllable(
        self, job: _Job, fscale: float, mono: bool, speed: float, rng: np.random.Generator
    ) -> np.ndarray:
        """One syllable: obstruent onset/coda noise + a voiced nucleus gliding to sonorant loci."""
        sr = self.sample_rate
        s = job.syl
        n = max(16, round((job.t1 - job.t0) * sr))
        seg = np.zeros(n)
        vow = np.array(s.vowel) * fscale
        on_son, co_son = s.onset in _SONORANT, s.coda in _SONORANT
        d_on = _ONSET_SEC[s.onset] / speed
        d_co = _CODA_SEC[s.coda] / speed if s.last else 0.0
        room = max(n / sr - 0.07, 0.03)
        if d_on + d_co > room:
            k = room / (d_on + d_co)
            d_on, d_co = d_on * k, d_co * k
        i_on, i_co = int(d_on * sr), int(d_co * sr)
        if s.onset and not on_son and i_on > 8:
            seg[:i_on] += self._obstruent(s.onset, i_on, vow, rng, job.f0)
        if s.last and s.coda and not co_son and i_co > 8:
            seg[n - i_co :] += self._obstruent(s.coda, i_co, vow, rng, job.f0)
        v0 = i_on if (s.onset and not on_son and i_on > 8) else 0
        v1 = n - i_co if (s.last and s.coda and not co_son and i_co > 8) else n
        m = max(v1 - v0, 8)
        t = np.arange(m) / sr
        u = t / (m / sr)
        pitch = job.f0 * (1.0 + 0.006 * np.sin(2 * np.pi * 4.8 * t + rng.uniform(0, 6.28)))
        if not mono:
            pitch = (
                pitch
                * (1.0 + 0.05 * (0.5 - u))
                * (1.0 + (0.04 * smooth_bump(u, 0.35) if s.stressed else 0.0))
            )
        tracks = np.tile(vow[:, None], (1, m))
        attack = np.clip(t / 0.016, 0.0, 1.0)
        if on_son:
            locus = np.array(_LOCUS[s.onset]) * fscale
            tracks += (locus - vow)[:, None] * np.exp(-t / (0.03 + 0.5 * d_on))[None, :]
            attack = 0.4 + 0.6 * _ramp(t, 0.0, d_on + 0.025)
        elif s.onset:
            tracks[1] *= 1.0 + (_F2_SHIFT.get(s.onset, 1.0) - 1.0) * np.exp(-t / 0.03)
        release = 1.0 - _ramp(t, m / sr - 0.03, m / sr)
        if s.last and co_son:
            locus = np.array(_LOCUS[s.coda]) * fscale
            w_end = np.exp(-(m / sr - t) / 0.035)
            tracks += (locus[:, None] - tracks) * w_end[None, :]
            release = (1.0 - 0.45 * _ramp(t, m / sr - 0.07, m / sr - 0.02)) * release
        vowel = vocal_tract(pitch, tracks, sr, (95.0, 115.0, 170.0), tilt=1.05, max_freq=5600.0)
        f1, f2, f3 = vow
        asp = spectral_filter(
            rng.standard_normal(m),
            sr,
            lambda f: formant_response(f, (f1, f2, f3), (180.0, 220.0, 320.0)),
        )
        asp /= float(np.max(np.abs(asp))) + 1e-9
        seg[v0 : v0 + m] += (vowel * 0.95 + asp * 0.06) * attack * release
        return seg

    def _obstruent(
        self, kind: str, n: int, vow: np.ndarray, rng: np.random.Generator, f0: float
    ) -> np.ndarray:
        """Noise-based consonant of ``n`` samples: stop (closure + burst), fricative, or 'h'."""
        sr = self.sample_rate
        t = np.arange(n) / sr
        w = rng.standard_normal(n)
        out = np.zeros(n)
        voiced = kind in ("b", "d", "g", "z", "v")
        if kind in ("p", "t", "k", "b", "d", "g"):
            burst_n = min(n, int(0.014 * sr))
            lo, hi, amp = {
                "p": (400, 2500, 0.28),
                "b": (400, 2500, 0.2),
                "t": (3500, 9000, 0.3),
                "d": (3200, 8000, 0.24),
                "k": (1300, 3600, 0.3),
                "g": (1200, 3400, 0.24),
            }[kind]
            b = bandpass(w[:burst_n], sr, lo, min(hi, 0.45 * sr), order=2) * np.exp(
                -np.arange(burst_n) / sr / 0.005
            )
            out[n - burst_n :] += b * (amp / (float(np.max(np.abs(b))) + 1e-9))
            if voiced:  # low voicing bar through the closure
                out += np.sin(2 * np.pi * f0 * t) * 0.12 * (1.0 - _ramp(t, n / sr * 0.7, n / sr))
        elif kind in ("s", "z", "sh", "f", "v"):
            lo, hi, amp = {
                "s": (4500, 9800, 0.3),
                "z": (4500, 9800, 0.22),
                "sh": (2200, 6500, 0.3),
                "f": (1500, 9000, 0.14),
                "v": (1500, 9000, 0.1),
            }[kind]
            x = bandpass(w, sr, lo, min(hi, 0.45 * sr), order=2)
            shape = _ramp(t, 0.0, n / sr * 0.35) * (1.0 - _ramp(t, n / sr * 0.7, n / sr))
            out += x * (amp / (float(np.max(np.abs(x))) + 1e-9)) * shape
            if voiced:
                out += np.sin(2 * np.pi * f0 * t) * 0.1 * shape
        elif kind == "h":
            x = spectral_filter(
                w, sr, lambda f: formant_response(f, tuple(vow), (200.0, 240.0, 340.0))
            )
            shape = _ramp(t, 0.0, n / sr * 0.3) * (1.0 - _ramp(t, n / sr * 0.7, n / sr))
            out += x * (0.2 / (float(np.max(np.abs(x))) + 1e-9)) * shape
        return out


# ============================================================================== macOS say
_SAY_NOVELTY = frozenset(
    [
        "Albert",
        "Bad News",
        "Bahh",
        "Bells",
        "Boing",
        "Bubbles",
        "Cellos",
        "Deranged",
        "Good News",
        "Hysterical",
        "Jester",
        "Junior",
        "Organ",
        "Pipe Organ",
        "Superstar",
        "Trinoids",
        "Whisper",
        "Wobble",
        "Zarvox",
        "Fred",
        "Kathy",
        "Ralph",
    ]
)
_FEMALE_TERMS = frozenset(
    """mrs ms miss mom mum mother mama grandma granny nana aunt sister girl woman lady queen princess
    mia emma olivia sophia sophie lily anna sara sarah maya zoe ella ava nora ruby grace chloe amy emily
    lucy luna nina rosa julia kate katie laura eva ivy jane jill kim lena lisa mary priya riya diya aisha
    maria sofia isla amelia ananya kavya neha pooja anjali sana fatima leila meera""".split()  # noqa: SIM905
)


def reads_female(name: str | None) -> bool:
    """True when a display name looks female (honorific, kinship word or common given name)."""
    words = re.findall(r"[a-z]+", (name or "").lower())
    return any(w in _FEMALE_TERMS for w in words)


_SAY_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<lang>[a-z]{2,3}_[A-Za-z0-9]{2,})\s+#")
#: a pitch riding on a ``say`` voice name: ``"Lekha (Hindi (India))|pbas=62"`` (``pbas`` is say's baseline pitch, 0-127)
_SAY_PITCH = re.compile(r"\|pbas=(\d{1,3})$")
#: baseline pitch (``[[pbas N]]``) that sets a character's voice apart when the language leaves only one voice to share
_SAY_PITCHES: dict[str, int] = {
    "junior": 64, "flo": 60, "kathy": 58, "tessa": 56, "sandy": 56, "karen": 53, "samantha": 50,
    "shelley": 52, "moira": 50, "grandma": 45, "eddy": 46, "daniel": 40, "albert": 38, "grandpa": 36,
    "reed": 34, "rocko": 32, "ralph": 30, "fred": 28, "zarvox": 24, "trinoids": 24,
}  # fmt: skip


class MacSayTTS(CachedEngine):
    """macOS ``say`` -> AIFF -> mono float32 (ffmpeg).  Default voice Samantha, else an en_US voice."""

    name = "say"
    version = "1"
    DEFAULT_VOICE = "Samantha"
    BASE_WPM = 175.0

    def __init__(
        self, *, say_binary: str | None = None, runner: Callable[..., Any] | None = None
    ) -> None:
        self._say = say_binary or shutil.which("say")
        self._runner = runner or subprocess.run
        self._voice_cache: tuple[tuple[str, str], ...] | None = None

    def available(self) -> bool:
        return sys.platform == "darwin" and self._say is not None

    # -- voices ---------------------------------------------------------------------------
    def _voices(self) -> tuple[tuple[str, str], ...]:
        """Installed voices as ``(name, language)`` from ``say -v ?``."""
        if self._voice_cache is None:
            found: list[tuple[str, str]] = []
            if self._say:
                try:
                    res = self._runner(
                        [self._say, "-v", "?"], capture_output=True, text=True, timeout=20
                    )
                    seen: set[str] = set()
                    for line in (res.stdout or "").splitlines():
                        m = _SAY_LINE.match(line)
                        if m and m["name"] not in seen:
                            seen.add(m["name"])
                            found.append((m["name"], m["lang"]))
                except (OSError, subprocess.SubprocessError):
                    pass
            self._voice_cache = tuple(found)
        return self._voice_cache

    def list_voices(self) -> list[str]:
        return [name for name, _ in self._voices()]

    def _match(self, voice: str) -> str | None:
        want = voice.strip().lower()
        voices = self._voices()
        for name, _ in voices:
            if name.lower() == want:
                return name
        loose = [(name, lang) for name, lang in voices if name.lower().split(" (")[0] == want]
        if not loose:
            return None
        # "Flo" is installed in a dozen languages: prefer English (US first), then whatever comes first
        return min(
            loose, key=lambda v: 0 if v[1] == "en_US" else 1 if v[1].startswith("en_") else 2
        )[0]

    def default_voice(self) -> str | None:
        hit = self._match(self.DEFAULT_VOICE)
        if hit:
            return hit
        voices = self._voices()
        for name, lang in voices:
            if lang == "en_US" and name.split(" (")[0] not in _SAY_NOVELTY:
                return name
        return voices[0][0] if voices else None

    #: archetype -> voices to try in order (``_f`` = a female-sounding name); the first installed wins
    ARCHETYPE_VOICES: dict[str, tuple[str, ...]] = {
        "kid": ("Junior", "Flo", "Kathy"),
        "kid_f": ("Flo", "Kathy", "Junior"),
        "elder": ("Grandpa", "Albert", "Fred"),
        "elder_f": ("Grandma", "Moira", "Samantha"),
        "robot": ("Fred", "Zarvox", "Trinoids"),
        "boss": ("Ralph", "Reed", "Rocko"),
        "hero": ("Daniel", "Rocko", "Eddy"),
    }
    #: everyone else (and archetypes whose voices are not installed): a stable pick by character id
    GENERIC_VOICES = ("Samantha", "Daniel", "Karen", "Moira", "Tessa", "Reed", "Sandy", "Shelley")

    def speaker_voice(
        self, speaker: str | None, character: CharacterSpec | None = None
    ) -> str | None:
        """A voice that suits the character, so a story does not sound like one person doing every part."""
        if speaker is None and character is None:
            return None
        arch = character.archetype if character else ""
        label = (character.name or character.id) if character else (speaker or "")
        key = f"{arch}_f" if reads_female(label) and f"{arch}_f" in self.ARCHETYPE_VOICES else arch
        for candidate in self.ARCHETYPE_VOICES.get(key, ()):
            hit = self._match(candidate)
            if hit:
                return hit
        pool = [hit for c in self.GENERIC_VOICES if (hit := self._match(c))]
        return pool[stable_int("say-voice", speaker or label) % len(pool)] if pool else None

    @staticmethod
    def split_pitch(voice: str | None) -> tuple[str | None, int | None]:
        """``"Lekha|pbas=62"`` -> ``("Lekha", 62)``; a plain voice name has no pitch of its own."""
        if voice:
            m = _SAY_PITCH.search(voice)
            if m:
                return voice[: m.start()], min(127, int(m.group(1)))
        return voice, None

    def voice_language(self, voice: str | None) -> str | None:
        name = self.resolve_voice(self.split_pitch(voice)[0])
        for vname, lang in self._voices():
            if vname == name:
                return lang.split("_")[0].lower()
        return None

    def voice_for_text(self, text: str, voice: str | None) -> str | None:
        """An English voice reads Hindi or Japanese as gibberish: use an installed voice for the text's language.

        When the swap puts several characters on the one voice a language has (a Hindi story with one Hindi voice
        installed), each keeps a pitch of their own, taken from the voice they would have had, so the cast
        is still told apart by ear.  Narration (no voice of its own) and an explicit voice in the right language
        are left alone.
        """
        lang = text_language(text)
        if lang is None or self.voice_language(voice) == lang:
            return voice
        for vname, vlang in self._voices():
            if vlang.split("_")[0].lower() == lang:
                if not voice:
                    return vname
                name, pitch = self.split_pitch(voice)
                if pitch is None:
                    key = (name or "").split(" (")[0].strip().lower()
                    pitch = _SAY_PITCHES.get(key, 36 + stable_int("say-pitch", key) % 28)
                return f"{vname}|pbas={pitch}"
        return voice  # none installed: the pipeline warns

    def resolve_voice(self, voice: str | None) -> str | None:
        name, pitch = self.split_pitch(voice)
        resolved = None
        if name:
            resolved = self._match(name)
        if resolved is None:
            resolved = self.default_voice()
        return resolved if pitch is None or resolved is None else f"{resolved}|pbas={pitch}"

    def voice_warning(self, voice: str | None) -> str | None:
        name = self.split_pitch(voice)[0]
        if name and self._match(name) is None:
            return f"macOS has no voice named {name!r}; using {self.default_voice()!r}"
        return None

    # -- synthesis ------------------------------------------------------------------------
    def build_command(
        self, voice: str | None, speed: float, out: Path, text_file: Path, *, wav: bool = False
    ) -> list[str]:
        """The exact ``say`` invocation (``-r`` words/min from ``speed``); exposed for tests."""
        cmd = [self._say or "say"]
        if voice:
            cmd += ["-v", voice]
        if abs(speed - 1.0) > 1e-3:
            cmd += ["-r", str(int(np.clip(round(self.BASE_WPM * speed), 90, 400)))]
        if wav:
            cmd += ["--file-format=WAVE", "--data-format=LEI16@22050"]
        cmd += ["-o", str(out), "-f", str(text_file)]
        return cmd

    def _render(self, text: str, voice: str | None, speed: float) -> TTSClip:
        if not self.available():
            raise TTSUnavailableError("the macOS `say` command is not available on this machine")
        chosen, pitch = self.split_pitch(self.resolve_voice(voice))
        use_ffmpeg = ffmpeg_exe() is not None
        with tempfile.TemporaryDirectory(prefix="reel-say-") as td:
            tmp = Path(td)
            txt = tmp / "in.txt"
            # an embedded command, not spoken: it sets the baseline pitch for this line
            txt.write_text(
                f"[[pbas {pitch}]] {text}" if pitch is not None else text, encoding="utf-8"
            )
            out = tmp / ("out.aiff" if use_ffmpeg else "out.wav")
            cmd = self.build_command(chosen, speed, out, txt, wav=not use_ffmpeg)
            res = self._runner(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode != 0 or not out.exists():
                raise RuntimeError(f"`say` failed ({res.returncode}): {(res.stderr or '').strip()}")
            if use_ffmpeg:
                data = decode_audio(out, sr=48_000, channels=1)
                return TTSClip(data[:, 0].copy(), 48_000)
            try:
                wav, sr = read_wav(out)
            except Exception as exc:
                raise AudioDecodeError(f"cannot read `say` output: {exc}") from exc
            return TTSClip(to_mono(wav), sr)


# ============================================================================== piper
_PIPER_HEALTH: dict[str, bool] = {}


class PiperTTS(CachedEngine):
    """Piper neural TTS through its CLI (``piper --model voice.onnx --output_file out.wav``).

    ``voice`` is a path to an ``.onnx`` model or a short name looked up (as ``<name>.onnx``) in
    ``$PIPER_VOICES``, ``~/.local/share/piper`` and ``./voices``; ``name:3`` picks speaker 3 of a
    multi-speaker model.  When the ``piper`` Python package is importable (and ``use_python`` is
    not disabled) it runs in-process, which avoids reloading the model for every caption.
    """

    name = "piper"
    version = "1"

    def __init__(
        self,
        voice: str | None = None,
        *,
        binary: str | None = None,
        voice_dirs: Sequence[str | Path] | None = None,
        runner: Callable[..., Any] | None = None,
        use_python: bool = True,
        timeout: float = 180.0,
    ) -> None:
        self.default_voice = voice
        self._binary = binary
        self._dirs = [Path(d).expanduser() for d in voice_dirs] if voice_dirs is not None else None
        self._runner = runner
        self._use_python = use_python
        self.timeout = timeout
        self._py_voices: dict[str, Any] = {}

    # -- discovery ------------------------------------------------------------------------
    def voice_dirs(self) -> list[Path]:
        if self._dirs is not None:
            return self._dirs
        dirs = [
            Path(p).expanduser()
            for p in os.environ.get("PIPER_VOICES", "").split(os.pathsep)
            if p.strip()
        ]
        dirs += [Path.home() / ".local" / "share" / "piper", Path("voices")]
        return dirs

    def models(self) -> dict[str, Path]:
        """Voice name (file stem) -> ``.onnx`` path for every model in the search dirs."""
        found: dict[str, Path] = {}
        for d in self.voice_dirs():
            if d.is_dir():
                for p in sorted([*d.glob("*.onnx"), *d.glob("*/*.onnx")]):
                    found.setdefault(p.stem, p)
        return found

    def list_voices(self) -> list[str]:
        return sorted(self.models())

    @staticmethod
    def _first_model(models: dict[str, Path]) -> Path | None:
        for prefix in ("en_US", "en_GB", "en_"):
            for stem, p in models.items():
                if stem.startswith(prefix):
                    return p
        return next(iter(models.values()), None)

    def resolve_model(self, voice: str | None) -> Path | None:
        """The ``.onnx`` for ``voice`` (path or name); ``None`` voice -> configured/first model."""
        want = voice or self.default_voice or os.environ.get("PIPER_VOICE")
        models = self.models()
        if not want:
            return self._first_model(models)
        p = Path(want).expanduser()
        if p.suffix == ".onnx" and p.is_file():
            return p
        stem = p.stem if p.suffix == ".onnx" else str(want)
        if stem in models:
            return models[stem]
        return None if voice else self._first_model(models)

    @staticmethod
    def split_speaker(voice: str | None) -> tuple[str | None, int | None]:
        """``'name:3'`` -> ``('name', 3)``; paths and plain names come back unchanged."""
        if voice and re.search(r":\d+$", voice) and not Path(voice).exists():
            base, _, sid = voice.rpartition(":")
            return base, int(sid)
        return voice, None

    def resolve_voice(self, voice: str | None) -> str | None:
        base, speaker = self.split_speaker(voice)
        model = self.resolve_model(base)
        if model is None:
            return voice
        try:
            size = model.stat().st_size
        except OSError:
            size = 0
        return f"{model}:{size}" + (f"#{speaker}" if speaker is not None else "")

    def voice_warning(self, voice: str | None) -> str | None:
        base, _ = self.split_speaker(voice)
        if base and self.resolve_model(base) is None:
            searched = ", ".join(map(str, self.voice_dirs()))
            return f"piper has no voice model {base!r} (searched {searched}); using the default"
        return None

    def _python_ok(self) -> bool:
        if not self._use_python:
            return False
        try:
            import piper

            return hasattr(piper, "PiperVoice")
        except Exception:
            return False

    def _launcher(self) -> list[str] | None:
        exe = self._binary or shutil.which("piper")
        if exe is None:
            return None
        if self._runner is None:  # real subprocess: check the install really starts (cached)
            ok = _PIPER_HEALTH.get(exe)
            if ok is None:
                try:
                    ok = (
                        subprocess.run([exe, "--help"], capture_output=True, timeout=30).returncode
                        == 0
                    )
                except (OSError, subprocess.SubprocessError):
                    ok = False
                _PIPER_HEALTH[exe] = ok
            if not ok:
                return None
        return [exe]

    def available(self) -> bool:
        if self.resolve_model(None) is None:
            return False
        return self._python_ok() or self._launcher() is not None

    # -- synthesis ------------------------------------------------------------------------
    def build_command(
        self, model: Path, out_wav: Path, speed: float = 1.0, speaker: int | None = None
    ) -> list[str]:
        """The exact CLI invocation (the text goes on stdin); exposed for tests."""
        exe = self._binary or shutil.which("piper") or "piper"
        cmd = [exe, "--model", str(model), "--output_file", str(out_wav)]
        if abs(speed - 1.0) > 1e-3:
            cmd += ["--length_scale", f"{1.0 / max(speed, 0.1):.4f}"]
        if speaker is not None:
            cmd += ["--speaker", str(speaker)]
        return cmd

    def _render(self, text: str, voice: str | None, speed: float) -> TTSClip:
        voice, speaker = self.split_speaker(voice)
        model = self.resolve_model(voice)
        if model is None:
            raise TTSUnavailableError(
                "no piper voice model found: pass an .onnx path, or put models in $PIPER_VOICES, "
                "~/.local/share/piper or ./voices"
            )
        if self._python_ok():
            try:
                return self._render_python(model, text, speed, speaker)
            except Exception:
                if self._launcher() is None:
                    raise
        return self._render_cli(model, text, speed, speaker)

    def _render_cli(self, model: Path, text: str, speed: float, speaker: int | None) -> TTSClip:
        runner = self._runner or subprocess.run
        with tempfile.TemporaryDirectory(prefix="reel-piper-") as td:
            out = Path(td) / "out.wav"
            cmd = self.build_command(model, out, speed, speaker)
            res = runner(
                cmd,
                input=(text.replace("\n", " ").strip() + "\n").encode("utf-8"),
                capture_output=True,
                timeout=self.timeout,
            )
            if res.returncode != 0:
                err = (
                    res.stderr.decode(errors="replace")
                    if isinstance(res.stderr, bytes)
                    else str(res.stderr)
                )
                raise RuntimeError(f"piper failed ({res.returncode}): {err.strip()[-400:]}")
            if not out.exists():
                raise RuntimeError("piper produced no output file")
            data, sr = read_wav(out)
        return TTSClip(to_mono(data), sr)

    def _render_python(self, model: Path, text: str, speed: float, speaker: int | None) -> TTSClip:
        from piper import PiperVoice, SynthesisConfig

        key = str(model)
        voice = self._py_voices.get(key)
        if voice is None:
            voice = PiperVoice.load(model)
            self._py_voices[key] = voice
        cfg = SynthesisConfig(length_scale=1.0 / max(speed, 0.1), speaker_id=speaker)
        parts: list[np.ndarray] = []
        sr = 22_050
        for chunk in voice.synthesize(text, cfg):
            sr = int(chunk.sample_rate)
            parts.append(np.asarray(chunk.audio_float_array, dtype=np.float32).reshape(-1))
        if not parts:
            raise RuntimeError("piper returned no audio")
        return TTSClip(np.concatenate(parts), sr)


# ============================================================================== factory
ENGINE_NAMES = ("auto", "piper", "say", "babble", "elevenlabs")
_ALIASES = {
    "mac": "say",
    "macos": "say",
    "macsay": "say",
    "apple": "say",
    "eleven": "elevenlabs",
    "11labs": "elevenlabs",
    "elevenlab": "elevenlabs",
    "default": "auto",
    "": "auto",
}


def get_tts_engine(name: str | None = None) -> TTSEngine:
    """Engine by name: ``auto`` (piper -> say -> babble), ``piper``, ``say``, ``babble`` or ``elevenlabs``.

    With no name at all, ``REEL_TTS`` (shell or ``.env``) decides, else ``auto``.  ``auto`` never picks the online
    engine ``elevenlabs``: it sends your dialogue to a third party and bills credits, so it has to be asked for.
    A named engine that cannot run here raises :class:`TTSUnavailableError`; an unknown name
    raises :class:`UnknownTTSEngineError` listing the valid ones.
    """
    if name is None:
        from reel.llm.client import llm_env

        name = llm_env().get("REEL_TTS")
    key = (name or "auto").strip().lower()
    key = _ALIASES.get(key, key)
    if key == "elevenlabs":
        from reel.audio.elevenlabs import ElevenLabsTTS

        eleven = ElevenLabsTTS()
        eleven.require_key()
        return eleven
    if key == "auto":
        piper = PiperTTS()
        if piper.available():
            return piper
        say = MacSayTTS()
        if say.available():
            return say
        return BabbleTTS()
    if key == "babble":
        return BabbleTTS()
    if key == "say":
        say = MacSayTTS()
        if not say.available():
            raise TTSUnavailableError("TTS engine 'say' needs macOS and the `say` command")
        return say
    if key == "piper":
        piper = PiperTTS()
        if not piper.available():
            raise TTSUnavailableError(
                "TTS engine 'piper' is not usable: it needs the `piper` command (or package) to start "
                "(`piper --help`) and a voice .onnx model in $PIPER_VOICES, ~/.local/share/piper or ./voices"
            )
        return piper
    raise UnknownTTSEngineError(
        f"unknown TTS engine {name!r}; choose one of: {', '.join(ENGINE_NAMES)}"
    )
