"""Font lookup.  Nothing is bundled or downloaded: fonts come from, in order,
``$REEL_FONT_DIR`` / ``assets/fonts`` next to the working directory, then the system.
Every request has a fallback chain, so a missing font degrades instead of failing.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import skia

_SYSTEM_DIRS = (
    "/System/Library/Fonts/Supplemental",
    "/System/Library/Fonts",
    "/Library/Fonts",
    str(Path.home() / "Library/Fonts"),
    "/usr/share/fonts/truetype",
    "/usr/share/fonts",
    "C:/Windows/Fonts",
)


def _user_dirs() -> list[Path]:
    dirs = []
    env = os.environ.get("REEL_FONT_DIR")
    if env:
        dirs += [Path(p) for p in env.split(os.pathsep) if p]
    dirs.append(Path.cwd() / "assets" / "fonts")
    return dirs


@lru_cache(maxsize=256)
def _from_file(stem: str) -> skia.Typeface | None:
    wanted = stem.lower().replace(" ", "")
    for base in [*_user_dirs(), *(Path(d) for d in _SYSTEM_DIRS)]:
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if (
                f.suffix.lower() in (".ttf", ".otf", ".ttc")
                and f.stem.lower().replace(" ", "") == wanted
            ):
                tf = skia.Typeface.MakeFromFile(str(f))
                if tf is not None:
                    return tf
    return None


@lru_cache(maxsize=256)
def find_typeface(
    candidates: tuple[str, ...], bold: bool = False, italic: bool = False
) -> skia.Typeface:
    """First available of ``candidates`` (family or font file stem), else the platform default."""
    style = skia.FontStyle(
        skia.FontStyle.kBold_Weight if bold else skia.FontStyle.kNormal_Weight,
        skia.FontStyle.kNormal_Width,
        skia.FontStyle.kItalic_Slant if italic else skia.FontStyle.kUpright_Slant,
    )
    mgr = skia.FontMgr.RefDefault()
    for name in candidates:
        tf = _from_file(name)
        if tf is not None:
            return tf
        tf = mgr.matchFamilyStyle(name, style)
        if tf is not None and tf.getFamilyName().lower().replace(" ", "") == name.lower().replace(
            " ", ""
        ):
            return tf
    return skia.Typeface.MakeDefault()


def typeface(
    candidates: Sequence[str], *, bold: bool = False, italic: bool = False
) -> skia.Typeface:
    return find_typeface(tuple(candidates), bold, italic)


_FONT_SUFFIXES = (".ttf", ".otf", ".ttc")


@lru_cache(maxsize=1)
def inventory_fingerprint() -> str:
    """Digest of the fonts this machine can offer: the system's families and the font files in every folder searched.

    Cached frames and video chunks carry whichever font was found when they were drawn, so installing a font that a
    style or caption asks for (or one that a script such as Devanagari needs) must change every cache key.
    """
    h = hashlib.sha256()
    mgr = skia.FontMgr.RefDefault()
    for name in sorted(mgr.getFamilyName(i) for i in range(mgr.countFamilies())):
        h.update(name.encode("utf-8", "replace") + b"\0")
    user = _user_dirs()
    for base in [*user, *(Path(d) for d in _SYSTEM_DIRS)]:
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if f.suffix.lower() not in _FONT_SUFFIXES:
                continue
            h.update(f.name.encode("utf-8", "replace"))
            if base in user:  # a font replaced in place keeps its name: size and date tell
                try:
                    st = f.stat()
                    h.update(f":{st.st_size}:{int(st.st_mtime)}".encode())
                except OSError:
                    pass
            h.update(b"\0")
    return h.hexdigest()[:16]


#: Per-role font stacks; the first one installed wins.
HEADLINE = ("Bangers", "Impact", "Arial Black", "Helvetica Neue Condensed Bold", "Arial Bold")
SANS_BOLD = (
    "Montserrat ExtraBold",
    "Avenir Next Heavy",
    "Arial Black",
    "Arial Rounded Bold",
    "Helvetica Neue",
)
ROUNDED = (
    "Arial Rounded Bold",
    "SF Pro Rounded",
    "Avenir Next Rounded",
    "Trebuchet MS Bold",
    "Arial Bold",
)
MARKER = ("Marker Felt", "MarkerFelt", "Bradley Hand Bold", "Chalkboard", "Comic Sans MS Bold")
HAND = ("Bradley Hand Bold", "Noteworthy", "Marker Felt", "Comic Sans MS Bold")
