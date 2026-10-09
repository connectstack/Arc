"""CLI behaviour through typer's runner (exercises the real registries)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reel.cli.main import app
from reel.core.catalog import CATALOG

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def test_list_actions_shows_all_starters() -> None:
    res = runner.invoke(app, ["list", "actions"])
    assert res.exit_code == 0, res.output
    for name in (
        "idle",
        "walk",
        "run",
        "jump",
        "wave",
        "point",
        "talk",
        "laugh",
        "cry",
        "think",
        "fall",
        "pick_up",
        "enter_from",
        "exit_to",
        "look_at",
        "surprise",
    ):
        assert name in res.output


def test_list_json_is_machine_readable_and_includes_params() -> None:
    res = runner.invoke(app, ["list", "actions", "--json"])
    rows = json.loads(res.output)
    walk = next(r for r in rows if r["name"] == "walk")
    assert walk["category"] == "locomotion" and walk["moves_root"] is True
    assert {p["name"] for p in walk["params"]} >= {"to", "dx", "style"}


def test_list_unknown_thing_fails_cleanly() -> None:
    res = runner.invoke(app, ["list", "unicorns"])
    assert res.exit_code == 2


def test_lint_json_on_broken_spec_exits_nonzero_and_lists_missing() -> None:
    res = runner.invoke(app, ["lint", str(FIXTURES / "broken_spec.json"), "--json"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False
    assert "moonwalk" in data["missing"]["action"]
    assert "spaceship" in data["missing"]["background"]


def test_lint_human_report_names_what_to_add() -> None:
    res = runner.invoke(app, ["lint", str(FIXTURES / "broken_spec.json")])
    assert res.exit_code == 1
    assert "Missing from the registry" in res.output
    assert "reel new-action moonwalk" in res.output


def test_lint_missing_file_is_a_usage_error() -> None:
    res = runner.invoke(app, ["lint", "does_not_exist.json"])
    assert res.exit_code != 0


def test_schema_and_manifest_commands(tmp_path: Path) -> None:
    out = tmp_path / "s.json"
    assert runner.invoke(app, ["schema", "-o", str(out)]).exit_code == 0
    assert json.loads(out.read_text())["title"] == "Reel scene spec"
    pinned = runner.invoke(app, ["schema", "--enums"])
    assert '"walk"' in pinned.output
    man = runner.invoke(app, ["manifest"])
    data = json.loads(man.output)
    assert {a["name"] for a in data["actions"]} >= {"walk", "wave"}
    assert {a["name"] for a in data["archetypes"]} >= {"everyman", "kid", "robot"}


def test_generated_files_are_fresh() -> None:
    res = runner.invoke(app, ["schema", "--check"])
    assert res.exit_code == 0, res.output


def test_new_action_scaffold_loads_as_a_plugin_and_bakes(tmp_path: Path) -> None:
    res = runner.invoke(app, ["new-action", "moonwalk", "--dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    f = tmp_path / "moonwalk.py"
    assert f.exists() and "register_action" in f.read_text()
    assert "moonwalk" not in CATALOG.actions  # scaffolding alone must not register anything

    CATALOG.load_plugins([str(f)])
    try:
        assert "moonwalk" in CATALOG.actions
        from reel.core.debug import bake_action

        baked, _, _ = bake_action("moonwalk")
        assert baked.n > 0
        listed = runner.invoke(app, ["list", "actions", "--json"])
        assert any(r["name"] == "moonwalk" for r in json.loads(listed.output))
        again = runner.invoke(app, ["new-action", "moonwalk", "--dir", str(tmp_path)])
        assert again.exit_code == 1  # already registered: refuses
    finally:
        CATALOG.actions.unregister("moonwalk")


def test_new_action_rejects_bad_names_and_existing_files(tmp_path: Path) -> None:
    assert runner.invoke(app, ["new-action", "Moon-Walk", "--dir", str(tmp_path)]).exit_code == 1
    assert runner.invoke(app, ["new-action", "class", "--dir", str(tmp_path)]).exit_code == 1
    assert runner.invoke(app, ["new-action", "dance", "--dir", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["new-action", "dance", "--dir", str(tmp_path)]).exit_code == 1
    assert (
        runner.invoke(app, ["new-action", "dance", "--dir", str(tmp_path), "--force"]).exit_code
        == 0
    )


def test_debug_actions_writes_a_png(tmp_path: Path) -> None:
    out = tmp_path / "sheet.png"
    res = runner.invoke(app, ["debug-actions", "walk", "wave", "-o", str(out)])
    assert res.exit_code == 0, res.output
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    bad = runner.invoke(app, ["debug-actions", "levitate", "-o", str(out)])
    assert bad.exit_code == 1 and "unknown action" in bad.output


def test_bitrate_cap_defaults_and_overrides() -> None:
    """Full renders are capped (grain makes plain CRF output 30+ Mbps); previews and `none` are not."""
    from reel.cli.pipeline import RenderRequest, _maxrate

    full = RenderRequest(spec_path=Path("x.json"))
    assert _maxrate(full) == "10M"
    assert _maxrate(RenderRequest(spec_path=Path("x.json"), preview=True)) is None
    assert _maxrate(RenderRequest(spec_path=Path("x.json"), maxrate="16M")) == "16M"
    for off in ("none", "NONE", "0", "off", ""):
        assert _maxrate(RenderRequest(spec_path=Path("x.json"), maxrate=off)) is None


def test_render_options_reach_the_segment_cache_key(tmp_path: Path) -> None:
    """Changing the bitrate cap must not reuse chunks encoded with another one."""
    from reel.core.render import Renderer, RenderOptions
    from reel.core.spec import ReelSpec

    spec = ReelSpec.from_file(Path(__file__).parents[1] / "examples" / "explainer_45s.json")
    keys = {
        cap: [
            s.key
            for s in Renderer(
                spec,
                RenderOptions(scale=0.1, cache_dir=str(tmp_path), frame_range=(0, 30), maxrate=cap),
            ).plan_segments()
        ]
        for cap in ("10M", "4M", None)
    }
    assert len({tuple(v) for v in keys.values()}) == 3


def test_generated_reference_tables_are_well_formed(tmp_path: Path) -> None:
    """Regression: enum cells were double-escaped (`\\\\|`), which split the table into extra columns."""
    import re

    res = runner.invoke(app, ["reference", "-o", str(tmp_path)])
    assert res.exit_code == 0, res.output
    pages = sorted(tmp_path.glob("*.md"))
    assert len(pages) >= 10
    for page in pages:
        header_cells = None
        for line in page.read_text().splitlines():
            if not line.startswith("|"):
                header_cells = None
                continue
            cells = len(re.findall(r"(?<!\\)\|", line)) - 1
            if header_cells is None:
                header_cells = cells
            assert cells == header_cells, f"{page.name}: ragged table row: {line[:100]}"
            assert "\\\\|" not in line, f"{page.name}: double-escaped pipe: {line[:100]}"


def test_bitrate_suffixes_are_normalised_because_ffmpeg_reads_a_lowercase_m_as_milli() -> None:
    """Regression: `--maxrate 10m` reached ffmpeg as 0.01 bits/s, so the cap was silently off."""
    from reel.cli.pipeline import parse_rate

    assert (
        parse_rate("10M") == "10M"
        and parse_rate("10m") == "10M"
        and parse_rate(" 8000k ") == "8000k"
    )
    assert (
        parse_rate("8000K") == "8000k"
        and parse_rate("12000000") == "12000000"
        and parse_rate("1.5M") == "1.5M"
    )
    assert parse_rate("2g") == "2G" and parse_rate("10Mbps") == "10M"
    for off in ("none", "NONE", "0", "off", "", "  "):
        assert parse_rate(off) is None
    for bad in ("fast", "10 megabits", "-5M", "M10", "1e3"):
        with pytest.raises(ValueError, match="cannot read the bitrate"):
            parse_rate(bad)


def test_an_unreadable_maxrate_is_a_usage_error(tmp_path: Path) -> None:
    res = runner.invoke(app, ["render", str(FIXTURES / "semantic_spec.json"), "--maxrate", "fast"])
    assert res.exit_code == 2 and "bitrate" in res.output
