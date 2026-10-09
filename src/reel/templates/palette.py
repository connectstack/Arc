"""Colour moods: time of day x mood -> a `ColorScheme`.

A template names its surfaces by *role* (wall, floor, foliage ...) with base colours; this module
turns "dusk + gloomy" into sky colours, ambient light and tinted surfaces.  Templates adapt to
parameters; callers never pick colours.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from reel.core.geometry import adjust, mix
from reel.core.ir import ColorScheme

TimeOfDay = Literal["dawn", "day", "dusk", "night"]
Mood = Literal["neutral", "warm", "cool", "dramatic", "playful", "gloomy"]
TIMES: tuple[str, ...] = ("dawn", "day", "dusk", "night")
MOODS: tuple[str, ...] = ("neutral", "warm", "cool", "dramatic", "playful", "gloomy")


@dataclass(frozen=True)
class SkyLight:
    top: str
    bottom: str
    glow: str  # horizon glow / sun colour
    ambient: str
    ambient_amt: float
    exposure: float
    shadow: str
    light_dir: tuple[float, float]
    window: str  # colour seen through windows
    lamps_on: bool


SKY: dict[str, SkyLight] = {
    "dawn": SkyLight(
        "#f4a3a0",
        "#ffe7bd",
        "#ffb27a",
        "#ffd9bd",
        0.20,
        0.98,
        "#4a3a63",
        (-0.80, -0.55),
        "#ffd9a8",
        True,
    ),
    "day": SkyLight(
        "#5fb8f0",
        "#d6f2ff",
        "#fff3c4",
        "#ffffff",
        0.0,
        1.0,
        "#2b2d52",
        (-0.45, -0.89),
        "#bfe8ff",
        False,
    ),
    "dusk": SkyLight(
        "#4b3f93",
        "#ff9b63",
        "#ffd08a",
        "#ffa070",
        0.30,
        0.95,
        "#2a1b4d",
        (0.85, -0.50),
        "#ffcf8a",
        True,
    ),
    "night": SkyLight(
        "#0a1230",
        "#2a4580",
        "#dfe8ff",
        "#6a7bd0",
        0.62,
        0.84,
        "#050816",
        (-0.35, -0.94),
        "#ffe9a6",
        True,
    ),
}


def _mood(c: str, mood: str) -> str:
    if mood == "warm":
        return mix(adjust(c, sat=1.06), "#ffb36b", 0.09)
    if mood == "cool":
        return mix(adjust(c, sat=0.95), "#6aa8ff", 0.11)
    if mood == "dramatic":
        return adjust(c, sat=1.12, light=0.9)
    if mood == "playful":
        return adjust(c, sat=1.28, light=1.03)
    if mood == "gloomy":
        return mix(adjust(c, sat=0.55, light=0.95), "#5b6b7a", 0.14)
    return c


def make_scheme(time_of_day: str, mood: str, roles: dict[str, str]) -> ColorScheme:
    """Base roles + sky/lighting for the chosen time and mood."""
    sky = SKY[time_of_day]
    out = {k: _mood(v, mood) for k, v in roles.items()}
    out.update(
        sky_top=_mood(sky.top, mood),
        sky_bottom=_mood(sky.bottom, mood),
        glow=sky.glow,
        window=sky.window,
        shadow=sky.shadow,
        white="#ffffff",
        black="#101018",
    )
    exposure = sky.exposure * {"dramatic": 0.92, "gloomy": 0.88, "playful": 1.03}.get(mood, 1.0)
    amt = min(0.85, sky.ambient_amt + {"dramatic": 0.08, "gloomy": 0.10}.get(mood, 0.0))
    return ColorScheme(out, sky.ambient, amt, exposure, sky.shadow, sky.light_dir)


def lamps_on(time_of_day: str) -> bool:
    return SKY[time_of_day].lamps_on
