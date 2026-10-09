"""Swappable LLM clients.

One tiny abstraction, ``LLMClient.complete(system, messages, ...) -> str`` (the model's JSON
text), and three HTTP implementations built on :mod:`httpx`:

* :class:`OllamaClient`        - a local Ollama server (``POST /api/chat``, ``format`` = JSON schema)
* :class:`OpenAICompatClient`  - OpenAI and anything that speaks ``/chat/completions`` (LM Studio,
  vLLM, llama.cpp server, ...); asks for ``json_schema`` output and degrades to ``json_object`` /
  plain text if the server rejects it
* :class:`AnthropicClient`     - the Messages API, forcing a single ``emit_spec`` tool whose
  ``input_schema`` is the spec schema

All three take an injectable ``transport`` (:class:`httpx.BaseTransport`) so tests run against
``httpx.MockTransport`` and never touch the network, and :class:`ReplayClient` is a scripted test
double.  Network trouble becomes :class:`LLMUnavailable` with a message that says what to try
(``ollama serve``, check the key, ...) so a CLI can fall back to the manual-JSON path.  API keys
are only ever put in request headers; they are scrubbed from every error message and ``repr``.

Use :func:`get_client` to build a client from a string like ``ollama:llama3.1``.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_TIMEOUT = 300.0
CONNECT_TIMEOUT = 10.0
#: statuses worth retrying (rate limit, transient server trouble, Anthropic's "overloaded")
RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 529})

OLLAMA_URL = "http://localhost:11434"
OPENAI_URL = "https://api.openai.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
EMIT_TOOL = "emit_spec"


# --------------------------------------------------------------------------- errors
class LLMError(RuntimeError):
    """Base class of everything that goes wrong while talking to a model."""


class LLMUnavailable(LLMError):
    """The model cannot be used right now: server down, bad key, unknown model, rate limit...

    ``str(error)`` says what happened and what to try.  ``status_code`` is the HTTP status when
    there was one.  Catch this (or :class:`LLMError`) to fall back to the manual JSON path.
    """

    def __init__(self, message: str, *, provider: str = "", status_code: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class LLMConfigError(LLMError, ValueError):
    """The client could not even be configured: unknown provider, missing API key, bad spec."""


class LLMResponseError(LLMError):
    """The server answered but the answer is unusable (empty, refused, not the expected shape)."""


# --------------------------------------------------------------------------- messages
@dataclass(frozen=True)
class Message:
    """One chat turn.  The system prompt is passed separately to :meth:`LLMClient.complete`."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ValueError(f"Message.role must be 'user' or 'assistant', got {self.role!r}")


class LLMClient(ABC):
    """``complete`` returns the model's reply text, which should be one JSON document."""

    #: short label for logs and notes, e.g. ``ollama:llama3.1``
    name: str = "llm"
    #: True when the last reply was cut off by the token limit (the JSON is probably incomplete)
    last_truncated: bool = False
    #: tokens the model can hold in total (prompt + reply) when that is known and small enough to
    #: matter; ``None`` means "large or unknown".  Generators use it to pick the compact prompt.
    context_window: int | None = None

    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.4,
        seed: int | None = None,
        max_tokens: int = 8192,
    ) -> str:
        """Run one chat completion and return the reply text (JSON when ``json_schema`` is given)."""


# --------------------------------------------------------------------------- test double
@dataclass
class RecordedRequest:
    """What a :class:`ReplayClient` was asked (messages are copied, so later mutation is safe)."""

    system: str
    messages: list[Message]
    json_schema: dict[str, Any] | None
    temperature: float
    seed: int | None
    max_tokens: int


class Truncated(str):
    """A scripted reply that the (pretend) model was cut off in the middle of
    (``ReplayClient`` sets ``last_truncated`` for it, like a real client does on ``length``)."""


class ReplayClient(LLMClient):
    """Returns scripted replies in order and records every request it receives.

    ``replies`` may hold strings (returned as is), :class:`Truncated` strings (returned with
    ``last_truncated`` set) or dicts (returned as JSON text).  When the script runs out it raises
    :class:`LLMError`, so a test that expects N calls notices a call N+1 instead of looping.
    ``context_window`` lets a test pretend to be a small-context model.
    """

    def __init__(
        self,
        replies: Sequence[str | Mapping[str, Any]],
        *,
        name: str = "replay",
        context_window: int | None = None,
    ) -> None:
        self.name = name
        self.context_window = context_window
        self._replies: list[str] = [
            r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in replies
        ]
        self.requests: list[RecordedRequest] = []

    @property
    def remaining(self) -> int:
        return len(self._replies)

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.4,
        seed: int | None = None,
        max_tokens: int = 8192,
    ) -> str:
        self.requests.append(
            RecordedRequest(system, list(messages), json_schema, temperature, seed, max_tokens)
        )
        if not self._replies:
            raise LLMError(
                f"ReplayClient has no scripted reply left for request #{len(self.requests)}"
            )
        reply = self._replies.pop(0)
        self.last_truncated = isinstance(reply, Truncated)
        return str(reply)


# --------------------------------------------------------------------------- HTTP plumbing
def _server_message(resp: httpx.Response) -> str:
    """The human-readable part of an error body (JSON ``error`` / ``message`` or raw text)."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text.strip()[:300]
    if isinstance(data, dict):
        err = data.get("error", data.get("message"))
        if isinstance(err, dict):
            err = err.get("message") or err.get("type")
        if isinstance(err, str) and err.strip():
            return err.strip()[:300]
    return resp.text.strip()[:300]


_TOKEN_CAP = re.compile(
    r"supports at most (\d+)"  # OpenAI: "...This model supports at most 4096 completion tokens"
    r"|> (\d+), which is the maximum"  # Anthropic: "max_tokens: 12288 > 4096, which is the maximum allowed ..."
    r"|maximum (?:allowed )?(?:number of )?(?:output|completion) tokens[^\d]{0,40}(\d+)"
    r"|max_(?:completion_)?tokens[^.]{0,40}<= (\d+)",  # "Expected a value <= 4096"
    re.IGNORECASE,
)


def token_cap_from_error(text: str) -> int | None:
    """The output-token limit a 400 error names ("this model supports at most 4096 ..."), else ``None``.

    Older and smaller models cap the reply well below what a 50 s spec needs asked for; knowing the cap lets
    the client retry once with it instead of failing.
    """
    m = _TOKEN_CAP.search(text or "")
    n = next((g for g in m.groups() if g), None) if m else None
    return int(n) if n and int(n) >= 256 else None


def clean_key(raw: str | None, variable: str) -> str | None:
    """A key as it will be sent: surrounding whitespace removed (a trailing newline from a copy/paste or a CRLF file is the usual
    culprit), ``None`` when empty.  A key with a space or control character inside is refused WITHOUT quoting it."""
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if not key.isprintable() or any(c.isspace() for c in key):
        raise LLMConfigError(
            f"{variable} contains a space or a control character (a stray newline from copy/paste?); "
            "remove it and try again"
        )
    return key


def display_host(url: str) -> str:
    """``host[:port]`` of a URL, without any credentials in it (what a user should see about where requests go)."""
    parts = urlparse(url)
    host = parts.hostname or "?"
    return f"{host}:{parts.port}" if parts.port else host


class _HTTPClient(LLMClient):
    """Shared request/response handling of the three HTTP clients."""

    provider = "llm"

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not model:
            raise LLMConfigError(f"{self.provider}: a model name is required")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._transport = transport
        self._max_retries = max_retries
        self._sleep = sleep

    def __repr__(self) -> str:  # never includes credentials
        return (
            f"{type(self).__name__}(model={self.model!r}, base_url={display_host(self.base_url)!r})"
        )

    @property
    def destination(self) -> str:
        """``host[:port]`` requests go to (so a surprising base URL is visible, not silent)."""
        return display_host(self.base_url)

    # -- hooks ---------------------------------------------------------------------------
    def _secrets(self) -> list[str]:
        return []

    def _unreachable_hint(self) -> str:
        return "check your network connection and the base URL"

    def _auth_hint(self) -> str:
        return "check the API key"

    def _not_found_hint(self, server_msg: str) -> str:
        return "check the model name and the base URL"

    # -- helpers -------------------------------------------------------------------------
    def _scrub(self, text: str) -> str:
        for secret in self._secrets():
            if secret and len(secret) >= 4:
                for form in (
                    secret,
                    repr(secret)[1:-1],
                ):  # as is, and as an exception message would escape it
                    text = text.replace(form, "***")
        return text

    def _post(self, url: str, body: dict[str, Any], headers: Mapping[str, str]) -> httpx.Response:
        """POST JSON; network failures -> LLMUnavailable; transient statuses are retried."""
        attempt = 0
        while True:
            try:
                with httpx.Client(
                    transport=self._transport,
                    timeout=httpx.Timeout(self.timeout, connect=CONNECT_TIMEOUT),
                ) as http:
                    resp = http.post(url, json=body, headers=dict(headers))
            except httpx.TimeoutException as exc:
                raise LLMUnavailable(
                    f"{self.provider} did not answer within {self.timeout:g}s ({url}). The model "
                    "may be too slow for this machine: try a smaller one, or raise the timeout.",
                    provider=self.provider,
                ) from exc
            except httpx.InvalidURL as exc:
                raise LLMConfigError(f"{self.provider}: invalid URL {url!r}") from exc
            except httpx.HTTPError as exc:
                reason = self._scrub(str(exc)) or exc.__class__.__name__
                raise LLMUnavailable(
                    f"cannot reach {self.provider} at {display_host(self.base_url)} ({reason}); "
                    f"{self._unreachable_hint()}.",
                    provider=self.provider,
                ) from exc
            except (
                OSError
            ) as exc:  # a missing/unreadable CA bundle (SSL_CERT_FILE), a socket problem ...
                reason = self._scrub(f"{exc.__class__.__name__}: {exc}")
                raise LLMUnavailable(
                    f"cannot use {self.provider} at {display_host(self.base_url)} ({reason}); "
                    f"{self._unreachable_hint()}.",
                    provider=self.provider,
                ) from exc
            if resp.status_code in RETRY_STATUS and attempt < self._max_retries:
                self._sleep(self._retry_delay(resp, attempt))
                attempt += 1
                continue
            return resp

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        try:
            return float(min(30.0, max(0.0, float(resp.headers.get("retry-after", "")))))
        except ValueError:
            return float(min(30.0, 1.5 * (2**attempt)))

    def _raise_for_status(self, resp: httpx.Response) -> None:
        code = resp.status_code
        if code < 400:
            return
        msg = self._scrub(_server_message(resp))
        detail = f": {msg}" if msg else ""
        p = self.provider
        if code in (401, 403):
            raise LLMUnavailable(
                f"{p} rejected the credentials (HTTP {code}){detail}. {self._auth_hint()}.",
                provider=p,
                status_code=code,
            )
        if code == 404:
            raise LLMUnavailable(
                f"{p} answered 404 Not Found{detail}. {self._not_found_hint(msg)}.",
                provider=p,
                status_code=code,
            )
        if code == 429:
            raise LLMUnavailable(
                f"{p} is rate limiting this client or the account is out of quota "
                f"(HTTP 429){detail}. Wait a moment and try again.",
                provider=p,
                status_code=code,
            )
        if code >= 500:
            raise LLMUnavailable(
                f"{p} had a server error (HTTP {code}){detail}. Try again in a moment.",
                provider=p,
                status_code=code,
            )
        raise LLMError(f"{p} rejected the request (HTTP {code}){detail}")

    def _json_body(self, resp: httpx.Response) -> dict[str, Any]:
        self._raise_for_status(resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMResponseError(
                f"{self.provider} replied with something that is not JSON: {self._scrub(resp.text[:200])!r}"
            ) from exc
        if not isinstance(data, dict):
            raise LLMResponseError(f"{self.provider} replied with an unexpected JSON shape")
        return data


def _chat_messages(system: str, messages: Sequence[Message]) -> list[dict[str, str]]:
    out = [{"role": "system", "content": system}] if system else []
    out.extend({"role": m.role, "content": m.content} for m in messages)
    return out


# --------------------------------------------------------------------------- Ollama
class OllamaClient(_HTTPClient):
    """Local Ollama server: ``POST {base_url}/api/chat`` with ``stream: false``.

    The JSON schema goes in ``format`` (structured outputs), so even small local models can only
    emit schema-shaped JSON.  ``num_ctx`` raises Ollama's small default context window - without it
    the long system prompt would be silently truncated - but is clamped to what the model actually
    supports: the first use asks ``POST /api/show`` for ``<architecture>.context_length`` (cached),
    and :attr:`context_window` reports the window really in force so callers can pick a prompt that
    fits (a 2B model with an 8192-token window cannot take a 6k-token prompt *and* write the spec).
    """

    provider = "Ollama"

    def __init__(
        self,
        model: str = "llama3.1",
        base_url: str = OLLAMA_URL,
        *,
        num_ctx: int | None = 16384,
        timeout: float = DEFAULT_TIMEOUT * 2,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            model,
            base_url,
            timeout=timeout,
            transport=transport,
            max_retries=max_retries,
            sleep=sleep,
        )
        self.num_ctx = num_ctx
        self.name = f"ollama:{model}"
        self._model_ctx: int | None = None
        self._probed = False

    def _unreachable_hint(self) -> str:
        return "is Ollama running? try `ollama serve` (or pass the server address: ollama:MODEL@http://HOST:11434)"

    def _not_found_hint(self, server_msg: str) -> str:
        if "model" in server_msg.lower():
            return f"download the model first with `ollama pull {self.model}`"
        return f"check the server address ({self.base_url}) and that it is an Ollama server"

    # -- context window ---------------------------------------------------------------------
    def _probe_context(self) -> int | None:
        """The model's maximum context length from ``/api/show`` (None if the server will not say).
        Asked once; any failure just means "unknown" - the chat call reports real problems."""
        if self._probed:
            return self._model_ctx
        self._probed = True
        try:
            resp = self._post(
                f"{self.base_url}/api/show",
                {"model": self.model, "name": self.model},
                {"Content-Type": "application/json"},
            )
            data = resp.json() if resp.status_code == 200 else {}
        except (LLMError, ValueError):
            return None
        info = data.get("model_info") if isinstance(data, dict) else None
        if isinstance(info, dict):
            arch = info.get("general.architecture")
            candidates = [info.get(f"{arch}.context_length")] if isinstance(arch, str) else []
            candidates += [v for k, v in info.items() if str(k).endswith(".context_length")]
            for v in candidates:
                if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                    self._model_ctx = v
                    break
        return self._model_ctx

    @property
    def context_window(self) -> int | None:  # type: ignore[override]
        """``min(num_ctx, the model's own maximum)``; the model maximum alone when ``num_ctx`` is
        None; None when neither is known."""
        model_max = self._probe_context()
        if self.num_ctx and model_max:
            return min(self.num_ctx, model_max)
        return self.num_ctx or model_max

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.4,
        seed: int | None = None,
        max_tokens: int = 8192,
    ) -> str:
        options: dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens}
        if seed is not None:
            options["seed"] = seed
        window = self.context_window
        if window:
            options["num_ctx"] = window
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _chat_messages(system, messages),
            "stream": False,
            "options": options,
        }
        if json_schema is not None:
            body["format"] = json_schema
        resp = self._post(f"{self.base_url}/api/chat", body, {"Content-Type": "application/json"})
        data = self._json_body(resp)
        if isinstance(data.get("error"), str):
            raise LLMError(f"Ollama error: {data['error']}")
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        self.last_truncated = data.get("done_reason") == "length"
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError(
                "Ollama returned an empty reply (is the model loaded and chat-capable?)"
            )
        return content


# --------------------------------------------------------------------------- OpenAI-compatible
class OpenAICompatClient(_HTTPClient):
    """OpenAI ``/chat/completions`` and compatible servers (LM Studio, vLLM, llama.cpp, ...).

    ``api_key`` defaults to ``$OPENAI_API_KEY``; it is required for api.openai.com and optional
    for other base URLs.  Output is requested as ``response_format: json_schema``; a server that
    answers 400/422 is retried with ``json_object`` and then with no ``response_format`` at all
    (the system prompt already demands JSON), and parameters a model refuses (``max_tokens`` vs
    ``max_completion_tokens``, ``temperature``, ``seed``) are adapted the same way.  The mode that
    worked is remembered for the next call.
    """

    provider = "OpenAI-compatible server"
    MAX_ADAPTATIONS = 4

    def __init__(
        self,
        model: str,
        base_url: str = OPENAI_URL,
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        env: Mapping[str, str] | None = None,
        context_window: int | None = None,
    ) -> None:
        super().__init__(
            model,
            base_url,
            timeout=timeout,
            transport=transport,
            max_retries=max_retries,
            sleep=sleep,
        )
        self.context_window = context_window  # servers do not say; tell us if yours is small
        key = clean_key(
            api_key
            if api_key is not None
            else (env if env is not None else os.environ).get("OPENAI_API_KEY"),
            "OPENAI_API_KEY",
        )
        self._api_key = key or None
        if self.hosted and not self._api_key:
            raise LLMConfigError(
                "OPENAI_API_KEY is not set (it is required for api.openai.com). Export it in your shell "
                "or put it in a git-ignored .env file (see .env.example); or point at a local server: "
                "openai:MODEL@http://localhost:1234/v1"
            )
        self.name = f"openai:{model}"
        # response_format ladder state: "json_schema" -> "json_object" -> "none"
        self._rf_mode = "json_schema"
        self._max_tokens_param = "max_tokens"
        self._cap: int | None = None  # the model's output-token limit once a 400 error has named it
        self._drop: set[str] = set()

    @property
    def hosted(self) -> bool:
        return (urlparse(self.base_url).hostname or "") == "api.openai.com"

    def _secrets(self) -> list[str]:
        return [self._api_key] if self._api_key else []

    def _unreachable_hint(self) -> str:
        if self.hosted:
            return "check your network connection"
        return "is the server (LM Studio / vLLM / llama.cpp) running and listening on that address?"

    def _auth_hint(self) -> str:
        return (
            "check OPENAI_API_KEY"
            if self.hosted
            else "this server wants an API key: set OPENAI_API_KEY"
        )

    def _not_found_hint(self, server_msg: str) -> str:
        return (
            f"is {self.model!r} a model this server offers? The base URL usually ends in /v1 "
            f"(now {self.base_url})"
        )

    def _body(
        self,
        system: str,
        messages: Sequence[Message],
        schema: dict[str, Any] | None,
        temperature: float,
        seed: int | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _chat_messages(system, messages),
            self._max_tokens_param: min(max_tokens, self._cap) if self._cap else max_tokens,
        }
        if "temperature" not in self._drop:
            body["temperature"] = temperature
        if seed is not None and "seed" not in self._drop:
            body["seed"] = seed
        if self._rf_mode == "json_schema" and schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reel_scene_spec", "schema": schema, "strict": False},
            }
        elif self._rf_mode in ("json_schema", "json_object") and schema is not None:
            body["response_format"] = {"type": "json_object"}
        return body

    def _adapt(self, error_text: str, has_schema: bool) -> bool:
        """Change one rejected option; True if something changed (so a retry makes sense)."""
        low = error_text.lower()
        cap = token_cap_from_error(error_text)
        if cap and (self._cap is None or cap < self._cap):
            self._cap = cap
            return True
        if "max_completion_tokens" in low and self._max_tokens_param == "max_tokens":
            self._max_tokens_param = "max_completion_tokens"
            return True
        if "temperature" in low and "temperature" not in self._drop:
            self._drop.add("temperature")
            return True
        if "seed" in low and "seed" not in self._drop:
            self._drop.add("seed")
            return True
        if has_schema and self._rf_mode == "json_schema":
            self._rf_mode = "json_object"
            return True
        if has_schema and self._rf_mode == "json_object":
            self._rf_mode = "none"
            return True
        return False

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.4,
        seed: int | None = None,
        max_tokens: int = 8192,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = f"{self.base_url}/chat/completions"
        for _ in range(self.MAX_ADAPTATIONS + 1):
            body = self._body(system, messages, json_schema, temperature, seed, max_tokens)
            resp = self._post(url, body, headers)
            if resp.status_code in (400, 422) and self._adapt(
                _server_message(resp), json_schema is not None
            ):
                continue
            break
        return self._reply_text(self._json_body(resp))

    def _reply_text(self, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMResponseError("the server's reply has no `choices`")
        choice = choices[0]
        self.last_truncated = choice.get("finish_reason") == "length"
        raw_message = choice.get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
        if message.get("refusal"):
            raise LLMResponseError(
                f"the model refused: {self._scrub(str(message['refusal']))[:200]}"
            )
        content = message.get("content")
        if isinstance(content, list):  # some servers return content parts
            content = "".join(
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type", "text") == "text"
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("the server returned an empty reply")
        return content


# --------------------------------------------------------------------------- Anthropic
class AnthropicClient(_HTTPClient):
    """Anthropic Messages API.  With a schema the model is forced to call one tool,
    ``emit_spec``, whose ``input_schema`` is the spec schema; the tool input is the JSON reply.
    Without one (or if no tool call comes back) the text blocks are returned."""

    provider = "Anthropic"

    def __init__(
        self,
        model: str = "claude-sonnet-5-5",
        api_key: str | None = None,
        base_url: str = ANTHROPIC_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            model,
            base_url,
            timeout=timeout,
            transport=transport,
            max_retries=max_retries,
            sleep=sleep,
        )
        key = clean_key(
            api_key
            if api_key is not None
            else (env if env is not None else os.environ).get("ANTHROPIC_API_KEY"),
            "ANTHROPIC_API_KEY",
        )
        if not key:
            raise LLMConfigError(
                "ANTHROPIC_API_KEY is not set. Export it in your shell (get one at console.anthropic.com) "
                "or put it in a git-ignored .env file (see .env.example); or use a local model: ollama:llama3.1"
            )
        self._api_key = key
        self.name = f"anthropic:{model}"

    def _secrets(self) -> list[str]:
        return [self._api_key]

    def _auth_hint(self) -> str:
        return "check ANTHROPIC_API_KEY"

    def _not_found_hint(self, server_msg: str) -> str:
        return f"is {self.model!r} a valid model id for your account?"

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.4,
        seed: int | None = None,  # the Messages API has no seed parameter
        max_tokens: int = 8192,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if json_schema is not None:
            body["tools"] = [
                {
                    "name": EMIT_TOOL,
                    "description": "Emit the finished reel scene spec. The input is the spec itself.",
                    "input_schema": json_schema,
                }
            ]
            body["tool_choice"] = {"type": "tool", "name": EMIT_TOOL}
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        url = f"{self.base_url}/v1/messages"
        resp = self._post(url, body, headers)
        if resp.status_code == 400:
            msg = _server_message(resp)
            cap = token_cap_from_error(msg)
            if (
                cap and cap < max_tokens
            ):  # an older model with a smaller output limit than we asked for
                body["max_tokens"] = cap
                resp = self._post(url, body, headers)
            elif "tools" in body and re.search(r"input_schema|tool_choice|tools\.\d", msg, re.I):
                # the API did not like the tool schema: fall back to plain JSON text (the prompt already demands it)
                body.pop("tools", None)
                body.pop("tool_choice", None)
                resp = self._post(url, body, headers)
        data = self._json_body(resp)
        self.last_truncated = data.get("stop_reason") == "max_tokens"
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMResponseError("Anthropic's reply has no `content`")
        for b in blocks:
            if (
                isinstance(b, dict)
                and b.get("type") == "tool_use"
                and isinstance(b.get("input"), dict)
            ):
                return json.dumps(b["input"], ensure_ascii=False)
        text = "".join(
            str(b.get("text", ""))
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
        if not text.strip():
            raise LLMResponseError("Anthropic returned neither a tool call nor text")
        return text


# --------------------------------------------------------------------------- factory
PROVIDERS = ("ollama", "openai", "anthropic")
#: spoken names accepted for a provider: ``--llm claude`` is Anthropic, ``--llm gpt`` or ``chatgpt`` is OpenAI
PROVIDER_ALIASES = {"claude": "anthropic", "gpt": "openai", "chatgpt": "openai"}
#: what a bare provider (``--llm openai``) means; any model name can be given instead
PROVIDER_DEFAULT_MODELS = {
    "ollama": "llama3.1",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5-5",
}
#: the only variables reel reads from a ``.env`` file (see :func:`llm_env`)
ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "REEL_LLM",
    # the online speech engine (reel.audio.elevenlabs) shares the same .env rules
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_BASE_URL",
    "ELEVENLABS_MODEL",
    "ELEVENLABS_VOICE",
    "REEL_TTS",
)
#: variables that say *where* a key is sent: an untrusted .env may not set them
_DESTINATION_KEYS = ("OPENAI_BASE_URL", "ELEVENLABS_BASE_URL")


@dataclass
class ClientSpec:
    """A parsed ``provider:model[@base_url]`` string."""

    provider: str
    model: str
    base_url: str | None = None


def parse_client_spec(spec: str) -> ClientSpec:
    """``'ollama:llama3.1'``, ``'openai:gpt-4o-mini'``, ``'openai:model@http://localhost:1234/v1'``,
    ``'anthropic:claude-sonnet-5-5'``.  Only the first ``:`` separates the provider (model names
    such as ``llama3.1:8b`` contain colons) and the first ``@`` starts the base URL."""
    if not isinstance(spec, str) or not spec.strip():
        raise LLMConfigError(
            "empty LLM spec; use provider:model, e.g. ollama:llama3.1, openai:gpt-4o-mini or "
            "anthropic:claude-sonnet-5-5"
        )
    text = spec.strip()
    provider, sep, rest = text.partition(":")
    provider = provider.strip().lower()
    provider = PROVIDER_ALIASES.get(provider, provider)
    if not sep and provider in PROVIDERS:  # bare "ollama": the provider's default model
        return ClientSpec(provider, "")
    if provider not in PROVIDERS:
        guess = difflib.get_close_matches(provider, PROVIDERS, n=1)
        if not sep:
            hint = (
                f"Did you mean {guess[0]!r}?" if guess else f"For a local model try ollama:{text}."
            )
            raise LLMConfigError(
                f"LLM spec {text!r} has no provider; it must look like provider:model "
                f"(providers: {', '.join(PROVIDERS)}). {hint}"
            )
        hint = f" Did you mean {guess[0]!r}?" if guess else ""
        raise LLMConfigError(
            f"unknown LLM provider {provider!r} in {text!r}; known providers: "
            f"{', '.join(PROVIDERS)}.{hint}"
        )
    model, _, url = rest.partition("@")
    return ClientSpec(provider, model.strip(), url.strip() or None)


def get_client(
    spec: str,
    *,
    transport: httpx.BaseTransport | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> LLMClient:
    """Build a client from ``provider:model[@base_url]`` (see :func:`parse_client_spec`).

    Keys come from the environment (``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``, or ``env`` when
    given); a missing key is a :class:`LLMConfigError` that says which variable to set.
    """
    parsed = parse_client_spec(spec)
    model = parsed.model or PROVIDER_DEFAULT_MODELS[parsed.provider]
    lookup = env if env is not None else os.environ
    kw: dict[str, Any] = {"transport": transport}
    if timeout is not None:
        kw["timeout"] = timeout
    if parsed.provider == "ollama":
        return OllamaClient(model, parsed.base_url or OLLAMA_URL, **kw)
    if parsed.provider == "openai":
        base = parsed.base_url or lookup.get("OPENAI_BASE_URL") or OPENAI_URL
        return OpenAICompatClient(model, base, env=env, **kw)
    return AnthropicClient(model, base_url=parsed.base_url or ANTHROPIC_URL, env=env, **kw)


def load_env_file(path: str | Path, *, trusted: bool = True) -> dict[str, str]:
    """The :data:`ENV_KEYS` assignments of a ``.env`` file (``KEY=value``, ``export KEY=value``, quotes and ``#`` comments
    allowed); every other name in the file is ignored, and a missing, unreadable or undecodable file gives ``{}``.

    A file that is not ``trusted`` (the ``.env`` of whatever directory you happen to be in) may set keys but cannot say *where*
    requests go: ``OPENAI_BASE_URL``, ``ELEVENLABS_BASE_URL`` and a ``REEL_LLM`` carrying an ``@url`` are dropped, so a cloned
    repository cannot redirect a key you exported in your shell."""
    out: dict[str, str] = {}
    try:
        text = (
            Path(path).expanduser().read_text(encoding="utf-8-sig")
        )  # tolerate an editor's byte-order mark
    except (OSError, ValueError):  # ValueError: not UTF-8
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in ENV_KEYS:
            continue
        if value[:1] in (
            "'",
            '"',
        ):  # quoted: take what is inside the quotes, ignore anything after (a comment)
            end_quote = value.find(value[0], 1)
            value = value[1:end_quote] if end_quote > 0 else value[1:]
        elif " #" in value:  # an inline comment after an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        if not value:
            continue
        if not trusted and (key in _DESTINATION_KEYS or (key == "REEL_LLM" and "@" in value)):
            continue
        out[key] = value
    return out


def llm_env(path: str | Path | None = None) -> dict[str, str]:
    """Where keys and defaults (LLM and speech engines) are looked up: the real environment, over ``./.env``, over ``~/.config/reel/.env``
    (or only the file named by ``path`` / ``$REEL_ENV_FILE``, which you chose and is trusted).  Only :data:`ENV_KEYS` are ever
    read from files, nothing here prints a value, and keep real keys out of the repository (``.env`` is git-ignored)."""
    explicit = path or os.environ.get("REEL_ENV_FILE")
    merged: dict[str, str] = {}
    if explicit:
        merged.update(load_env_file(explicit))
    else:  # later files win, the real environment wins over all of them
        merged.update(load_env_file(Path.home() / ".config" / "reel" / ".env"))
        merged.update(load_env_file(Path.cwd() / ".env", trusted=False))
    merged.update({k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)})
    return merged


__all__ = [
    "ANTHROPIC_URL",
    "DEFAULT_TIMEOUT",
    "ENV_KEYS",
    "OLLAMA_URL",
    "OPENAI_URL",
    "PROVIDER_DEFAULT_MODELS",
    "AnthropicClient",
    "ClientSpec",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMResponseError",
    "LLMUnavailable",
    "Message",
    "OllamaClient",
    "OpenAICompatClient",
    "RecordedRequest",
    "ReplayClient",
    "clean_key",
    "display_host",
    "get_client",
    "llm_env",
    "load_env_file",
    "parse_client_spec",
    "token_cap_from_error",
]
