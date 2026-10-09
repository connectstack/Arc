"""Using your own OpenAI / Anthropic key: defaults, `.env` keys, output-limit recovery.  No network: httpx mock transports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from reel.llm.client import (
    ENV_KEYS,
    LLMConfigError,
    LLMUnavailable,
    Message,
    OpenAICompatClient,
    get_client,
    llm_env,
    load_env_file,
    token_cap_from_error,
)

MSGS = [Message("user", "hello")]
SECRET = "sk-test-0123456789-SECRET"


def serve(*replies: tuple[int, dict[str, Any]]) -> tuple[httpx.MockTransport, list[dict[str, Any]]]:
    """A transport answering with the given (status, json) pairs in order, recording each request body."""
    bodies: list[dict[str, Any]] = []
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        status, payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler), bodies


OPENAI_OK = (200, {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]})
ANTHROPIC_OK = (
    200,
    {
        "content": [{"type": "tool_use", "name": "emit_spec", "input": {"ok": True}}],
        "stop_reason": "tool_use",
    },
)


# ------------------------------------------------------------------ output-token limits of older/smaller models
@pytest.mark.parametrize(
    ("message", "cap"),
    [
        ("max_tokens is too large: 12288. This model supports at most 4096 completion tokens, whereas you provided 12288.", 4096),
        ("max_tokens: 12288 > 4096, which is the maximum allowed number of output tokens for claude-3-haiku-20240307", 4096),
        ("The maximum number of output tokens is 8192 for this model", 8192),
        ("Incorrect API key provided", None),
        ("max_tokens must be at least 1", None),
        ("", None),
    ],
)  # fmt: skip
def test_the_output_limit_is_read_from_an_error_message(message: str, cap: int | None) -> None:
    assert token_cap_from_error(message) == cap


def test_openai_retries_once_with_the_models_output_limit_and_remembers_it() -> None:
    too_big = (
        400,
        {
            "error": {
                "message": "max_tokens is too large: 12288. This model supports at most 4096 completion tokens."
            }
        },
    )
    transport, bodies = serve(too_big, OPENAI_OK)
    client = OpenAICompatClient("gpt-4-turbo", api_key=SECRET, transport=transport)
    assert client.complete("sys", MSGS, max_tokens=12288) == '{"ok": true}'
    assert [b["max_tokens"] for b in bodies] == [12288, 4096]
    assert (
        client.complete("sys", MSGS, max_tokens=12288) == '{"ok": true}'
    )  # the next call asks for 4096 straight away
    assert bodies[-1]["max_tokens"] == 4096 and len(bodies) == 3


def test_anthropic_retries_once_with_the_models_output_limit() -> None:
    too_big = (
        400,
        {"type": "error", "error": {"type": "invalid_request_error",
         "message": "max_tokens: 12288 > 4096, which is the maximum allowed number of output tokens for claude-3-haiku-20240307"}},
    )  # fmt: skip
    transport, bodies = serve(too_big, ANTHROPIC_OK)
    client = get_client(
        "anthropic:claude-3-haiku-20240307", transport=transport, env={"ANTHROPIC_API_KEY": SECRET}
    )
    assert json.loads(
        client.complete("sys", MSGS, json_schema={"type": "object"}, max_tokens=12288)
    ) == {"ok": True}
    assert [b["max_tokens"] for b in bodies] == [12288, 4096]
    assert bodies[0]["tool_choice"] == {"type": "tool", "name": "emit_spec"}


def test_anthropic_falls_back_to_plain_json_when_the_tool_schema_is_rejected() -> None:
    rejected = (
        400,
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "tools.0.input_schema: JSON schema is invalid",
            },
        },
    )
    plain = (
        200,
        {"content": [{"type": "text", "text": '{"ok": true}'}], "stop_reason": "end_turn"},
    )
    transport, bodies = serve(rejected, plain)
    client = get_client(
        "anthropic:claude-sonnet-5-5", transport=transport, env={"ANTHROPIC_API_KEY": SECRET}
    )
    assert client.complete("sys", MSGS, json_schema={"type": "object"}) == '{"ok": true}'
    assert "tools" in bodies[0] and "tools" not in bodies[1] and "tool_choice" not in bodies[1]


def test_an_unrelated_400_is_not_retried_forever() -> None:
    transport, bodies = serve((400, {"error": {"message": "something else is wrong"}}))
    client = get_client(
        "anthropic:claude-sonnet-5-5", transport=transport, env={"ANTHROPIC_API_KEY": SECRET}
    )
    with pytest.raises(Exception, match="something else"):
        client.complete("sys", MSGS, max_tokens=8000)
    assert len(bodies) == 1


# ------------------------------------------------------------------ bare providers and base URLs
def test_bare_providers_use_their_default_models() -> None:
    assert get_client("openai", env={"OPENAI_API_KEY": SECRET}).name == "openai:gpt-4o-mini"
    assert (
        get_client("anthropic", env={"ANTHROPIC_API_KEY": SECRET}).name
        == "anthropic:claude-sonnet-5-5"
    )
    assert get_client("openai:gpt-4o", env={"OPENAI_API_KEY": SECRET}).name == "openai:gpt-4o"


def test_spoken_provider_names_are_accepted() -> None:
    assert (
        get_client("claude", env={"ANTHROPIC_API_KEY": SECRET}).name
        == "anthropic:claude-sonnet-5-5"
    )
    assert (
        get_client("claude:claude-opus-5-5", env={"ANTHROPIC_API_KEY": SECRET}).name
        == "anthropic:claude-opus-5-5"
    )
    assert get_client("gpt", env={"OPENAI_API_KEY": SECRET}).name == "openai:gpt-4o-mini"
    assert get_client("chatgpt:gpt-4o", env={"OPENAI_API_KEY": SECRET}).name == "openai:gpt-4o"


def test_openai_base_url_comes_from_the_environment_unless_the_spec_names_one() -> None:
    env = {"OPENAI_API_KEY": SECRET, "OPENAI_BASE_URL": "http://localhost:4000/v1/"}
    assert get_client("openai:m", env=env).base_url == "http://localhost:4000/v1"  # type: ignore[attr-defined]
    assert get_client("openai:m@http://host:1/v1", env=env).base_url == "http://host:1/v1"  # type: ignore[attr-defined]
    assert (
        get_client("openai:m", env={"OPENAI_API_KEY": SECRET}).base_url
        == "https://api.openai.com/v1"
    )  # type: ignore[attr-defined]


def test_a_missing_key_says_which_variable_to_set_and_never_asks_for_it_in_a_url() -> None:
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        get_client("openai", env={})
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        get_client("anthropic", env={})
    get_client("openai:m@http://localhost:1234/v1", env={})  # a local server needs no key


# ------------------------------------------------------------------ .env keys
def test_env_file_parsing_keeps_only_the_names_reel_reads(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text(
        "# my keys\n"
        f"OPENAI_API_KEY={SECRET}\n"
        "export ANTHROPIC_API_KEY='sk-ant-quoted'\n"
        'REEL_LLM="openai:gpt-4o-mini"  \n'
        "OPENAI_BASE_URL=http://localhost:4000/v1 # a proxy\n"
        "AWS_SECRET_ACCESS_KEY=not-reels-business\n"
        "PATH=/evil\n"
        "EMPTY=\n"
        "garbage line\n"
    )
    got = load_env_file(f)
    assert got == {
        "OPENAI_API_KEY": SECRET,
        "ANTHROPIC_API_KEY": "sk-ant-quoted",
        "REEL_LLM": "openai:gpt-4o-mini",
        "OPENAI_BASE_URL": "http://localhost:4000/v1",
    }
    assert set(got) <= set(ENV_KEYS)
    assert load_env_file(tmp_path / "missing.env") == {}
    bom = (
        tmp_path / "bom.env"
    )  # saved by an editor that adds a byte-order mark and Windows line endings
    bom.write_bytes(b"\xef\xbb\xbfOPENAI_API_KEY=" + SECRET.encode() + b"\r\nREEL_LLM=openai\r\n")
    assert load_env_file(bom) == {"OPENAI_API_KEY": SECRET, "REEL_LLM": "openai"}


def test_the_real_environment_wins_over_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "keys.env"
    f.write_text("OPENAI_API_KEY=from-file\nREEL_LLM=ollama:from-file\n")
    for name in ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REEL_ENV_FILE", str(f))
    assert llm_env() == {"OPENAI_API_KEY": "from-file", "REEL_LLM": "ollama:from-file"}
    monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
    assert (
        llm_env()["OPENAI_API_KEY"] == "from-shell" and llm_env()["REEL_LLM"] == "ollama:from-file"
    )
    assert llm_env(path=tmp_path / "nothing.env") == {"OPENAI_API_KEY": "from-shell"}


def test_a_key_from_a_file_never_leaks_into_errors_or_reprs(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text(f"OPENAI_API_KEY={SECRET}\n")
    transport, _ = serve((401, {"error": {"message": f"Incorrect API key provided: {SECRET}."}}))
    client = get_client("openai:gpt-4o-mini", transport=transport, env=load_env_file(f))
    assert SECRET not in repr(client)
    with pytest.raises(LLMUnavailable) as err:
        client.complete("sys", MSGS)
    assert SECRET not in str(err.value) and "OPENAI_API_KEY" in str(err.value)


# ------------------------------------------------------------------ hardening found by the independent review
def test_a_key_is_cleaned_and_a_malformed_one_is_refused_without_quoting_it() -> None:
    """Regression: a key ending in a newline made the HTTP stack raise an error whose text contained the key."""
    client = get_client("openai", env={"OPENAI_API_KEY": SECRET + "\n"})
    assert client._api_key == SECRET  # type: ignore[attr-defined]
    assert get_client("anthropic", env={"ANTHROPIC_API_KEY": f"  {SECRET}\r\n"})._api_key == SECRET  # type: ignore[attr-defined]
    for bad in (f"{SECRET} extra", f"{SECRET}\nsecond-line", f"{SECRET}\x07"):
        with pytest.raises(LLMConfigError) as err:
            get_client("openai", env={"OPENAI_API_KEY": bad})
        assert SECRET not in str(err.value) and "control character" in str(err.value)


def test_os_level_failures_become_a_clean_error_without_the_key() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise OSError(f"could not load the CA bundle while sending {SECRET}")

    client = get_client(
        "openai", transport=httpx.MockTransport(boom), env={"OPENAI_API_KEY": SECRET}
    )
    with pytest.raises(LLMUnavailable) as err:
        client.complete("sys", MSGS)
    assert SECRET not in str(err.value) and "OSError" in str(err.value)


def test_a_cwd_env_file_cannot_say_where_a_key_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cloned repository's .env may hold keys, but must not redirect the key you exported in your shell."""
    f = tmp_path / ".env"
    f.write_text(
        "OPENAI_BASE_URL=http://attacker.example/v1\nREEL_LLM=openai:m@http://attacker.example/v1\nANTHROPIC_API_KEY=sk-ant-ok\n"
    )
    assert load_env_file(f, trusted=False) == {"ANTHROPIC_API_KEY": "sk-ant-ok"}
    assert (
        load_env_file(f)["OPENAI_BASE_URL"] == "http://attacker.example/v1"
    )  # a file you named yourself is trusted
    for name in ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("REEL_ENV_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    env = llm_env()
    assert env == {"ANTHROPIC_API_KEY": "sk-ant-ok"}  # nothing about where requests go survived
    monkeypatch.setenv("REEL_ENV_FILE", str(f))
    assert llm_env()["OPENAI_BASE_URL"] == "http://attacker.example/v1"


def test_env_file_quotes_comments_and_encodings(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text(
        f"OPENAI_API_KEY=\"{SECRET}\" # my key\nANTHROPIC_API_KEY='sk-ant x'\nREEL_LLM=openai # default\n"
    )
    assert load_env_file(f) == {
        "OPENAI_API_KEY": SECRET,
        "ANTHROPIC_API_KEY": "sk-ant x",
        "REEL_LLM": "openai",
    }
    latin = tmp_path / "latin1.env"
    latin.write_bytes("OPENAI_API_KEY=caf\xe9\n".encode("latin-1"))
    utf16 = tmp_path / "utf16.env"
    utf16.write_bytes("OPENAI_API_KEY=x\n".encode("utf-16"))
    assert (
        load_env_file(latin) == {} and load_env_file(utf16) == {}
    )  # undecodable: ignored, not a crash


def test_the_destination_never_contains_credentials() -> None:
    from reel.llm.client import display_host

    assert display_host("http://user:pass@host.example:9000/v1") == "host.example:9000"
    assert display_host("https://api.openai.com/v1") == "api.openai.com"
    client = get_client("openai:m@http://user:pass@127.0.0.1:18765/v1", env={})
    assert "pass" not in repr(client) and client.destination == "127.0.0.1:18765"  # type: ignore[attr-defined]


def test_the_anthropic_fallback_only_fires_for_errors_about_the_tool() -> None:
    """A 400 that merely mentions a schema-ish word elsewhere must not trigger a second, billed request."""
    other = (
        400,
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "messages.0.content: the schema of this message is odd",
            },
        },
    )
    transport, bodies = serve(other)
    client = get_client(
        "anthropic:claude-sonnet-5-5", transport=transport, env={"ANTHROPIC_API_KEY": SECRET}
    )
    with pytest.raises(Exception, match="odd"):
        client.complete("sys", MSGS, json_schema={"type": "object"})
    assert len(bodies) == 1
