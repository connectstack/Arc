"""The scene spec: the one contract between "script -> JSON" and "JSON -> video".

Everything the renderer needs is in this document; the renderer never needs the
network, the original script or an LLM.  The models are strict (`extra="forbid"`)
so a typo like ``"duration"`` instead of ``"duration_sec"`` is reported instead of
silently ignored.  Names that live in a registry (actions, backgrounds, styles,
transitions ...) are plain strings here and are checked by :mod:`reel.core.lint`,
which is what lets plugins add new ones without touching the schema code.

Time convention: every ``t``/``t0``/``t1`` inside a scene is **scene-local seconds**.
Positions are normalised screen units: ``[0,0]`` top-left of the frame, ``[1,1]``
bottom-right, and a character's position is where its *feet* touch the ground.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reel.core.text import clean_data

SPEC_VERSION = "1.0"

MIN_TOTAL_SEC = 45.0
MAX_TOTAL_SEC = 60.0

Vec2 = Annotated[list[float], Field(min_length=2, max_length=2)]
Seconds = Annotated[float, Field(ge=0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------- meta
class FxSpec(_Strict):
    """Optional per-spec overrides of the style's post-processing. ``null`` keeps the style default;
    ``0`` switches an effect off.  Values are strengths, not absolute units."""

    grain: float | None = Field(None, ge=0, le=2, description="film grain strength")
    vignette: float | None = Field(None, ge=0, le=2, description="corner darkening strength")
    bloom: float | None = Field(None, ge=0, le=2, description="glow around bright areas")
    chromatic_aberration: float | None = Field(
        None, ge=0, le=2, description="subtle RGB fringing towards the frame edges"
    )
    saturation: float | None = Field(None, ge=0, le=2, description="1 = style default")
    contrast: float | None = Field(None, ge=0, le=2, description="1 = style default")
    warmth: float | None = Field(None, ge=-1, le=1, description="-1 cool .. +1 warm colour grade")
    letterbox: float | None = Field(
        None, ge=0, le=0.3, description="fraction of frame height blanked top and bottom"
    )


class SafeArea(_Strict):
    """Fractions of the frame kept clear of captions (platform UI overlays on reels/shorts)."""

    top: float = Field(0.10, ge=0, le=0.4)
    bottom: float = Field(0.20, ge=0, le=0.4)
    left: float = Field(0.07, ge=0, le=0.4)
    right: float = Field(0.07, ge=0, le=0.4)


class MetaSpec(_Strict):
    title: str = Field(description="Working title of the reel")
    style: str = Field(description="Style pack name, e.g. paper_cutout | stickman | flat_vector")
    fps: int = Field(30, ge=12, le=60)
    resolution: Annotated[list[int], Field(min_length=2, max_length=2)] = Field(
        default_factory=lambda: [1080, 1920], description="[width, height] in pixels"
    )
    seed: int = Field(0, description="Seeds every random choice: same spec + seed => same video")
    target_duration_sec: float = Field(
        50.0, ge=MIN_TOTAL_SEC, le=MAX_TOTAL_SEC, description="Intended length, 45-60 seconds"
    )
    aspect: Literal["9:16"] = "9:16"
    fx: FxSpec | None = Field(None, description="override the style's post-FX strengths")
    safe_area: SafeArea | None = Field(None, description="caption safe-area margins")


# --------------------------------------------------------------------------- characters
class CharacterSpec(_Strict):
    id: str = Field(min_length=1, description="Unique id referenced by scene layers")
    archetype: str = Field(
        description="Body/face template, e.g. everyman | kid | elder | hero | robot"
    )
    palette: dict[str, str] = Field(
        default_factory=dict,
        description="Colour overrides by role (skin, hair, shirt, shirt2, pants, shoes, accent), hex",
    )
    props: list[str] = Field(default_factory=list, description="Props this character owns/wears")
    name: str | None = Field(None, description="Display name (used for TTS voice mapping)")
    voice: str | None = Field(None, description="TTS voice override for this character")


# --------------------------------------------------------------------------- scene parts
class BackgroundSpec(_Strict):
    template: str = Field(description="Background template, e.g. room | street | forest | abstract")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Template parameters (time_of_day, mood, density, ...)"
    )


class CameraMoveSpec(_Strict):
    """One camera move. `from`/`to` meaning depends on `type`:
    pan [x,y] screen-fraction offset | zoom scale (1=none) | dolly 0..1 push-in |
    shake intensity 0..1 | rack_focus a depth ('background'|'midground'|'foreground'|0..1)."""

    type: str = Field(description="pan | zoom | shake | dolly | rack_focus ...")
    from_: float | str | Vec2 | None = Field(None, alias="from")
    to: float | str | Vec2 | None = None
    t0: Seconds
    t1: Seconds
    ease: str = Field("ease_in_out", description="easing curve name")
    params: dict[str, Any] = Field(default_factory=dict)


class CameraSpec(_Strict):
    moves: list[CameraMoveSpec] = Field(default_factory=list)


class ActionSpec(_Strict):
    name: str = Field(description="Registered action: walk, wave, talk, ...")
    t0: Seconds
    t1: Seconds
    params: dict[str, Any] = Field(default_factory=dict)


class LayerSpec(_Strict):
    character: str = Field(description="Character id")
    position: Vec2 | str = Field(
        default_factory=lambda: [0.5, 0.8],
        description="[x,y] of the feet in screen fractions, or a named slot of the background "
        "(left | center | right | far_left | far_right | ...)",
    )
    scale: float = Field(1.0, gt=0, le=4)
    depth: Literal["background", "mid", "foreground"] = "mid"
    facing: Literal["auto", "left", "right"] = "auto"
    actions: list[ActionSpec] = Field(default_factory=list)


class CaptionSpec(_Strict):
    text: str
    t0: Seconds
    t1: Seconds
    style: str = Field("subtitle", description="subtitle | title | shout")
    speaker: str | None = Field(None, description="Character id who says it (drives lip-sync)")
    speak: bool | None = Field(
        None, description="Read aloud by TTS. Default: true for subtitles, false otherwise"
    )
    anchor: Literal["auto", "top", "center", "bottom"] = "auto"


class SfxSpec(_Strict):
    name: str
    t: Seconds
    volume: float = Field(1.0, ge=0, le=2)


class TransitionSpec(_Strict):
    type: str = Field("cut", description="cut | crossfade | wipe | page_flip")
    duration: float = Field(0.0, ge=0, le=3.0, description="seconds; overlaps the next scene")
    params: dict[str, Any] = Field(default_factory=dict)


class SceneSpec(_Strict):
    id: str = Field(min_length=1)
    duration_sec: float = Field(gt=0, le=30)
    background: BackgroundSpec
    camera: CameraSpec = Field(default_factory=CameraSpec)
    layers: list[LayerSpec] = Field(default_factory=list)
    captions: list[CaptionSpec] = Field(default_factory=list)
    sfx: list[SfxSpec] = Field(default_factory=list)
    transition_out: TransitionSpec = Field(default_factory=TransitionSpec)
    notes: str | None = Field(
        None, description="Free-form director's note; ignored by the renderer"
    )


# --------------------------------------------------------------------------- audio
class AudioSpec(_Strict):
    music: str | None = Field(
        None,
        description="Path to a music file, 'procedural' or 'procedural:<mood>' for the built-in "
        "generated bed, or null for no music",
    )
    voiceover: Literal["tts", "file", "none"] = "none"
    ducking: bool = Field(True, description="lower the music under voice")
    voiceover_file: str | None = Field(None, description="Audio file used when voiceover='file'")
    tts_voice: str | None = Field(None, description="Default TTS voice id/model")
    music_gain_db: float = Field(-16.0, ge=-60, le=12)
    voice_gain_db: float = Field(0.0, ge=-60, le=12)
    sfx_gain_db: float = Field(-8.0, ge=-60, le=12)
    auto_sfx: bool = Field(False, description="add footsteps/landings emitted by actions")


# --------------------------------------------------------------------------- root
class ReelSpec(_Strict):
    schema_ref: str | None = Field(
        None,
        alias="$schema",
        description="optional JSON Schema pointer for editor validation; ignored",
    )
    version: str = SPEC_VERSION
    meta: MetaSpec
    characters: list[CharacterSpec] = Field(default_factory=list)
    scenes: list[SceneSpec] = Field(min_length=1)
    audio: AudioSpec = Field(default_factory=AudioSpec)

    @model_validator(mode="before")
    @classmethod
    def _clean_strings(cls, data: Any) -> Any:
        """Lone surrogates and control characters in any string would crash text drawing, TTS or JSON output."""
        return clean_data(data)

    # -- io ------------------------------------------------------------------------------
    @classmethod
    def from_file(cls, path: str | Path) -> ReelSpec:
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_json(self, *, indent: int | None = 2) -> str:
        data = self.model_dump(mode="json", by_alias=True)
        if data.get("$schema") is None:
            data.pop("$schema", None)
        return json.dumps(data, indent=indent)

    def character(self, cid: str) -> CharacterSpec | None:
        return next((c for c in self.characters if c.id == cid), None)

    @property
    def size(self) -> tuple[int, int]:
        return self.meta.resolution[0], self.meta.resolution[1]


#: Models whose registry-name fields the exported JSON Schema constrains (see core/schema.py)
SCHEMA_ENUM_FIELDS: dict[str, tuple[str, str]] = {
    # definition name -> (property, catalog kind)
    "MetaSpec": ("style", "style"),
    "CharacterSpec": ("archetype", "archetype"),
    "BackgroundSpec": ("template", "background"),
    "CameraMoveSpec": ("type", "camera_move"),
    "ActionSpec": ("name", "action"),
    "CaptionSpec": ("style", "caption_style"),
    "SfxSpec": ("name", "sfx"),
    "TransitionSpec": ("type", "transition"),
}
