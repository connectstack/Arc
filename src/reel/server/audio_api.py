"""Speech engines, voices, auditions and the cost estimate, as plain data for the UI.

Nothing here makes a network call unless the caller asks for an online engine's voices or a sample; engine *status* is read
from the environment only (the key's presence and where it was found, never its value).
"""

from __future__ import annotations

import io
import os
import shutil
import wave
from typing import Any

import numpy as np

from reel.audio.tts import (
    BabbleTTS,
    MacSayTTS,
    PiperTTS,
    TTSEngine,
    TTSUnavailableError,
    UnknownTTSEngineError,
    get_tts_engine,
)


def wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    """Float audio (mono ``(n,)`` or ``(n, channels)``) as a 16-bit PCM WAV file in memory."""
    a = np.asarray(samples, dtype=np.float32)
    if a.ndim == 1:
        a = a[:, None]
    pcm = np.clip(np.rint(np.clip(a, -1.0, 1.0) * 32767.0), -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _key_source(name: str) -> str | None:
    """Where an environment variable's value comes from, without ever reading it out: ``shell``, ``.env`` or ``None``."""
    from reel.llm.client import llm_env

    if os.environ.get(name):
        return "shell"
    return ".env" if llm_env().get(name) else None


def engines_status() -> dict[str, Any]:
    """Every speech engine with whether it can run here (no network, no key value)."""
    from reel.audio.elevenlabs import DEFAULT_MODEL, ElevenLabsTTS
    from reel.llm.client import llm_env

    piper = PiperTTS()
    piper_exe = shutil.which("piper")
    if piper.available():
        piper_detail = f"ready; {len(piper.list_voices())} voice model(s)"
    elif piper_exe:
        piper_detail = "installed but no voice model: put a .onnx voice in ~/.local/share/piper (reel never downloads one)"
    else:
        piper_detail = "not installed (optional: pip install piper-tts, then add a voice model)"
    say = MacSayTTS()
    env = llm_env()
    key_source = _key_source("ELEVENLABS_API_KEY")
    eleven_detail = (
        "no key found: set ELEVENLABS_API_KEY in your shell or in a git-ignored .env file"
    )
    eleven_ok = False
    model = env.get("ELEVENLABS_MODEL") or DEFAULT_MODEL
    destination = "api.elevenlabs.io"
    if key_source:
        try:
            eleven = ElevenLabsTTS(env=env)
            eleven_ok = eleven.available()
            destination = eleven.destination
            model = eleven.model
            eleven_detail = f"key found in {key_source}; model {model}"
        except TTSUnavailableError as exc:
            eleven_detail = str(exc)
    engines = [
        {
            "name": "piper",
            "label": "Piper",
            "online": False,
            "available": piper.available(),
            "detail": piper_detail,
        },
        {
            "name": "say",
            "label": "macOS Say",
            "online": False,
            "available": say.available(),
            "detail": "ready" if say.available() else "macOS only",
        },
        {
            "name": "babble",
            "label": "Placeholder",
            "online": False,
            "available": True,
            "detail": "always available: a synthetic voice for lip-sync tests, not meant to be intelligible",
        },
        {
            "name": "elevenlabs",
            "label": "ElevenLabs",
            "online": True,
            "available": eleven_ok,
            "detail": eleven_detail,
            "destination": destination,
            "model": model,
            "key_source": key_source,
        },
    ]
    try:
        default = get_tts_engine(None).name
    except (TTSUnavailableError, UnknownTTSEngineError):
        default = "babble"
    return {"engines": engines, "default": default}


def get_engine(name: str | None) -> TTSEngine:
    """The engine ``name`` ('auto' / None = what ``--tts auto`` would pick), or a clear error."""
    return get_tts_engine(None if name in (None, "", "auto") else name)


def list_voices(name: str) -> list[dict[str, Any]]:
    """Voices of an engine as ``{id, name, traits}``; an online engine contacts its service (no credits are spent)."""
    engine = get_engine(name)
    fetch = getattr(engine, "fetch_voices", None)
    if fetch is not None:
        out: list[dict[str, Any]] = []
        for v in fetch():
            labels = v.get("labels") if isinstance(v.get("labels"), dict) else {}
            traits = {
                k: str(labels[k]) for k in ("gender", "age", "accent", "use_case") if labels.get(k)
            }
            if v.get("category"):
                traits["category"] = str(v["category"])
            out.append(
                {"id": str(v.get("voice_id")), "name": str(v.get("name", "?")), "traits": traits}
            )
        return sorted(out, key=lambda x: x["name"].lower())
    if isinstance(engine, MacSayTTS):
        return [
            {"id": n, "name": n, "traits": {"language": lang}}
            for n, lang in sorted(engine._voices())
        ]
    return [{"id": n, "name": n, "traits": {}} for n in engine.list_voices()]


def check_engine(name: str) -> dict[str, Any]:
    """One account check of an online engine (plan, credits left, model, voices); spends no credits."""
    from reel.audio.elevenlabs import ElevenLabsTTS

    engine = get_engine(name)
    if not isinstance(engine, ElevenLabsTTS):
        return {"ok": True, "lines": [f"{engine.name}: runs on this machine, nothing to check"]}
    lines = engine.check()
    return {
        "ok": True,
        "lines": lines,
        "account": engine.account(),
        "model": engine.model,
        "destination": engine.destination,
    }


def babble() -> BabbleTTS:
    return BabbleTTS()
