"""The catalog the UI is built from: everything a spec may reference, with the details forms need.

It starts from :func:`reel.core.manifest.build_manifest` (the same data the LLM reads) and adds what a visual editor needs:
where each background's named slots are on screen, each archetype's default colours, the music moods and the hard limits.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from reel.core.catalog import CATALOG
from reel.core.lint import PALETTE_ROLES, UNIVERSAL_SLOTS
from reel.core.manifest import build_manifest
from reel.core.planner import DEFAULT_GROUND_Y, UNIVERSAL_SLOT_X
from reel.core.spec import MAX_TOTAL_SEC, MIN_TOTAL_SEC


def build_catalog() -> dict[str, Any]:
    from reel.audio.music import MUSIC_MOODS

    cat = build_manifest(CATALOG)
    for bg in cat["backgrounds"]:
        obj = CATALOG.backgrounds.get(bg["name"])
        ground = float(getattr(obj, "ground_y", DEFAULT_GROUND_Y))
        slots: dict[str, list[float]] = {name: [x, ground] for name, x in UNIVERSAL_SLOT_X.items()}
        for name, pt in dict(getattr(obj, "slots", {}) or {}).items():
            slots[str(name)] = [float(pt[0]), float(pt[1])]
        bg["slots"] = slots
        bg["ground_y"] = ground
        bg["perspective"] = float(
            getattr(obj, "perspective", 1.0)
        )  # on-screen size = CHAR_UNIT * scale * max(.35, 1 + perspective * (y - .8))
    for arch in cat["archetypes"]:
        obj = CATALOG.archetypes.get(arch["name"])
        arch["palette"] = dict(getattr(obj, "palette", {}) or {})
        arch["height"] = float(
            getattr(obj, "height", 575.0)
        )  # design units; a character is ~height*1.12/1920 of the frame
    cat["easings"] = [
        {"name": n, "summary": ""} if isinstance(n, str) else n for n in cat["easings"]
    ]
    cat["music_moods"] = list(MUSIC_MOODS)
    cat["palette_roles"] = list(PALETTE_ROLES)
    cat["universal_slots"] = list(UNIVERSAL_SLOTS)
    cat["limits"] = {
        "min_total_sec": MIN_TOTAL_SEC,
        "max_total_sec": MAX_TOTAL_SEC,
        "max_scene_sec": 30,
    }
    return cat


_WORDS = re.compile(r"\w+", re.UNICODE)


def _script_language(text: str) -> str:
    for ch in text:
        o = ord(ch)
        if 0x0900 <= o <= 0x097F:
            return "hi"
        if 0x0600 <= o <= 0x06FF:
            return "ar"
        if 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF:
            return "ja"
    return "en"


def list_examples(examples_dir: Path | None) -> dict[str, Any]:
    """The repository's example specs and scripts (empty when running from an install without ``examples/``)."""
    from reel.server.workspace import spec_summary

    specs: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    if examples_dir is None:
        return {"specs": specs, "scripts": scripts}
    import json

    for p in sorted(examples_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        specs.append({"id": p.stem, **spec_summary(data)})
    files = sorted([*examples_dir.glob("*.txt"), *(examples_dir / "scripts").glob("*.txt")])
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        first = text.strip().splitlines()[0].strip() if text.strip() else p.stem
        scripts.append(
            {
                "id": p.stem,
                "title": first[:80],
                "language": _script_language(text),
                "words": len(_WORDS.findall(text)),
                "text": text,
            }
        )
    return {"specs": specs, "scripts": scripts}
