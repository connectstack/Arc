"""The prompt that turns a script into a scene spec.

The prompt text itself lives in ``prompts/script_to_spec.md`` (a template with ``{{placeholders}}``
and ``{{#has KIND NAME}}...{{/has}}`` conditionals); this module fills it in:

* :func:`build_system_prompt` - rules + a COMPACT catalog rendered from the *live* manifest + a worked
  example, all derived from the catalog passed in (default: the real one), so the prompt can only
  mention backgrounds, actions, sfx, ... that exist right now.
* :func:`build_user_message`  - the script.
* :func:`json_schema_for_llm` - the enum-pinned JSON Schema handed to the provider's structured-output
  channel (Ollama ``format``, OpenAI ``response_format``, Anthropic tool ``input_schema``).
* :func:`render_prompt_preview` - system prompt + sample user message as plain text, for the docs.

``python -m reel.llm [style] [--compact] [--schema]`` prints the preview.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from reel.core.catalog import CATALOG, Catalog
from reel.core.lint import PALETTE_ROLES, UNIVERSAL_SLOTS
from reel.core.manifest import build_manifest
from reel.core.schema import build_schema
from reel.core.spec import MAX_TOTAL_SEC, MIN_TOTAL_SEC, CameraMoveSpec
from reel.llm.base import (
    COMPACT_MAX_ON_SCREEN,
    COMPACT_MAX_SCENE_SEC,
    COMPACT_MAX_SCENES,
    COMPACT_MIN_SCENE_SEC,
    COMPACT_MIN_SCENES,
    FEET_Y_MAX,
    FEET_Y_MIN,
    MAX_ON_SCREEN,
    MAX_SCENE_SEC,
    MAX_SCENES,
    MIN_SCENE_SEC,
    MIN_SCENES,
    SFX_PER_SCENE,
    X_MAX,
    X_MIN,
    clamp_target,
    fmt_num,
    slot_positions,
)

PROMPT_FILE = Path(__file__).with_name("prompts") / "script_to_spec.md"
#: the short variant for models with a small context window (~2k tokens instead of ~5k)
COMPACT_PROMPT_FILE = Path(__file__).with_name("prompts") / "script_to_spec_compact.md"

SAMPLE_SCRIPT = (
    "Mia tiptoes into the kitchen at midnight. The cookie jar is empty!\n"
    '"Grandpa, someone stole our cookies!" she whispers.\n'
    'Grandpa walks in, yawning. "Impossible. I guarded that jar all day."'
)

#: a param description shorter than this only restates the param name, so the catalog omits it
MIN_DESC_CHARS = 28

#: how many overlapping transitions / of which length the prompt's arithmetic example uses
EXAMPLE_OVERLAPS = 5
EXAMPLE_OVERLAP_SEC = 0.6

_ASCII = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "→": "->",
        "×": "x",
        " ": " ",
    }
)


# =============================================================================== template engine
_COMMENT = re.compile(r"<!--.*?-->[ \t]*\n?", re.S)
_BLOCK = re.compile(
    r"^[ \t]*\{\{#(has|unless)[ \t]+([^}\n]*?)[ \t]*\}\}[ \t]*\n(.*?)^[ \t]*\{\{/\1\}\}[ \t]*(?:\n|\Z)",
    re.S | re.M,
)
_INLINE = re.compile(r"\{\{#(has|unless)[ \t]+([^}\n]*?)[ \t]*\}\}(.*?)\{\{/\1\}\}", re.S)
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def load_template(path: str | Path | None = None, *, compact: bool = False) -> str:
    """The prompt template text (default: the file shipped next to this module)."""
    default = COMPACT_PROMPT_FILE if compact else PROMPT_FILE
    return Path(path or default).read_text(encoding="utf-8")


def _holds(condition: str, catalog: Catalog) -> bool:
    """``'action enter_from,exit_to'`` -> are all those names registered?  ``'sfx'`` -> any entry?"""
    parts = condition.replace(",", " ").split()
    if not parts:
        raise ValueError("empty {{#has}} condition in the prompt template")
    registry = catalog.registry(parts[0])
    names = parts[1:]
    if not names:
        return len(registry) > 0
    return all(n in registry for n in names)


def render_template(template: str, values: Mapping[str, Any], catalog: Catalog) -> str:
    """Strip comments, resolve conditionals against ``catalog``, then fill the placeholders."""
    text = _COMMENT.sub("", template)

    def cond(m: re.Match[str]) -> str:
        keep = _holds(m.group(2), catalog) == (m.group(1) == "has")
        return m.group(3) if keep else ""

    text = _BLOCK.sub(cond, text)
    text = _INLINE.sub(cond, text)
    if "{{#" in text or "{{/" in text:
        raise ValueError("unbalanced or nested {{#has}} / {{#unless}} block in the prompt template")
    unknown = sorted(set(_PLACEHOLDER.findall(text)) - set(values))
    if unknown:
        raise KeyError(f"prompt template uses unknown placeholder(s): {', '.join(unknown)}")
    text = _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


# =============================================================================== catalog -> text
def _ascii(text: str) -> str:
    return " ".join(str(text).translate(_ASCII).split())


def _short(text: str, limit: int) -> str:
    text = _ascii(text)
    return text if len(text) <= limit else text[: max(1, limit - 3)].rstrip() + "..."


def _fmt_default(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:g}"
    if isinstance(v, str):
        return v
    return json.dumps(v, separators=(",", ":"))


def _range(p: dict[str, Any], integer: bool) -> str:
    lo = p.get("minimum", p.get("exclusiveMinimum"))
    hi = p.get("maximum", p.get("exclusiveMaximum"))
    word = "int" if integer else "number"
    if lo is None and hi is None:
        return word
    pre = "int " if integer else ""
    return f"{pre}{'' if lo is None else f'{lo:g}'}..{'' if hi is None else f'{hi:g}'}"


def _param_text(p: dict[str, Any], max_desc: int) -> str:
    """One param as ``name: choices-or-range (=default) - description``."""
    typ = str(p.get("type", "any"))
    kinds = [t for t in typ.split(" | ") if t != "null"] or ["any"]
    if p.get("enum"):
        body = "|".join(str(e) for e in p["enum"])
    elif kinds == ["boolean"]:
        body = "true|false"
    elif kinds in (["number"], ["integer"]):
        body = _range(p, kinds == ["integer"])
    else:
        body = "|".join(kinds)
    out = f"{p['name']}: {body}"
    if "default" in p:
        out += f" (={_fmt_default(p['default'])})"
    elif p.get("required"):
        out += " (required)"
    desc = _ascii(p.get("description") or "")
    if len(desc) >= MIN_DESC_CHARS:  # shorter ones just restate the name
        out += f" - {_short(desc, max_desc)}"
    return out


def _params_line(params: list[dict[str, Any]], max_desc: int) -> str:
    return "; ".join(_param_text(p, max_desc) for p in params)


def _sig(p: dict[str, Any]) -> str:
    return json.dumps(
        {k: p.get(k) for k in ("name", "type", "enum", "default", "minimum", "maximum")},
        sort_keys=True,
        default=str,
    )


def _slots_text(catalog: Catalog, template: str) -> str:
    """Slots a character can stand on, with their x (only those whose feet line fits the rules)."""
    items: list[tuple[float, str]] = []
    plain: list[str] = []
    for name, pos in slot_positions(catalog, template).items():
        if pos is None:
            plain.append(name)
        elif FEET_Y_MIN - 0.05 <= pos[1] <= FEET_Y_MAX + 0.04:
            items.append((pos[0], f"{name}@{pos[0]:.2f}"))
    return ", ".join([t for _, t in sorted(items)] + sorted(plain))


def _render_backgrounds(rows: list[dict[str, Any]], catalog: Catalog) -> list[str]:
    out = [
        'BACKGROUNDS - scenes[].background = {"template": NAME, "params": {...}}; '
        "pick the set that fits each scene"
    ]
    shared: list[dict[str, Any]] = []
    if len(rows) >= 2:
        first = rows[0].get("params", [])
        shared = [
            p
            for p in first
            if all(_sig(p) in {_sig(q) for q in r.get("params", [])} for r in rows[1:])
        ]
        if shared:
            out.append(f"  params every background takes: {_params_line(shared, 60)}")
    shared_names = {p["name"] for p in shared}
    for r in rows:
        words = _words(r.get("tags"), r["name"]) if r.get("library") else ""
        out.append(
            f"- {r['name']}: {_short(r.get('summary', ''), 160)}"
            + (f" [words {words}]" if words else "")
        )
        own = [p for p in r.get("params", []) if p["name"] not in shared_names]
        if own:
            out.append(f"    params: {_params_line(own, 100)}")
        slots = _slots_text(catalog, r["name"])
        if slots:
            out.append(f"    slots: {slots}")
    return out


def _render_actions(rows: list[dict[str, Any]]) -> list[str]:
    out = [
        'ACTIONS - scenes[].layers[].actions[] = {"name": NAME, "t0": s, "t1": s, "params": {...}}; '
        "typ = usual length, min = shortest that reads well, * = moves the character"
    ]
    for r in rows:
        bits: list[str] = []
        if "default_duration" in r:
            bits.append(f"typ {r['default_duration']:g}s")
        if r.get("min_duration"):
            bits.append(f"min {r['min_duration']:g}s")
        star = "*" if r.get("moves_root") else ""
        head = f"- {r['name']}{star}" + (f" ({', '.join(bits)})" if bits else "")
        out.append(f"{head}: {_short(r.get('summary', ''), 130)}")
        if r.get("params"):
            out.append(f"    {_params_line(r['params'], 60)}")
    return out


def _render_transitions(rows: list[dict[str, Any]]) -> list[str]:
    out = [
        'TRANSITIONS - scenes[].transition_out = {"type": NAME, "duration": 0..3 s, "params": {...}}; '
        "the duration overlaps the next scene"
    ]
    for r in rows:
        note = " [hard change: duration 0, no overlap]" if r["name"] == "cut" else ""
        out.append(f"- {r['name']}{note}: {_short(r.get('summary', ''), 110)}")
        if r.get("params"):
            out.append(f"    params: {_params_line(r['params'], 50)}")
    return out


def _render_camera(rows: list[dict[str, Any]], easings: list[str]) -> list[str]:
    default_ease = CameraMoveSpec.model_fields["ease"].default
    out = [
        'CAMERA MOVES - scenes[].camera.moves[] = {"type": NAME, "from": v, "to": v, "t0": s, "t1": s, '
        '"ease": EASING}; a move\'s end value is kept for the rest of the scene'
    ]
    for r in rows:
        ft = f" from/to: {_short(r['from_to'], 80)}" if r.get("from_to") else ""
        out.append(f"- {r['name']}: {_short(r.get('summary', ''), 70)}.{ft}")
    if easings:
        pick = f" (default {default_ease})" if default_ease in easings else ""
        out.append(f"  EASING names{pick}: {', '.join(easings)}")
    return out


def _render_simple(title: str, rows: list[dict[str, Any]], limit: int) -> list[str]:
    out = [title]
    for r in rows:
        summary = _short(r.get("summary", ""), limit)
        out.append(f"- {r['name']}: {summary}" if summary else f"- {r['name']}")
    return out


def _words(tags: list[str] | None, name: str, *, latin: int = 5, indic: int = 0) -> str:
    """The words a script may use for a library asset, short: plurals folded away, the Latin ones (English synonyms and
    Hinglish) first, then any Indic-script ones.  The full prompt leaves Indic words out (they cost many tokens and a model
    that reads Hindi maps "गाँव" to ``village`` by itself); they are what the offline planner and the script check match."""
    have = set(tags or [])
    own = name.replace("_", " ")
    out_latin: list[str] = []
    out_indic: list[str] = []
    for t in tags or []:
        if t == own or (t.endswith("s") and t[:-1] in have):
            continue
        (out_latin if t.isascii() else out_indic).append(t)
    return ", ".join([*out_latin[:latin], *out_indic[:indic]])


def _asset_tail(row: dict[str, Any], *, kind_note: str = "") -> str:
    """``[size 0.5; roles body, accent; words car, taxi]`` for a library asset's catalog line."""
    bits: list[str] = []
    if kind_note:
        bits.append(kind_note)
    if "size" in row:
        bits.append(f"size {row['size']:g}")
    if row.get("roles"):
        bits.append("roles " + ", ".join(row["roles"]))
    words = _words(row.get("tags"), row["name"])
    if words:
        bits.append("words " + words)
    return f" [{'; '.join(bits)}]" if bits else ""


#: how each object motion is written, for the catalog (a test keeps the keys equal to ``reel.assets.objects.MOTIONS``)
MOTION_HINTS: dict[str, str] = {
    "move": "to=[x,y] or SLOT",
    "hop": "count, amount",
    "float": "amount",
    "spin": "amount = turns",
    "pulse": "amount, count",
    "fade": "from, to = 0..1",
    "grow": "from, to = times its size",
    "shake": "amount",
}


def _render_objects(rows: list[dict[str, Any]]) -> list[str]:
    out = [
        'OBJECTS - scenes[].objects[] = {"asset": NAME, "position": [x,y] or SLOT, "scale": n, '
        '"layer": "behind"|"front", "t0": s, "t1": s, "motions": [{"type": MOTION, "t0": s, "t1": s, ...}], '
        '"palette": {ROLE: "#rrggbb"}}; size = height as a share of a person (1 = a person), '
        'roles = the colours "palette" can change, words = what a script may call it',
        "  MOTION types: " + "; ".join(f"{k} ({v})" for k, v in MOTION_HINTS.items()),
    ]
    for r in rows:
        out.append(f"- {r['name']}: {_short(r.get('summary', ''), 80)}{_asset_tail(r)}")
    return out


def render_catalog(catalog: Catalog | None = None, *, compact: bool = False) -> str:
    """The compact, human-readable catalog of everything a spec may reference (live registries).

    ``compact=True`` is the small-window variant: names and one-line summaries plus the few params
    that matter; no camera, sfx, props or easings (the compact prompt tells the model to skip them).
    """
    cat = catalog or CATALOG
    m = build_manifest(cat)
    if compact:
        return _render_catalog_compact(m)
    sections: list[list[str]] = []
    if m["backgrounds"]:
        sections.append(_render_backgrounds(m["backgrounds"], cat))
    if m["actions"]:
        sections.append(_render_actions(m["actions"]))
    if m["transitions"]:
        sections.append(_render_transitions(m["transitions"]))
    if m["camera_moves"]:
        sections.append(_render_camera(m["camera_moves"], m["easings"]))
    if m["caption_styles"]:
        sections.append(
            _render_simple("CAPTION STYLES - scenes[].captions[].style", m["caption_styles"], 120)
        )
    if m["sfx"]:
        sections.append(
            _render_simple('SFX - scenes[].sfx[] = {"name": NAME, "t": s}', m["sfx"], 70)
        )
    if m["archetypes"]:
        has_pictures = any(r.get("category") == "sprite" for r in m["archetypes"])
        lines = [
            "ARCHETYPES - characters[].archetype"
            + (
                "; [picture] ones are one drawing: they cannot use their arms (no waving, pointing or holding things) "
                "and otherwise move like anyone else"
                if has_pictures
                else ""
            )
        ]
        for r in m["archetypes"]:
            sprite = r.get("category") == "sprite"
            lines.append(
                f"- {r['name']}: {_short(r.get('summary', ''), 70)}"
                f"{_asset_tail(r, kind_note='picture') if sprite else ''}"
            )
        sections.append(lines)
    if m["props"]:
        sections.append(
            _render_simple(
                "PROPS - characters[].props (the character has it for the whole story)",
                m["props"],
                45,
            )
        )
    if m["objects"]:
        sections.append(_render_objects(m["objects"]))
    return "\n\n".join("\n".join(s) for s in sections)


# -- the compact catalog ------------------------------------------------------------------------
#: presentational hint: which params the compact catalog shows for an action (an action not listed
#: shows its first choice-type param, if any).  Names that do not exist are simply not shown.
_COMPACT_PARAMS: dict[str, tuple[str, ...]] = {
    "idle": ("mood",),
    "walk": ("to", "dx"),
    "run": ("to", "dx"),
    "look_at": ("target",),
    "point": ("target",),
    "enter_from": ("style",),
    "exit_to": ("side",),
    "think": ("aha",),
    "jump": ("style",),
    "fall": ("kind",),
    "cry": ("style",),
    "laugh": ("style",),
    "wave": ("hand",),
    "pick_up": ("prop",),
}


_CUT_AT = (" and ", " with ", " that ", " which ", " while ", " for ", " to ")
_DANGLING = frozenset(
    {"a", "an", "the", "of", "and", "or", "with", "in", "on", "at", "to", "for", "from", "by"}
)


def _clause(text: str, limit: int, min_words: int = 3) -> str:
    """The first clause of a summary: parentheses dropped, cut at ``; : ,`` or at ``limit`` (at a
    connecting word if possible, never leaving a dangling "with" or "the")."""
    text = _ascii(re.sub(r"\s*\([^)]*\)", "", text)).replace("`", "")
    parts = [p.strip() for p in re.split(r"[;:,]", text) if p.strip()]
    out = parts[0] if parts else ""
    for nxt in parts[1:]:
        if len(out.split()) >= min_words:
            break
        out += ", " + nxt
    if len(out) > limit:
        window = out[:limit]
        cuts = [i for i in (window.rfind(c) for c in _CUT_AT) if i >= limit // 2]
        out = window[: max(cuts)] if cuts else window.rsplit(" ", 1)[0]
    words = out.rstrip(" ,.;:-").split()
    while len(words) > 1 and words[-1].lower() in _DANGLING:
        words.pop()
    return " ".join(words)


def _compact_value(p: dict[str, Any]) -> str:
    """``name=a|b|c`` for a choice, ``name=lo..hi`` for a number, ``name=true|false``..."""
    typ = str(p.get("type", "any"))
    kinds = [t for t in typ.split(" | ") if t != "null"] or ["any"]
    if p.get("enum"):
        vals = [str(e) for e in p["enum"]]
        body = "|".join(vals[:8]) + ("|..." if len(vals) > 8 else "")
    elif kinds == ["boolean"]:
        body = "true|false"
    elif kinds in (["number"], ["integer"]):
        body = _range(p, kinds == ["integer"])
    elif "array" in kinds and "string" in kinds:  # a target: [x,y], a slot name or a character id
        body = "character id|slot|[x,y]"
    else:
        body = "|".join(kinds)
    return f"{p['name']}={body}"


def _compact_params(row: dict[str, Any]) -> str:
    params = row.get("params", [])
    wanted = _COMPACT_PARAMS.get(row["name"])
    if wanted is not None:
        chosen = [p for n in wanted for p in params if p["name"] == n]
    else:
        first = next((p for p in params if p.get("enum")), None)
        chosen = [first] if first else []
    return "; ".join(_compact_value(p) for p in chosen)


def _render_catalog_compact(m: dict[str, Any]) -> str:
    sections: list[list[str]] = []
    rows = m["backgrounds"]
    if rows:
        shared: list[dict[str, Any]] = []
        if len(rows) >= 2:
            shared = [
                p
                for p in rows[0].get("params", [])
                if p.get("enum")
                and all(_sig(p) in {_sig(q) for q in r["params"]} for r in rows[1:])
            ]
        head = 'BACKGROUNDS (scene "background" = {"template": NAME, "params": {...}})'
        if shared:
            head += "; every one takes " + "; ".join(_compact_value(p) for p in shared)
        lines = [head]
        names = {p["name"] for p in shared}
        for r in rows:
            own = next(
                (
                    p
                    for p in r.get("params", [])
                    if p.get("enum") and p["name"] not in names and len(p["enum"]) <= 6
                ),
                None,
            )
            extra = f" | {_compact_value(own)}" if own else ""
            lines.append(f"- {r['name']}: {_clause(r.get('summary', ''), 64)}{extra}")
        sections.append(lines)
    rows = [r for r in m["actions"] if r["name"] != "talk"]  # the speaker talks by itself
    if rows:
        lines = ["ACTIONS (min = shortest length in s; * = moves the character)"]
        for r in rows:
            mn = f" (min {r['min_duration']:g})" if r.get("min_duration") else ""
            star = "*" if r.get("moves_root") else ""
            params = _compact_params(r)
            extra = f" | {params}" if params else ""
            lines.append(f"- {r['name']}{star}{mn}: {_clause(r.get('summary', ''), 56)}{extra}")
        sections.append(lines)
    rows = m["transitions"]
    if rows:
        lines = ['TRANSITIONS (scene "transition_out" = {"type": NAME, "duration": 0.4-0.8})']
        for r in rows:
            params = _compact_params(r)
            note = " (duration 0)" if r["name"] == "cut" else ""
            lines.append(f"- {r['name']}{note}" + (f" | {params}" if params else ""))
        sections.append(lines)
    for title, key, limit in (
        ("CAPTION STYLES", "caption_styles", 50),
        ("ARCHETYPES", "archetypes", 40),
    ):
        rows = m[key]
        if rows:
            lines = [title]
            for r in rows:
                clause = _clause(r.get("summary", ""), limit)
                lines.append(f"- {r['name']}: {clause}" if clause else f"- {r['name']}")
            sections.append(lines)
    return "\n\n".join("\n".join(s) for s in sections)


# =============================================================================== worked example
_MOVE_VALUES: dict[str, tuple[Any, Any]] = {
    "dolly": (0, 0.2),
    "zoom": (1.0, 1.15),
    "pan": ([0.0, 0.0], [0.06, 0.0]),
    "shake": (0.0, 0.3),
}


def _first(registry: Any, prefer: tuple[str, ...]) -> str | None:
    """The first preferred name that exists, else the first registered name, else None."""
    for n in prefer:
        if n in registry:
            return n
    names = registry.names()
    return names[0] if names else None


def _valid_params(registry: Any, name: str, params: dict[str, Any]) -> dict[str, Any]:
    """``params`` if the entry's own params model accepts them, else ``{}``."""
    if not params:
        return {}
    model = getattr(registry.get(name), "params_model", None)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        return {}
    try:
        model.model_validate(params)
    except ValidationError:
        return {}
    return params


def example_parts(
    catalog: Catalog | None = None, *, compact: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(characters, one scene) of the prompt's worked example - built from the live catalog, so it
    only uses names that exist; ``tests/test_llm.py`` lints it.  ``compact=True`` is the small
    version: one character, one layer, no camera, no sfx."""
    cat = catalog or CATALOG
    arch = cat.archetypes.names()
    a1 = "kid" if "kid" in arch else (arch[0] if arch else "")
    a2 = "elder" if "elder" in arch else (arch[-1] if arch else "")
    characters = [
        {"id": "mia", "archetype": a1, "palette": {"shirt": "#e63946", "pants": "#264653"}},
        {"id": "gran", "archetype": a2, "palette": {"shirt": "#2a9d8f", "accent": "#f4a261"}},
    ]
    characters = [c for c in characters if c["archetype"]]

    # --- background: prefer a real set over the abstract stage, show time_of_day/mood + one own param
    bg_names = cat.backgrounds.names()
    template = next(
        (
            n for n in bg_names if n != "abstract" and n not in cat.assets
        ),  # the engine's own set, not a library one
        next((n for n in bg_names if n != "abstract"), bg_names[0] if bg_names else ""),
    )
    params: dict[str, Any] = {}
    slots = ["left", "right"]
    if template:
        model = getattr(cat.backgrounds.get(template), "params_model", None)
        for key, prefer in (("time_of_day", "dusk"), ("mood", "warm")):
            values = _enum(model, key)
            if values:
                params[key] = prefer if prefer in values else values[0]
        for key in _own_enum_params(model):
            default = model.model_fields[key].default if model is not None else None
            other = [v for v in (_enum(model, key) or []) if v != default]
            if other:
                params[key] = other[0]
                break
        pos = slot_positions(cat, template)
        fitting = sorted(
            (xy[0], n) for n, xy in pos.items() if xy and FEET_Y_MIN <= xy[1] <= FEET_Y_MAX
        )
        if fitting:
            slots = [fitting[0][1], "right"]

    dur = 5.2
    layers: list[dict[str, Any]] = []
    act = cat.actions
    first_actions: list[dict[str, Any]] = []
    if "enter_from" in act:
        first_actions.append({"name": "enter_from", "t0": 0, "t1": 1.4})
    react = _first(act, ("surprise", "think", "wave", "idle"))
    if react:
        first_actions.append({"name": react, "t0": 2.6, "t1": 3.8})
    layers.append({"character": "mia", "position": slots[0], "actions": first_actions})
    if len(characters) > 1:
        second = []
        if "look_at" in act:
            p = _valid_params(act, "look_at", {"target": "mia"})
            second.append({"name": "look_at", "t0": 1.8, "t1": 3.0, **({"params": p} if p else {})})
        elif "idle" in act:
            second.append({"name": "idle", "t0": 0.0, "t1": 3.0})
        layers.append({"character": "gran", "position": slots[1], "actions": second})

    scene: dict[str, Any] = {
        "id": "s03",
        "duration_sec": dur,
        "background": {"template": template, **({"params": params} if params else {})},
    }
    move = _first(cat.camera_moves, ("dolly", "zoom", "pan"))
    if move in _MOVE_VALUES:
        a, b = _MOVE_VALUES[move]
        scene["camera"] = {"moves": [{"type": move, "from": a, "to": b, "t0": 0, "t1": dur}]}
    scene["layers"] = layers
    thing = _first(cat.objects, ("tree", "car", "gift")) if not compact else None
    if thing:
        scene["objects"] = [{"asset": thing, "position": [0.88, 0.8], "scale": 0.7}]
    cap_style = _first(cat.caption_styles, ("subtitle",)) or "subtitle"
    scene["captions"] = [
        {
            "text": "Hello! Is anyone here?",
            "t0": 0.8,
            "t1": 3.4,
            "style": cap_style,
            "speaker": "mia",
        }
    ]
    sfx = _first(cat.sfx, ("pop", "ding", "whoosh"))
    if sfx:
        scene["sfx"] = [{"name": sfx, "t": 2.6}]
    tr = _first(cat.transitions, ("wipe", "crossfade"))
    if tr and tr != "cut":
        tp = _valid_params(cat.transitions, tr, {"direction": "left"})
        scene["transition_out"] = {"type": tr, "duration": 0.5, **({"params": tp} if tp else {})}
    if compact:
        characters = characters[:1]
        scene["id"] = "s02"
        scene["layers"] = scene["layers"][:1]
        scene["layers"][0]["position"] = "left"  # the compact rules only use left / center / right
        scene["layers"][0]["actions"] = []  # tiny models copy example actions into every scene
        scene["captions"][0]["text"] = "Hi there!"  # short and generic: tiny models copy examples
        if cat.backgrounds.names():  # a neutral first stage: tiny models copy the example verbatim
            first = cat.backgrounds.names()[0]
            model = getattr(cat.backgrounds.get(first), "params_model", None)
            tod = _enum(model, "time_of_day")
            scene["background"] = {"template": first}
            if tod:
                scene["background"]["params"] = {"time_of_day": "dusk" if "dusk" in tod else tod[0]}
        scene.pop("camera", None)
        scene.pop("sfx", None)
        if "transition_out" in scene:
            scene["transition_out"].pop("params", None)
    return characters, scene


def _enum(model: Any, key: str) -> list[str] | None:
    from reel.llm.base import enum_values

    return enum_values(model, key) if isinstance(model, type) else None


def _own_enum_params(model: Any) -> list[str]:
    """Enum params a template defines itself (not time_of_day / mood / density)."""
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        return []
    skip = {"time_of_day", "mood", "density"}
    return [k for k in model.model_fields if k not in skip and _enum(model, k)]


def _compact(obj: Any) -> str:
    return _ascii_json(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _ascii_json(text: str) -> str:
    return text.translate(_ASCII)


# =============================================================================== public API
#: the rule that decides who owns the words of the captions (``{{script_rule}}`` in the templates)
SCRIPT_RULE_FREE = (
    "Follow the SCRIPT closely: keep its characters, the order of its events and its twist. Shorten long speeches "
    "and spread them over several scenes; keep each caption under about 12 words."
)
SCRIPT_RULE_VERBATIM = (
    "THE SCRIPT IS THE TEXT OF THE REEL. Every sentence of the script, narration and dialogue, appears as caption text in "
    "the SAME ORDER and in the script's OWN WORDS (same language, same spelling): copy it, never paraphrase, translate, "
    "summarize, reorder or add to it, and never put a word in a caption that the script does not have. Split a long "
    "sentence over consecutive captions at a natural pause (under about 12 words each) instead of rewording it. A script "
    "line written as 'Name: words' becomes a caption whose text is ONLY 'words' (never the name, never a colon) with "
    '"speaker" set to that character\'s id; set that character\'s "name" to the name exactly as the script writes it. '
    "Keep the script's characters, the order of its events and its twist."
)
SCRIPT_RULE_VERBATIM_COMPACT = (
    "Captions are the script's own words, in order: copy them, never reword (a long sentence goes over several "
    "captions); a script line 'Name: words' is a caption with text 'words' only and \"speaker\" = that character's id. "
    "Keep the order of events and the twist."
)


def prompt_values(
    style: str,
    target_duration: float,
    catalog: Catalog,
    *,
    compact: bool = False,
    verbatim: bool = True,
) -> dict[str, Any]:
    """Every ``{{placeholder}}`` of the template, computed from the catalog."""
    target = clamp_target(target_duration)
    style_rows = {r["name"]: r for r in build_manifest(catalog)["styles"]}
    summary = style_rows.get(style, {}).get("summary") or "a registered style"
    overlap = EXAMPLE_OVERLAPS * EXAMPLE_OVERLAP_SEC
    characters, scene = example_parts(catalog, compact=compact)
    return {
        "style": style,
        "script_rule": (
            (SCRIPT_RULE_VERBATIM_COMPACT if compact else SCRIPT_RULE_VERBATIM)
            if verbatim
            else (
                "Follow the script's order of events and its twist."
                if compact
                else SCRIPT_RULE_FREE
            )
        ),
        "style_summary": _short(summary, 160),
        "target_duration": fmt_num(target),
        "min_total": fmt_num(MIN_TOTAL_SEC),
        "max_total": fmt_num(MAX_TOTAL_SEC),
        "example_sum": f"{target + overlap:.1f}",
        "example_total": f"{target:.1f}",
        "min_scenes": MIN_SCENES,
        "max_scenes": MAX_SCENES,
        "min_scene": fmt_num(MIN_SCENE_SEC),
        "max_scene": fmt_num(MAX_SCENE_SEC),
        "feet_y_min": fmt_num(FEET_Y_MIN),
        "feet_y_max": fmt_num(FEET_Y_MAX),
        "x_min": fmt_num(X_MIN),
        "x_max": fmt_num(X_MAX),
        "max_on_screen": MAX_ON_SCREEN,
        "sfx_min": SFX_PER_SCENE[0],
        "sfx_max": SFX_PER_SCENE[1],
        "universal_slots": ", ".join(UNIVERSAL_SLOTS),
        "palette_roles": ", ".join(PALETTE_ROLES),
        "catalog": render_catalog(catalog, compact=compact),
        "example_characters": "\n".join(_compact(c) for c in characters),
        "example_scene": _compact(scene),
        "compact_min": COMPACT_MIN_SCENES,
        "compact_max": COMPACT_MAX_SCENES,
        "compact_scene_min": fmt_num(COMPACT_MIN_SCENE_SEC),
        "compact_scene_max": fmt_num(COMPACT_MAX_SCENE_SEC),
        "compact_cast": COMPACT_MAX_ON_SCREEN,
        "compact_sum": f"{target + 4 * 0.5:.1f}",
    }


def build_system_prompt(
    style: str,
    target_duration: float = 50.0,
    catalog: Catalog | None = None,
    *,
    compact: bool = False,
    verbatim: bool = True,
) -> str:
    """THE prompt: the template filled in from the live catalog.

    ``style`` is the fixed style pack the spec will use, ``target_duration`` the wanted length in
    seconds (clamped to the 45-60 s budget).  Everything that names a registry entry comes from
    ``catalog`` (default: the real, fully loaded one).  ``compact=True`` renders the short variant
    for models with a small context window (about 2k tokens instead of 5k): names and one-line
    summaries only, a tiny example, and a request for fewer and simpler scenes.  ``verbatim`` (the default)
    makes the script the text of the captions, word for word; ``False`` lets the model shorten and reword it.
    """
    cat = catalog or CATALOG
    values = prompt_values(style, target_duration, cat, compact=compact, verbatim=verbatim)
    return render_template(load_template(compact=compact), values, cat)


def build_user_message(
    script: str,
    *,
    style: str,
    target_duration: float = 50.0,
    seed: int = 0,
    compact: bool = False,
    verbatim: bool = True,
) -> str:
    """The user turn: the script, fenced so the model cannot mistake it for instructions."""
    target = fmt_num(clamp_target(target_duration))
    brief = (
        f" Keep it SHORT: {COMPACT_MIN_SCENES}-{COMPACT_MAX_SCENES} scenes, one caption each."
        if compact
        else ""
    )
    words = (
        " The captions must be this script's own words, in order: the same language, nothing reworded, nothing added."
        if verbatim
        else ""
    )
    return (
        f'Turn this script into the scene spec. Style: "{style}". Target length: {target} s '
        f"(meta.target_duration_sec = {target}). meta.seed = {seed}.{brief}{words}\n\n"
        f"SCRIPT\n<<<\n{script.strip()}\n>>>\n\n"
        "Reply with the JSON object only."
    )


#: properties the compact schema leaves out: optional, decorative, or filled in by the normaliser
_COMPACT_DROP: dict[str, tuple[str, ...]] = {
    "MetaSpec": ("fps", "resolution", "aspect", "fx", "safe_area", "library_gaps"),
    "CharacterSpec": ("props", "voice"),
    "SceneSpec": ("camera", "sfx", "notes", "objects"),
    "LayerSpec": ("scale", "depth", "facing"),
    "CaptionSpec": ("speak", "anchor"),
}
_COMPACT_REQUIRE: dict[str, tuple[str, ...]] = {
    "SceneSpec": ("id", "duration_sec", "background", "layers", "captions"),
    "LayerSpec": ("character", "position", "actions"),
    "CaptionSpec": ("text", "t0", "t1", "style"),
}


def json_schema_for_llm(catalog: Catalog | None = None, *, compact: bool = False) -> dict[str, Any]:
    """The spec's JSON Schema with every registry-bound name pinned to what is registered.

    ``compact=True`` is the schema for small models on constrained-decoding backends: the same
    names, but the optional/decorative properties (camera, sfx, props, fx, ...) are gone so they
    cannot be written, ``characters`` and each scene's ``layers`` / ``captions`` are required, and
    the scene, layer, action and caption counts are capped - so a reply has to be short.
    """
    schema = build_schema(catalog or CATALOG, enums=True)
    if not compact:
        return schema
    defs = schema.get("$defs", {})
    for name, props in _COMPACT_DROP.items():
        d = defs.get(name, {})
        for prop in props:
            d.get("properties", {}).pop(prop, None)
    for name, required in _COMPACT_REQUIRE.items():
        if name in defs:
            defs[name]["required"] = list(required)
    root = schema.get("properties", {})
    root.pop("audio", None)
    root.pop("$schema", None)
    schema["required"] = ["meta", "characters", "scenes"]
    if "characters" in root:
        root["characters"]["minItems"] = 1
    if "scenes" in root:
        root["scenes"]["minItems"] = COMPACT_MIN_SCENES
        root["scenes"]["maxItems"] = COMPACT_MAX_SCENES + 2
    caps = {
        "SceneSpec": {"layers": COMPACT_MAX_ON_SCREEN, "captions": 3},
        "LayerSpec": {"actions": 3},
    }
    for name, limits in caps.items():
        for prop, limit in limits.items():
            node = defs.get(name, {}).get("properties", {}).get(prop)
            if isinstance(node, dict):
                node["maxItems"] = limit
    # a constrained decoder honours enums but not numeric ranges: offer only the paced scene lengths
    steps = int((COMPACT_MAX_SCENE_SEC - COMPACT_MIN_SCENE_SEC) / 0.5)
    lengths = [COMPACT_MIN_SCENE_SEC + 0.5 * i for i in range(steps + 1)]
    scene_props = defs.get("SceneSpec", {}).get("properties", {})
    if "duration_sec" in scene_props:
        scene_props["duration_sec"] = {
            "type": "number",
            "enum": lengths,
            "description": "scene length in seconds",
        }
    text = defs.get("CaptionSpec", {}).get("properties", {}).get("text")
    if isinstance(text, dict):
        text["minLength"] = 2
    return schema


def approx_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token for English prose / JSON)."""
    return max(1, round(len(text) / 4))


def render_prompt_preview(
    style: str = "flat_vector",
    target_duration: float = 50.0,
    catalog: Catalog | None = None,
    script: str | None = None,
    *,
    include_schema: bool = False,
    seed: int = 0,
    compact: bool = False,
) -> str:
    """The exact system prompt and a sample user message, as plain text (used to generate docs)."""
    cat = catalog or CATALOG
    system = build_system_prompt(style, target_duration, cat, compact=compact)
    user = build_user_message(
        script or SAMPLE_SCRIPT,
        style=style,
        target_duration=target_duration,
        seed=seed,
        compact=compact,
    )
    parts = [
        f"=== SYSTEM PROMPT ({len(system)} characters, about {approx_tokens(system)} tokens) ===",
        system.rstrip(),
        "",
        "=== USER MESSAGE ===",
        user,
    ]
    if include_schema:
        schema = json.dumps(json_schema_for_llm(cat, compact=compact), indent=1)
        parts += [
            "",
            f"=== JSON SCHEMA ({len(schema)} characters; sent through the provider's "
            "structured-output channel, not in the prompt text) ===",
            schema,
        ]
    return "\n".join(parts) + "\n"


def main(argv: list[str], out: Callable[[str], Any] = sys.stdout.write) -> int:
    """``python -m reel.llm [style] [--compact] [--schema]``: print the exact prompt a model gets."""
    args = [a for a in argv if not a.startswith("--")]
    style = args[0] if args else "flat_vector"
    out(
        render_prompt_preview(style, include_schema="--schema" in argv, compact="--compact" in argv)
    )
    return 0
