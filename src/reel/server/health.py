"""Environment health for the UI (the structured twin of ``reel doctor``).

Status checks never make a paid or key-bearing request.  The explicit *tests* (:func:`test_llm`, :func:`test_tts`) do, once,
only when the user clicks the button that says what it costs; their results never contain a key.
"""

from __future__ import annotations

import functools
import os
import sys
import time
from typing import Any

from reel import __version__
from reel.core.cache import default_cache_dir


@functools.lru_cache(maxsize=1)
def _ffmpeg() -> dict[str, Any]:
    from reel.core.ffmpeg import ffmpeg_version

    v = ffmpeg_version()
    return {"ok": not v.startswith("unavailable"), "version": v}


@functools.lru_cache(maxsize=1)
def _skia() -> dict[str, Any]:
    try:
        import skia

        return {"ok": True, "version": skia.__version__}
    except ImportError:
        return {"ok": False, "version": "missing"}


def health() -> dict[str, Any]:
    from reel.core.catalog import CATALOG

    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "ffmpeg": _ffmpeg()["version"],
        "ffmpeg_ok": _ffmpeg()["ok"],
        "skia": _skia()["version"],
        "workers": max(1, (os.cpu_count() or 4) - 2),
        "cache_dir": str(default_cache_dir()),
        "plugins": list(getattr(CATALOG, "plugin_files", [])),
    }


def _llm_keys() -> list[dict[str, Any]]:
    from reel.llm.client import llm_env

    env = llm_env()
    out = []
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        source = "shell" if os.environ.get(name) else (".env" if env.get(name) else None)
        out.append({"name": name, "source": source})
    return out


def _ollama() -> dict[str, Any]:
    import httpx

    from reel.llm.client import OLLAMA_URL

    try:
        data = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=0.7).json()
        models = [
            {"name": m["name"], "remote": str(m["name"]).endswith("-cloud")} for m in data["models"]
        ]
        return {"running": True, "url": OLLAMA_URL, "models": models}
    except Exception:
        return {"running": False, "url": OLLAMA_URL, "models": []}


def doctor() -> dict[str, Any]:
    from reel.core import fonts
    from reel.core.catalog import CATALOG
    from reel.llm.client import llm_env
    from reel.server.audio_api import engines_status

    roles = (("headline", fonts.HEADLINE), ("sans", fonts.SANS_BOLD), ("marker", fonts.MARKER))
    env = llm_env()
    tts = engines_status()
    return {
        "reel": __version__,
        "python": sys.version.split()[0],
        "ffmpeg": _ffmpeg(),
        "skia": _skia(),
        "fonts": [
            {"role": role, "family": fonts.typeface(stack).getFamilyName()} for role, stack in roles
        ],
        "tts": tts["engines"],
        "tts_default": tts["default"],
        "llm": {"ollama": _ollama(), "keys": _llm_keys(), "default": env.get("REEL_LLM")},
        "counts": {
            "actions": len(CATALOG.actions),
            "styles": len(CATALOG.styles),
            "backgrounds": len(CATALOG.backgrounds),
            "transitions": len(CATALOG.transitions),
            "assets": len(CATALOG.assets),
        },
        "cache_dir": str(default_cache_dir()),
    }


def test_llm(target: str) -> dict[str, Any]:
    """ONE tiny request (about 100 tokens) to check the key, the model name and the connection."""
    from reel.llm.client import LLMError, Message, get_client, llm_env

    t0 = time.perf_counter()
    try:
        client = get_client(target, env=llm_env())
        reply = client.complete(
            "You are a connectivity test for a video tool. Answer with a single word.",
            [Message("user", "Reply with exactly: OK")],
            temperature=0.0,
            max_tokens=256,
        )
    except (LLMError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}
    dest = getattr(client, "destination", "")
    return {
        "ok": True,
        "message": f"{client.name}{' at ' + dest if dest else ''} answered {reply.strip()[:30]!r} in {time.perf_counter() - t0:.1f}s",
        "cost": "about 100 tokens",
    }


def test_tts(target: str) -> dict[str, Any]:
    from reel.audio.tts import TTSUnavailableError, UnknownTTSEngineError
    from reel.server.audio_api import check_engine

    try:
        return {**check_engine(target), "cost": "no credits spent"}
    except (TTSUnavailableError, UnknownTTSEngineError) as exc:
        return {"ok": False, "message": str(exc)}
