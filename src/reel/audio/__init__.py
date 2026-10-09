"""Offline audio for reel: synthesised SFX, generative music, text-to-speech, word alignment,
mixing and the :func:`prepare_audio` pipeline that turns a scene spec into a soundtrack.

Names are imported lazily (PEP 562), so ``import reel.audio.sfx`` - which the catalog does to
register the effects - does not pull in the TTS, music and pipeline modules.

* ``sfx``      - ``register_sfx``, ``SfxDef``, ``synth`` (34 procedural effects + ``assets/sfx/*.wav``)
* ``music``    - ``MUSIC_MOODS``, ``generate_music``, ``parse_music_spec``
* ``tts``      - ``TTSEngine``/``TTSClip``, ``PiperTTS``, ``MacSayTTS``, ``BabbleTTS``, ``get_tts_engine``
* ``elevenlabs`` - ``ElevenLabsTTS``, the optional online voice (opt-in; ``--tts elevenlabs``)
* ``align``    - ``estimate_word_timings``
* ``mix``      - ``MixPlan``/``MixClip``/``MixReport``, ``mix_tracks``, ``duck_envelope``
* ``pipeline`` - ``prepare_audio``/``AudioPlan``
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "SfxDef": "sfx",
    "register_sfx": "sfx",
    "synth": "sfx",
    "MUSIC_MOODS": "music",
    "generate_music": "music",
    "parse_music_spec": "music",
    "TTSClip": "tts",
    "TTSEngine": "tts",
    "PiperTTS": "tts",
    "MacSayTTS": "tts",
    "BabbleTTS": "tts",
    "get_tts_engine": "tts",
    "ElevenLabsTTS": "elevenlabs",
    "estimate_word_timings": "align",
    "MixClip": "mix",
    "MixPlan": "mix",
    "MixReport": "mix",
    "duck_envelope": "mix",
    "mix_tracks": "mix",
    "AudioPlan": "pipeline",
    "prepare_audio": "pipeline",
}

__all__ = [
    "MUSIC_MOODS",
    "AudioPlan",
    "BabbleTTS",
    "ElevenLabsTTS",
    "MacSayTTS",
    "MixClip",
    "MixPlan",
    "MixReport",
    "PiperTTS",
    "SfxDef",
    "TTSClip",
    "TTSEngine",
    "duck_envelope",
    "estimate_word_timings",
    "generate_music",
    "get_tts_engine",
    "mix_tracks",
    "parse_music_spec",
    "prepare_audio",
    "register_sfx",
    "synth",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_EXPORTS])


if TYPE_CHECKING:
    from reel.audio.align import estimate_word_timings
    from reel.audio.elevenlabs import ElevenLabsTTS
    from reel.audio.mix import MixClip, MixPlan, MixReport, duck_envelope, mix_tracks
    from reel.audio.music import MUSIC_MOODS, generate_music, parse_music_spec
    from reel.audio.pipeline import AudioPlan, prepare_audio
    from reel.audio.sfx import SfxDef, register_sfx, synth
    from reel.audio.tts import BabbleTTS, MacSayTTS, PiperTTS, TTSClip, TTSEngine, get_tts_engine
