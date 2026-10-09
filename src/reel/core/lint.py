"""The spec linter.  Never raises on a bad spec - it reports.

Passes (all of them run even when an earlier one found problems, so one run shows
everything that is wrong):

1. JSON syntax (with line/column).
2. *Reference scan*: every name that must exist in a registry (action, background,
   transition, style, sfx, ...) is looked up.  Missing ones are collected into
   ``report.missing`` - the exact list of things to add - and each gets a
   remediation hint.  This pass is tolerant: it works on the raw dict, so it still
   runs when the document has unrelated schema errors.
3. Pydantic schema validation, with errors translated to JSON paths and
   "did you mean" hints.
4. Parameter validation for registered things (action params, background params,
   transition/camera params) against their own schemas.
5. Semantic checks on the typed spec: ids, timing windows, caption safety,
   transition budget, and the total-duration budget (45-60 s).
"""

from __future__ import annotations

import difflib
import itertools
import json
import re
import shlex
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from reel.core.catalog import CATALOG, Catalog
from reel.core.spec import (
    MAX_TOTAL_SEC,
    MIN_TOTAL_SEC,
    CharacterSpec,
    ReelSpec,
    SceneSpec,
)
from reel.core.text import bad_text_reason, clean_text
from reel.core.timeline import compute_timeline, overlap_seconds

#: positions every background understands, in addition to its own named slots
UNIVERSAL_SLOTS = ("left", "center", "right", "far_left", "far_right", "off_left", "off_right")
PALETTE_ROLES = ("skin", "hair", "shirt", "shirt2", "pants", "shoes", "accent", "eye", "outline")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


_SEV_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


@dataclass
class LintIssue:
    severity: Severity
    code: str
    path: str
    message: str
    hint: str | None = None
    kind: str | None = None  # registry kind, for REGISTRY_MISSING
    name: str | None = None  # the missing name

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "severity": self.severity.value,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.hint:
            d["hint"] = self.hint
        if self.kind:
            d["kind"] = self.kind
            d["name"] = self.name
        return d


@dataclass
class LintOptions:
    check_duration: bool = True
    duration_range: tuple[float, float] = (MIN_TOTAL_SEC, MAX_TOTAL_SEC)
    base_dir: Path | None = None  # relative file references (music, voiceover) resolve here
    check_files: bool = True
    style_override: str | None = (
        None  # `--style` on the CLI: lint against this instead of meta.style
    )


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    total_sec: float | None = None
    n_scenes: int | None = None
    source: str | None = None

    # -- views -----------------------------------------------------------------------------
    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def infos(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity is Severity.INFO]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def missing(self) -> dict[str, dict[str, list[str]]]:
        """kind -> name -> JSON paths where it is referenced.  Exactly what needs to be added."""
        out: dict[str, dict[str, list[str]]] = {}
        for i in self.issues:
            if i.code == "REGISTRY_MISSING" and i.kind and i.name:
                out.setdefault(i.kind, {}).setdefault(i.name, []).append(i.path)
        return out

    def sorted_issues(self) -> list[LintIssue]:
        return sorted(self.issues, key=lambda i: (_SEV_ORDER[i.severity], i.path, i.code))

    # -- output ----------------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "total_sec": None if self.total_sec is None else round(self.total_sec, 3),
            "n_scenes": self.n_scenes,
            "counts": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "infos": len(self.infos),
            },
            "missing": self.missing(),
            "issues": [i.to_dict() for i in self.sorted_issues()],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def format_text(self) -> str:
        lines: list[str] = []
        head = self.source or "<spec>"
        stats = []
        if self.n_scenes is not None:
            stats.append(f"{self.n_scenes} scenes")
        if self.total_sec is not None:
            stats.append(f"{self.total_sec:.2f}s total")
        lines.append(f"reel lint: {head}" + (f"  ({', '.join(stats)})" if stats else ""))
        for i in self.sorted_issues():
            tag = {"error": "ERROR", "warning": "warn ", "info": "info "}[i.severity.value]
            lines.append(f"  {tag} {i.code:<22} {i.path or '<root>'}")
            lines.append(f"        {i.message}")
            if i.hint:
                lines.append(f"        -> {i.hint}")
        missing = self.missing()
        if missing:
            lines.append("")
            lines.append("Missing from the registry (add these, or fix the spec):")
            for kind, names in sorted(missing.items()):
                for name, paths in sorted(names.items()):
                    more = f" (+{len(paths) - 1} more)" if len(paths) > 1 else ""
                    lines.append(f"  - {kind}: {name}   used at {paths[0]}{more}")
        lines.append("")
        lines.append(
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s), {len(self.infos)} note(s)"
            + ("  -> OK" if self.ok else "  -> FAILED")
        )
        return "\n".join(lines)


# ------------------------------------------------------------------------------ helpers
def jp(*parts: str | int) -> str:
    """Format a JSON path: scenes[2].layers[0].actions[1].name"""
    out = ""
    for p in parts:
        if isinstance(p, int):
            out += f"[{p}]"
        else:
            out += ("." if out else "") + p
    return out


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _as_dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


@dataclass(frozen=True)
class Ref:
    kind: str
    name: str
    path: str


def iter_refs(data: Any, *, style_override: str | None = None) -> Iterator[Ref]:
    """Every registry-bound name in a raw spec dict, tolerant of malformed documents."""
    d = _as_dict(data)
    meta = _as_dict(d.get("meta"))
    if style_override:
        yield Ref("style", style_override, "--style")
    elif isinstance(meta.get("style"), str):
        yield Ref("style", meta["style"], "meta.style")
    for ci, ch in enumerate(_as_list(d.get("characters"))):
        c = _as_dict(ch)
        if isinstance(c.get("archetype"), str):
            yield Ref("archetype", c["archetype"], jp("characters", ci, "archetype"))
        for pi, prop in enumerate(_as_list(c.get("props"))):
            if isinstance(prop, str):
                yield Ref("prop", prop, jp("characters", ci, "props", pi))
    for si, sc in enumerate(_as_list(d.get("scenes"))):
        s = _as_dict(sc)
        bg = _as_dict(s.get("background"))
        if isinstance(bg.get("template"), str):
            yield Ref("background", bg["template"], jp("scenes", si, "background", "template"))
        for mi, mv in enumerate(_as_list(_as_dict(s.get("camera")).get("moves"))):
            m = _as_dict(mv)
            if isinstance(m.get("type"), str):
                yield Ref("camera_move", m["type"], jp("scenes", si, "camera", "moves", mi, "type"))
            if isinstance(m.get("ease"), str):
                yield Ref("easing", m["ease"], jp("scenes", si, "camera", "moves", mi, "ease"))
        for li, ly in enumerate(_as_list(s.get("layers"))):
            for ai, ac in enumerate(_as_list(_as_dict(ly).get("actions"))):
                a = _as_dict(ac)
                if isinstance(a.get("name"), str):
                    yield Ref(
                        "action", a["name"], jp("scenes", si, "layers", li, "actions", ai, "name")
                    )
        for oi, ob in enumerate(_as_list(s.get("objects"))):
            o = _as_dict(ob)
            if isinstance(o.get("asset"), str):
                yield Ref("object", o["asset"], jp("scenes", si, "objects", oi, "asset"))
            for mi, mo in enumerate(_as_list(o.get("motions"))):
                m2 = _as_dict(mo)
                if isinstance(m2.get("ease"), str):
                    yield Ref(
                        "easing", m2["ease"], jp("scenes", si, "objects", oi, "motions", mi, "ease")
                    )
        for ki, cp in enumerate(_as_list(s.get("captions"))):
            c2 = _as_dict(cp)
            if isinstance(c2.get("style"), str):
                yield Ref("caption_style", c2["style"], jp("scenes", si, "captions", ki, "style"))
        for xi, sf in enumerate(_as_list(s.get("sfx"))):
            sx = _as_dict(sf)
            if isinstance(sx.get("name"), str):
                yield Ref("sfx", sx["name"], jp("scenes", si, "sfx", xi, "name"))
        tr = _as_dict(s.get("transition_out"))
        if isinstance(tr.get("type"), str):
            yield Ref("transition", tr["type"], jp("scenes", si, "transition_out", "type"))


_REMEDIATION = {
    "action": "scaffold it with `reel new-action {name}` and implement it (docs/adding-an-action.md), "
    "or run with --lenient to fall back to `idle`",
    "background": "add the place to your asset library (drop an SVG or picture in assets/places/, or run "
    "`reel assets add FILE --kind place`; docs/assets.md), or write a template module with @register_background "
    "(docs/adding-a-background.md)",
    "style": "add a folder in src/reel/styles/ with a StylePack and register it "
    "(docs/adding-a-style.md)",
    "transition": "implement it in core/transitions.py with @register_transition",
    "easing": "register it with @register_easing in core/easing.py",
    "camera_move": "register it with @register_camera_move in core/camera.py",
    "sfx": "add a synth with @register_sfx in audio/sfx.py or drop assets/sfx/{name}.wav",
    "archetype": "add the character to your asset library (`reel assets add FILE --kind character`; docs/assets.md), "
    "or register an Archetype in core/archetypes.py",
    "prop": "register a PropDef in core/archetypes.py",
    "object": "add it to your asset library (drop an SVG or PNG in assets/objects/, or run "
    "`reel assets add FILE`; docs/assets.md) or run with --lenient to leave it out",
    "caption_style": "register it with @register_caption_style in core/captions.py",
}


def _model_field_names() -> list[str]:
    from reel.core import spec as s

    names: set[str] = set()
    for obj in vars(s).values():
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            for n, f in obj.model_fields.items():
                names.add(n)
                if f.alias:
                    names.add(f.alias)
    return sorted(names)


def _path_from_loc(loc: Sequence[Any]) -> str:
    parts: list[str | int] = []
    for p in loc:
        # strip pydantic's union branch labels (list[float], str, function-after[...] ...)
        if isinstance(p, str) and (
            "[" in p or p in {"str", "int", "float", "bool", "dict", "list"}
        ):
            continue
        parts.append(p)
    return jp(*parts)


def schema_issues(exc: ValidationError) -> list[LintIssue]:
    fields = _model_field_names()
    grouped: dict[tuple[str, str], LintIssue] = {}
    for err in exc.errors():
        path = _path_from_loc(err["loc"])
        etype = err["type"]
        msg = err["msg"]
        hint = None
        code = "SCHEMA"
        if etype == "missing":
            msg = "required field is missing"
            code = "SCHEMA_MISSING"
        elif etype == "extra_forbidden":
            bad = str(err["loc"][-1])
            msg = f"unknown field {bad!r}"
            code = "SCHEMA_UNKNOWN_FIELD"
            close = difflib.get_close_matches(bad, fields, n=2, cutoff=0.6)
            if close:
                hint = "did you mean " + " or ".join(repr(c) for c in close) + "?"
        elif etype == "literal_error":
            msg = err["msg"].replace("Input should be", "must be")
        elif etype.endswith(("_type", "_parsing")):
            got = type(err.get("input")).__name__ if "input" in err else "?"
            msg = f"{msg} (got {got})"
        gkey = (path, code)
        if gkey in grouped:
            if msg not in grouped[gkey].message:
                grouped[gkey].message += f"; or {msg}"
        else:
            grouped[gkey] = LintIssue(Severity.ERROR, code, path, msg, hint)
    return list(grouped.values())


def _param_issues(
    model: type[BaseModel] | None, params: Any, path: str, what: str
) -> list[LintIssue]:
    out: list[LintIssue] = []
    if not isinstance(params, dict):
        return out
    if model is None:
        if params:
            out.append(
                LintIssue(
                    Severity.WARNING,
                    "PARAMS_UNUSED",
                    path,
                    f"{what} takes no params but got {sorted(params)}; they will be ignored",
                )
            )
        return out
    try:
        model.model_validate(params)
    except ValidationError as exc:
        for e in exc.errors():
            sub = _path_from_loc(e["loc"])
            msg = e["msg"]
            hint = None
            if e["type"] == "extra_forbidden":
                key = str(e["loc"][-1])
                msg = f"unknown param {key!r} for {what}"
                close = difflib.get_close_matches(key, list(model.model_fields), n=2, cutoff=0.5)
                allowed = ", ".join(model.model_fields) or "none"
                hint = (
                    ("did you mean " + " or ".join(repr(c) for c in close) + "? ") if close else ""
                ) + f"allowed params: {allowed}"
            elif e["type"] == "missing":
                msg = f"missing required param {str(e['loc'][-1])!r} for {what}"
            out.append(
                LintIssue(
                    Severity.ERROR,
                    "PARAMS_INVALID",
                    jp(path, sub) if sub else path,
                    msg,
                    hint,
                )
            )
    return out


# ------------------------------------------------------------------------------ the linter
def lint_file(
    path: str | Path, *, catalog: Catalog | None = None, options: LintOptions | None = None
) -> LintReport:
    p = Path(path)
    opts = options or LintOptions()
    if opts.base_dir is None:
        opts = LintOptions(**{**opts.__dict__, "base_dir": p.parent})
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        rep = LintReport(source=str(p))
        rep.issues.append(LintIssue(Severity.ERROR, "FILE", "", f"cannot read {p}: {exc}"))
        return rep
    rep = lint_text(text, catalog=catalog, options=opts)
    rep.source = str(p)
    return rep


def lint_text(
    text: str, *, catalog: Catalog | None = None, options: LintOptions | None = None
) -> LintReport:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        rep = LintReport()
        rep.issues.append(
            LintIssue(
                Severity.ERROR,
                "JSON_SYNTAX",
                "",
                f"not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}",
                "check for trailing commas, unquoted keys, or comments (JSON has none)",
            )
        )
        return rep
    return lint_data(data, catalog=catalog, options=options)


def lint_data(
    data: Any, *, catalog: Catalog | None = None, options: LintOptions | None = None
) -> LintReport:
    cat = catalog or CATALOG
    opts = options or LintOptions()
    rep = LintReport()
    if not isinstance(data, dict):
        rep.issues.append(
            LintIssue(
                Severity.ERROR, "SCHEMA", "", "the spec must be a JSON object at the top level"
            )
        )
        return rep

    _text_pass(data, rep)
    _registry_pass(data, cat, opts, rep)
    _params_pass(data, cat, rep)
    _gaps_pass(data, cat, rep)

    spec: ReelSpec | None = None
    try:
        spec = ReelSpec.model_validate(data)
    except ValidationError as exc:
        rep.issues.extend(schema_issues(exc))
    if spec is not None:
        _semantic_pass(spec, cat, opts, rep)
    return rep


# -- pass 1b: characters that cannot be drawn or spoken -------------------------------------
def _iter_strings(data: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(data, str):
        yield path, data
    elif isinstance(data, dict):
        for k, v in data.items():
            yield from _iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from _iter_strings(v, f"{path}[{i}]")


def _text_pass(data: dict[str, Any], rep: LintReport, limit: int = 8) -> None:
    """Lone surrogates / control characters are removed by the spec loader; say so rather than do it silently."""
    found = [
        (clean_text(path), why) for path, s in _iter_strings(data) if (why := bad_text_reason(s))
    ]
    for path, why in found[:limit]:
        rep.issues.append(
            LintIssue(
                Severity.WARNING,
                "BAD_TEXT",
                path,
                f"contains {why}; it is removed when the spec is loaded",
                "an emoji cut in half by a string slice is the usual cause",
            )
        )
    if len(found) > limit:
        rep.issues.append(
            LintIssue(
                Severity.WARNING,
                "BAD_TEXT",
                "",
                f"{len(found) - limit} more strings contain characters that are removed on load",
            )
        )


# -- pass 2: registry membership ------------------------------------------------------------
def _registry_pass(data: dict[str, Any], cat: Catalog, opts: LintOptions, rep: LintReport) -> None:
    for ref in iter_refs(data, style_override=opts.style_override):
        registry = cat.registry(ref.kind)
        if ref.name in registry:
            continue
        close = registry.suggest(ref.name)
        known = registry.names()
        hint_parts: list[str] = []
        if close:
            hint_parts.append("closest registered: " + ", ".join(close))
        elif known:
            hint_parts.append(
                "registered: " + ", ".join(known[:12]) + (" ..." if len(known) > 12 else "")
            )
        hint_parts.append(_REMEDIATION[ref.kind].format(name=ref.name))
        rep.issues.append(
            LintIssue(
                Severity.ERROR,
                "REGISTRY_MISSING",
                ref.path,
                f"{ref.kind.replace('_', ' ')} {ref.name!r} is not registered",
                "; ".join(hint_parts),
                kind=ref.kind,
                name=ref.name,
            )
        )


# -- pass 3b: what the asset library lacked when the spec was written ------------------------
def _gaps_pass(data: dict[str, Any], cat: Catalog, rep: LintReport) -> None:
    """``meta.library_gaps``: say what the library still lacks, and what it has now (the swap is ``reel assets fill``)."""
    from reel.assets.gaps import resolve_gap
    from reel.assets.model import slug

    for gi, g in enumerate(_as_list(_as_dict(data.get("meta")).get("library_gaps"))):
        gap = _as_dict(g)
        kind, name = gap.get("kind"), gap.get("name")
        if kind not in ("character", "object", "place") or not isinstance(name, str):
            continue
        path = jp("meta", "library_gaps", gi)
        have = resolve_gap(gap, cat)
        shown = gap.get("stand_in")
        if have is not None:
            rep.issues.append(
                LintIssue(
                    Severity.INFO,
                    "LIBRARY_GAP_FILLED",
                    path,
                    f"the library has {kind} {have.name!r} now (the script asked for {name!r})",
                    "make the spec use it: `reel assets fill SPEC.json`, or Fill library gaps in Reel Studio",
                    kind=kind,
                    name=have.name,
                )
            )
        else:
            rep.issues.append(
                LintIssue(
                    Severity.INFO,
                    "LIBRARY_GAP",
                    path,
                    f"the script wants {'a' if kind != 'object' else 'an'} {kind} {name!r} that the asset library does not have"
                    + (f"; {shown!r} stands in for it" if shown else "; it was left out"),
                    f"add it: `reel assets add FILE --kind {kind} --name {slug(name)} --tags {shlex.quote(name.lower())}`, "
                    "or Library > Add asset in Reel Studio (docs/assets.md)",
                    kind=kind,
                    name=name,
                )
            )


# -- pass 4: params -------------------------------------------------------------------------
def _params_pass(data: dict[str, Any], cat: Catalog, rep: LintReport) -> None:
    for si, sc in enumerate(_as_list(data.get("scenes"))):
        s = _as_dict(sc)
        bg = _as_dict(s.get("background"))
        if isinstance(bg.get("template"), str) and bg["template"] in cat.backgrounds:
            obj = cat.backgrounds.get(bg["template"])
            rep.issues.extend(
                _param_issues(
                    getattr(obj, "params_model", None),
                    bg.get("params"),
                    jp("scenes", si, "background", "params"),
                    f"background {bg['template']!r}",
                )
            )
        for li, ly in enumerate(_as_list(s.get("layers"))):
            for ai, ac in enumerate(_as_list(_as_dict(ly).get("actions"))):
                a = _as_dict(ac)
                nm = a.get("name")
                if isinstance(nm, str) and nm in cat.actions:
                    obj = cat.actions.get(nm)
                    rep.issues.extend(
                        _param_issues(
                            getattr(obj, "params_model", None),
                            a.get("params"),
                            jp("scenes", si, "layers", li, "actions", ai, "params"),
                            f"action {nm!r}",
                        )
                    )
        tr = _as_dict(s.get("transition_out"))
        if isinstance(tr.get("type"), str) and tr["type"] in cat.transitions:
            obj = cat.transitions.get(tr["type"])
            rep.issues.extend(
                _param_issues(
                    getattr(obj, "params_model", None),
                    tr.get("params"),
                    jp("scenes", si, "transition_out", "params"),
                    f"transition {tr['type']!r}",
                )
            )
        for mi, mv in enumerate(_as_list(_as_dict(s.get("camera")).get("moves"))):
            m = _as_dict(mv)
            nm = m.get("type")
            if isinstance(nm, str) and nm in cat.camera_moves:
                obj = cat.camera_moves.get(nm)
                check = getattr(obj, "check", None)
                if callable(check):
                    for msg in check(m):
                        rep.issues.append(
                            LintIssue(
                                Severity.ERROR,
                                "CAMERA_MOVE_INVALID",
                                jp("scenes", si, "camera", "moves", mi),
                                msg,
                            )
                        )


# -- pass 5: semantics on the typed spec ----------------------------------------------------
def _semantic_pass(spec: ReelSpec, cat: Catalog, opts: LintOptions, rep: LintReport) -> None:
    add = rep.issues.append
    E, W, I = Severity.ERROR, Severity.WARNING, Severity.INFO  # noqa: E741

    # ids ---------------------------------------------------------------------------------
    seen: dict[str, int] = {}
    for ci, ch in enumerate(spec.characters):
        if ch.id in seen:
            add(
                LintIssue(
                    E,
                    "DUPLICATE_ID",
                    jp("characters", ci, "id"),
                    f"character id {ch.id!r} already used at characters[{seen[ch.id]}]",
                )
            )
        seen.setdefault(ch.id, ci)
        _check_palette(ch, ci, rep, cat)
    char_ids = set(seen)
    seen = {}
    for si, sc in enumerate(spec.scenes):
        if sc.id in seen:
            add(
                LintIssue(
                    E,
                    "DUPLICATE_ID",
                    jp("scenes", si, "id"),
                    f"scene id {sc.id!r} already used at scenes[{seen[sc.id]}]",
                )
            )
        seen.setdefault(sc.id, si)

    # meta --------------------------------------------------------------------------------
    w, h = spec.meta.resolution
    if w <= 0 or h <= 0 or abs(w / h - 9 / 16) > 0.01:
        add(
            LintIssue(
                W,
                "ASPECT",
                "meta.resolution",
                f"resolution {w}x{h} is not 9:16; reels are 1080x1920",
                "use [1080, 1920]",
            )
        )
    if spec.meta.fps != 30:
        add(LintIssue(I, "FPS", "meta.fps", f"fps is {spec.meta.fps}; deliverable spec is 30 fps"))

    # timeline / duration -----------------------------------------------------------------
    tl = compute_timeline(spec)
    rep.total_sec = tl.total_sec
    rep.n_scenes = len(spec.scenes)
    total = tl.total_sec
    lo, hi = opts.duration_range
    if opts.check_duration:
        if total < lo - 1e-6 or total > hi + 1e-6:
            raw = sum(s.duration_sec for s in spec.scenes)
            ovl = raw - total
            need = lo - total if total < lo else hi - total
            add(
                LintIssue(
                    E,
                    "DURATION_BUDGET",
                    "scenes",
                    f"total duration is {total:.2f}s (scenes {raw:.2f}s - transition overlaps {ovl:.2f}s); "
                    f"the budget is {lo:g}-{hi:g}s",
                    f"{'add' if need > 0 else 'remove'} about {abs(need):.1f}s of scene time",
                )
            )
        elif abs(total - spec.meta.target_duration_sec) > 1.5:
            add(
                LintIssue(
                    W,
                    "DURATION_TARGET",
                    "meta.target_duration_sec",
                    f"computed duration {total:.2f}s differs from target_duration_sec "
                    f"{spec.meta.target_duration_sec:g}s by more than 1.5s",
                )
            )

    # per scene --------------------------------------------------------------------------
    used_chars: set[str] = set()
    for si, sc in enumerate(spec.scenes):
        nxt = spec.scenes[si + 1] if si + 1 < len(spec.scenes) else None
        _scene_checks(spec, sc, si, nxt, char_ids, used_chars, cat, rep)
    for ci, ch in enumerate(spec.characters):
        if ch.id not in used_chars:
            add(
                LintIssue(
                    I,
                    "UNUSED_CHARACTER",
                    jp("characters", ci),
                    f"character {ch.id!r} is never placed in a scene",
                )
            )

    # transitions budget (overlap vs neighbours) ----------------------------------------
    prev_ov = 0.0
    for si, sc in enumerate(spec.scenes):
        nxt = spec.scenes[si + 1] if si + 1 < len(spec.scenes) else None
        t = sc.transition_out
        if nxt is None:
            if t.type != "cut" and t.duration > 0:
                add(
                    LintIssue(
                        I,
                        "TRANSITION_LAST",
                        jp("scenes", si, "transition_out"),
                        "transition_out on the last scene is ignored",
                    )
                )
            continue
        ov = overlap_seconds(sc, nxt)
        if t.type != "cut" and t.duration == 0:
            add(
                LintIssue(
                    W,
                    "TRANSITION_ZERO",
                    jp("scenes", si, "transition_out", "duration"),
                    f"{t.type!r} transition has duration 0 and will behave like a cut",
                    "set a duration such as 0.5",
                )
            )
        if t.type != "cut" and ov + 1e-9 < t.duration:
            add(
                LintIssue(
                    W,
                    "TRANSITION_CLAMPED",
                    jp("scenes", si, "transition_out", "duration"),
                    f"transition {t.duration:g}s is longer than a neighbouring scene; "
                    f"it will be clamped to {ov:g}s",
                )
            )
        if prev_ov + ov > sc.duration_sec - 0.2 + 1e-9:
            add(
                LintIssue(
                    W,
                    "TRANSITION_OVERLAP",
                    jp("scenes", si, "transition_out"),
                    f"incoming ({prev_ov:g}s) and outgoing ({ov:g}s) transitions leave under "
                    f"0.2s of scene {sc.id!r} on its own; they will be clamped",
                )
            )
        prev_ov = ov

    # audio -------------------------------------------------------------------------------
    _audio_checks(spec, opts, rep)


def _sprite_roles(ch: CharacterSpec, cat: Catalog) -> list[str] | None:
    """The colour roles of a character made from library art (None for a body): what its drawing marks as recolourable."""
    if ch.archetype not in cat.archetypes:
        return None
    asset = (getattr(cat.archetypes.get(ch.archetype), "features", None) or {}).get("asset")
    if asset is None:
        return None
    from reel.assets.art import ArtError, load_art

    try:
        return sorted(load_art(asset).roles)
    except ArtError:
        return []


def _picture_notes(spec: ReelSpec, ly: Any, path: str, cat: Catalog, rep: LintReport) -> None:
    """What a character made from library art (one picture, not a jointed body) cannot do among what its layer asks."""
    ch = next((c for c in spec.characters if c.id == ly.character), None)
    if ch is None or ch.archetype not in cat.archetypes:
        return
    arch = cat.archetypes.get(ch.archetype)
    if getattr(arch, "category", "") != "sprite":
        return
    from reel.assets.sprite import sprite_notes

    for note in sprite_notes(arch, [], {a.name for a in ly.actions}):
        rep.issues.append(LintIssue(Severity.INFO, "PICTURE_LIMIT", path, f"{ch.id}: {note}"))


def _object_roles(asset: str, cat: Catalog) -> tuple[str, ...] | None:
    """The recolourable parts of a library object (``None`` when it is not a known object: another check says so)."""
    if asset not in cat.objects:
        return None
    return tuple(getattr(cat.objects.get(asset), "roles", ()) or ())


def _check_palette(ch: CharacterSpec, ci: int, rep: LintReport, cat: Catalog) -> None:
    if ch.props and _sprite_roles(ch, cat) is not None:
        rep.issues.append(
            LintIssue(
                Severity.INFO,
                "PICTURE_LIMIT",
                jp("characters", ci, "props"),
                f"{ch.id}: props are not drawn on a picture character",
            )
        )
    sprite = _sprite_roles(ch, cat)
    known = PALETTE_ROLES if sprite is None else tuple(sprite)
    for role, color in ch.palette.items():
        path = jp("characters", ci, "palette", role)
        if role not in known:
            rep.issues.append(
                LintIssue(
                    Severity.WARNING,
                    "PALETTE_ROLE",
                    path,
                    f"unknown palette role {role!r}; "
                    + (
                        f"the recolourable parts of {ch.archetype!r} are: {', '.join(known)}"
                        if sprite
                        else "this picture has no recolourable parts"
                        if sprite is not None
                        else f"known roles: {', '.join(PALETTE_ROLES)}"
                    ),
                )
            )
        if not _HEX.match(color):
            rep.issues.append(
                LintIssue(
                    Severity.ERROR,
                    "PALETTE_COLOR",
                    path,
                    f"{color!r} is not a hex colour like #e63946",
                )
            )


def _scene_checks(
    spec: ReelSpec,
    sc: SceneSpec,
    si: int,
    nxt: SceneSpec | None,
    char_ids: set[str],
    used_chars: set[str],
    cat: Catalog,
    rep: LintReport,
) -> None:
    add = rep.issues.append
    E, W, _I = Severity.ERROR, Severity.WARNING, Severity.INFO
    dur = sc.duration_sec
    base = jp("scenes", si)

    slots = set(UNIVERSAL_SLOTS)
    if sc.background.template in cat.backgrounds:
        provided = getattr(cat.backgrounds.get(sc.background.template), "slot_names", None)
        if callable(provided):
            slots |= set(provided(sc.background.params))
        elif provided:
            slots |= set(provided)

    if not sc.layers and not sc.captions:
        add(LintIssue(W, "EMPTY_SCENE", base, f"scene {sc.id!r} has no characters and no captions"))

    # camera moves
    for mi, mv in enumerate(sc.camera.moves):
        p = jp("scenes", si, "camera", "moves", mi)
        if mv.t1 <= mv.t0:
            add(
                LintIssue(
                    E, "TIME_RANGE", p, f"camera move t1 ({mv.t1:g}) must be after t0 ({mv.t0:g})"
                )
            )
        elif mv.t1 > dur + 1e-6:
            add(
                LintIssue(
                    W,
                    "TIME_OVERFLOW",
                    p,
                    f"camera move ends at {mv.t1:g}s, after the scene ({dur:g}s); it will be cut off",
                )
            )

    # objects from the asset library
    from reel.assets.objects import motion_problems

    if len(sc.objects) > 14:
        add(
            LintIssue(
                W,
                "OBJECT_CLUTTER",
                jp("scenes", si, "objects"),
                f"scene {sc.id!r} has {len(sc.objects)} objects; more than 14 makes the picture busy and slow to draw",
            )
        )
    for oi, ob in enumerate(sc.objects):
        op = jp("scenes", si, "objects", oi)
        if isinstance(ob.position, str) and ob.position not in slots:
            add(
                LintIssue(
                    E,
                    "POSITION_SLOT",
                    jp(op, "position"),
                    f"position slot {ob.position!r} does not exist in background {sc.background.template!r}",
                    "available: " + ", ".join(sorted(slots)),
                )
            )
        elif isinstance(ob.position, list) and not (
            -1.0 <= ob.position[0] <= 2.0 and -0.2 <= ob.position[1] <= 1.4
        ):
            add(
                LintIssue(
                    W,
                    "POSITION_RANGE",
                    jp(op, "position"),
                    f"position {ob.position} is far outside the frame (x in [0,1], y in [0,1])",
                )
            )
        if ob.t1 is not None and ob.t1 <= ob.t0:
            add(
                LintIssue(
                    E, "TIME_RANGE", op, f"object t1 ({ob.t1:g}) must be after t0 ({ob.t0:g})"
                )
            )
        elif ob.t0 >= dur - 1e-6:
            add(
                LintIssue(
                    W,
                    "TIME_OVERFLOW",
                    op,
                    f"object appears at {ob.t0:g}s, at/after the scene end ({dur:g}s); it is never seen",
                )
            )
        if ob.palette:
            parts = _object_roles(ob.asset, cat)
            for role, color in ob.palette.items():
                pp = jp(op, "palette", role)
                if parts is not None and role not in parts:
                    add(
                        LintIssue(
                            W,
                            "PALETTE_ROLE",
                            pp,
                            f"unknown palette role {role!r}; "
                            + (
                                f"the recolourable parts of {ob.asset!r} are: {', '.join(parts)}"
                                if parts
                                else f"{ob.asset!r} has no recolourable parts"
                            ),
                        )
                    )
                if not _HEX.match(color):
                    add(
                        LintIssue(
                            E,
                            "PALETTE_COLOR",
                            pp,
                            f"{color!r} is not a hex colour like #e63946",
                        )
                    )
        for mi, mo in enumerate(ob.motions):
            mp = jp(op, "motions", mi)
            for code, msg in motion_problems(mo):
                add(LintIssue(E, code, mp, msg))
            if isinstance(mo.to, str) and mo.type == "move" and mo.to not in slots:
                add(
                    LintIssue(
                        E,
                        "POSITION_SLOT",
                        jp(mp, "to"),
                        f"position slot {mo.to!r} does not exist in background {sc.background.template!r}",
                        "available: " + ", ".join(sorted(slots)),
                    )
                )
            if mo.t1 > dur + 1e-6 and mo.t1 > mo.t0:
                add(
                    LintIssue(
                        W,
                        "TIME_OVERFLOW",
                        mp,
                        f"motion ends at {mo.t1:g}s, after the scene ({dur:g}s); it will be cut off",
                    )
                )

    # layers
    for li, ly in enumerate(sc.layers):
        lp = jp("scenes", si, "layers", li)
        if ly.character not in char_ids:
            add(
                LintIssue(
                    E,
                    "CHARACTER_UNDEFINED",
                    jp(lp, "character"),
                    f"character {ly.character!r} is not defined in `characters`",
                    "defined: " + (", ".join(sorted(char_ids)) or "(none)"),
                )
            )
        else:
            used_chars.add(ly.character)
            _picture_notes(spec, ly, lp, cat, rep)
        if isinstance(ly.position, str) and ly.position not in slots:
            add(
                LintIssue(
                    E,
                    "POSITION_SLOT",
                    jp(lp, "position"),
                    f"position slot {ly.position!r} does not exist in background {sc.background.template!r}",
                    "available: " + ", ".join(sorted(slots)),
                )
            )
        elif isinstance(ly.position, list) and not (
            -1.0 <= ly.position[0] <= 2.0 and -0.2 <= ly.position[1] <= 1.4
        ):
            add(
                LintIssue(
                    W,
                    "POSITION_RANGE",
                    jp(lp, "position"),
                    f"position {ly.position} is far outside the frame (x in [0,1], y in [0,1])",
                )
            )
        root_windows: list[tuple[float, float, int]] = []
        for ai, ac in enumerate(ly.actions):
            ap = jp(lp, "actions", ai)
            if ac.t1 <= ac.t0:
                add(
                    LintIssue(
                        E, "TIME_RANGE", ap, f"action t1 ({ac.t1:g}) must be after t0 ({ac.t0:g})"
                    )
                )
                continue
            if ac.t0 >= dur - 1e-6:
                add(
                    LintIssue(
                        W,
                        "TIME_OVERFLOW",
                        ap,
                        f"action starts at {ac.t0:g}s, at/after the scene end ({dur:g}s); it never plays",
                    )
                )
            elif ac.t1 > dur + 1e-6:
                add(
                    LintIssue(
                        W,
                        "TIME_OVERFLOW",
                        ap,
                        f"action ends at {ac.t1:g}s, after the scene ({dur:g}s); it will be cut off",
                    )
                )
            if ac.name in cat.actions:
                adef = cat.actions.get(ac.name)
                if getattr(adef, "moves_root", False):
                    root_windows.append((ac.t0, ac.t1, ai))
                mind = getattr(adef, "min_duration", 0.0)
                if mind and ac.t1 - ac.t0 < mind - 1e-6:
                    add(
                        LintIssue(
                            W,
                            "ACTION_TOO_SHORT",
                            ap,
                            f"action {ac.name!r} lasts {ac.t1 - ac.t0:.2f}s; it needs at least {mind:g}s to read",
                        )
                    )
                check = getattr(adef, "check", None)
                if callable(check) and ly.character in char_ids:
                    chr_ = spec.character(ly.character)
                    for msg in check(ac.params, chr_):
                        add(LintIssue(W, "ACTION_CHECK", ap, msg))
        root_windows.sort()
        for (a0, a1, ai), (b0, _b1, bi) in itertools.pairwise(root_windows):
            if b0 < a1 - 1e-6:
                add(
                    LintIssue(
                        W,
                        "ACTION_OVERLAP",
                        jp(lp, "actions", bi),
                        f"moves the character while actions[{ai}] is still moving it ({a0:g}-{a1:g}s); "
                        "the later one wins",
                    )
                )

    # captions
    caps = sorted(enumerate(sc.captions), key=lambda ic: ic[1].t0)
    for ki, cp in caps:
        kp = jp("scenes", si, "captions", ki)
        if cp.t1 <= cp.t0:
            add(
                LintIssue(
                    E, "TIME_RANGE", kp, f"caption t1 ({cp.t1:g}) must be after t0 ({cp.t0:g})"
                )
            )
        elif cp.t0 >= dur - 1e-6:
            add(
                LintIssue(
                    W,
                    "TIME_OVERFLOW",
                    kp,
                    f"caption starts at {cp.t0:g}s, after the scene ({dur:g}s)",
                )
            )
        elif cp.t1 > dur + 1e-6:
            add(
                LintIssue(
                    W,
                    "TIME_OVERFLOW",
                    kp,
                    f"caption ends at {cp.t1:g}s, after the scene ({dur:g}s); it will be cut off",
                )
            )
        if cp.speaker and cp.speaker not in char_ids:
            add(
                LintIssue(
                    E,
                    "CHARACTER_UNDEFINED",
                    jp(kp, "speaker"),
                    f"speaker {cp.speaker!r} is not a defined character",
                )
            )
        if not cp.text.strip():
            add(LintIssue(W, "CAPTION_EMPTY", kp, "caption text is empty"))
        elif len(cp.text) > 140:
            add(
                LintIssue(
                    W,
                    "CAPTION_LONG",
                    kp,
                    f"caption is {len(cp.text)} characters; reels read best under ~90",
                    "split it into two captions",
                )
            )
        elif cp.t1 > cp.t0:
            wps = len(cp.text.split()) / (cp.t1 - cp.t0)
            if wps > 5.0:
                add(
                    LintIssue(
                        W,
                        "CAPTION_FAST",
                        kp,
                        f"{wps:.1f} words/second is too fast to read; show it longer",
                    )
                )
    for (ia, a), (ib, b) in itertools.pairwise(caps):
        if b.t0 < a.t1 - 1e-6 and a.style == b.style:
            add(
                LintIssue(
                    W,
                    "CAPTION_OVERLAP",
                    jp("scenes", si, "captions", ib),
                    f"overlaps captions[{ia}] ({a.t0:g}-{a.t1:g}s) in the same style",
                )
            )

    # sfx
    for xi, sf in enumerate(sc.sfx):
        if sf.t > dur + 1e-6:
            add(
                LintIssue(
                    W,
                    "TIME_OVERFLOW",
                    jp("scenes", si, "sfx", xi),
                    f"sfx at {sf.t:g}s is after the scene ({dur:g}s); it will not play",
                )
            )


def _audio_checks(spec: ReelSpec, opts: LintOptions, rep: LintReport) -> None:
    a = spec.audio
    add = rep.issues.append
    base = opts.base_dir or Path.cwd()

    def exists(p: str) -> bool:
        pp = Path(p).expanduser()
        return (pp if pp.is_absolute() else base / pp).exists()

    music = a.music or ""
    if music.partition(":")[0].strip().lower() == "procedural":
        from reel.audio.music import MUSIC_MOODS

        mood = music.partition(":")[2].strip().lower()
        if mood and mood not in MUSIC_MOODS:
            close = difflib.get_close_matches(mood, MUSIC_MOODS, n=1)
            add(
                LintIssue(
                    Severity.ERROR,
                    "AUDIO_CONFIG",
                    "audio.music",
                    f"unknown music mood {mood!r}",
                    (f"did you mean {close[0]!r}? " if close else "")
                    + f"moods: {', '.join(MUSIC_MOODS)}",
                )
            )
    elif music and opts.check_files and not exists(music):
        add(
            LintIssue(
                Severity.ERROR,
                "FILE_MISSING",
                "audio.music",
                f"music file {music!r} does not exist",
                "use a real path, 'procedural' / 'procedural:<mood>', or null",
            )
        )
    if a.voiceover == "file":
        if not a.voiceover_file:
            add(
                LintIssue(
                    Severity.ERROR,
                    "AUDIO_CONFIG",
                    "audio.voiceover_file",
                    "voiceover is 'file' but voiceover_file is not set",
                )
            )
        elif opts.check_files and not exists(a.voiceover_file):
            add(
                LintIssue(
                    Severity.ERROR,
                    "FILE_MISSING",
                    "audio.voiceover_file",
                    f"voiceover file {a.voiceover_file!r} does not exist",
                )
            )
    if a.voiceover == "tts":
        any_speakable = any(
            (c.speak if c.speak is not None else c.style == "subtitle")
            for s in spec.scenes
            for c in s.captions
        )
        if not any_speakable:
            add(
                LintIssue(
                    Severity.WARNING,
                    "AUDIO_CONFIG",
                    "audio.voiceover",
                    "voiceover is 'tts' but no caption is marked to be spoken",
                )
            )
