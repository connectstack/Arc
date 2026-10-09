"""LLM-driven script -> spec: robust JSON extraction, deterministic fixes, lint-driven repair.

The loop in :class:`LLMSpecGenerator`:

1. render the system prompt from the live catalog and send ``prompt + script`` to the client together
   with the enum-pinned JSON schema;
2. :func:`extract_json` pulls the first balanced ``{...}`` out of whatever came back (fences, prose,
   ``<think>`` blocks, trailing commas and comments are tolerated);
3. :func:`normalize_spec` applies the fixes that are *always* right (forced style/seed, the 45-60 s
   budget, time windows clamped into their scene, unique ids, ...) so the model is not asked to redo
   arithmetic it is bad at;
4. the linter judges the result; on errors its report goes back to the model as a user message
   ("Fix these problems and return the FULL corrected JSON"), at most ``max_repairs`` times.

Nothing here touches the network except through the injected client.
"""

from __future__ import annotations

import contextlib
import copy
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from reel.core.camera import clamp_move_values
from reel.core.catalog import CATALOG, Catalog
from reel.core.lint import (
    UNIVERSAL_SLOTS,
    LintIssue,
    LintOptions,
    LintReport,
    Severity,
    lint_data,
)
from reel.core.spec import (
    MAX_TOTAL_SEC,
    MIN_TOTAL_SEC,
    SPEC_VERSION,
    ActionSpec,
    BackgroundSpec,
    CameraMoveSpec,
    CameraSpec,
    CaptionSpec,
    CharacterSpec,
    LayerSpec,
    LibraryGapSpec,
    MetaSpec,
    ObjectMotionSpec,
    ObjectSpec,
    SceneSpec,
    SfxSpec,
    TransitionSpec,
)
from reel.llm.base import (
    CHARACTER_PALETTES,
    COMPACT_BELOW_TOKENS,
    COMPACT_MAX_SCENES,
    COMPACT_MIN_SCENES,
    FEET_Y_MAX,
    FEET_Y_MIN,
    HARD_MAX_SCENE_SEC,
    HARD_MIN_SCENE_SEC,
    MAX_CAPTION_WPS,
    MIN_REPLY_TOKENS,
    READ_WPS,
    SKIN_TONES,
    X_MAX,
    X_MIN,
    GenerationResult,
    SpecGenerationError,
    SpecGenerator,
    clamp_target,
    default_audio,
    estimate_tokens,
    guess_archetype,
    synthetic_report,
)
from reel.llm.client import LLMClient, Message
from reel.llm.fidelity import ScriptLock
from reel.llm.prompt import build_system_prompt, build_user_message, json_schema_for_llm


# =============================================================================== extract_json
class JSONExtractionError(ValueError):
    """The reply contains no usable JSON object.  ``cut_off`` is True when it looks like a reply
    that ran out of room mid-object (an opening brace that never closes)."""

    def __init__(self, message: str, *, cut_off: bool = False) -> None:
        super().__init__(message)
        self.cut_off = cut_off


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```[A-Za-z0-9_+-]*[ \t]*\n?(.*?)```", re.S)
_MAX_CANDIDATES = 400


def _match_brace(s: str, i: int) -> int:
    """Index just past the ``}`` that closes the ``{`` at ``s[i]`` (string-aware), or -1."""
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(s)):
        c = s[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


def _object_spans(s: str) -> tuple[list[tuple[int, int]], bool]:
    """Spans of balanced top-level ``{...}`` blobs that look like JSON objects (``{"`` or ``{}``),
    and whether some such object never closed (a reply cut off in the middle)."""
    spans: list[tuple[int, int]] = []
    unclosed = False
    pos = 0
    tries = 0
    while tries < _MAX_CANDIDATES:
        i = s.find("{", pos)
        if i < 0:
            break
        tries += 1
        k = i + 1
        while k < len(s) and s[k] in " \t\r\n":
            k += 1
        if k >= len(s) or s[k] not in '"}':  # `{style}` in prose, a template placeholder, ...
            pos = i + 1
            continue
        end = _match_brace(s, i)
        if end < 0:
            unclosed = True
            pos = i + 1
            continue
        spans.append((i, end))
        pos = end
    return spans, unclosed


def _strip_comments(s: str) -> str:
    out: list[str] = []
    i, n = 0, len(s)
    in_str = False
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and s[i : i + 2] == "//":
            while i < n and s[i] != "\n":
                i += 1
            continue
        elif c == "/" and s[i : i + 2] == "/*":
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _strip_trailing_commas(s: str) -> str:
    out: list[str] = []
    in_str = False
    n = len(s)
    i = 0
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1  # drop the trailing comma
                continue
            out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _loads_lenient(blob: str) -> Any:
    """``json.loads`` that also accepts comments, trailing commas and raw newlines in strings."""
    try:
        return json.loads(blob, strict=False)
    except json.JSONDecodeError:
        return json.loads(_strip_trailing_commas(_strip_comments(blob)), strict=False)


def extract_json(text: str) -> dict[str, Any]:
    """The JSON object in a model reply.

    Handles ``` fences (with or without a language tag), prose before and after, ``<think>...</think>``
    blocks, braces inside strings, trailing commas and ``//`` / ``/* */`` comments.  When several
    objects are present the first one that looks like a spec (has ``scenes`` or ``meta``) wins; a
    reply whose outer object never closes is *cut off* - the fragments inside it are not returned.
    Raises :class:`JSONExtractionError` with a hint for the repair message.
    """
    if not isinstance(text, str) or not text.strip():
        raise JSONExtractionError("the reply is empty")
    cleaned = _THINK.sub(" ", text)
    chunks = [m.group(1) for m in _FENCE.finditer(cleaned)] + [cleaned]
    first: dict[str, Any] | None = None
    first_error: json.JSONDecodeError | None = None
    saw_open = False
    unclosed = False
    for chunk in chunks:
        saw_open = saw_open or "{" in chunk
        spans, open_ended = _object_spans(chunk)
        unclosed = unclosed or open_ended
        for start, end in spans:
            try:
                obj = _loads_lenient(chunk[start:end])
            except json.JSONDecodeError as exc:
                first_error = first_error or exc
                continue
            if isinstance(obj, dict):
                if "scenes" in obj or "meta" in obj:
                    return obj
                first = first or obj
    if unclosed:
        raise JSONExtractionError(
            "the reply has an opening '{' but no complete JSON object - it looks cut off "
            "(unbalanced braces); write the JSON more compactly",
            cut_off=True,
        )
    if first is not None:
        return first
    if first_error is not None:
        raise JSONExtractionError(
            f"the JSON object is malformed: {first_error.msg} at line {first_error.lineno}, "
            f"column {first_error.colno}"
        )
    if saw_open:
        raise JSONExtractionError("the reply has no JSON object (only braces in prose)")
    raise JSONExtractionError("the reply contains no JSON object")


_WS = " \t\r\n"


def _skip(s: str, i: int, also: str = "") -> int:
    while i < len(s) and (s[i] in _WS or s[i] in also):
        i += 1
    return i


def _partial_array(s: str, i: int, dec: json.JSONDecoder) -> tuple[list[dict[str, Any]], int, bool]:
    """Complete object elements of the array opening at ``s[i]``; ``closed`` is False when the text
    ends (or breaks) inside it."""
    items: list[dict[str, Any]] = []
    i += 1
    while True:
        i = _skip(s, i, ",")
        if i >= len(s):
            return items, i, False
        if s[i] == "]":
            return items, i + 1, True
        try:
            value, j = dec.raw_decode(s, i)
        except ValueError:
            return items, i, False
        if isinstance(value, dict):
            items.append(value)
        i = j


def _partial_object(s: str, i: int) -> tuple[dict[str, Any], bool]:
    """The complete members of the object opening at ``s[i]``, up to the point the text is cut, and
    whether the ``scenes`` array itself was closed (so only closing braces are missing)."""
    dec = json.JSONDecoder(strict=False)
    out: dict[str, Any] = {}
    scenes_closed = False
    i += 1
    while True:
        i = _skip(s, i, ",")
        if i >= len(s) or s[i] != '"':
            break
        try:
            key, i = dec.raw_decode(s, i)
        except ValueError:
            break
        i = _skip(s, i)
        if i >= len(s) or s[i] != ":":
            break
        i = _skip(s, i + 1)
        if i >= len(s):
            break
        if key == "scenes" and s[i] == "[":
            scenes, i, scenes_closed = _partial_array(s, i, dec)
            out["scenes"] = scenes
            if not scenes_closed:
                break
            continue
        try:
            out[key], i = dec.raw_decode(s, i)
        except ValueError:
            break
    return out, scenes_closed


def salvage(text: str) -> tuple[dict[str, Any], bool] | None:
    """What a cut-off reply managed to say: ``meta`` / ``characters`` that closed and every COMPLETE
    scene object of the ``scenes`` array, up to the cut; plus whether the array was complete (the
    reply merely lacks its closing braces).  None when not even one scene is complete."""
    cleaned = _THINK.sub(" ", text)
    chunks = [m.group(1) for m in _FENCE.finditer(cleaned)]
    if "```" in cleaned and not chunks:  # an opening fence whose closing fence never came
        chunks.append(cleaned.split("```", 1)[1].split("\n", 1)[-1])
    chunks.append(cleaned)
    for chunk in chunks:
        chunk = _strip_trailing_commas(_strip_comments(chunk))
        pos = 0
        for _ in range(_MAX_CANDIDATES):
            i = chunk.find("{", pos)
            if i < 0:
                break
            k = _skip(chunk, i + 1)
            if k < len(chunk) and chunk[k] == '"':
                got, closed = _partial_object(chunk, i)
                if got.get("scenes"):
                    return got, closed
            pos = i + 1
    return None


def salvage_json(text: str) -> dict[str, Any] | None:
    """The complete scenes (and closed ``meta`` / ``characters``) of a cut-off reply, or None.
    Used when a reply ran out of room, so a long story is not thrown away entirely."""
    got = salvage(text)
    return got[0] if got else None


# =============================================================================== normalize_spec
_COLOR_NAMES = {
    "red": "#e63946",
    "orange": "#f4a261",
    "yellow": "#f6c945",
    "green": "#4c9a5b",
    "blue": "#2d6cdf",
    "purple": "#6a4c93",
    "violet": "#7b5ea7",
    "pink": "#ff6b9d",
    "brown": "#8d5a3b",
    "black": "#1c1c24",
    "white": "#f5f5f5",
    "gray": "#8a96a3",
    "grey": "#8a96a3",
    "teal": "#2a9d8f",
    "navy": "#1d3557",
    "beige": "#e9d8b4",
    "cyan": "#4fc3f7",
    "gold": "#e9b949",
    "tan": "#d2b48c",
}
_HEX6 = re.compile(r"^#?[0-9a-fA-F]{6}$")
_HEX3 = re.compile(r"^#[0-9a-fA-F]{3}$")
_NUMBER = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)?\s*$", re.I)

#: spelling slips models make, per container: wrong key -> right key
_ALIASES: dict[str, dict[str, str]] = {
    "scene": {"duration": "duration_sec", "length": "duration_sec", "dur": "duration_sec"},
    "window": {"start": "t0", "end": "t1", "t_start": "t0", "t_end": "t1"},
    "sfx": {"time": "t", "at": "t", "start": "t"},
    "object": {
        "name": "asset",
        "id": "asset",
        "object": "asset",
        "pos": "position",
        "size": "scale",
    },
}
#: containers whose unknown keys are dropped (their params dicts are left to the linter)
_MODELS: dict[str, type[BaseModel]] = {
    "meta": MetaSpec,
    "character": CharacterSpec,
    "scene": SceneSpec,
    "background": BackgroundSpec,
    "camera": CameraSpec,
    "move": CameraMoveSpec,
    "layer": LayerSpec,
    "object": ObjectSpec,
    "motion": ObjectMotionSpec,
    "gap": LibraryGapSpec,
    "action": ActionSpec,
    "caption": CaptionSpec,
    "sfx": SfxSpec,
    "transition": TransitionSpec,
}
_DEFAULT_TRANSITION_SEC = {"page_flip": 0.8}
#: how far inside 45-60 s the fitted total is kept
FIT_MARGIN_SEC = 0.05


def _allowed_keys(kind: str) -> set[str]:
    model = _MODELS[kind]
    keys = set(model.model_fields)
    keys |= {f.alias for f in model.model_fields.values() if f.alias}
    return keys


def _num(v: Any) -> float | None:
    """A finite float from a number or a numeric string ('5', '5s'), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)  # an integer too big for a float is not a time or a position either
        except OverflowError:
            return None
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        m = _NUMBER.match(v)
        if m:
            return float(m.group(1))
    return None


def _r(x: float, places: int = 3) -> float:
    return round(float(x), places)


def _n_words(text: str) -> int:
    return len(text.split())


def _reading_sec(text: str) -> float:
    return max(1.2, _n_words(text) / (READ_WPS - 0.1) + 0.5)


class _Log:
    """Collects the notes of one normalisation pass (repeats are counted, not repeated)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        #: things the library lacks that the pass found (an object it had to drop): merged into meta.library_gaps
        self.gaps: list[dict[str, Any]] = []

    def hit(self, message: str) -> None:
        self.counts[message] = self.counts.get(message, 0) + 1

    def gap(self, kind: str, name: str, scene: str | None) -> None:
        self.gaps.append({"kind": kind, "name": name, "scenes": [scene] if scene else []})

    def notes(self) -> list[str]:
        return [m if n == 1 else f"{m} (x{n})" for m, n in self.counts.items()]


def _list_of_dicts(v: Any, log: _Log, what: str) -> list[dict[str, Any]]:
    if v is None:
        return []
    if not isinstance(v, list):
        log.hit(f"dropped {what}: expected a list")
        return []
    out = [x for x in v if isinstance(x, dict)]
    if len(out) != len(v):
        log.hit(f"dropped malformed entries from {what}")
    return out


def _tidy_keys(d: dict[str, Any], kind: str, log: _Log, where: str) -> None:
    """Rename common slips (duration -> duration_sec, start -> t0) and drop unknown keys."""
    table = (
        "window"
        if kind in ("action", "caption", "move", "motion")
        else kind
        if kind in ("scene", "sfx", "object")
        else ""
    )
    for wrong, right in _ALIASES.get(table, {}).items():
        if wrong in d and right not in d:
            d[right] = d.pop(wrong)
            log.hit(f"renamed {where} field '{wrong}' to '{right}'")
    allowed = _allowed_keys(kind)
    for key in [k for k in d if k not in allowed]:
        del d[key]
        log.hit(f"dropped unknown field '{key}' from {where}")


def _unwrap(data: dict[str, Any], log: _Log) -> dict[str, Any]:
    """Models sometimes wrap the spec: ``{"spec": {...}}`` / ``{"reel": {...}}``."""
    if "scenes" in data or "meta" in data:
        return data
    for key, value in data.items():
        if isinstance(value, dict) and ("scenes" in value or "meta" in value):
            log.hit(f"unwrapped the spec from the '{key}' key")
            return value
    return data


def _fix_color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if _HEX3.match(v) or (v.startswith("#") and _HEX6.match(v)):
        return v
    if _HEX6.match(v):
        return "#" + v
    return _COLOR_NAMES.get(v.lower())


def _fix_meta(
    out: dict[str, Any],
    style: str,
    seed: int,
    target: float,
    scenes: list[dict[str, Any]],
    log: _Log,
) -> None:
    meta = out.get("meta")
    if not isinstance(meta, dict):
        if meta is not None:
            log.hit("replaced a malformed 'meta' block")
        meta = {}
    _tidy_keys(meta, "meta", log, "meta")
    title = meta.get("title")
    if not isinstance(title, str) or not title.strip():
        title = _guess_title(scenes)
        log.hit(f"filled meta.title with {title!r}")
    meta["title"] = title.strip()
    if meta.get("style") != style:
        if meta.get("style") is not None:
            log.hit(f"meta.style was {meta.get('style')!r}; set to {style!r}")
        meta["style"] = style
    if meta.get("seed") != seed:
        meta["seed"] = int(seed)
    fps = _num(meta.get("fps"))
    meta["fps"] = int(fps) if fps is not None and 12 <= fps <= 60 else 30
    res = meta.get("resolution")
    if not (
        isinstance(res, list)
        and len(res) == 2
        and all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in res)
    ):
        meta["resolution"] = [1080, 1920]
    meta["aspect"] = "9:16"
    meta["target_duration_sec"] = target
    for key in ("fx", "safe_area"):  # post-processing / safe-area overrides are the user's call
        if key in meta:
            del meta[key]
            log.hit(f"dropped meta.{key} (the model must not override the style's look)")
    out["meta"] = meta


# -- objects from the asset library -------------------------------------------------------------------
_OBJECT_ENUMS = {
    "depth": ("background", "mid", "foreground"),
    "layer": ("behind", "front"),
    "facing": ("auto", "left", "right"),
}
_MOTION_DEFAULT_SEC = 1.0


def _resolve_object(name: str, cat: Catalog) -> str | None:
    """A library object a model's word stands for: its exact name, any spelling of it, or one of its tags."""
    from reel.assets.match import asset_index

    low = name.strip().lower().replace(" ", "_").replace("-", "_")
    if low in cat.objects:
        return low
    hit = asset_index(cat, ("object",)).lookup(name)
    return hit.name if hit else None


def _fix_motion(m: dict[str, Any], log: _Log, cat: Catalog) -> dict[str, Any] | None:
    from reel.assets.objects import MOTIONS

    _tidy_keys(m, "motion", log, "an object motion")
    if not isinstance(m.get("type"), str) or m.get("type") not in MOTIONS:
        log.hit(f"dropped object motions that are not in the catalog ({m.get('type')!r})")
        return None
    t0, t1 = _num(m.get("t0")), _num(m.get("t1"))
    if t0 is None or t1 is None or t1 <= t0:
        log.hit("dropped object motions with an empty or missing time window")
        return None
    m["t0"], m["t1"] = _r(max(0.0, t0)), _r(t1)
    to = m.get("to")
    if m["type"] == "move":
        if isinstance(to, list) and len(to) == 2 and all(_num(v) is not None for v in to):
            m["to"] = [_r(float(_num(to[0]) or 0.0)), _r(float(_num(to[1]) or 0.0))]
        elif not (isinstance(to, str) and to.strip()):
            log.hit("dropped object moves without a destination")
            return None
    elif m["type"] in ("fade", "grow"):
        val = _num(to)
        if val is None:
            m.pop("to", None)
        else:
            m["to"] = _r(val)
    elif "to" in m:
        del m["to"]
    for key in ("from", "amount"):
        if key in m:
            val = _num(m[key])
            if val is None:
                del m[key]
            else:
                m[key] = _r(val)
    if "count" in m:
        val = _num(m["count"])
        if val is None:
            del m["count"]
        else:
            m["count"] = int(min(20, max(1, round(val))))
    if "ease" in m and (not isinstance(m["ease"], str) or m["ease"] not in cat.easings):
        del m["ease"]
        log.hit("dropped unknown easing names")
    return m


def _fix_object(
    ob: dict[str, Any], sc: dict[str, Any], cat: Catalog, log: _Log
) -> dict[str, Any] | None:
    _tidy_keys(ob, "object", log, "an object")
    name = ob.get("asset")
    if not isinstance(name, str) or not name.strip():
        log.hit("dropped objects without a name")
        return None
    if name not in cat.objects:
        resolved = _resolve_object(name, cat)
        if resolved is None:
            log.hit(
                "dropped objects that are not in the asset library (recorded in meta.library_gaps)"
            )
            log.gap("object", name.strip(), sc.get("id"))
            return None
        log.hit("matched object names to the asset library's own words")
        ob["asset"] = resolved
    pos = ob.get("position")
    if isinstance(pos, list) and len(pos) == 2 and all(_num(v) is not None for v in pos):
        x, y = float(_num(pos[0]) or 0.0), float(_num(pos[1]) or 0.0)
        nx, ny = min(2.0, max(-1.0, x)), min(1.4, max(-0.2, y))
        if (nx, ny) != (x, y):
            log.hit("moved objects that were far outside the frame")
        ob["position"] = [_r(nx), _r(ny)]
    elif not (isinstance(pos, str) and pos.strip()):
        ob.pop("position", None)
    for key, lo, hi in (("scale", 0.05, 6.0), ("rotation", -360.0, 360.0), ("alpha", 0.0, 1.0)):
        if key in ob:
            val = _num(ob[key])
            if val is None:
                del ob[key]
            else:
                ob[key] = _r(min(hi, max(lo, val)))
    for key, allowed in _OBJECT_ENUMS.items():
        if key in ob and ob[key] not in allowed:
            del ob[key]
    t0, t1 = _num(ob.get("t0")), _num(ob.get("t1"))
    if t0 is None:
        ob.pop("t0", None)
    else:
        ob["t0"] = _r(max(0.0, t0))
    if t1 is None or t1 <= (t0 or 0.0):
        ob.pop("t1", None)
    else:
        ob["t1"] = _r(t1)
    motions = _list_of_dicts(ob.get("motions"), log, "an object's motions")
    ob["motions"] = [mo for mo in (_fix_motion(m, log, cat) for m in motions) if mo is not None]
    if not ob["motions"]:
        del ob["motions"]
    pal = ob.get("palette")
    if isinstance(pal, dict):
        fixed = {str(k): c for k, v in pal.items() if (c := _fix_color(v)) is not None}
        if len(fixed) != len(pal):
            log.hit("dropped palette colours that are not hex")
        if fixed:
            ob["palette"] = fixed
        else:
            del ob["palette"]
    elif "palette" in ob:
        del ob["palette"]
    return ob


def _fix_objects(sc: dict[str, Any], cat: Catalog, log: _Log, where: str) -> None:
    if "objects" not in sc:
        return
    items = _list_of_dicts(sc.get("objects"), log, f"{where}.objects")
    keep = [o for o in (_fix_object(o, sc, cat, log) for o in items) if o is not None]
    if keep:
        sc["objects"] = keep
    else:
        del sc["objects"]


def _merge_gaps(
    meta: dict[str, Any], found: list[dict[str, Any]], cat: Catalog, log: _Log
) -> list[tuple[dict[str, Any], str]]:
    """Clean ``meta.library_gaps`` (what the model wrote plus what this pass had to drop): valid entries only, one per
    thing, and none for something the library does have: those come back as ``(gap, asset name)`` to be applied."""
    from reel.assets.gaps import resolve_gap

    raw = meta.get("library_gaps")
    entries = _list_of_dicts(raw, log, "meta.library_gaps") + [dict(g) for g in found]
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for g in entries:
        _tidy_keys(g, "gap", log, "a library gap")
        kind, name = g.get("kind"), g.get("name")
        if kind not in ("character", "object", "place") or not isinstance(name, str):
            log.hit("dropped malformed meta.library_gaps entries")
            continue
        name = " ".join(name.split())[:60]
        if not name:
            continue
        scenes = (
            [str(x) for x in g.get("scenes", []) if isinstance(x, (str, int))]
            if isinstance(g.get("scenes"), list)
            else []
        )
        clean: dict[str, Any] = {"kind": kind, "name": name, "scenes": scenes}
        for key in ("character", "stand_in"):
            if isinstance(g.get(key), str) and g[key].strip():
                clean[key] = g[key].strip()
        slot = merged.setdefault((kind, name.lower()), clean)
        if slot is not clean:
            slot["scenes"] = list(dict.fromkeys([*slot["scenes"], *scenes]))
            for key in ("character", "stand_in"):
                if key in clean and key not in slot:
                    slot[key] = clean[key]
    kept: list[dict[str, Any]] = []
    resolved: list[tuple[dict[str, Any], str]] = []
    for g in list(merged.values())[:24]:
        have = resolve_gap(g, cat)
        if have is not None:  # the library has it after all: the spec is fixed up by the caller
            log.hit(f"dropped a library gap for {g['name']!r}: the library has {have.name!r}")
            resolved.append((g, have.name))
            continue
        kept.append(g)
    if kept:
        meta["library_gaps"] = kept
    else:
        meta.pop("library_gaps", None)
    return resolved


def _guess_title(scenes: list[dict[str, Any]]) -> str:
    texts: list[tuple[bool, str]] = []
    for sc in scenes:
        for cp in sc.get("captions", []) if isinstance(sc.get("captions"), list) else []:
            if isinstance(cp, dict) and isinstance(cp.get("text"), str) and cp["text"].strip():
                texts.append((cp.get("style") == "title", cp["text"].strip()))
    for is_title, text in texts:
        if is_title:
            return text
    if texts:
        return " ".join(texts[0][1].split()[:6])
    return "Untitled reel"


def _fix_characters(out: dict[str, Any], cat: Catalog, log: _Log) -> list[str]:
    chars = _list_of_dicts(out.get("characters"), log, "characters")
    seen: set[str] = set()
    for i, ch in enumerate(chars):
        _tidy_keys(ch, "character", log, f"characters[{i}]")
        cid = ch.get("id")
        if not isinstance(cid, str) or not cid.strip():
            name = ch.get("name")
            cid = (
                re.sub(r"\W+", "_", name.strip().lower()).strip("_")
                if isinstance(name, str)
                else ""
            )
            cid = cid or f"c{i + 1}"
            log.hit("filled a missing character id")
        cid = cid.strip()
        base, n = cid, 2
        while cid in seen:
            cid = f"{base}_{n}"
            n += 1
        if cid != ch.get("id") and isinstance(ch.get("id"), str) and ch["id"].strip():
            log.hit(f"renamed duplicate character id {ch['id']!r} to {cid!r}")
        ch["id"] = cid
        seen.add(cid)
        if "archetype" not in ch and "everyman" in cat.archetypes:
            ch["archetype"] = "everyman"
            log.hit("filled a missing archetype with 'everyman'")
        pal = ch.get("palette")
        if pal is not None:
            if not isinstance(pal, dict):
                del ch["palette"]
                log.hit("dropped a malformed palette")
            else:
                fixed: dict[str, str] = {}
                for role, color in pal.items():
                    good = _fix_color(color)
                    if good is None:
                        log.hit(f"dropped palette colour {color!r} (not a hex colour)")
                    else:
                        if good != color:
                            log.hit("converted colour names to hex")
                        fixed[str(role)] = good
                ch["palette"] = fixed
        props = ch.get("props")
        if props is not None:
            keep = (
                [p for p in props if isinstance(p, str) and p in cat.props]
                if isinstance(props, list)
                else []
            )
            if not isinstance(props, list) or len(keep) != len(props):
                log.hit("dropped props that are not in the catalog")
            ch["props"] = keep
    out["characters"] = chars
    return [c["id"] for c in chars]


def _define_missing_characters(
    out: dict[str, Any], scenes: list[dict[str, Any]], cat: Catalog, log: _Log
) -> None:
    """Characters that layers or captions use but ``characters`` never defined (a small model
    often forgets the block) get a definition: an archetype guessed from the id, default colours.
    "Mia" and "mia" are one character (the first spelling wins)."""
    defined = {c["id"] for c in out.get("characters", [])}
    canon: dict[str, str] = {}  # lowercase -> first spelling seen
    for sc in scenes:
        refs = [ly.get("character") for ly in sc.get("layers", [])]
        refs += [cp.get("speaker") for cp in sc.get("captions", [])]
        for ref in refs:
            if isinstance(ref, str) and ref.strip() and ref not in defined:
                canon.setdefault(ref.lower(), ref)
    if not canon:
        return
    available = set(cat.archetypes.names())
    chars = out.setdefault("characters", [])
    for ref in canon.values():
        arch = guess_archetype([w.lower() for w in re.split(r"[\W_]+", ref) if w], available)
        if arch not in available:
            continue  # nothing to define it with: the linter reports the reference
        chars.append({"id": ref, "archetype": arch, "name": ref.replace("_", " ").title()})
        log.hit("defined characters that scenes use but `characters` did not list")
    for sc in scenes:  # one spelling everywhere
        for ly in sc.get("layers", []):
            ref = ly.get("character")
            if isinstance(ref, str) and ref.lower() in canon and ref not in defined:
                ly["character"] = canon[ref.lower()]
        for cp in sc.get("captions", []):
            ref = cp.get("speaker")
            if isinstance(ref, str) and ref.lower() in canon and ref not in defined:
                cp["speaker"] = canon[ref.lower()]


def _picture_roles(cat: Catalog, archetype: Any) -> tuple[str, ...] | None:
    """The recolourable roles of a library character (a picture), or None for the engine's own bodies."""
    if isinstance(archetype, str) and archetype in cat.assets:
        a = cat.assets.get(archetype)
        if a.kind == "character":
            return a.roles
    return None


def _give_palettes(chars: list[dict[str, Any]], cat: Catalog, log: _Log) -> None:
    """Characters without colours would all look alike: hand out distinct ones.  A picture keeps its own colours
    (only the roles its drawing marks can change), so it gets none and loses roles it does not have."""
    for i, ch in enumerate(chars):
        roles = _picture_roles(cat, ch.get("archetype"))
        if roles is not None:
            pal = ch.get("palette")
            if isinstance(pal, dict):
                keep = {k: v for k, v in pal.items() if k in roles}
                if keep != pal:
                    log.hit("dropped palette roles that a picture character does not have")
                if keep:
                    ch["palette"] = keep
                else:
                    ch.pop("palette", None)
            continue
        if ch.get("palette"):
            continue
        shirt, pants, accent = CHARACTER_PALETTES[i % len(CHARACTER_PALETTES)]
        ch["palette"] = {"shirt": shirt, "pants": pants, "accent": accent}
        if ch.get("archetype") != "robot":
            ch["palette"]["skin"] = SKIN_TONES[(2 * i) % len(SKIN_TONES)]
        log.hit("gave characters without colours distinct ones")


def _valid_slots(cat: Catalog, bg: Any) -> set[str]:
    """Slot names a layer may use on this background (the linter's own rule)."""
    slots = set(UNIVERSAL_SLOTS)
    if (
        isinstance(bg, dict)
        and isinstance(bg.get("template"), str)
        and bg["template"] in cat.backgrounds
    ):
        provided = getattr(cat.backgrounds.get(bg["template"]), "slot_names", None)
        params = bg.get("params") if isinstance(bg.get("params"), dict) else {}
        if callable(provided):
            with contextlib.suppress(Exception):
                slots |= set(provided(params))
        elif provided:
            slots |= set(provided)
    return slots


def _fix_slots(scenes: list[dict[str, Any]], cat: Catalog, log: _Log) -> None:
    """A slot the background does not have ("stump" on a street) becomes left / center / right,
    by the layer's place in the scene."""
    for sc in scenes:
        layers = sc.get("layers", [])
        valid = _valid_slots(cat, sc.get("background"))
        spread = {1: ["center"], 2: ["left", "right"]}.get(len(layers), ["left", "center", "right"])
        for i, ly in enumerate(layers):
            pos = ly.get("position")
            if isinstance(pos, str) and pos not in valid:
                ly["position"] = spread[min(i, len(spread) - 1)]
                log.hit("replaced position slots the background does not have")
        for ob in sc.get("objects", []):
            if isinstance(ob.get("position"), str) and ob["position"] not in valid:
                ob["position"] = "center"
                log.hit("replaced object position slots the background does not have")
            keep = []
            for mo in ob.get("motions", []):
                if isinstance(mo.get("to"), str) and mo["type"] == "move" and mo["to"] not in valid:
                    log.hit("dropped object moves to a slot the background does not have")
                    continue
                keep.append(mo)
            if "motions" in ob:
                ob["motions"] = keep
                if not keep:
                    del ob["motions"]


def _fit_pictures(spec: dict[str, Any], cat: Catalog, log: _Log) -> None:
    """Pictures come in every width, bodies in one: narrow the pictures that would stand on one another."""
    from reel.assets.layout import fit_spec

    for _scene, _character in fit_spec(spec, cat):
        log.hit("narrowed wide picture characters so they do not stand on one another")


#: directions / words an action's ``target`` or ``to`` may use besides slots and character ids
_TARGET_WORDS = frozenset({"camera", "up", "down", "left", "right", "forward", "back"})


def _fix_targets(scenes: list[dict[str, Any]], cat: Catalog, log: _Log) -> None:
    """``look_at`` / ``point`` / ``walk`` take a character id, a slot or a direction as target.  A
    target the scene cannot resolve (a character who is not in this scene, an invented place) makes
    the renderer raise while a spec that lints clean looks fine, so such params are dropped."""
    for sc in scenes:
        here = {ly.get("character"): ly.get("character") for ly in sc.get("layers", [])}
        lower = {str(k).lower(): k for k in here}
        valid = _valid_slots(cat, sc.get("background")) | _TARGET_WORDS
        for ly in sc.get("layers", []):
            for a in ly.get("actions", []):
                params = a.get("params")
                if not isinstance(params, dict):
                    continue
                for key in ("target", "to"):
                    val = params.get(key)
                    if not isinstance(val, str):
                        continue
                    if val in here or val in valid:
                        continue
                    if val.lower() in lower:
                        params[key] = lower[val.lower()]
                        log.hit("matched character references case-insensitively")
                    else:
                        del params[key]
                        log.hit("dropped action targets the scene cannot resolve")


def _guess_duration(sc: dict[str, Any]) -> float:
    ends = [
        t
        for cp in sc.get("captions", [])
        if isinstance(cp, dict) and (t := _num(cp.get("t1"))) is not None
    ]
    return _r(max(ends) + 0.6, 2) if ends else 5.0


def _fix_scene(
    sc: dict[str, Any], idx: int, used: set[str], ids: dict[str, str], cat: Catalog, log: _Log
) -> None:
    where = f"scenes[{idx}]"
    _tidy_keys(sc, "scene", log, where)
    sid = sc.get("id")
    sid = (
        str(sid).strip() if isinstance(sid, (str, int)) and str(sid).strip() else f"s{idx + 1:02d}"
    )
    base, n = sid, 2
    while sid in used:
        sid = f"{base}_{n}"
        n += 1
    if sid != sc.get("id"):
        log.hit("fixed missing or duplicate scene ids")
    sc["id"] = sid
    used.add(sid)

    dur = _num(sc.get("duration_sec"))
    if dur is None:
        dur = _guess_duration(sc)
        log.hit("filled missing scene durations")
    sc["duration_sec"] = _r(min(HARD_MAX_SCENE_SEC, max(HARD_MIN_SCENE_SEC, dur)), 3)

    bg = sc.get("background")
    if isinstance(bg, str):
        bg = {"template": bg}
    if isinstance(bg, dict):
        _tidy_keys(bg, "background", log, f"{where}.background")
        if not isinstance(bg.get("params", {}), dict):
            bg["params"] = {}
        sc["background"] = bg

    cam = sc.get("camera")
    if cam is not None:
        if isinstance(cam, dict):
            _tidy_keys(cam, "camera", log, f"{where}.camera")
            moves = _list_of_dicts(cam.get("moves"), log, f"{where}.camera.moves")
            cam["moves"] = [m for m in (_fix_move(m, cat, log) for m in moves) if m is not None]
            sc["camera"] = cam
        else:
            del sc["camera"]
            log.hit("dropped a malformed camera block")

    layers = _list_of_dicts(sc.get("layers"), log, f"{where}.layers")
    for layer in layers:
        _fix_layer(layer, ids, cat, log)
    sc["layers"] = layers
    _fix_objects(sc, cat, log, where)

    caps = _list_of_dicts(sc.get("captions"), log, f"{where}.captions")
    sc["captions"] = [c for c in (_fix_caption(c, ids, log) for c in caps) if c is not None]

    sfx = _list_of_dicts(sc.get("sfx"), log, f"{where}.sfx")
    sc["sfx"] = [s for s in (_fix_sfx(s, cat, log) for s in sfx) if s is not None]

    tr = sc.get("transition_out")
    if tr is not None:
        if isinstance(tr, dict):
            _tidy_keys(tr, "transition", log, f"{where}.transition_out")
        else:
            del sc["transition_out"]
            log.hit("dropped a malformed transition_out")
    if "notes" in sc and not isinstance(sc["notes"], str):
        del sc["notes"]


def _fix_move(m: dict[str, Any], cat: Catalog, log: _Log) -> dict[str, Any] | None:
    _tidy_keys(m, "move", log, "a camera move")
    if m.get("type") not in cat.camera_moves:
        log.hit(f"dropped camera move {m.get('type')!r} (not in the catalog)")
        return None
    t0, t1 = _num(m.get("t0")), _num(m.get("t1"))
    if t0 is None or t1 is None or t1 <= t0:
        log.hit("dropped camera moves with an empty or missing time window")
        return None
    m["t0"], m["t1"] = _r(t0), _r(t1)
    if "ease" in m and (not isinstance(m["ease"], str) or m["ease"] not in cat.easings):
        del m["ease"]
        log.hit("dropped unknown camera easing names")
    if not isinstance(m.get("params", {}), dict):
        m["params"] = {}
    for note in clamp_move_values(
        m
    ):  # a pan written as a screen position would carry the picture off the set
        log.hit(note)
    return m


def _fix_layer(layer: dict[str, Any], ids: dict[str, str], cat: Catalog, log: _Log) -> None:
    _tidy_keys(layer, "layer", log, "a layer")
    ref = layer.get("character")
    if isinstance(ref, str) and ref not in ids.values() and ref.lower() in ids:
        layer["character"] = ids[ref.lower()]
        log.hit("matched character references case-insensitively")
    pos = layer.get("position")
    if isinstance(pos, list) and len(pos) == 2 and all(_num(v) is not None for v in pos):
        x, y = float(_num(pos[0]) or 0.0), float(_num(pos[1]) or 0.0)
        nx, ny = x, min(FEET_Y_MAX, max(FEET_Y_MIN, y))
        found = layer.get("actions")
        raw_actions: list[Any] = found if isinstance(found, list) else []
        names = {
            a["name"] for a in raw_actions if isinstance(a, dict) and isinstance(a.get("name"), str)
        }
        moving = any(
            n in cat.actions and getattr(cat.actions.get(n), "moves_root", False) for n in names
        )
        if not moving:
            nx = min(X_MAX, max(X_MIN, x))
        if (nx, ny) != (x, y):
            log.hit("moved characters whose feet were outside the safe placement band")
        layer["position"] = [_r(nx), _r(ny)]
    actions = _list_of_dicts(layer.get("actions"), log, "a layer's actions")
    keep: list[dict[str, Any]] = []
    for a in actions:
        _tidy_keys(a, "action", log, "an action")
        t0, t1 = _num(a.get("t0")), _num(a.get("t1"))
        if not isinstance(a.get("name"), str) or t0 is None or t1 is None:
            log.hit("dropped actions with a missing name or time window")
            continue
        if t1 <= t0:
            log.hit("dropped zero-length actions")
            continue
        a["t0"], a["t1"] = _r(t0), _r(t1)
        if not isinstance(a.get("params", {}), dict):
            a["params"] = {}
        keep.append(a)
    layer["actions"] = keep


def _fix_caption(c: dict[str, Any], ids: dict[str, str], log: _Log) -> dict[str, Any] | None:
    _tidy_keys(c, "caption", log, "a caption")
    text = c.get("text")
    if not isinstance(text, str) or not text.strip():
        log.hit("dropped captions without text")
        return None
    c["text"] = text.strip()
    t0, t1 = _num(c.get("t0")), _num(c.get("t1"))
    if t0 is None:
        t0 = 0.5
        log.hit("filled missing caption start times")
    if t1 is None or t1 <= t0:
        t1 = t0 + _reading_sec(c["text"])
        log.hit("gave empty caption windows a reading-time length")
    c["t0"], c["t1"] = _r(max(0.0, t0)), _r(t1)
    spk = c.get("speaker")
    if isinstance(spk, str) and spk not in ids.values() and spk.lower() in ids:
        c["speaker"] = ids[spk.lower()]
        log.hit("matched character references case-insensitively")
    if spk is None:
        c.pop("speaker", None)
    return c


def _fix_sfx(s: dict[str, Any], cat: Catalog, log: _Log) -> dict[str, Any] | None:
    _tidy_keys(s, "sfx", log, "an sfx")
    t = _num(s.get("t"))
    if t is None or not isinstance(s.get("name"), str):
        log.hit("dropped sfx without a name or time")
        return None
    if s["name"] not in cat.sfx:
        log.hit(f"dropped sfx {s['name']!r} (not in the catalog)")
        return None
    s["t"] = _r(max(0.0, t))
    return s


# -- transitions, total duration ------------------------------------------------------------------
def _transition(sc: dict[str, Any]) -> tuple[str, float]:
    tr = sc.get("transition_out")
    if not isinstance(tr, dict):
        return "cut", 0.0
    kind = tr.get("type")
    d = _num(tr.get("duration"))
    return (kind if isinstance(kind, str) else "cut"), max(0.0, d if d is not None else 0.0)


def _overlaps(durs: list[float], trans: list[tuple[str, float]]) -> list[float]:
    """Same rule as ``reel.core.timeline.overlap_seconds``."""
    n = len(durs)
    out = [0.0] * n
    for i in range(n - 1):
        typ, d = trans[i]
        if typ != "cut" and d > 0:
            out[i] = min(d, durs[i], durs[i + 1])
    return out


def total_seconds(durs: list[float], trans: list[tuple[str, float]]) -> float:
    return sum(durs) - sum(_overlaps(durs, trans))


def _fix_transitions(scenes: list[dict[str, Any]], log: _Log) -> None:
    """Defaults for missing durations, caps relative to the neighbouring scenes, no tail transition."""
    n = len(scenes)
    for i, sc in enumerate(scenes):
        tr = sc.get("transition_out")
        if not isinstance(tr, dict):
            continue
        if i == n - 1:
            sc["transition_out"] = {"type": "cut", "duration": 0}
            continue
        typ, d = _transition(sc)
        if typ != "cut" and d <= 0:
            d = _DEFAULT_TRANSITION_SEC.get(typ, 0.6)
            log.hit("gave duration-less transitions a default length")
        cap = min(3.0, 0.4 * min(scenes[i]["duration_sec"], scenes[i + 1]["duration_sec"]))
        if d > cap + 1e-9:
            d = cap
            log.hit("shortened transitions that were too long for their scenes")
        if typ != "cut":
            tr["duration"] = _r(d, 2)
        elif "duration" in tr:
            tr["duration"] = 0


def _scale_inner(sc: dict[str, Any], f: float) -> None:
    for layer in sc.get("layers", []):
        for a in layer.get("actions", []):
            a["t0"], a["t1"] = _r(a["t0"] * f), _r(a["t1"] * f)
    for c in sc.get("captions", []):
        c["t0"], c["t1"] = _r(c["t0"] * f), _r(c["t1"] * f)
    for s in sc.get("sfx", []):
        s["t"] = _r(s["t"] * f)
    for m in sc.get("camera", {}).get("moves", []) if isinstance(sc.get("camera"), dict) else []:
        m["t0"], m["t1"] = _r(m["t0"] * f), _r(m["t1"] * f)
    for ob in sc.get("objects", []):
        if "t0" in ob:
            ob["t0"] = _r(ob["t0"] * f)
        if "t1" in ob:
            ob["t1"] = _r(ob["t1"] * f)
        for mo in ob.get("motions", []):
            mo["t0"], mo["t1"] = _r(mo["t0"] * f), _r(mo["t1"] * f)


def fit_total_duration(
    scenes: list[dict[str, Any]],
    goal: float,
    log: _Log | None = None,
    tolerance: float = 1.0,
    floor: float = HARD_MIN_SCENE_SEC,
    ceiling: float = HARD_MAX_SCENE_SEC,
) -> float:
    """Rescale ``duration_sec`` (and the times inside each scene) so that
    ``sum(durations) - sum(transition overlaps)`` lands on ``goal`` seconds.

    Every scene is first brought into ``[floor, ceiling]`` (default 0.5-30 s, the model's hard
    limits); then, unless the total is already within ``tolerance`` of the goal and inside the
    45-60 s budget, one factor is found by bisection (overlaps depend on the durations, so there is
    no closed form) and applied to all scenes, keeping them inside the same range.  Returns the
    resulting total.
    """
    log = log or _Log()
    lo_b = max(HARD_MIN_SCENE_SEC, floor)
    hi_b = max(lo_b, min(HARD_MAX_SCENE_SEC, ceiling))

    def clamp(x: float) -> float:
        return min(hi_b, max(lo_b, x))

    def current() -> tuple[list[float], list[tuple[str, float]]]:
        return [float(sc["duration_sec"]) for sc in scenes], [_transition(sc) for sc in scenes]

    def in_budget(t: float) -> bool:
        return 45.0 <= t <= 60.0

    first_durs, first_trans = current()
    start_total = total_seconds(first_durs, first_trans)
    total = start_total
    for passno in range(4):  # capping transitions after a rescale can move the total: re-solve
        orig, trans = current()
        base = [clamp(d) for d in orig]
        if passno == 0 and abs(total_seconds(base, trans) - goal) <= tolerance:
            new = [_r(d, 2) for d in base]
            if not in_budget(total_seconds(new, trans)):
                new = []
        else:
            new = []
        if not new:
            lo, hi = 1e-3, 1e3

            def at(s: float, base: list[float] = base) -> list[float]:
                return [clamp(b * s) for b in base]

            if total_seconds(at(lo), trans) >= goal:
                scale = lo
            elif total_seconds(at(hi), trans) <= goal:
                scale = hi
            else:
                for _ in range(100):
                    mid = math.sqrt(lo * hi)
                    if total_seconds(at(mid), trans) < goal:
                        lo = mid
                    else:
                        hi = mid
                scale = (lo + hi) / 2
            new = [_r(d, 2) for d in at(scale)]
            for _ in range(6):  # absorb rounding in the longest scene
                resid = goal - total_seconds(new, trans)
                if abs(resid) < 0.004:
                    break
                j = max(range(len(new)), key=new.__getitem__)
                adj = _r(clamp(new[j] + resid), 2)
                if adj == new[j]:
                    break
                new[j] = adj
        for sc, old, nd in zip(scenes, orig, new):
            if abs(nd / old - 1.0) > 0.02:
                _scale_inner(sc, nd / old)
            sc["duration_sec"] = nd
        _fix_transitions(scenes, log)
        durs, trans = current()
        total = total_seconds(durs, trans)
        if abs(total - goal) <= 0.03 or (
            passno == 0 and abs(total - goal) <= tolerance and in_budget(total)
        ):
            break
    if abs(total - start_total) > 0.05:
        log.hit(f"rescaled scene durations: total {start_total:.1f}s -> {total:.1f}s")
    return total


def _fit_window(t0: float, t1: float, dur: float, min_len: float) -> tuple[float, float] | None:
    """Clamp a window into ``[0, dur]``; None if nothing worth keeping is left."""
    t0 = max(0.0, t0)
    if t0 >= dur - 0.05:
        return None
    t1 = min(t1, dur)
    if t1 - t0 < min_len:
        return None
    return _r(t0), _r(t1)


def _clamp_object_windows(sc: dict[str, Any], dur: float, log: _Log) -> None:
    keep: list[dict[str, Any]] = []
    for ob in sc.get("objects", []):
        t0 = float(ob.get("t0", 0.0))
        if t0 >= dur - 0.05:
            log.hit("dropped objects that appear after their scene ends")
            continue
        if "t1" in ob:
            if ob["t1"] >= dur:
                del ob["t1"]  # until the end of the scene
            elif ob["t1"] - t0 < 0.1:
                log.hit("dropped objects that are on screen for less than a tenth of a second")
                continue
        moves = []
        for mo in ob.get("motions", []):
            win = _fit_window(mo["t0"], mo["t1"], dur, 0.1)
            if win is None:
                log.hit("dropped object motions that fall outside their scene")
                continue
            if win != (mo["t0"], mo["t1"]):
                log.hit("clamped time windows into their scene")
            mo["t0"], mo["t1"] = win
            moves.append(mo)
        if "motions" in ob:
            ob["motions"] = moves
            if not moves:
                del ob["motions"]
        keep.append(ob)
    if "objects" in sc:
        if keep:
            sc["objects"] = keep
        else:
            del sc["objects"]


def _clamp_windows(scenes: list[dict[str, Any]], cat: Catalog, log: _Log) -> None:
    for sc in scenes:
        dur = float(sc["duration_sec"])
        _clamp_object_windows(sc, dur, log)
        for layer in sc["layers"]:
            keep = []
            for a in layer["actions"]:
                win = _fit_window(a["t0"], a["t1"], dur, 0.1)
                if win is None:
                    log.hit("dropped actions that fall outside their scene")
                    continue
                if win != (a["t0"], a["t1"]):
                    log.hit("clamped time windows into their scene")
                a["t0"], a["t1"] = win
                if a["name"] in cat.actions:  # give actions the minimum length they need to read
                    need = float(getattr(cat.actions.get(a["name"]), "min_duration", 0.0) or 0.0)
                    if a["t1"] - a["t0"] < need - 1e-6 and need <= dur:
                        if a["t0"] + need <= dur:
                            a["t1"] = _r(a["t0"] + need)
                        else:
                            a["t0"], a["t1"] = _r(dur - need), _r(dur)
                        log.hit("lengthened actions shorter than their minimum")
                keep.append(a)
            layer["actions"] = keep
        caps = []
        for c in sc["captions"]:
            t0, t1 = c["t0"], c["t1"]
            if t0 >= dur - 0.2:  # starts after the scene: pull it back so the text still shows
                length = min(dur, max(t1 - t0, _reading_sec(c["text"])))
                t0, t1 = max(0.0, dur - length), dur
            t1 = min(t1, dur)
            need = _n_words(c["text"]) / (MAX_CAPTION_WPS - 0.4)
            if t1 - t0 < need:  # too fast to read: give it the time it needs if the scene has it
                t1 = min(dur, t0 + need)
                t0 = max(0.0, t1 - need)
            if t1 - t0 < 0.3:
                log.hit("dropped captions that do not fit their scene")
                continue
            if (_r(t0), _r(t1)) != (c["t0"], c["t1"]):
                log.hit("clamped time windows into their scene")
            c["t0"], c["t1"] = _r(t0), _r(t1)
            caps.append(c)
        sc["captions"] = caps
        for s in sc["sfx"]:
            s["t"] = _r(min(max(0.0, s["t"]), max(0.0, dur - 0.05)))
        if isinstance(sc.get("camera"), dict):
            moves = []
            for m in sc["camera"]["moves"]:
                win = _fit_window(m["t0"], m["t1"], dur, 0.1)
                if win is None:
                    log.hit("dropped camera moves that fall outside their scene")
                    continue
                m["t0"], m["t1"] = win
                moves.append(m)
            sc["camera"]["moves"] = moves


def normalize_spec(
    spec: Mapping[str, Any],
    *,
    style: str,
    seed: int = 0,
    target_duration: float = 50.0,
    audio: Mapping[str, Any] | None = None,
    notes: list[str] | None = None,
    catalog: Catalog | None = None,
    min_scene_sec: float = HARD_MIN_SCENE_SEC,
    max_scene_sec: float = HARD_MAX_SCENE_SEC,
    fit_tolerance: float = 1.0,
) -> dict[str, Any]:
    """Apply the deterministic fixes; returns a new dict (the input is not modified).

    * fill ``version`` and the ``meta`` defaults; force ``meta.style``, ``meta.seed`` and
      ``meta.target_duration_sec`` (clamped to the 45-60 s budget); drop unknown ``meta`` keys
    * unique scene/character ids; ``duration_sec`` clamped to 0.5-30 s
    * rescale scene durations (and the times inside them) so that
      ``sum(durations) - sum(transition overlaps)`` hits the target, accounting for overlaps (the
      rescale keeps every scene within ``min_scene_sec`` .. ``max_scene_sec``; a total already
      within ``fit_tolerance`` seconds of the target is left alone)
    * drop zero-length actions, clamp action/caption/sfx/camera times into their scene, give actions
      their minimum length, keep feet inside the safe placement band
    * tidy slips: ``duration`` -> ``duration_sec``, ``start``/``end`` -> ``t0``/``t1``, colour names
      -> hex, unknown *cosmetic* names (props, sfx, camera moves, easings) dropped
    * objects from the asset library: names resolved through the library's own words (``automobile`` -> ``car``),
      times and positions clamped like layers', an object the library lacks dropped and recorded in
      ``meta.library_gaps`` (as is anything the model reports there itself)
    * picture characters that would stand on one another get room (``reel.assets.layout``): the outer two move to the far
      slots, and the scene is scaled down together if that is not enough
    * ``audio``: when given, replaces the model's block (models must not invent music paths)

    Names that carry meaning - actions, backgrounds, transitions, archetypes, caption styles - are
    never touched: the linter reports them and the model corrects them.  ``notes`` (if given)
    receives one human-readable line per kind of fix.
    """
    if not isinstance(spec, Mapping):
        raise TypeError(f"normalize_spec expects a JSON object, got {type(spec).__name__}")
    cat = catalog or CATALOG
    log = _Log()
    out = _unwrap(copy.deepcopy(dict(spec)), log)
    goal = clamp_target(target_duration)
    # aim a hair inside the budget so rounding can never push the total across 45 or 60
    aim = min(max(goal, MIN_TOTAL_SEC + FIT_MARGIN_SEC), MAX_TOTAL_SEC - FIT_MARGIN_SEC)

    if not isinstance(out.get("version"), str):
        out["version"] = SPEC_VERSION

    raw_scenes = _list_of_dicts(out.get("scenes"), log, "scenes")
    ids = _fix_characters(out, cat, log)
    id_map = {i.lower(): i for i in ids}
    used: set[str] = set()
    for idx, sc in enumerate(raw_scenes):
        _fix_scene(sc, idx, used, id_map, cat, log)
    out["scenes"] = raw_scenes
    _define_missing_characters(out, raw_scenes, cat, log)
    _give_palettes(out.get("characters", []), cat, log)
    _fix_slots(raw_scenes, cat, log)
    _fix_targets(raw_scenes, cat, log)
    _fix_meta(out, style, seed, goal, raw_scenes, log)
    for gap, found in _merge_gaps(out["meta"], log.gaps, cat, log):
        from reel.assets.gaps import apply_library_gap

        apply_library_gap(out, gap, found, cat)
    _fit_pictures(out, cat, log)
    if raw_scenes:
        _fix_transitions(raw_scenes, log)
        fit_total_duration(
            raw_scenes,
            aim,
            log,
            tolerance=fit_tolerance,
            floor=min_scene_sec,
            ceiling=max_scene_sec,
        )
        _clamp_windows(raw_scenes, cat, log)

    if audio is not None:
        out["audio"] = copy.deepcopy(dict(audio))
    elif not isinstance(out.get("audio"), dict):
        out.pop("audio", None)
    out = {k: out[k] for k in ("version", "meta", "characters", "scenes", "audio") if k in out}
    if notes is not None:
        notes.extend(log.notes())
    return out


# =============================================================================== repair message
def format_repair_message(
    report: LintReport,
    *,
    max_errors: int = 30,
    max_warnings: int = 10,
    truncated: bool = False,
    restart: bool = False,
    hint_chars: int | None = None,
) -> str:
    """The user turn that asks the model to correct its JSON.  With ``restart`` the previous reply
    is not part of the conversation (small windows have no room for it), so the message asks for the
    whole spec again, avoiding the listed problems; ``hint_chars`` caps the length of each hint."""
    errs = report.errors
    plural = "s" if len(errs) != 1 else ""
    if restart:
        head = (
            f"Machine validation of your previous reply FAILED with {len(errs)} error{plural}. "
            "Write the whole spec again from the start (one JSON object, no explanation) and avoid "
            "these problems:"
        )
    else:
        head = (
            f"Machine validation FAILED with {len(errs)} error{plural}. "
            "Fix these problems and return the FULL corrected JSON (the whole spec as one JSON "
            "object, not a diff, with no explanation):"
        )
    lines = [head, ""]
    ordered = report.sorted_issues()
    for i in [x for x in ordered if x.severity is Severity.ERROR][:max_errors]:
        lines.append(_issue_line(i, hint_chars))
    if len(errs) > max_errors:
        lines.append(f"... and {len(errs) - max_errors} more error(s)")
    warns = report.warnings
    if warns and max_warnings > 0:
        lines += ["", "Warnings (fix these too while you are at it):"]
        lines += [_issue_line(i, hint_chars) for i in warns[:max_warnings]]
        if len(warns) > max_warnings:
            lines.append(f"... and {len(warns) - max_warnings} more warning(s)")
    if report.total_sec is not None and any(i.code == "DURATION_BUDGET" for i in errs):
        lines += [
            "",
            f"The current total is {report.total_sec:.2f} s = sum of the {report.n_scenes} scene "
            "durations minus the transition overlaps; it must be 45-60 s.",
        ]
    if truncated:
        lines += [
            "",
            "Your previous reply was cut off by the length limit: write more compact JSON "
            "(no indentation, no optional fields that equal their default).",
        ]
    return "\n".join(lines)


def _issue_line(i: LintIssue, hint_chars: int | None = None) -> str:
    where = f" at {i.path}" if i.path else ""
    hint = i.hint or ""
    if hint and hint_chars is not None and len(hint) > hint_chars:
        hint = hint[: max(0, hint_chars - 3)].rstrip() + "..."
    return f"- [{i.code}]{where}: {i.message}" + (f"  -> {hint}" if hint else "")


def shorter_reply_request(kept_scenes: int, truncations: int) -> str:
    """What a restarted request adds after a reply was cut off: ask for a much shorter spec, and
    for an even shorter one each time it happens again."""
    if truncations <= 1:
        shape = (
            f"{COMPACT_MIN_SCENES - 1}-{COMPACT_MAX_SCENES - 2} scenes of 5-7 s, at most 2 characters "
            "per scene, at most 2 actions per layer"
        )
    else:
        shape = "6 scenes of about 8 s, ONE character per scene, ONE action per layer"
    got = f" after {kept_scenes} complete scene(s)" if kept_scenes else ""
    return (
        f"Your previous reply was cut off by the length limit{got}. Write a MUCH SHORTER spec this "
        f"time: {shape}, one caption of under 10 words per scene, no optional fields. Reply with the "
        "FULL JSON object only."
    )


# =============================================================================== the generator
#: the fewest complete scenes of a cut-off reply worth keeping as the result
MIN_PARTIAL_SCENES = 3
#: scenes outside this range (the prompt asks for 3-8 s) are pulled into it: models, small ones
#: especially, write 1 s or 20 s scenes, which the rescale to 45-60 s would only scale along
LLM_MIN_SCENE_SEC, LLM_MAX_SCENE_SEC = 2.5, 9.0


@dataclass
class _Attempt:
    raw: str
    spec: dict[str, Any] | None
    report: LintReport
    notes: list[str] = field(default_factory=list)
    partial: bool = False  # the spec was salvaged from a reply that never became valid JSON
    cut_off: bool = False  # the reply ran out of room (token limit / context window)
    parse_error: str = ""

    @property
    def n_scenes(self) -> int:
        return len(self.spec["scenes"]) if self.spec else 0


class LLMSpecGenerator(SpecGenerator):
    """Ask a model for the spec, normalise it, lint it, and let the linter drive corrections.

    ``max_repairs`` is the number of *extra* rounds after the first reply, so a stubborn model is
    called at most ``1 + max_repairs`` times before :class:`SpecGenerationError` (carrying the last
    lint report and raw reply) is raised.  ``audio`` is the block written into the spec (default:
    procedural music + TTS voice-over); pass ``{}`` for the schema defaults (silent).

    Small context windows: ``compact`` chooses the short prompt variant (``None`` = automatic: used
    when the client reports a context window under :data:`reel.llm.base.COMPACT_BELOW_TOKENS`
    tokens); the reply budget (``max_tokens``) is cut down to what fits next to the prompt.  When a
    reply is cut off anyway, the complete scenes it contained are salvaged, the request restarts
    with the compact prompt and a demand for a much shorter spec (the cut-off reply is not echoed:
    there is no room for it), and if the attempts run out the best salvaged spec is returned (at
    least :data:`MIN_PARTIAL_SCENES` scenes, lint-clean) with a note, instead of nothing.

    ``verbatim`` (default on) makes the script the text of the captions: the prompt demands it, and whatever the model
    still got wrong (a dropped or reworded line, a speaker's name where the line belongs, an invented line) is put right
    deterministically by :class:`reel.llm.fidelity.ScriptLock` (said in ``notes``).  A script too long to be read in a
    reel is condensed by the model instead.

    ``enrich`` (default on) fills what a weak model left empty once the spec has been accepted -
    gestures for characters that only stand there, camera moves, sound effects, varied scene
    changes, caption windows long enough to read (:func:`reel.llm.enrich.enrich_spec`; nothing the
    model wrote is replaced).  It runs after the lint-driven repair, so the model's own mistakes
    are never hidden behind it, and is dropped again (with a note) if it would make the lint worse.
    """

    label = "llm"

    def __init__(
        self,
        client: LLMClient,
        max_repairs: int = 3,
        temperature: float = 0.4,
        *,
        max_tokens: int = 12288,
        catalog: Catalog | None = None,
        audio: Mapping[str, Any] | None = None,
        lint_options: LintOptions | None = None,
        compact: bool | None = None,
        enrich: bool = True,
        verbatim: bool = True,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs must be >= 0")
        self.client = client
        self.max_repairs = max_repairs
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.catalog = catalog
        self.audio: dict[str, Any] = default_audio() if audio is None else dict(audio)
        self.lint_options = lint_options
        self.compact = compact
        self.enrich = enrich
        self.verbatim = verbatim

    # -- the public entry ------------------------------------------------------------------------
    def generate(
        self,
        script: str,
        *,
        style: str,
        seed: int = 0,
        target_duration: float = 50.0,
    ) -> GenerationResult:
        cat = self.catalog or CATALOG
        if not isinstance(script, str) or not script.strip():
            raise SpecGenerationError("the script is empty; write at least a few sentences")
        if style not in cat.styles:
            close = cat.styles.suggest(style)
            raise SpecGenerationError(
                f"unknown style {style!r}; registered styles: {', '.join(cat.styles.names())}"
                + (f" (did you mean {close[0]!r}?)" if close else "")
            )
        notes: list[str] = []
        target = clamp_target(target_duration)
        if abs(target - float(target_duration)) > 1e-9:
            notes.append(
                f"target duration {target_duration:g}s clamped to {target:g}s (budget 45-60s)"
            )
        window = self._window()
        compact = (
            self.compact
            if self.compact is not None
            else (window is not None and window < COMPACT_BELOW_TOKENS)
        )
        name = self.client.name

        def first_message(truncations: int) -> str:
            text = build_user_message(
                script,
                style=style,
                target_duration=target,
                seed=seed,
                compact=compact,
                verbatim=self.verbatim,
            )
            return text + (f"\n\n{shorter_reply_request(kept, truncations)}" if truncations else "")

        lock = ScriptLock(script, cat) if self.verbatim else None
        script_fits = True
        if lock is not None and not lock.fits():
            script_fits = False
            notes.append(
                f"the script takes about {round(lock.reading_sec)} s to read, more than a reel can show: "
                "it is condensed instead of copied word for word"
            )
        kept = 0  # complete scenes of the last cut-off reply (for the "shorter" request)
        truncations = 0
        pending: list[Message] | None = None
        best_partial: _Attempt | None = None
        last: _Attempt | None = None
        calls = 0
        system = ""
        for attempt in range(1, self.max_repairs + 2):
            system = build_system_prompt(
                style, target, cat, compact=compact, verbatim=self.verbatim and script_fits
            )
            schema = json_schema_for_llm(cat, compact=compact)
            messages = pending if pending is not None else [Message("user", first_message(0))]
            if attempt == 1:
                win = f", {window}-token window" if window else ""
                notes.append(
                    f"model {name}{win}; {'compact ' if compact else ''}system prompt "
                    f"{len(system)} characters (~{estimate_tokens(system)} tokens)"
                )
            budget = self._reply_budget(window, system, messages)
            raw = self.client.complete(
                system,
                messages,
                json_schema=schema,
                temperature=min(self.temperature, 0.3) if compact else self.temperature,
                seed=seed,
                max_tokens=budget,
            )
            calls += 1
            truncated = bool(getattr(self.client, "last_truncated", False))
            last = self._judge(
                raw, style, seed, target, cat, truncated, lock if script_fits else None
            )
            tag = f"attempt {attempt}: " if attempt > 1 or not last.report.ok else ""
            notes.extend(f"{tag}{n}" for n in last.notes)
            if last.report.ok and last.spec is not None and not last.partial:
                if attempt > 1:
                    notes.append(
                        f"the model corrected its JSON after {attempt - 1} repair round(s)"
                    )
                spec, report = self._enriched(last, seed, cat, notes)
                spec, report = self._with_gaps(spec, report, script, cat, notes)
                return GenerationResult(
                    spec, report, attempts=calls, notes=notes, generator=f"llm:{name}"
                )
            if (
                last.partial
                and last.report.ok
                and last.spec is not None
                and (best_partial is None or last.n_scenes >= best_partial.n_scenes)
            ):
                best_partial = last
            if attempt > self.max_repairs:
                break
            if last.cut_off:  # out of room: start over, shorter, with the small prompt
                truncations += 1
                kept = last.n_scenes
                if not compact:
                    compact = True
                    notes.append(
                        f"attempt {attempt}: the reply was cut off - switching to the compact prompt"
                    )
                pending = [Message("user", first_message(truncations))]
                continue
            small = compact or (window is not None and window < COMPACT_BELOW_TOKENS)
            base = first_message(truncations)
            if small:  # no room to echo the previous reply: restate the task with the complaint
                complaint = self._complaint(last, restart=True)
                pending = [Message("user", f"{base}\n\n{complaint}")]
            else:  # script + the latest reply + the latest complaint keeps the context bounded
                pending = [
                    Message("user", base),
                    Message("assistant", raw),
                    Message("user", self._complaint(last, restart=False)),
                ]
        assert last is not None
        if best_partial is not None and best_partial.n_scenes >= MIN_PARTIAL_SCENES:
            assert best_partial.spec is not None
            notes.append(
                f"the model's reply kept getting cut off: using the {best_partial.n_scenes} complete "
                "scene(s) it did write (the reel is shorter on story than the script; a model with "
                "a larger context window would do better)"
            )
            spec, report = self._enriched(best_partial, seed, cat, notes)
            spec, report = self._with_gaps(spec, report, script, cat, notes)
            return GenerationResult(
                spec, report, attempts=calls, notes=notes, generator=f"llm:{name}"
            )
        n = len(last.report.errors)
        if last.cut_off:
            win = f"{window}-token" if window else "limited"
            message = (
                f"the model's reply was cut off on every one of {calls} attempt(s) with {name}: it "
                f"has a {win} context window and even the {'compact ' if compact else ''}prompt "
                f"(~{estimate_tokens(system)} tokens) leaves too little room for a whole spec. Use "
                "a model with a larger context window, or build without a model (the offline planner)."
            )
        else:
            message = (
                f"the model's JSON still has {n} error{'s' if n != 1 else ''} after "
                f"{calls} attempt(s) with {name}"
            )
        raise SpecGenerationError(
            message, lint=last.report, raw=last.raw, spec=last.spec, attempts=calls
        )

    # -- internals ---------------------------------------------------------------------------------
    def _window(self) -> int | None:
        try:
            w = getattr(self.client, "context_window", None)
        except Exception:  # a client that cannot say is a client with an unknown window
            return None
        return int(w) if isinstance(w, int) and not isinstance(w, bool) and w > 0 else None

    def _reply_budget(self, window: int | None, system: str, messages: list[Message]) -> int:
        """``max_tokens``, cut down to what fits in the window next to the prompt."""
        if window is None:
            return self.max_tokens
        used = estimate_tokens(system) + sum(estimate_tokens(m.content) + 8 for m in messages) + 32
        return int(max(MIN_REPLY_TOKENS, min(self.max_tokens, window - used)))

    def _judge(
        self,
        raw: str,
        style: str,
        seed: int,
        target: float,
        cat: Catalog,
        truncated: bool,
        lock: ScriptLock | None = None,
    ) -> _Attempt:
        partial = False
        cut_off = False
        parse_error = ""
        try:
            data = extract_json(raw)
        except JSONExtractionError as exc:
            parse_error = str(exc)
            cut_off = truncated or exc.cut_off
            got = salvage(raw)
            if got is None:
                hint = "reply with ONLY the JSON object, starting with '{' and ending with '}'"
                return _Attempt(
                    raw,
                    None,
                    synthetic_report("JSON_SYNTAX", parse_error, hint),
                    cut_off=cut_off,
                    parse_error=parse_error,
                )
            data, scenes_closed = got
            if scenes_closed:  # only the closing braces are missing: nothing was lost
                cut_off = False
            else:
                partial = True
        notes: list[str] = []
        spec = normalize_spec(
            data,
            style=style,
            seed=seed,
            target_duration=target,
            audio=self.audio,
            notes=notes,
            catalog=cat,
            min_scene_sec=LLM_MIN_SCENE_SEC,
            max_scene_sec=HARD_MAX_SCENE_SEC if partial else LLM_MAX_SCENE_SEC,
        )
        if lock is not None and not partial:
            spec = self._locked(spec, lock, style, seed, target, cat, notes)
        report = lint_data(spec, catalog=cat, options=self.lint_options)
        if partial:
            notes.insert(
                0, f"salvaged {len(spec['scenes'])} complete scene(s) from a cut-off reply"
            )
        return _Attempt(
            raw,
            spec,
            report,
            notes,
            partial=partial,
            cut_off=cut_off and partial,
            parse_error=parse_error if partial else "",
        )

    def _locked(
        self,
        spec: dict[str, Any],
        lock: ScriptLock,
        style: str,
        seed: int,
        target: float,
        cat: Catalog,
        notes: list[str],
    ) -> dict[str, Any]:
        """The spec with its captions put back to the script (and the budget refitted when scenes were added)."""
        try:
            locked, done = lock.apply(spec)
        except Exception as exc:  # the lock is a safety net: it must never cost the spec itself
            notes.append(f"script check skipped ({type(exc).__name__}: {exc})")
            return spec
        if not done.changed():
            return spec
        again: list[str] = []
        fixed = normalize_spec(
            locked,
            style=style,
            seed=seed,
            target_duration=target,
            audio=self.audio,
            notes=again,
            catalog=cat,
            min_scene_sec=LLM_MIN_SCENE_SEC,
            max_scene_sec=LLM_MAX_SCENE_SEC,
        )
        notes.extend(done.notes())
        notes.extend(n for n in again if n not in notes)
        return fixed

    def _enriched(
        self, attempt: _Attempt, seed: int, cat: Catalog, notes: list[str]
    ) -> tuple[dict[str, Any], LintReport]:
        """The accepted spec with its empty places filled, and its new lint report - or the
        attempt's own spec and report when enrichment is off, has nothing to add, fails, or would
        make the lint worse (said in ``notes``)."""
        assert attempt.spec is not None
        if not self.enrich or not attempt.report.ok:
            return attempt.spec, attempt.report
        from reel.llm.enrich import enrich_spec  # here: that module imports this one

        found: list[str] = []
        try:
            richer = enrich_spec(attempt.spec, cat, seed, found)
            if not found:
                return attempt.spec, attempt.report
            report = lint_data(richer, catalog=cat, options=self.lint_options)
        except Exception as exc:  # decoration must never cost the spec itself
            notes.append(f"enrichment skipped ({type(exc).__name__}: {exc})")
            return attempt.spec, attempt.report
        if report.ok and len(report.warnings) <= len(attempt.report.warnings):
            notes.extend(found)
            return richer, report
        notes.append("enrichment skipped: it would have made the lint report worse")
        return attempt.spec, attempt.report

    def _with_gaps(
        self, spec: dict[str, Any], report: LintReport, script: str, cat: Catalog, notes: list[str]
    ) -> tuple[dict[str, Any], LintReport]:
        """The spec with what its script needs from the asset library and the library lacks noted in ``meta.library_gaps``
        (what the model reported stays; the rest comes from reading the script), and its lint report brought up to date."""
        from reel.llm.library_plan import attach_library_gaps

        try:
            if not attach_library_gaps(spec, script, cat, notes):
                return spec, report
            return spec, lint_data(spec, catalog=cat, options=self.lint_options)
        except Exception as exc:  # advice about the library must never cost the spec itself
            notes.append(f"library check skipped ({type(exc).__name__}: {exc})")
            return spec, report

    @staticmethod
    def _complaint(attempt: _Attempt, *, restart: bool) -> str:
        if attempt.spec is None or attempt.partial:
            msg = attempt.parse_error or (
                attempt.report.errors[0].message if attempt.report.errors else "not valid JSON"
            )
            return (
                f"Your reply could not be parsed: {msg}. Fix this and return the FULL corrected "
                "JSON as one object, with nothing before or after it."
            )
        if restart:
            return format_repair_message(
                attempt.report, max_errors=8, max_warnings=0, restart=True, hint_chars=100
            )
        return format_repair_message(attempt.report)


__all__ = [
    "MIN_PARTIAL_SCENES",
    "JSONExtractionError",
    "LLMSpecGenerator",
    "extract_json",
    "fit_total_duration",
    "format_repair_message",
    "normalize_spec",
    "salvage_json",
    "shorter_reply_request",
    "total_seconds",
]
