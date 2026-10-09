"""The asset library: finding assets on disk, putting them in the catalog, and drawing them (objects, character pictures, places)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from reel.assets.art import load_art
from reel.assets.library import (
    BUILTIN_DIR,
    asset_from_files,
    discover,
    load_dirs,
    register_asset,
    reset_assets,
)
from reel.assets.model import KINDS, NAME_RE, slug
from reel.assets.preview import preview_spec, render_preview
from reel.cli.main import app
from reel.core.catalog import CATALOG, Catalog
from reel.core.lint import LintOptions, lint_data
from reel.core.render import Renderer, RenderOptions
from reel.core.spec import ReelSpec

NS = 'xmlns="http://www.w3.org/2000/svg"'
RED = "#e63946"
SQUARE = f'<svg {NS} viewBox="0 0 100 100"><rect data-role="body" x="0" y="0" width="100" height="100" fill="{RED}"/></svg>'
WEDGE = f'<svg {NS} viewBox="0 0 100 100"><path d="M0 0 L100 0 L0 100 Z" fill="{RED}"/></svg>'
runner = CliRunner()


def write(root: Path, folder: str, name: str, svg: str = SQUARE, **sidecar: Any) -> Path:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    art = d / f"{name}.svg"
    art.write_text(svg, encoding="utf-8")
    if sidecar:
        (d / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return art


@pytest.fixture
def cat() -> Catalog:
    """The real catalog, copied: an asset added in a test never reaches another test."""
    return CATALOG.fork()


def scene_spec(
    *,
    objects: list[dict[str, Any]] | None = None,
    characters: list[dict[str, Any]] | None = None,
    layers: list[dict[str, Any]] | None = None,
    background: dict[str, Any] | None = None,
    style: str = "flat_vector",
    duration: float = 4.0,
) -> dict[str, Any]:
    return {
        "version": "1.0",
        "meta": {"title": "t", "style": style, "target_duration_sec": 50},
        "characters": characters or [],
        "scenes": [
            {
                "id": "s1",
                "duration_sec": duration,
                "background": background or {"template": "abstract"},
                "layers": layers or [],
                "objects": objects or [],
                "captions": [],
            }
        ],
    }


def lint(spec: dict[str, Any], cat: Catalog) -> Any:
    """The lint report of a one-scene test spec (its length is not what is being tested)."""
    return lint_data(spec, catalog=cat, options=LintOptions(check_duration=False))


def frame(spec: dict[str, Any], cat: Catalog, n: int = 15, scale: float = 0.25) -> np.ndarray:
    model = ReelSpec.model_validate(spec)
    return Renderer(
        model, RenderOptions(scale=scale, cache=False, lenient=False), catalog=cat
    ).frame(n)


def at(img: np.ndarray, x: float, y: float) -> tuple[int, int, int]:
    """BGR of the pixel at screen fractions (x, y)."""
    h, w = img.shape[:2]
    px = img[int(y * h), int(x * w)]
    return int(px[0]), int(px[1]), int(px[2])


def is_red(bgr: tuple[int, int, int], tol: int = 40) -> bool:
    b, g, r = bgr
    return abs(b - 0x46) <= tol and abs(g - 0x39) <= tol and abs(r - 0xE6) <= tol


# ======================================================================= finding assets on disk
class TestDiscovery:
    def test_folders_set_the_kind_and_a_loose_file_is_an_object(self, tmp_path: Path) -> None:
        write(tmp_path, "characters", "pal")
        write(tmp_path, "places", "town")
        write(tmp_path, "objects", "box")
        write(tmp_path, ".", "loose")
        found = discover(tmp_path)
        kinds = {a.name: a.kind for a in found.assets}
        assert kinds == {"pal": "character", "town": "place", "box": "object", "loose": "object"}
        assert not found.problems

    def test_a_sidecar_sets_the_facts_and_the_name_is_always_a_tag(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "objects",
            "gadget_x",
            kind="character",
            summary="A small gadget",
            tags=["Gizmo", "gizmo", "ग़ैजेट"],
            height=123,
            anchor=[0.5, 0.5],
            facing="left",
            credit="me, CC0",
        )
        (a,) = discover(tmp_path).assets
        assert (a.name, a.kind, a.height, a.anchor, a.facing) == (
            "gadget_x",
            "character",
            123.0,
            (0.5, 0.5),
            "left",
        )
        assert a.tags == ("gadget x", "gizmo", "ग़ैजेट")  # lower-cased, deduplicated, the name first
        assert a.summary == "A small gadget" and a.credit == "me, CC0" and a.origin == "user"

    def test_a_place_always_gets_ground_facts(self, tmp_path: Path) -> None:
        write(tmp_path, "places", "town")
        (a,) = discover(tmp_path).assets
        assert a.place is not None and 0.3 <= a.place.ground_y <= 1.0

    def test_one_bad_file_never_hides_the_others(self, tmp_path: Path) -> None:
        write(tmp_path, "objects", "good")
        (tmp_path / "objects" / "broken.json").write_text("{not json", encoding="utf-8")
        write(tmp_path, "objects", "broken")
        write(tmp_path, "objects", "Bad Name!")
        (tmp_path / "objects" / "notes.txt").write_text("hello", encoding="utf-8")
        write(tmp_path, "objects", "oddkind", kind="vehicle")
        found = discover(tmp_path)
        assert [a.name for a in found.assets] == [
            "bad_name",
            "good",
        ]  # a name is slugged from the file
        by_file = {p.file.split("/")[-1]: p.message for p in found.problems}
        assert "not valid JSON" in by_file["broken.svg"]
        assert "kind" in by_file["oddkind.svg"]

    def test_two_files_with_one_name_are_reported(self, tmp_path: Path) -> None:
        write(tmp_path, "objects", "twin")
        write(tmp_path, "characters", "twin")
        found = discover(tmp_path)
        assert len(found.assets) == 1
        assert any("already used" in p.message for p in found.problems)

    def test_missing_or_oversized_things_are_said(self, tmp_path: Path) -> None:
        assert discover(tmp_path / "nowhere").problems[0].message.endswith("does not exist")
        art = write(tmp_path, "objects", "huge")
        with open(art, "ab") as f:
            f.write(b" " * (13 * 1024 * 1024))
        (p,) = discover(tmp_path).problems
        assert "MB" in p.message

    def test_hidden_files_and_folders_are_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, ".git", "ghost")
        write(tmp_path / "objects", ".", ".hidden")
        write(tmp_path, "objects", "seen")
        assert [a.name for a in discover(tmp_path).assets] == ["seen"]


def test_a_name_with_nothing_latin_in_it_gets_a_stable_name_of_its_own() -> None:
    dog, cat_ = slug("कुत्ता"), slug("बिल्ली")
    assert dog == slug("कुत्ता") and dog != cat_ and NAME_RE.match(dog) and NAME_RE.match(cat_)
    assert (
        slug("") == "asset"
        and slug("123 car") == "a_123_car"
        and slug("Gadget Pro") == "gadget_pro"
    )


class TestHostileLibraries:
    """A folder of assets somebody else made is data: it may be wrong in any way, but never a way out or a way in."""

    def test_a_sidecar_file_field_must_name_a_file_next_to_it(self, tmp_path: Path) -> None:
        outside = tmp_path / "secret.png"
        outside.write_bytes(b"\x89PNG not really")
        lib = tmp_path / "lib"
        for name, target in (
            ("up", "../secret.png"),
            ("abs", str(outside)),
            ("sub", "deeper/x.svg"),
            ("dots", ".."),
        ):
            write(lib, "objects", name, file=target)
        found = discover(lib)
        assert found.assets == []
        assert len(found.problems) == 4 and all(
            "name of a file next to the sidecar" in p.message for p in found.problems
        )

    def test_a_sidecar_file_field_still_points_at_a_sibling(self, tmp_path: Path) -> None:
        write(tmp_path, "objects", "art_a")
        (tmp_path / "objects" / "alias.json").write_text(
            json.dumps({"file": "art_a.svg", "name": "alias"}), encoding="utf-8"
        )
        (tmp_path / "objects" / "alias.svg").write_text(SQUARE, encoding="utf-8")
        assert {a.name for a in discover(tmp_path).assets} == {"art_a", "alias"}

    def test_a_symbolic_link_is_not_followed(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.svg"
        secret.write_text(SQUARE, encoding="utf-8")
        d = tmp_path / "lib" / "objects"
        d.mkdir(parents=True)
        (d / "link.svg").symlink_to(secret)
        found = discover(tmp_path / "lib")
        assert found.assets == [] and "symbolic link" in found.problems[0].message

    @pytest.mark.parametrize(
        "svg",
        [
            b'<?xml version="1.0" encoding="gb2312"?><svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5" transform="rotate(1e999)" fill="rgb(1e999,0,0)"/><rect width="1e999" height="5"/></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1e999 1e999"><rect width="5" height="5"/></svg>',
        ],
    )
    def test_a_file_that_trips_the_importer_is_a_problem_not_a_crash(
        self, tmp_path: Path, svg: bytes, cat: Catalog
    ) -> None:
        d = tmp_path / "objects"
        d.mkdir()
        (d / "odd.svg").write_bytes(svg)
        write(tmp_path, "objects", "fine")
        found = reset_assets(
            cat, [tmp_path], validate=True
        )  # what the server does at start: it must still start
        names = {a.name for a in found.assets}
        assert "fine" in names
        if "odd" in names:  # parsed with the bad numbers left out
            assert all(abs(v) < 1e9 for v in load_art(cat.assets.get("odd")).bounds)
        else:
            assert any(p.file == "odd.svg" for p in found.problems)

    def test_a_broken_override_never_replaces_a_built_in_asset_whoever_loads_the_folder(
        self, tmp_path: Path, cat: Catalog
    ) -> None:
        d = tmp_path / "objects"
        d.mkdir()
        (d / "car.svg").write_bytes(
            b'<?xml version="1.0" encoding="gb2312"?><svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>'
        )
        found = load_dirs([tmp_path], cat)  # what a command or a job does
        assert [p.file for p in found.problems] == ["car.svg"] and found.assets == []
        assert cat.assets.get("car").origin == "builtin"
        cat2 = CATALOG.fork()
        assert [p.file for p in reset_assets(cat2, [tmp_path], validate=True).problems] == [
            "car.svg"
        ]  # the server
        assert cat2.assets.get("car").origin == "builtin"

    def test_sidecar_numbers_must_be_ordinary(self, tmp_path: Path) -> None:
        write(tmp_path, "objects", "far", anchor=[1e300, 0.5])
        write(tmp_path, "places", "slotty", place={"slots": {"left": [1e300, 0.5]}})
        write(tmp_path, "objects", "ok_one", anchor=[0.5, 0.5])
        found = discover(tmp_path)
        assert [a.name for a in found.assets] == ["ok_one"]
        assert len(found.problems) == 2

    def test_a_name_with_a_trailing_newline_is_not_a_name(self, tmp_path: Path) -> None:
        assert not NAME_RE.match("cat\n") and NAME_RE.match("cat")
        write(tmp_path, "objects", "newline")
        (tmp_path / "objects" / "newline.json").write_text(
            json.dumps({"name": "cat\n"}), encoding="utf-8"
        )
        found = discover(tmp_path)
        assert found.assets == [] and len(found.problems) == 1

    def test_a_deeply_nested_sidecar_is_a_problem(self, tmp_path: Path) -> None:
        write(tmp_path, "objects", "deep")
        (tmp_path / "objects" / "deep.json").write_text(
            "[" * 100_000 + "]" * 100_000, encoding="utf-8"
        )
        found = discover(tmp_path)
        assert found.assets == [] and len(found.problems) == 1

    def test_forgetting_an_asset_frees_its_decoded_picture(self, tmp_path: Path) -> None:
        from PIL import Image

        from reel.assets.art import forget
        from reel.assets.raster import _IMAGES

        d = tmp_path / "objects"
        d.mkdir()
        pic = Image.new("RGBA", (60, 60), (255, 255, 255, 255))
        pic.paste(
            (200, 30, 30, 255), (15, 15, 45, 45)
        )  # something on a plain background, so something is left
        pic.save(d / "pic.png")
        (a,) = discover(tmp_path).assets
        before = len(_IMAGES)
        load_art(a)
        assert len(_IMAGES) == before + 1
        forget(a)
        assert len(_IMAGES) == before


# ======================================================================= the catalog
class TestRegistration:
    def test_each_kind_lands_in_its_registry(self, tmp_path: Path, cat: Catalog) -> None:
        write(tmp_path, "objects", "widget")
        write(tmp_path, "characters", "blob")
        write(tmp_path, "places", "plaza")
        found = load_dirs([tmp_path], cat)
        assert not found.problems
        assert "widget" in cat.objects and "blob" in cat.archetypes and "plaza" in cat.backgrounds
        assert {"widget", "blob", "plaza"} <= set(cat.assets.names())
        blob = cat.archetypes.get("blob")
        assert blob.category == "sprite" and blob.features["asset"].name == "blob"

    def test_a_later_folder_replaces_an_earlier_asset_of_the_same_name(
        self, tmp_path: Path, cat: Catalog
    ) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        write(a, "objects", "thing", summary="first")
        write(b, "objects", "thing", summary="second")
        load_dirs([a, b], cat)
        assert cat.assets.get("thing").summary == "second"

    def test_a_user_asset_can_replace_a_built_in_one(self, tmp_path: Path, cat: Catalog) -> None:
        write(tmp_path, "objects", "tree", summary="My own tree")
        load_dirs([tmp_path], cat)
        assert cat.assets.get("tree").origin == "user"
        assert cat.assets.get("tree").summary == "My own tree"

    @pytest.mark.parametrize(
        ("kind", "name"), [("place", "forest"), ("character", "hero"), ("character", "robot")]
    )
    def test_the_engines_own_names_are_taken(
        self, tmp_path: Path, cat: Catalog, kind: str, name: str
    ) -> None:
        write(tmp_path, {"place": "places", "character": "characters"}[kind], name)
        found = load_dirs([tmp_path], cat)
        assert any("taken by the engine" in p.message for p in found.problems)
        engine = (cat.backgrounds if kind == "place" else cat.archetypes).get(name)
        assert (
            getattr(engine, "category", "") != "sprite" and getattr(engine, "asset", None) is None
        )

    def test_reset_brings_back_a_replaced_built_in_and_forgets_deleted_files(
        self, tmp_path: Path, cat: Catalog
    ) -> None:
        mine = write(tmp_path, "objects", "tree", summary="mine")
        write(tmp_path, "objects", "extra")
        reset_assets(cat, [tmp_path])
        assert cat.assets.get("tree").origin == "user" and "extra" in cat.objects
        mine.unlink()
        mine.with_suffix(".json").unlink()
        (tmp_path / "objects" / "extra.svg").unlink()
        reset_assets(cat, [tmp_path])
        assert cat.assets.get("tree").origin == "builtin" and "extra" not in cat.assets

    def test_validation_leaves_out_art_that_cannot_be_read_and_keeps_the_built_in(
        self, tmp_path: Path, cat: Catalog
    ) -> None:
        (tmp_path / "objects").mkdir()
        (tmp_path / "objects" / "tree.svg").write_text(
            "<svg", encoding="utf-8"
        )  # would replace the built-in tree
        (tmp_path / "objects" / "phantomx.svg").write_text("<svg", encoding="utf-8")
        write(tmp_path, "objects", "fine")
        found = reset_assets(cat, [tmp_path], validate=True)
        assert {p.file for p in found.problems} == {"tree.svg", "phantomx.svg"}
        assert (
            cat.assets.get("tree").origin == "builtin"
            and "phantomx" not in cat.assets
            and "fine" in cat.objects
        )

    def test_reset_never_empties_the_library(self, cat: Catalog) -> None:
        before = set(cat.assets.names())
        seen: list[int] = []
        original = cat.assets.unregister

        def spy(name: str) -> None:
            seen.append(len(cat.assets.names()))
            original(name)

        cat.assets.unregister = spy  # type: ignore[method-assign]
        reset_assets(cat, [])
        assert set(cat.assets.names()) == before and not seen  # nothing was removed and re-added

    def test_a_kind_change_moves_the_asset_between_registries(
        self, tmp_path: Path, cat: Catalog
    ) -> None:
        a = write(tmp_path, "objects", "shifty")
        load_dirs([tmp_path], cat)
        assert "shifty" in cat.objects
        a.with_suffix(".json").write_text(json.dumps({"kind": "character"}), encoding="utf-8")
        reset_assets(cat, [tmp_path])
        assert "shifty" in cat.archetypes and "shifty" not in cat.objects


# ======================================================================= the built-in library
BUILTIN = discover(BUILTIN_DIR, origin="builtin")


class TestBuiltInLibrary:
    def test_every_file_reads_without_problems(self) -> None:
        assert not BUILTIN.problems, [str(p) for p in BUILTIN.problems]
        assert len(BUILTIN.assets) >= 4

    def test_names_summaries_sizes_and_anchors_are_sane(self) -> None:
        for a in BUILTIN.assets:
            assert NAME_RE.match(a.name), a.name
            assert 0 < len(a.summary) <= 120, a.name
            assert (
                a.kind in KINDS and a.height > 0 and 0 <= a.anchor[0] <= 1 and 0 <= a.anchor[1] <= 1
            )
            assert a.origin == "builtin"
            if a.kind == "place":
                assert a.place is not None and {"left", "center", "right"} <= set(a.place.slots)

    def test_every_asset_has_words_a_script_can_use(self) -> None:
        for a in BUILTIN.assets:
            assert len(a.tags) >= 3, f"{a.name}: give it synonyms and Hindi words"
        with_hindi = [a for a in BUILTIN.assets if any(re.search("[ऀ-ॿ]", t) for t in a.tags)]
        assert len(with_hindi) >= 0.9 * len(BUILTIN.assets)

    def test_no_asset_claims_the_name_of_another(self) -> None:
        """A category word ("fruit", "vehicle") may be shared; an asset's own name belongs to that asset alone."""
        names = {(a.kind, a.name.replace("_", " ")): a.name for a in BUILTIN.assets}
        clashes = [
            f"{a.name} claims {t!r}, the name of {names[(a.kind, t)]}"
            for a in BUILTIN.assets
            for t in a.tags
            if (a.kind, t) in names and names[(a.kind, t)] != a.name
        ]
        assert not clashes, clashes

    def test_every_drawing_reads_cleanly(self) -> None:
        for a in BUILTIN.assets:
            art = load_art(a)
            assert not art.warnings, f"{a.name}: {art.warnings}"
            assert len(art.shapes) >= 3 and art.width > 0 and art.height > 0

    def test_eyes_and_main_parts_survive_line_art(self) -> None:
        """A character's drawing keeps shapes in the first detail level (the stickman style draws only those)."""
        for a in BUILTIN.assets:
            if a.kind == "character":
                art = load_art(a)
                assert sum(1 for s in art.shapes if s.lod == 0) >= 5, a.name

    def test_the_wider_library_is_there(self) -> None:
        names = {a.name for a in BUILTIN.assets}
        assert {"car", "tree", "cat", "beach"} <= names


@pytest.mark.render
@pytest.mark.slow
@pytest.mark.parametrize("style", ["paper_cutout", "flat_vector", "stickman"])
def test_every_built_in_asset_draws_in_every_style(style: str) -> None:
    for a in BUILTIN.assets:
        img = render_preview(a, style, scale=0.1)
        assert img.shape[0] > 0 and img.std() > 3, f"{a.name} in {style} looks empty"
        again = render_preview(a, style, scale=0.1, true_scale=True)
        assert again.std() > 3, f"{a.name} next to a person in {style} looks empty"


# ======================================================================= objects in scenes
@pytest.mark.render
class TestObjects:
    def lib(self, tmp_path: Path, cat: Catalog, **sidecar: Any) -> Path:
        write(tmp_path, "objects", "block", height=300, **sidecar)
        load_dirs([tmp_path], cat)
        return tmp_path

    def test_an_object_is_drawn_where_it_stands(self, tmp_path: Path, cat: Catalog) -> None:
        self.lib(tmp_path, cat)
        bare = frame(scene_spec(), cat)
        shown = frame(scene_spec(objects=[{"asset": "block", "position": [0.5, 0.8]}]), cat)
        assert not is_red(at(bare, 0.5, 0.7))
        assert is_red(at(shown, 0.5, 0.7))  # bottom-centre anchor: the art rises from y 0.8
        assert not is_red(at(shown, 0.5, 0.83))  # and nothing below the standing point

    def test_scale_and_position_move_and_size_it(self, tmp_path: Path, cat: Catalog) -> None:
        self.lib(tmp_path, cat)
        small = frame(
            scene_spec(objects=[{"asset": "block", "position": [0.25, 0.8], "scale": 0.4}]), cat
        )
        assert is_red(at(small, 0.25, 0.78)) and not is_red(at(small, 0.75, 0.7))
        assert not is_red(at(small, 0.25, 0.6))  # 0.4 x 300 design px is a small block

    def test_a_palette_recolours_a_marked_role(self, tmp_path: Path, cat: Catalog) -> None:
        self.lib(tmp_path, cat)
        blue = frame(
            scene_spec(
                objects=[{"asset": "block", "position": [0.5, 0.8], "palette": {"body": "#2a6fdb"}}]
            ),
            cat,
        )
        b, _g, r = at(blue, 0.5, 0.7)
        assert b > r + 60

    def test_facing_left_mirrors_art_that_faces_right(self, tmp_path: Path, cat: Catalog) -> None:
        write(tmp_path, "objects", "wedge", WEDGE, height=300)
        load_dirs([tmp_path], cat)
        right = frame(scene_spec(objects=[{"asset": "wedge", "position": [0.5, 0.8]}]), cat)
        left = frame(
            scene_spec(objects=[{"asset": "wedge", "position": [0.5, 0.8], "facing": "left"}]), cat
        )
        # the wedge is filled along its top and down its left edge: mirrored, down its right edge instead
        assert is_red(at(right, 0.376, 0.765)) and not is_red(at(right, 0.624, 0.765))
        assert is_red(at(left, 0.624, 0.765)) and not is_red(at(left, 0.376, 0.765))

    def test_a_move_carries_it_across_the_frame(self, tmp_path: Path, cat: Catalog) -> None:
        self.lib(tmp_path, cat)
        spec = scene_spec(
            objects=[
                {
                    "asset": "block",
                    "position": [0.2, 0.8],
                    "scale": 0.5,
                    "motions": [{"type": "move", "t0": 0, "t1": 2, "to": [0.8, 0.8]}],
                }
            ]
        )
        start, mid, end = (frame(spec, cat, n) for n in (0, 30, 75))
        assert is_red(at(start, 0.2, 0.75)) and not is_red(at(start, 0.8, 0.75))
        assert is_red(at(mid, 0.5, 0.75))
        assert is_red(at(end, 0.8, 0.75)) and not is_red(at(end, 0.2, 0.75))  # and it stays there

    def test_visibility_follows_t0_and_t1(self, tmp_path: Path, cat: Catalog) -> None:
        self.lib(tmp_path, cat)
        spec = scene_spec(objects=[{"asset": "block", "position": [0.5, 0.8], "t0": 1, "t1": 2}])
        before, during, after = (frame(spec, cat, n) for n in (15, 45, 75))
        assert not is_red(at(before, 0.5, 0.7)) and is_red(at(during, 0.5, 0.7))
        assert not is_red(at(after, 0.5, 0.7))

    def test_fade_and_grow_change_opacity_and_size(self, tmp_path: Path, cat: Catalog) -> None:
        self.lib(tmp_path, cat)
        fade = scene_spec(
            objects=[
                {
                    "asset": "block",
                    "position": [0.5, 0.8],
                    "motions": [{"type": "fade", "t0": 0, "t1": 2}],
                }
            ]
        )
        assert not is_red(at(frame(fade, cat, 0), 0.5, 0.7)) and is_red(
            at(frame(fade, cat, 75), 0.5, 0.7)
        )
        grow = scene_spec(
            objects=[
                {
                    "asset": "block",
                    "position": [0.5, 0.8],
                    "motions": [{"type": "grow", "t0": 0, "t1": 2, "from": 0.2}],
                }
            ]
        )
        assert not is_red(at(frame(grow, cat, 0), 0.5, 0.7)) and is_red(
            at(frame(grow, cat, 75), 0.5, 0.7)
        )

    def test_a_grow_that_has_not_begun_is_at_the_size_it_starts_from(
        self, tmp_path: Path, cat: Catalog
    ) -> None:
        """Before its start a grow with ``from`` stands at that size (it used to be full size, then jump)."""
        self.lib(tmp_path, cat)

        def red_pixels(spec: dict[str, Any], n: int) -> int:
            img = frame(spec, cat, n)
            return int(sum(is_red(tuple(int(v) for v in px[:3])) for row in img for px in row))  # type: ignore[arg-type]

        def grow(**motion: Any) -> dict[str, Any]:
            return scene_spec(
                objects=[
                    {
                        "asset": "block",
                        "position": [0.5, 0.8],
                        "motions": [{"type": "grow", "t0": 2, "t1": 3, **motion}],
                    }
                ]
            )

        small = red_pixels(grow(**{"from": 0.2}), 0)  # t = 0 s, a second before the grow
        full = red_pixels(grow(**{"from": 0.2}), 110)  # t = 3.7 s, after it
        nothing = red_pixels(grow(), 0)  # no start given: it starts from nothing
        assert nothing == 0 and 0 < small < 0.2 * full  # 0.2 of the size is 0.04 of the area

    def test_layer_front_draws_over_a_character(self, tmp_path: Path, cat: Catalog) -> None:
        self.lib(tmp_path, cat)
        base = {
            "characters": [{"id": "mia", "archetype": "everyman", "palette": {"shirt": "#2a6fdb"}}],
            "layers": [
                {
                    "character": "mia",
                    "position": [0.5, 0.8],
                    "actions": [{"name": "idle", "t0": 0, "t1": 4}],
                }
            ],
        }
        behind = frame(
            scene_spec(objects=[{"asset": "block", "position": [0.5, 0.8], "scale": 1.5}], **base),
            cat,
        )
        front = frame(
            scene_spec(
                objects=[
                    {"asset": "block", "position": [0.5, 0.8], "scale": 1.5, "layer": "front"}
                ],
                **base,
            ),
            cat,
        )
        assert not is_red(at(behind, 0.5, 0.65)) and is_red(at(front, 0.5, 0.65))

    def test_a_slot_of_the_background_places_it(self, tmp_path: Path, cat: Catalog) -> None:
        self.lib(tmp_path, cat)
        img = frame(
            scene_spec(objects=[{"asset": "block", "position": "right", "scale": 0.5}]), cat
        )
        assert is_red(
            at(img, 0.73, 0.70)
        )  # the slot's x, and the background's own ground line (0.74 on this set)
        assert not is_red(at(img, 0.73, 0.78))

    @pytest.mark.parametrize("style", ["paper_cutout", "flat_vector", "stickman"])
    def test_every_style_draws_it(self, tmp_path: Path, cat: Catalog, style: str) -> None:
        self.lib(tmp_path, cat)
        spec = scene_spec(objects=[{"asset": "block", "position": [0.5, 0.8]}], style=style)
        bare = scene_spec(style=style)
        assert np.abs(frame(spec, cat).astype(int) - frame(bare, cat).astype(int)).sum() > 10_000

    @pytest.mark.parametrize("style", ["paper_cutout", "flat_vector", "stickman"])
    def test_a_hole_in_a_shape_stays_a_hole_in_every_style(
        self, tmp_path: Path, cat: Catalog, style: str
    ) -> None:
        """An even-odd ring (a window frame, a donut) must not fill in: paper cutout's wobble used to drop the fill rule."""
        ring = (
            f'<svg {NS} viewBox="0 0 100 100"><path fill="{RED}" fill-rule="evenodd" '
            'd="M0 0 h100 v100 h-100 Z M30 30 h40 v40 h-40 Z"/></svg>'
        )
        write(tmp_path, "objects", "ring", ring, height=300)
        load_dirs([tmp_path], cat)
        spec = scene_spec(objects=[{"asset": "ring", "position": [0.5, 0.8]}], style=style)
        img = frame(spec, cat)
        assert not is_red(at(img, 0.5, 0.71), tol=60), "the middle of the ring is filled in"
        if style != "stickman":  # (the line-art style draws outlines, not the colour)
            assert is_red(at(img, 0.37, 0.71), tol=60), "the ring itself is missing"

    def test_new_art_is_never_served_from_a_stale_cache(self, tmp_path: Path, cat: Catalog) -> None:
        art = write(tmp_path, "objects", "morph", height=300)
        load_dirs([tmp_path], cat)
        spec = ReelSpec.model_validate(
            scene_spec(objects=[{"asset": "morph", "position": [0.5, 0.8]}])
        )
        opts = RenderOptions(scale=0.25, cache=True, cache_dir=str(tmp_path / "cache"))
        first = Renderer(spec, opts, catalog=cat).frame(15)
        art.write_text(SQUARE.replace(RED, "#2a9d8f"), encoding="utf-8")
        reset_assets(cat, [tmp_path])
        second = Renderer(spec, opts, catalog=cat).frame(15)
        assert is_red(at(first, 0.5, 0.7)) and not is_red(at(second, 0.5, 0.7))

    def test_a_changed_setting_changes_the_picture_too(self, tmp_path: Path, cat: Catalog) -> None:
        write(tmp_path, "objects", "sized", height=300)
        load_dirs([tmp_path], cat)
        spec = ReelSpec.model_validate(
            scene_spec(objects=[{"asset": "sized", "position": [0.5, 0.8]}])
        )
        opts = RenderOptions(scale=0.25, cache=True, cache_dir=str(tmp_path / "cache"))
        tall = Renderer(spec, opts, catalog=cat).frame(15)
        (tmp_path / "objects" / "sized.json").write_text(
            json.dumps({"height": 120}), encoding="utf-8"
        )
        reset_assets(cat, [tmp_path])
        short = Renderer(spec, opts, catalog=cat).frame(15)
        assert is_red(at(tall, 0.5, 0.7)) and not is_red(at(short, 0.5, 0.7))

    def test_an_unknown_object_stops_a_strict_render_and_is_left_out_leniently(
        self, cat: Catalog
    ) -> None:
        spec = ReelSpec.model_validate(scene_spec(objects=[{"asset": "dragon_egg"}]))
        with pytest.raises(Exception, match="dragon_egg"):
            Renderer(spec, RenderOptions(scale=0.1, cache=False), catalog=cat).frame(5)
        lenient = Renderer(spec, RenderOptions(scale=0.1, cache=False, lenient=True), catalog=cat)
        assert lenient.frame(5).shape[0] > 0


# ======================================================================= characters made from art
@pytest.mark.render
class TestPictureCharacters:
    def setup(self, tmp_path: Path, cat: Catalog) -> None:
        write(tmp_path, "characters", "blob", WEDGE, height=400, anchor=[0.5, 1], facing="right")
        load_dirs([tmp_path], cat)

    def spec(self, actions: list[dict[str, Any]] | None = None, **layer: Any) -> dict[str, Any]:
        return scene_spec(
            characters=[{"id": "pal", "archetype": "blob"}],
            layers=[
                {
                    "character": "pal",
                    "position": layer.pop("position", [0.5, 0.8]),
                    "facing": layer.pop(
                        "facing", "right"
                    ),  # "auto" turns towards the middle of the frame
                    "actions": actions or [{"name": "idle", "t0": 0, "t1": 4}],
                    **layer,
                }
            ],
        )

    def test_it_stands_on_its_position_like_a_person(self, tmp_path: Path, cat: Catalog) -> None:
        self.setup(tmp_path, cat)
        img = frame(self.spec(), cat)
        assert is_red(at(img, 0.35, 0.62)) and not is_red(at(img, 0.5, 0.85))

    def test_walking_carries_it_and_face_mirrors_it(self, tmp_path: Path, cat: Catalog) -> None:
        self.setup(tmp_path, cat)
        walk = self.spec(
            [{"name": "walk", "t0": 0, "t1": 2, "params": {"to": [0.8, 0.8]}}], position=[0.2, 0.8]
        )
        start, end = frame(walk, cat, 3), frame(walk, cat, 70)
        assert is_red(at(start, 0.17, 0.65)) and not is_red(at(start, 0.8, 0.7))
        assert is_red(at(end, 0.77, 0.65)) and not is_red(at(end, 0.2, 0.7))
        right, left = frame(self.spec(), cat), frame(self.spec(facing="left"), cat)
        assert is_red(at(right, 0.334, 0.75)) and not is_red(at(right, 0.666, 0.75))
        assert is_red(at(left, 0.666, 0.75)) and not is_red(at(left, 0.334, 0.75))

    def test_actions_a_picture_cannot_do_still_take_their_time(
        self, tmp_path: Path, cat: Catalog
    ) -> None:
        self.setup(tmp_path, cat)
        spec = self.spec([{"name": "wave", "t0": 0, "t1": 2}])
        report = lint(spec, cat)
        assert report.ok
        (note,) = [i for i in report.issues if i.code == "PICTURE_LIMIT"]
        assert (
            note.severity.value == "info"
            and "wave" in note.message
            and "only take time" in note.message
        )
        assert frame(spec, cat).shape[0] > 0

    def test_only_marked_roles_can_be_recoloured(self, tmp_path: Path, cat: Catalog) -> None:
        write(tmp_path, "characters", "paintable", SQUARE, height=300)
        load_dirs([tmp_path], cat)
        spec = scene_spec(
            characters=[
                {
                    "id": "p",
                    "archetype": "paintable",
                    "palette": {"body": "#2a6fdb", "shirt": "#00ff00"},
                }
            ],
            layers=[{"character": "p", "position": [0.5, 0.8], "actions": []}],
        )
        report = lint(spec, cat)
        warn = [i for i in report.warnings if i.code == "PALETTE_ROLE"]
        assert len(warn) == 1 and "shirt" in warn[0].message and "body" in warn[0].message
        b, _g, r = at(frame(spec, cat), 0.5, 0.7)
        assert b > r + 60

    @pytest.mark.parametrize("style", ["paper_cutout", "flat_vector", "stickman"])
    def test_every_style_draws_it(self, tmp_path: Path, cat: Catalog, style: str) -> None:
        self.setup(tmp_path, cat)
        spec = self.spec()
        spec["meta"]["style"] = style
        bare = scene_spec(style=style)
        assert np.abs(frame(spec, cat).astype(int) - frame(bare, cat).astype(int)).sum() > 10_000


# ======================================================================= places
@pytest.mark.render
class TestPlaces:
    def make(self, tmp_path: Path, cat: Catalog) -> None:
        write(
            tmp_path,
            "places",
            "plaza",
            f'<svg {NS} viewBox="0 0 1080 1920"><g data-plane="far"><rect width="1080" height="1000" fill="#4aa8e8"/></g>'
            f'<g data-plane="mid_back"><rect y="1000" width="1080" height="920" fill="{RED}"/></g></svg>',
            place={
                "ground_y": 0.7,
                "horizon": 0.52,
                "slots": {
                    "left": [0.2, 0.7],
                    "center": [0.5, 0.72],
                    "right": [0.8, 0.7],
                    "fountain": [0.35, 0.72],
                },
            },
        )
        load_dirs([tmp_path], cat)

    def test_it_is_used_like_any_background(self, tmp_path: Path, cat: Catalog) -> None:
        self.make(tmp_path, cat)
        spec = scene_spec(background={"template": "plaza", "params": {"time_of_day": "day"}})
        assert lint(spec, cat).ok
        img = frame(spec, cat)
        assert is_red(at(img, 0.5, 0.9), tol=70) and not is_red(at(img, 0.5, 0.2), tol=70)

    def test_its_slots_are_slots(self, tmp_path: Path, cat: Catalog) -> None:
        self.make(tmp_path, cat)
        ok = scene_spec(
            background={"template": "plaza"},
            characters=[{"id": "a", "archetype": "kid"}],
            layers=[{"character": "a", "position": "fountain", "actions": []}],
        )
        assert lint(ok, cat).ok
        bad = scene_spec(
            background={"template": "plaza"},
            characters=[{"id": "a", "archetype": "kid"}],
            layers=[{"character": "a", "position": "lighthouse", "actions": []}],
        )
        assert "POSITION_SLOT" in lint(bad, cat).codes()

    def test_the_picture_covers_the_frame_for_camera_moves(
        self, tmp_path: Path, cat: Catalog
    ) -> None:
        self.make(tmp_path, cat)
        spec = scene_spec(background={"template": "plaza"})
        spec["scenes"][0]["camera"] = {
            "moves": [{"type": "zoom", "from": 1, "to": 1.15, "t0": 0, "t1": 4}]
        }
        img = frame(spec, cat, 100)
        for x, y in ((0.02, 0.02), (0.98, 0.02), (0.02, 0.98), (0.98, 0.98)):
            assert sum(at(img, x, y)) > 60  # no bare edge showing

    @pytest.mark.parametrize("style", ["paper_cutout", "flat_vector", "stickman"])
    def test_every_style_draws_it(self, tmp_path: Path, cat: Catalog, style: str) -> None:
        self.make(tmp_path, cat)
        img = frame(scene_spec(background={"template": "plaza"}, style=style), cat)
        assert img.std() > 5


# ======================================================================= linting a spec that uses assets
class TestLint:
    @pytest.fixture
    def lib(self, tmp_path: Path, cat: Catalog) -> Catalog:
        write(tmp_path, "objects", "block")
        write(tmp_path, "characters", "blob")
        load_dirs([tmp_path], cat)
        return cat

    def codes(self, spec: dict[str, Any], cat: Catalog) -> set[str]:
        return lint(spec, cat).codes()

    def test_a_clean_spec_with_objects_passes(self, lib: Catalog) -> None:
        spec = scene_spec(
            objects=[
                {
                    "asset": "block",
                    "position": "left",
                    "motions": [{"type": "hop", "t0": 0.5, "t1": 1.5}],
                }
            ]
        )
        assert not [
            i
            for i in lint(spec, lib).issues
            if i.severity.value != "info" and i.code != "EMPTY_SCENE"
        ]

    def test_a_palette_colour_that_is_not_a_colour_is_an_error_and_never_stops_the_picture(
        self, lib: Catalog
    ) -> None:
        spec = scene_spec(
            objects=[
                {"asset": "block", "position": "left", "palette": {"body": "red", "gloss": "#fff"}}
            ],
            characters=[{"id": "b", "archetype": "blob", "palette": {"body": "blue"}}],
            layers=[{"character": "b", "position": "right", "actions": []}],
        )
        report = lint(spec, lib)
        by_code = {i.code: i for i in report.issues if i.code.startswith("PALETTE")}
        assert report.issues and {"PALETTE_COLOR", "PALETTE_ROLE"} <= set(by_code)
        assert any(
            i.code == "PALETTE_COLOR" and i.path == "scenes[0].objects[0].palette.body"
            for i in report.issues
        )
        assert any(
            i.code == "PALETTE_ROLE" and i.path == "scenes[0].objects[0].palette.gloss"
            for i in report.issues
        )
        # drawn all the same, with the drawing's own colours for what is not a colour (a lenient render stops for nothing)
        img = Renderer(
            ReelSpec.model_validate(spec),
            RenderOptions(scale=0.25, cache=False, lenient=True),
            catalog=lib,
        ).frame(15)
        assert img.std() > 5

    def test_an_unknown_object_is_missing_from_the_registry_with_how_to_add_it(
        self, lib: Catalog
    ) -> None:
        report = lint(scene_spec(objects=[{"asset": "castle"}]), lib)
        (issue,) = [i for i in report.issues if i.code == "REGISTRY_MISSING"]
        assert (issue.kind, issue.name) == ("object", "castle")
        assert issue.path == "scenes[0].objects[0].asset" and "reel assets add" in (
            issue.hint or ""
        )
        assert report.missing()["object"]["castle"] == ["scenes[0].objects[0].asset"]

    def test_slots_motions_and_times_are_checked(self, lib: Catalog) -> None:
        spec = scene_spec(
            objects=[
                {"asset": "block", "position": "lighthouse"},
                {"asset": "block", "motions": [{"type": "teleport", "t0": 0, "t1": 1}]},
                {"asset": "block", "motions": [{"type": "move", "t0": 0, "t1": 1}]},
                {"asset": "block", "motions": [{"type": "hop", "t0": 2, "t1": 1}]},
                {"asset": "block", "motions": [{"type": "hop", "t0": 3, "t1": 9}]},
                {"asset": "block", "t0": 9},
                {"asset": "block", "position": [5, 5]},
            ]
        )
        codes = self.codes(spec, lib)
        assert {
            "POSITION_SLOT",
            "OBJECT_MOTION",
            "TIME_RANGE",
            "TIME_OVERFLOW",
            "POSITION_RANGE",
        } <= codes

    def test_a_crowd_of_objects_is_a_warning(self, lib: Catalog) -> None:
        spec = scene_spec(
            objects=[{"asset": "block", "position": [0.05 * i, 0.8]} for i in range(15)]
        )
        assert "OBJECT_CLUTTER" in self.codes(spec, lib)

    def test_gaps_are_notes_and_a_filled_gap_says_so(self, lib: Catalog) -> None:
        spec = scene_spec()
        spec["meta"]["library_gaps"] = [
            {"kind": "object", "name": "castle", "scenes": ["s1"]},
            {
                "kind": "character",
                "name": "blob",
                "scenes": ["s1"],
                "character": "x",
                "stand_in": "everyman",
            },
        ]
        report = lint(spec, lib)
        notes = {i.code: i for i in report.issues if i.code.startswith("LIBRARY_GAP")}
        assert (
            notes["LIBRARY_GAP"].name == "castle" and notes["LIBRARY_GAP"].severity.value == "info"
        )
        assert notes["LIBRARY_GAP_FILLED"].name == "blob" and "reel assets fill" in (
            notes["LIBRARY_GAP_FILLED"].hint or ""
        )
        assert report.ok  # gaps are advice, never an error

    def test_the_command_in_a_gap_hint_is_safe_to_paste_whatever_the_name_says(
        self, lib: Catalog
    ) -> None:
        import shlex

        evil = 'x"; touch /tmp/pwned; echo "'
        spec = scene_spec()
        spec["meta"]["library_gaps"] = [{"kind": "object", "name": evil, "scenes": ["s1"]}]
        (note,) = [i for i in lint(spec, lib).issues if i.code == "LIBRARY_GAP"]
        command = (note.hint or "").split("`")[1]
        argv = shlex.split(command)
        assert (
            argv[:3] == ["reel", "assets", "add"] and argv[argv.index("--tags") + 1] == evil.lower()
        )
        assert (
            argv[-1] == evil.lower()
        )  # the name is one argument: nothing after the quote is a command of its own


# ======================================================================= the command line
class TestCli:
    def test_a_studio_project_finds_the_assets_of_its_workspace(self, tmp_path: Path) -> None:
        from reel.cli.assets import effective_dirs

        ws = tmp_path / "ws"
        (ws / "assets" / "objects").mkdir(parents=True)
        (ws / "projects").mkdir()
        project = ws / "projects" / "story.reel.json"
        project.write_text("{}", encoding="utf-8")
        assert str((ws / "assets").resolve()) in effective_dirs(None, near=project)
        elsewhere = tmp_path / "out" / "spec.json"
        elsewhere.parent.mkdir()
        elsewhere.write_text("{}", encoding="utf-8")
        assert str((ws / "assets").resolve()) not in effective_dirs(None, near=elsewhere)

    def test_add_list_check_preview_and_remove(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        art = tmp_path / "Gadget Pro.svg"
        art.write_text(SQUARE, encoding="utf-8")
        res = runner.invoke(
            app,
            [
                "assets",
                "add",
                str(art),
                "--kind",
                "object",
                "--tags",
                "gizmo, ग़ैजेट",
                "--summary",
                "A gadget",
                "--height",
                "222",
            ],
        )
        assert res.exit_code == 0, res.output
        assert (tmp_path / "assets" / "objects" / "gadget_pro.svg").is_file()
        sidecar = json.loads(
            (tmp_path / "assets" / "objects" / "gadget_pro.json").read_text(encoding="utf-8")
        )
        assert sidecar["tags"] == ["gizmo", "ग़ैजेट"] and sidecar["height"] == 222

        listed = runner.invoke(
            app, ["assets", "list", "--json", "--assets", str(tmp_path / "assets")]
        )
        row = next(r for r in json.loads(listed.output) if r["name"] == "gadget_pro")
        assert row["origin"] == "user" and row["kind"] == "object" and "roles" in row

        again = runner.invoke(app, ["assets", "add", str(art)])
        assert again.exit_code == 1 and "already exists" in again.output
        assert runner.invoke(app, ["assets", "add", str(art), "--force"]).exit_code == 0

        assert runner.invoke(app, ["assets", "check", str(tmp_path / "assets")]).exit_code == 0
        png = tmp_path / "sheet.png"
        shot = runner.invoke(
            app,
            [
                "assets",
                "preview",
                "gadget_pro",
                "--assets",
                str(tmp_path / "assets"),
                "-o",
                str(png),
                "--tile",
                "0.1",
            ],
        )
        assert shot.exit_code == 0 and png.stat().st_size > 1000
        removed = runner.invoke(app, ["assets", "remove", "gadget_pro", "--yes"])
        assert removed.exit_code == 0 and not list((tmp_path / "assets").rglob("gadget_pro.*"))

    def test_a_file_that_cannot_be_read_is_not_added(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        bad = tmp_path / "bad.svg"
        bad.write_text("<svg><text>hi</text></svg>", encoding="utf-8")
        res = runner.invoke(app, ["assets", "add", str(bad)])
        assert res.exit_code == 1 and "cannot add" in res.output
        assert not (tmp_path / "assets").exists()
        text = tmp_path / "note.txt"
        text.write_text("x", encoding="utf-8")
        assert runner.invoke(app, ["assets", "add", str(text)]).exit_code == 2

    def test_check_names_what_is_wrong_in_a_folder(self, tmp_path: Path) -> None:
        write(tmp_path, "objects", "fine")
        (tmp_path / "objects" / "oops.svg").write_text("<svg", encoding="utf-8")
        res = runner.invoke(app, ["assets", "check", str(tmp_path)])
        assert res.exit_code == 1 and "oops.svg" in res.output

    def test_a_name_the_engine_owns_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        art = tmp_path / "forest.svg"
        art.write_text(SQUARE, encoding="utf-8")
        res = runner.invoke(app, ["assets", "add", str(art), "--kind", "place"])
        assert res.exit_code == 1 and "taken by the engine" in res.output

    def test_commands_read_the_assets_folder_beside_the_spec(self, tmp_path: Path) -> None:
        write(tmp_path / "assets", "objects", "block")
        spec = scene_spec(objects=[{"asset": "block", "position": [0.5, 0.8]}])
        path = tmp_path / "story.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        ok = runner.invoke(app, ["lint", str(path), "--no-duration-check"])
        assert ok.exit_code == 0, ok.output

    def test_without_the_folder_lint_says_what_to_add(self, tmp_path: Path) -> None:
        path = tmp_path / "story.json"
        path.write_text(json.dumps(scene_spec(objects=[{"asset": "block"}])), encoding="utf-8")
        res = runner.invoke(app, ["lint", str(path), "--no-duration-check"])
        assert res.exit_code == 1 and "reel assets add FILE --name block" in res.output.replace(
            "\n", " "
        )


def test_the_preview_spec_is_valid_for_every_kind() -> None:
    for a in BUILTIN.assets:
        for true_scale in (False, True):
            spec = preview_spec(a, "flat_vector", true_scale=true_scale)
            assert all(i.code == "DURATION_BUDGET" for i in lint_data(spec).errors), a.name


def test_asset_from_files_rejects_what_is_not_art(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError, match="not a picture"):
        asset_from_files(f, root=tmp_path, origin="user")
    art = write(tmp_path, "objects", "ok")
    a = asset_from_files(art, root=tmp_path, origin="user", kind_hint="object")
    register_asset(a, CATALOG.fork())
