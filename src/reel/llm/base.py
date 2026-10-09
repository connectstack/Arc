"""Shared plumbing of the script -> scene-spec stage.

Three interchangeable ways to get a spec all implement :class:`SpecGenerator`:

* :class:`reel.llm.generator.LLMSpecGenerator`   - ask an LLM (any :class:`reel.llm.client.LLMClient`)
* :class:`reel.llm.heuristic.HeuristicSpecGenerator` - rule-based, fully offline, no model at all
* :class:`ManualSpecGenerator`                    - "write the JSON yourself": load, lint, return

Every generator returns a :class:`GenerationResult` whose ``lint`` report has *no errors* (or it
raises :class:`SpecGenerationError` carrying the last report), so whatever comes out of this package
can be handed straight to the renderer.

The house rules the prompt states and the normaliser/heuristic enforce live here once, so the three
cannot drift apart.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from reel.core.catalog import Catalog
from reel.core.lint import LintIssue, LintOptions, LintReport, Severity, lint_data, lint_text
from reel.core.spec import MAX_TOTAL_SEC, MIN_TOTAL_SEC, SPEC_VERSION

# --------------------------------------------------------------------------- house rules
#: scene count / scene length the prompt asks for and the heuristic produces
MIN_SCENES, MAX_SCENES = 8, 14
MIN_SCENE_SEC, MAX_SCENE_SEC = 3.0, 8.0
#: hard limits of the spec model for a single scene (``SceneSpec.duration_sec``) and the normaliser
HARD_MIN_SCENE_SEC, HARD_MAX_SCENE_SEC = 0.5, 30.0
#: where a character's feet go: the bottom quarter of the frame belongs to the captions
FEET_Y_MIN, FEET_Y_MAX = 0.62, 0.76
X_MIN, X_MAX = 0.15, 0.85
MAX_ON_SCREEN = 3
SFX_PER_SCENE = (1, 3)
#: reading speed used to size captions and scenes (words per second, comfortable) and the lint limit
READ_WPS = 2.6
MAX_CAPTION_WPS = 5.0

# --------------------------------------------------------------------------- small context windows
#: a client whose context window (tokens) is below this gets the compact prompt automatically
COMPACT_BELOW_TOKENS = 16_000
#: what the compact prompt asks for: fewer, shorter, simpler scenes
COMPACT_MIN_SCENES, COMPACT_MAX_SCENES = 8, 10
COMPACT_MIN_SCENE_SEC, COMPACT_MAX_SCENE_SEC = 4.0, 7.0
COMPACT_MAX_ON_SCREEN = 2
#: characters per token for JSON and catalogue text (measured: gemma2 splits our prompt at ~3.3);
#: deliberately pessimistic so a reply budget derived from it never overshoots the window
CHARS_PER_TOKEN = 3.2
#: smallest reply budget worth asking for
MIN_REPLY_TOKENS = 768


def estimate_tokens(text: str) -> int:
    """A pessimistic token count (see :data:`CHARS_PER_TOKEN`) - for budgeting, not billing."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


#: (shirt, pants, accent) colour sets handed to characters, chosen to stay apart from each other
CHARACTER_PALETTES: tuple[tuple[str, str, str], ...] = (
    ("#e63946", "#264653", "#f4a261"),
    ("#2a9d8f", "#3d405b", "#e9c46a"),
    ("#f4a261", "#355070", "#e56b6f"),
    ("#6a4c93", "#1d3557", "#ffca3a"),
    ("#1d70b8", "#4a4e69", "#ff595e"),
    ("#8ac926", "#3a3a5c", "#ff924c"),
)
SKIN_TONES: tuple[str, ...] = ("#f0c29a", "#c68642", "#8d5524", "#ffdbac", "#e0ac69")

#: the audio block every generated spec gets unless the caller says otherwise.  Models never write
#: it (a made-up music path would fail the linter); callers pass ``audio=`` to change it.
DEFAULT_AUDIO: dict[str, Any] = {"music": "procedural", "voiceover": "tts", "ducking": True}


def default_audio() -> dict[str, Any]:
    return deepcopy(DEFAULT_AUDIO)


def clamp_target(target_duration: float) -> float:
    """Keep a requested length inside the 45-60 s budget (``meta.target_duration_sec``'s range)."""
    return float(min(MAX_TOTAL_SEC, max(MIN_TOTAL_SEC, float(target_duration))))


def fmt_num(x: float) -> str:
    """Compact number for prompts: ``50.0 -> '50'``, ``2.5 -> '2.5'``."""
    return f"{x:g}"


# --------------------------------------------------------------------------- results & errors
@dataclass
class GenerationResult:
    """A lint-clean spec plus how it came to be.

    ``attempts`` counts model calls (1 for the heuristic and manual paths); ``notes`` are short
    human-readable remarks (deterministic fixes applied, fallbacks taken) the CLI may print.
    """

    spec: dict[str, Any]
    lint: LintReport
    attempts: int = 1
    notes: list[str] = field(default_factory=list)
    generator: str = ""

    @property
    def ok(self) -> bool:
        return self.lint.ok

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.spec, indent=indent, ensure_ascii=False)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json() + "\n", encoding="utf-8")
        return p


class SpecGenerationError(RuntimeError):
    """No valid spec could be produced.

    Carries what is needed to explain why: the last lint report (``lint``), the last raw model
    reply (``raw``), the last normalised spec (``spec``) and the number of model calls made.
    ``str(error)`` is a readable multi-line report, ready to print.
    """

    def __init__(
        self,
        message: str,
        *,
        lint: LintReport | None = None,
        raw: str | None = None,
        spec: dict[str, Any] | None = None,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.lint = lint
        self.raw = raw
        self.spec = spec
        self.attempts = attempts

    def report_text(self) -> str:
        return self.lint.format_text() if self.lint is not None else ""

    def __str__(self) -> str:
        report = self.report_text()
        return f"{self.message}\n\n{report}" if report else self.message


# --------------------------------------------------------------------------- the interface
class SpecGenerator(ABC):
    """script text -> a spec dict that passes the linter."""

    #: short label recorded in :attr:`GenerationResult.generator`
    label: str = "generator"

    @abstractmethod
    def generate(
        self,
        script: str,
        *,
        style: str,
        seed: int = 0,
        target_duration: float = 50.0,
    ) -> GenerationResult:
        """Return a lint-clean spec, or raise :class:`SpecGenerationError`."""


def synthetic_report(code: str, message: str, hint: str | None = None) -> LintReport:
    """A one-error lint report for failures that happen before there is a spec to lint."""
    rep = LintReport()
    rep.issues.append(LintIssue(Severity.ERROR, code, "", message, hint))
    return rep


class ManualSpecGenerator(SpecGenerator):
    """The "write the JSON yourself" path: load a spec, optionally pin style/seed, lint it.

    ``source`` is a path to a ``.json`` file or an already-parsed dict.  Nothing is rewritten
    except the optional ``meta.style`` / ``meta.seed`` overrides; if the spec has lint errors a
    :class:`SpecGenerationError` with the full readable report is raised, so a hand-written file
    gets exactly the feedback ``reel lint`` would give.
    """

    label = "manual"

    def __init__(
        self,
        source: str | Path | Mapping[str, Any],
        *,
        style: str | None = None,
        seed: int | None = None,
        catalog: Catalog | None = None,
        lint_options: LintOptions | None = None,
    ) -> None:
        self.source = source if isinstance(source, Mapping) else Path(source)
        self.style = style
        self.seed = seed
        self.catalog = catalog
        self.lint_options = lint_options

    # `script` is accepted (and ignored) so callers can treat all generators alike; `style`/`seed`
    # are optional here: when given they override meta.style / meta.seed of the loaded spec
    def generate(
        self,
        script: str = "",
        *,
        style: str | None = None,
        seed: int | None = None,
        target_duration: float = 50.0,
    ) -> GenerationResult:
        notes: list[str] = []
        base_dir: Path | None = None
        if isinstance(self.source, Path):
            path = self.source
            base_dir = path.parent
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise SpecGenerationError(
                    f"cannot read the spec file {path}: {exc}",
                    lint=synthetic_report("FILE", f"cannot read {path}: {exc}"),
                ) from exc
            try:
                data: Any = json.loads(text)
            except json.JSONDecodeError:
                rep = lint_text(text, catalog=self.catalog)
                rep.source = str(path)
                raise SpecGenerationError(
                    f"{path} is not valid JSON; fix the syntax and try again", lint=rep
                ) from None
            origin = str(path)
        else:
            data = deepcopy(dict(self.source))
            origin = "<dict>"

        if not isinstance(data, dict):
            rep = lint_data(data, catalog=self.catalog)
            rep.source = origin
            raise SpecGenerationError("the spec must be a JSON object", lint=rep)

        want_style = style if style is not None else self.style
        want_seed = seed if seed is not None else self.seed
        meta = data.get("meta")
        if isinstance(meta, dict):  # a spec without `meta` is reported by the linter, not patched
            if want_style is not None:
                if meta.get("style") != want_style:
                    notes.append(f"meta.style set to {want_style!r} (was {meta.get('style')!r})")
                meta["style"] = want_style
            if want_seed is not None:
                if meta.get("seed") != want_seed:
                    notes.append(f"meta.seed set to {want_seed} (was {meta.get('seed')!r})")
                meta["seed"] = want_seed

        options = self.lint_options or LintOptions()
        if options.base_dir is None and base_dir is not None:
            options = LintOptions(**{**options.__dict__, "base_dir": base_dir})
        rep = lint_data(data, catalog=self.catalog, options=options)
        rep.source = origin
        if not rep.ok:
            n = len(rep.errors)
            raise SpecGenerationError(
                f"{origin} has {n} error{'s' if n != 1 else ''}; fix them and try again",
                lint=rep,
                spec=data,
            )
        return GenerationResult(data, rep, attempts=1, notes=notes, generator=self.label)


# --------------------------------------------------------------------------- catalog helpers
def enum_values(model: type[BaseModel] | None, param: str) -> list[str] | None:
    """Allowed values of ``param`` if it exists on ``model`` and is an enum/Literal, else None."""
    if model is None:
        return None
    try:
        props = model.model_json_schema().get("properties", {})
    except Exception:  # a params model that cannot even describe itself is simply "unknown"
        return None
    p = props.get(param)
    if not isinstance(p, dict):
        return None
    if isinstance(p.get("enum"), list):
        return [str(v) for v in p["enum"]]
    for alt in p.get("anyOf", []) or []:
        if isinstance(alt, dict) and isinstance(alt.get("enum"), list):
            return [str(v) for v in alt["enum"]]
    return None


def has_param(model: type[BaseModel] | None, param: str) -> bool:
    return model is not None and param in model.model_fields


def slot_positions(catalog: Catalog, template: str) -> dict[str, tuple[float, float] | None]:
    """Named slots of a background: ``{name: (x, y)}`` (``None`` when only names are known)."""
    if template not in catalog.backgrounds:
        return {}
    obj = catalog.backgrounds.get(template)
    out: dict[str, tuple[float, float] | None] = {}
    table = getattr(obj, "slots", None)
    if isinstance(table, Mapping):
        for name, pos in table.items():
            try:
                out[str(name)] = (float(pos[0]), float(pos[1]))
            except (TypeError, ValueError, IndexError, KeyError):
                out[str(name)] = None
        return out
    names: Iterable[str] = ()
    provided = getattr(obj, "slot_names", None)
    if callable(provided):
        try:
            names = provided({})
        except Exception:
            names = ()
    elif provided:
        names = provided
    for name in names:
        out[str(name)] = None
    return out


#: archetype -> {word: weight}; matched next to a character's name (see ``_describe``)
ARCHETYPE_WORDS: dict[str, dict[str, int]] = {
    "robot": {"robot": 4, "android": 4, "droid": 4, "bot": 3, "cyborg": 3, "machine": 2, "ai": 2},
    "elder": {
        "grandpa": 4, "grandma": 4, "granny": 4, "grandfather": 4, "grandmother": 4, "nana": 4,
        "elder": 3, "elderly": 3, "senior": 2, "retired": 2, "old": 1, "wise": 1,
    },
    "boss": {
        "boss": 4, "manager": 3, "ceo": 3, "chief": 3, "executive": 3, "supervisor": 3,
        "director": 2, "principal": 2, "mayor": 2,
    },
    "hero": {
        "hero": 4, "superhero": 4, "knight": 3, "warrior": 3, "champion": 3, "adventurer": 3,
        "captain": 2, "explorer": 2, "brave": 1,
    },
    "kid": {
        "kid": 3, "boy": 3, "girl": 3, "child": 3, "toddler": 3, "baby": 3, "student": 2,
        "pupil": 2, "schoolgirl": 3, "schoolboy": 3, "little": 1, "young": 1, "tiny": 1,
    },
}  # fmt: skip
ARCHETYPE_PRIORITY = ("robot", "elder", "boss", "hero", "kid")


def guess_archetype(words: list[str], available: set[str]) -> str:
    scores: Counter[str] = Counter()
    for arch, table in ARCHETYPE_WORDS.items():
        for w in words:
            scores[arch] += table.get(w, 0)
    best, top = "everyman", 0
    for arch in ARCHETYPE_PRIORITY:
        if scores[arch] > top:
            best, top = arch, scores[arch]
    if best in available:
        return best
    if "everyman" in available:
        return "everyman"
    return sorted(available)[0] if available else best


def meta_defaults(title: str, style: str, seed: int, target: float) -> dict[str, Any]:
    return {
        "title": title,
        "style": style,
        "fps": 30,
        "resolution": [1080, 1920],
        "seed": int(seed),
        "target_duration_sec": clamp_target(target),
        "aspect": "9:16",
    }


__all__ = [
    "ARCHETYPE_PRIORITY",
    "ARCHETYPE_WORDS",
    "CHARACTER_PALETTES",
    "CHARS_PER_TOKEN",
    "COMPACT_BELOW_TOKENS",
    "COMPACT_MAX_ON_SCREEN",
    "COMPACT_MAX_SCENES",
    "COMPACT_MAX_SCENE_SEC",
    "COMPACT_MIN_SCENES",
    "COMPACT_MIN_SCENE_SEC",
    "DEFAULT_AUDIO",
    "FEET_Y_MAX",
    "FEET_Y_MIN",
    "HARD_MAX_SCENE_SEC",
    "HARD_MIN_SCENE_SEC",
    "MAX_CAPTION_WPS",
    "MAX_ON_SCREEN",
    "MAX_SCENES",
    "MAX_SCENE_SEC",
    "MIN_REPLY_TOKENS",
    "MIN_SCENES",
    "MIN_SCENE_SEC",
    "READ_WPS",
    "SFX_PER_SCENE",
    "SKIN_TONES",
    "SPEC_VERSION",
    "X_MAX",
    "X_MIN",
    "GenerationResult",
    "ManualSpecGenerator",
    "SpecGenerationError",
    "SpecGenerator",
    "clamp_target",
    "default_audio",
    "enum_values",
    "estimate_tokens",
    "fmt_num",
    "guess_archetype",
    "has_param",
    "meta_defaults",
    "slot_positions",
    "synthetic_report",
]
