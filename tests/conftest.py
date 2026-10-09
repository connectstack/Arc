"""Shared fixtures.

`mini_catalog` is an isolated Catalog with just enough registered for the linter tests,
so step-by-step behaviour is tested without depending on the real builtin content.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from reel.core.catalog import Catalog


class WalkParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: list[float] | str | None = None
    speed: float = Field(1.0, gt=0, le=3)


class WaveParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    waves: int = Field(3, ge=1, le=10)


class RoomParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_of_day: str = "day"
    density: float = Field(0.5, ge=0, le=1)


@pytest.fixture
def mini_catalog() -> Catalog:
    c = Catalog()
    c.styles.register("flat_vector", SimpleNamespace(summary="flat"))
    c.styles.register("stickman", SimpleNamespace(summary="stick"))
    c.actions.register(
        "idle", SimpleNamespace(params_model=None, moves_root=False, min_duration=0.0)
    )
    c.actions.register(
        "walk", SimpleNamespace(params_model=WalkParams, moves_root=True, min_duration=0.4)
    )
    c.actions.register(
        "wave", SimpleNamespace(params_model=WaveParams, moves_root=False, min_duration=0.5)
    )
    c.actions.register(
        "talk", SimpleNamespace(params_model=None, moves_root=False, min_duration=0.3)
    )
    c.backgrounds.register(
        "room", SimpleNamespace(params_model=RoomParams, slot_names=lambda p: ["sofa", "door"])
    )
    c.backgrounds.register("street", SimpleNamespace(params_model=None, slot_names=("curb",)))
    for t in ("cut", "crossfade", "wipe", "page_flip"):
        c.transitions.register(t, SimpleNamespace(params_model=None))
    for m in ("pan", "zoom", "shake", "dolly", "rack_focus"):
        c.camera_moves.register(m, SimpleNamespace(check=None))
    for e in ("linear", "ease_in_out", "overshoot"):
        c.easings.register(e, lambda u: u)
    for s in ("pop", "whoosh"):
        c.sfx.register(s, SimpleNamespace())
    for a in ("everyman", "kid"):
        c.archetypes.register(a, SimpleNamespace())
    for p in ("hat", "briefcase"):
        c.props.register(p, SimpleNamespace())
    for cs in ("subtitle", "title", "shout"):
        c.caption_styles.register(cs, SimpleNamespace())
    return c


def _scene(sid: str, dur: float, **kw: Any) -> dict[str, Any]:
    scene: dict[str, Any] = {
        "id": sid,
        "duration_sec": dur,
        "background": {"template": "room", "params": {"time_of_day": "day"}},
        "camera": {"moves": []},
        "layers": [
            {
                "character": "ana",
                "position": [0.4, 0.8],
                "scale": 1.0,
                "actions": [{"name": "wave", "t0": 0.5, "t1": min(dur, 2.5), "params": {}}],
            }
        ],
        "captions": [{"text": "Hello there", "t0": 0.2, "t1": 2.0, "style": "subtitle"}],
        "sfx": [{"name": "pop", "t": 0.5}],
        "transition_out": {"type": "cut", "duration": 0},
    }
    scene.update(kw)
    return scene


@pytest.fixture
def spec_dict() -> dict[str, Any]:
    """A valid 50 s spec: 10 scenes x 5 s, all cuts, against `mini_catalog`."""
    return copy.deepcopy(
        {
            "version": "1.0",
            "meta": {
                "title": "Test reel",
                "style": "flat_vector",
                "fps": 30,
                "resolution": [1080, 1920],
                "seed": 7,
                "target_duration_sec": 50,
                "aspect": "9:16",
            },
            "characters": [
                {
                    "id": "ana",
                    "archetype": "everyman",
                    "palette": {"shirt": "#e63946"},
                    "props": ["hat"],
                }
            ],
            "scenes": [_scene(f"s{i}", 5.0) for i in range(10)],
            "audio": {"music": None, "voiceover": "none", "ducking": True},
        }
    )


@pytest.fixture
def make_scene() -> Any:
    return _scene
