"""`reel build`: script -> spec (scripted model or offline planner) -> lint -> render, and how it fails.

No network: the model is a `ReplayClient`, swapped in for `get_client`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from reel.cli.main import app
from reel.core.lint import lint_data
from reel.llm import client as llm_client
from reel.llm.client import LLMClient, LLMUnavailable, Message, ReplayClient

runner = CliRunner()

SCRIPT = """\
Mia is a curious kid who finds a glowing map on her way home from school.
She follows it across town and meets Sam, an old hat maker who has waited years for someone to find it.
Together they discover the map leads to the school roof, where the whole neighbourhood is waiting to say thank you.
"""


def scripted_spec() -> dict[str, Any]:
    """What a well-behaved model would return: 10 scenes of 5.5 s (50.5 s total), only things the catalog has."""
    templates = ["street", "room", "abstract", "street", "room"] * 2
    scenes = []
    for i, template in enumerate(templates):
        last = i == len(templates) - 1
        scenes.append(
            {
                "id": f"s{i + 1:02d}",
                "duration_sec": 5.5,
                "background": {"template": template, "params": {}},
                "layers": [
                    {
                        "character": "mia",
                        "position": [0.4, 0.72],
                        "actions": [{"name": "wave", "t0": 0.5, "t1": 3.0}],
                    }
                ],
                "captions": [{"text": f"Scene {i + 1}", "t0": 0.5, "t1": 3.0, "style": "subtitle"}],
                "transition_out": {"type": "cut", "duration": 0}
                if last
                else {"type": "crossfade", "duration": 0.5},
            }
        )
    return {
        "meta": {"title": "Mia and the map", "style": "flat_vector", "seed": 7},
        "characters": [{"id": "mia", "archetype": "kid"}],
        "scenes": scenes,
        "audio": {"music": None, "voiceover": "none", "ducking": True},
    }


class DownClient(LLMClient):
    name = "down:model"

    def complete(self, system: str, messages: list[Message], **kw: Any) -> str:
        raise LLMUnavailable("connection refused at http://localhost:11434", provider="ollama")


def use_client(monkeypatch: pytest.MonkeyPatch, client: LLMClient) -> list[str]:
    """Make `get_client(spec)` return ``client``; returns the list of specs it was asked for."""
    asked: list[str] = []

    def fake_get_client(spec: str, **_: Any) -> LLMClient:
        asked.append(spec)
        return client

    monkeypatch.setattr(llm_client, "get_client", fake_get_client)
    return asked


@pytest.fixture(autouse=True)
def _no_ambient_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REEL_LLM", raising=False)


@pytest.fixture
def script(tmp_path: Path) -> Path:
    p = tmp_path / "story.txt"
    p.write_text(SCRIPT)
    return p


def build(script: Path, tmp_path: Path, *args: str) -> Any:
    return runner.invoke(app, ["build", str(script), "-o", str(tmp_path / "reel.mp4"), *args])


def flat(output: str) -> str:
    """The console output with rich's line wrapping undone."""
    return " ".join(output.split())


def test_a_scripted_model_writes_a_linted_spec_next_to_the_video(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = ReplayClient([scripted_spec()], name="fake:model")
    asked = use_client(monkeypatch, fake)
    res = build(script, tmp_path, "--llm", "fake:model", "--spec-only")
    assert res.exit_code == 0, res.output
    assert asked == ["fake:model"]
    spec_file = tmp_path / "reel.spec.json"
    spec = json.loads(spec_file.read_text())
    assert lint_data(spec).ok
    assert (
        7 <= len(spec["scenes"]) <= 10
    )  # the model's 10, minus trailing scenes the script has no words for
    request = fake.requests[0]
    assert request.json_schema is not None  # the spec schema is what constrains the model
    assert "glowing map" in request.messages[0].content  # the user's script reached the model
    assert not (tmp_path / "reel.mp4").exists()  # --spec-only stops before rendering


def test_the_compact_flag_selects_the_short_prompt_for_small_models(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sizes = {}
    for flag in ("--compact", "--full-prompt"):
        fake = ReplayClient([scripted_spec()], name="fake:model")
        use_client(monkeypatch, fake)
        res = build(script, tmp_path, "--llm", "fake:model", "--spec-only", flag)
        assert res.exit_code == 0, res.output
        sizes[flag] = len(fake.requests[0].system)
    assert sizes["--compact"] < 0.4 * sizes["--full-prompt"], sizes


def test_the_model_can_come_from_the_environment(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = use_client(monkeypatch, ReplayClient([scripted_spec()]))
    monkeypatch.setenv("REEL_LLM", "ollama:llama3.1")
    res = build(script, tmp_path, "--spec-only")
    assert res.exit_code == 0, res.output
    assert asked == ["ollama:llama3.1"]


def test_no_llm_overrides_the_environment_and_uses_the_offline_planner(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = use_client(monkeypatch, ReplayClient([]))
    monkeypatch.setenv("REEL_LLM", "ollama:llama3.1")
    res = build(script, tmp_path, "--no-llm", "--spec-only")
    assert res.exit_code == 0, res.output
    assert asked == []  # no model was even constructed
    spec = json.loads((tmp_path / "reel.spec.json").read_text())
    report = lint_data(spec)
    assert report.ok, report.format_text()
    assert report.total_sec is not None and 45 <= report.total_sec <= 60


def test_the_offline_planner_is_the_default_and_is_deterministic(
    script: Path, tmp_path: Path
) -> None:
    a = build(script, tmp_path, "--spec-only", "--spec-out", str(tmp_path / "a.json"))
    b = build(script, tmp_path, "--spec-only", "--spec-out", str(tmp_path / "b.json"))
    assert a.exit_code == 0 and b.exit_code == 0, a.output + b.output
    assert "offline planner" in flat(a.output)
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    other = build(
        script, tmp_path, "--spec-only", "--seed", "99", "--spec-out", str(tmp_path / "c.json")
    )
    assert other.exit_code == 0, other.output
    assert json.loads((tmp_path / "c.json").read_text())["meta"]["seed"] == 99


def test_duration_and_style_reach_the_spec(script: Path, tmp_path: Path) -> None:
    res = build(script, tmp_path, "--spec-only", "--style", "stickman", "--duration", "58")
    assert res.exit_code == 0, res.output
    spec = json.loads((tmp_path / "reel.spec.json").read_text())
    assert spec["meta"]["style"] == "stickman"
    report = lint_data(spec)
    assert report.total_sec is not None and 54 <= report.total_sec <= 60


def test_duration_outside_the_budget_is_a_usage_error(script: Path, tmp_path: Path) -> None:
    assert build(script, tmp_path, "--duration", "30").exit_code == 2


def test_an_unreachable_model_explains_the_alternatives(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_client(monkeypatch, DownClient())
    res = build(script, tmp_path, "--llm", "ollama:llama3.1", "--spec-only")
    assert res.exit_code == 2
    text = flat(res.output)
    assert "connection refused" in text
    assert "--no-llm" in text and "reel render" in text  # offline planner / hand-written JSON
    assert not (tmp_path / "reel.spec.json").exists()


def test_a_model_that_never_returns_valid_json_leaves_its_reply_for_hand_fixing(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = ReplayClient(["I'm sorry, here is a story instead.", "Still no JSON."], name="fake:bad")
    use_client(monkeypatch, fake)
    res = build(script, tmp_path, "--llm", "fake:bad", "--repairs", "1", "--spec-only")
    assert res.exit_code == 1
    assert len(fake.requests) == 2  # one try + one repair, no more
    raw = tmp_path / "reel.spec.raw.txt"
    assert raw.read_text() == "Still no JSON."
    assert "reel render" in flat(res.output)


def test_a_bad_llm_spec_is_a_clean_error_not_a_traceback(script: Path, tmp_path: Path) -> None:
    res = build(script, tmp_path, "--llm", "carrier-pigeon:fast", "--spec-only")
    assert res.exit_code == 2
    text = flat(res.output)
    assert "carrier-pigeon" in text and "provider" in text
    assert "Traceback" not in text


def test_a_missing_or_empty_script_is_reported(tmp_path: Path) -> None:
    missing = runner.invoke(app, ["build", str(tmp_path / "nope.txt"), "--spec-only"])
    assert missing.exit_code == 2 and "cannot read" in flat(missing.output)
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n")
    res = runner.invoke(app, ["build", str(empty), "--spec-only"])
    assert res.exit_code == 2 and "empty" in flat(res.output)


def test_the_script_can_come_from_stdin(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["build", "-", "--spec-only", "-o", str(tmp_path / "piped.mp4")], input=SCRIPT
    )
    assert res.exit_code == 0, res.output
    assert (tmp_path / "piped.spec.json").exists()


@pytest.mark.render
@pytest.mark.ffmpeg
def test_build_renders_a_video_end_to_end(script: Path, tmp_path: Path) -> None:
    res = build(
        script,
        tmp_path,
        "--preview",
        "--no-audio",
        "--range",
        "0:1",
        "-j",
        "1",
        "--cache-dir",
        str(tmp_path / "cache"),
    )
    assert res.exit_code == 0, res.output
    video = tmp_path / "reel.mp4"
    assert video.exists() and video.stat().st_size > 1000
    assert (
        tmp_path / "reel.spec.json"
    ).exists()  # the spec is kept so it can be edited and re-rendered


# ------------------------------------------------------------------ your own OpenAI / Claude key
def test_the_default_model_and_keys_can_live_in_a_dotenv_file(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "keys.env"
    env_file.write_text("REEL_LLM=fake:model\nOPENAI_API_KEY=sk-not-used-here\n")
    monkeypatch.setenv("REEL_ENV_FILE", str(env_file))
    asked = use_client(monkeypatch, ReplayClient([scripted_spec()]))
    res = build(script, tmp_path, "--spec-only")
    assert res.exit_code == 0, res.output
    assert asked == ["fake:model"]  # no --llm and no shell variable: the file's REEL_LLM was used
    assert "sk-not-used-here" not in res.output  # a key value is never printed


def test_without_a_model_the_hint_lists_what_is_ready(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REEL_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-used-here")
    monkeypatch.setattr("reel.cli.build.ollama_models", lambda timeout=0.6: ["gemma2:2b"])
    res = build(script, tmp_path, "--spec-only")
    assert res.exit_code == 0, res.output
    text = flat(res.output)
    assert "offline planner" in text and "--llm openai" in text and "--llm ollama:gemma2:2b" in text
    assert "sk-not-used-here" not in text


def test_doctor_can_test_a_model_with_one_tiny_request(monkeypatch: pytest.MonkeyPatch) -> None:
    use_client(monkeypatch, ReplayClient(["OK"], name="fake:model"))
    ok = runner.invoke(app, ["doctor", "--llm", "fake:model"])
    assert "model test ok" in flat(ok.output) and "fake:model" in ok.output
    use_client(monkeypatch, DownClient())
    bad = runner.invoke(app, ["doctor", "--llm", "down:model"])
    assert bad.exit_code == 1 and "model test failed" in flat(bad.output)
    assert "connection refused" in flat(bad.output)


def test_a_custom_destination_is_always_shown_and_credentials_never_are(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a base URL (from an env var, a file, a typo) sends the key somewhere unusual, the build says where."""
    from reel.llm.client import OpenAICompatClient

    def answer(request: Any) -> Any:
        import httpx

        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(scripted_spec())}, "finish_reason": "stop"}
                ]
            },
        )

    import httpx

    client = OpenAICompatClient(
        "m",
        "http://user:hunter2@127.0.0.1:18765/v1",
        api_key="sk-not-used-here",
        transport=httpx.MockTransport(answer),
    )
    use_client(monkeypatch, client)
    res = build(script, tmp_path, "--llm", "openai:m", "--spec-only")
    assert res.exit_code == 0, res.output
    text = flat(res.output)
    assert "127.0.0.1:18765" in text and "hunter2" not in text and "sk-not-used-here" not in text


def test_doctor_shows_hosts_not_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "keys.env"
    f.write_text(
        "OPENAI_BASE_URL=http://user:hunter2@proxy.example:4000/v1\nREEL_LLM=openai:m@http://user:hunter2@proxy.example:4000/v1\n"
    )
    monkeypatch.setenv("REEL_ENV_FILE", str(f))
    monkeypatch.setattr("reel.cli.build.ollama_models", lambda timeout=0.6: None)
    res = runner.invoke(app, ["doctor"])
    assert "hunter2" not in res.output and "proxy.example:4000" in flat(res.output)


def test_error_text_is_not_parsed_as_console_markup(
    script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Noisy(LLMClient):
        name = "noisy:model"

        def complete(self, system: str, messages: list[Message], **kw: Any) -> str:
            raise LLMUnavailable("the server said [/INST] and [red]no[/red] and [type=oops]")

    use_client(monkeypatch, Noisy())
    res = build(script, tmp_path, "--llm", "noisy:model", "--spec-only")
    assert res.exit_code == 2 and "MarkupError" not in res.output
    assert "[/INST]" in flat(res.output) and "[type=oops]" in flat(res.output)


def test_a_script_that_is_not_utf8_is_reported_not_a_traceback(tmp_path: Path) -> None:
    bad = tmp_path / "latin1.txt"
    bad.write_bytes("Caf\xe9 au lait, s'il vous pla\xeet.".encode("latin-1"))
    res = runner.invoke(app, ["build", str(bad), "--spec-only", "-o", str(tmp_path / "x.mp4")])
    assert (
        res.exit_code == 2 and "utf-8" in flat(res.output).lower() and "Traceback" not in res.output
    )


def test_the_app_never_prints_local_variables_of_a_crash() -> None:
    assert app.pretty_exceptions_show_locals is False
