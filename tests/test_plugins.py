"""Extensibility: plugin actions and a scaffolded style pack work without touching the package."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from reel.cli.main import app
from reel.core.catalog import CATALOG

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


@pytest.fixture
def clean_registry() -> object:
    before = {k: set(r.names()) for k, r in CATALOG.all_registries().items()}
    modules_before = set(sys.modules)
    yield None
    for kind, reg in CATALOG.all_registries().items():
        for name in set(reg.names()) - before[kind]:
            reg.unregister(name)
    for mod in (
        set(sys.modules) - modules_before
    ):  # plugins are imported once per process: forget them
        if mod.startswith("reel_plugin_") or mod == "pencil":
            del sys.modules[mod]


def test_example_plugin_action_loads_bakes_and_lints(clean_registry: object) -> None:
    assert "shrug" not in CATALOG.actions
    CATALOG.load_plugins([str(ROOT / "examples" / "plugins")])
    assert "shrug" in CATALOG.actions
    from reel.core.debug import bake_action
    from reel.core.lint import LintOptions, lint_data

    baked, t0, t1 = bake_action("shrug")
    mid = baked.pose(baked.frame_index((t0 + t1) / 2))
    assert mid["arm_r_el"] > 60 and mid["head_tilt"] < -4 and mid["brow_raise"] > 0.3
    spec = {
        "meta": {"title": "t", "style": "flat_vector"},
        "characters": [{"id": "a", "archetype": "everyman"}],
        "scenes": [
            {
                "id": "s",
                "duration_sec": 4,
                "background": {"template": "abstract"},
                "layers": [
                    {
                        "character": "a",
                        "position": "center",
                        "actions": [
                            {"name": "shrug", "t0": 0.5, "t1": 2.0, "params": {"hold": 0.4}}
                        ],
                    }
                ],
            }
        ],
    }
    assert lint_data(spec, options=LintOptions(check_duration=False)).ok
    spec["scenes"][0]["layers"][0]["actions"][0]["params"] = {"holdd": 1}
    assert "PARAMS_INVALID" in lint_data(spec, options=LintOptions(check_duration=False)).codes()


def test_cli_plugin_flag_exposes_the_plugin(clean_registry: object) -> None:
    res = runner.invoke(
        app, ["list", "actions", "--plugin", str(ROOT / "examples" / "plugins" / "shrug.py")]
    )
    assert res.exit_code == 0 and "shrug" in res.output


def test_scaffolded_style_is_a_working_style_pack(tmp_path: Path, clean_registry: object) -> None:
    res = runner.invoke(app, ["new-style", "pencil", "--dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "pencil" / "__init__.py").exists()
    CATALOG.load_plugins([str(tmp_path / "pencil")])
    assert "pencil" in CATALOG.styles
    from reel.core.render import Renderer, RenderOptions
    from reel.core.spec import ReelSpec

    spec = ReelSpec.model_validate(
        {
            "meta": {"title": "t", "style": "pencil"},
            "characters": [{"id": "a", "archetype": "kid"}],
            "scenes": [
                {
                    "id": "s",
                    "duration_sec": 2,
                    "background": {"template": "abstract"},
                    "layers": [
                        {
                            "character": "a",
                            "position": "center",
                            "actions": [{"name": "wave", "t0": 0.2, "t1": 1.5}],
                        }
                    ],
                }
            ],
        }
    )
    f = Renderer(spec, RenderOptions(scale=0.15, cache=False)).frame(20)
    assert f[..., :3].std() > 3  # the inherited defaults paint a recognisable frame


def test_style_override_needs_no_spec_change(clean_registry: object) -> None:
    from reel.core.lint import LintOptions, lint_data

    spec = {
        "meta": {"title": "t", "style": "flat_vector"},
        "characters": [],
        "scenes": [{"id": "s", "duration_sec": 3, "background": {"template": "abstract"}}],
    }
    for style in CATALOG.styles.names():
        assert lint_data(spec, options=LintOptions(check_duration=False, style_override=style)).ok
    np.testing.assert_equal(
        sorted(CATALOG.styles.names())[:3], ["flat_vector", "paper_cutout", "stickman"]
    )
