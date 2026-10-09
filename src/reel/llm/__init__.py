"""Script -> scene spec.

Three interchangeable ways to turn a script into the JSON the renderer consumes; all return a
lint-clean :class:`GenerationResult` (or raise :class:`SpecGenerationError`):

* :class:`LLMSpecGenerator`       - ask a model (Ollama / OpenAI-compatible / Anthropic, or a scripted
  :class:`ReplayClient` in tests); the prompt is built from the live catalog, the reply is
  normalised (45-60 s budget, time windows, ids) and linted, and the lint report drives corrections;
  what a weak model left empty (gestures, camera, sfx, scene changes) is then filled by
  :func:`enrich_spec` without replacing anything it wrote
* :class:`HeuristicSpecGenerator` - a rule-based planner that needs no model at all
* :class:`ManualSpecGenerator`    - you write the JSON; it is loaded, pinned to a style/seed and linted

:func:`generate_spec` is the front door: an LLM when a client is given, the heuristic otherwise.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from reel.core.catalog import Catalog
from reel.llm.base import (
    DEFAULT_AUDIO,
    GenerationResult,
    ManualSpecGenerator,
    SpecGenerationError,
    SpecGenerator,
)
from reel.llm.client import (
    AnthropicClient,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMResponseError,
    LLMUnavailable,
    Message,
    OllamaClient,
    OpenAICompatClient,
    ReplayClient,
    Truncated,
    get_client,
)
from reel.llm.enrich import enrich_spec
from reel.llm.generator import (
    JSONExtractionError,
    LLMSpecGenerator,
    extract_json,
    normalize_spec,
    salvage_json,
)
from reel.llm.heuristic import HeuristicSpecGenerator
from reel.llm.prompt import (
    build_system_prompt,
    build_user_message,
    json_schema_for_llm,
    render_prompt_preview,
)


def generate_spec(
    script: str,
    style: str,
    client: LLMClient | str | None = None,
    seed: int = 0,
    target_duration: float = 50,
    *,
    max_repairs: int = 3,
    audio: Mapping[str, Any] | None = None,
    catalog: Catalog | None = None,
    fallback: bool = False,
    compact: bool | None = None,
    enrich: bool = True,
    verbatim: bool = True,
) -> GenerationResult:
    """Script text -> a lint-clean spec dict (``result.spec``).

    With ``client`` (an :class:`LLMClient`, or a ``provider:model`` string for :func:`get_client`)
    the model writes the spec and the linter drives up to ``max_repairs`` corrections; without one
    the offline :class:`HeuristicSpecGenerator` plans it.  With ``fallback=True`` a model that cannot
    be reached or configured (:class:`LLMError`) degrades to the offline planner, recorded in
    ``result.notes``; otherwise the error propagates so the caller can suggest the manual JSON path.
    ``audio`` replaces the default audio block (``{}`` = silent).  ``compact`` picks the short prompt
    for small-context models (``None`` = automatic, from the client's reported context window).
    ``enrich`` (model output only; the offline planner already stages everything) lets
    :func:`enrich_spec` fill what the model left empty - gestures, camera moves, sound effects,
    scene changes, caption windows - after the spec has been accepted; ``enrich=False`` returns the
    model's spec exactly as normalised.  ``verbatim`` (model output only; the offline planner always copies the
    script) makes the captions the script's own words, in order: whatever the model reworded, dropped or invented is
    put right (:class:`reel.llm.fidelity.ScriptLock`); ``verbatim=False`` lets the model condense and reword.
    """
    if client is None:
        return HeuristicSpecGenerator(catalog=catalog, audio=_audio(audio)).generate(
            script, style=style, seed=seed, target_duration=target_duration
        )
    try:
        llm = get_client(client) if isinstance(client, str) else client
        return LLMSpecGenerator(
            llm,
            max_repairs=max_repairs,
            catalog=catalog,
            audio=_audio(audio),
            compact=compact,
            enrich=enrich,
            verbatim=verbatim,
        ).generate(script, style=style, seed=seed, target_duration=target_duration)
    except LLMError as exc:
        if not fallback:
            raise
        result = HeuristicSpecGenerator(catalog=catalog, audio=_audio(audio)).generate(
            script, style=style, seed=seed, target_duration=target_duration
        )
        result.notes.insert(
            0, f"the language model was not usable ({exc}); used the offline planner"
        )
        return result


def _audio(audio: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return None if audio is None else dict(audio)


__all__ = [
    "DEFAULT_AUDIO",
    "AnthropicClient",
    "GenerationResult",
    "HeuristicSpecGenerator",
    "JSONExtractionError",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMResponseError",
    "LLMSpecGenerator",
    "LLMUnavailable",
    "ManualSpecGenerator",
    "Message",
    "OllamaClient",
    "OpenAICompatClient",
    "ReplayClient",
    "SpecGenerationError",
    "SpecGenerator",
    "Truncated",
    "build_system_prompt",
    "build_user_message",
    "enrich_spec",
    "extract_json",
    "generate_spec",
    "get_client",
    "json_schema_for_llm",
    "normalize_spec",
    "render_prompt_preview",
    "salvage_json",
]
