"""Reel Studio's asset library API: list, add (draft then keep), change, remove, thumbnails, what a script needs."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from PIL import Image

from reel.assets.coverage import LexEntry
from reel.assets.model import MAX_ART_BYTES
from reel.llm.client import ENV_KEYS
from reel.server.app import create_app
from reel.server.config import ServerConfig

TOKEN = "test-token-0123456789"
NS = 'xmlns="http://www.w3.org/2000/svg"'
SQUARE = (
    f'<svg {NS} viewBox="0 0 100 100"><rect data-role="body" width="100" height="100" fill="#e63946"/>'
    f'<rect data-role="body_dark" y="70" width="100" height="30" fill="#b02a36"/></svg>'
).encode()
LEX = (
    LexEntry("castle", "place", ("castle", "fort", "किला")),
    LexEntry("sword", "object", ("sword", "तलवार")),
    LexEntry("dog", "character", ("dog", "puppy", "kutta")),
)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("REEL_ENV_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("REEL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)


def make_app(tmp_path: Path, **kw: Any) -> Any:
    cfg = ServerConfig(
        workspace=tmp_path / "ws",
        token=TOKEN,
        extra_hosts=("testserver",),
        static_dir=None,
        examples_dir=None,
    )
    return create_app(cfg, inline_jobs=True, **kw)


@pytest.fixture
def app(tmp_path: Path) -> Any:
    application = make_app(tmp_path)
    yield application
    application.state.ctx.jobs.shutdown()


@pytest.fixture
def client(app: Any) -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


def png(width: int = 160, height: int = 120) -> bytes:
    """A coloured disc on a plain white background (what a photo of an object looks like)."""
    im = Image.new("RGB", (width, height), "white")
    px = im.load()
    assert px is not None
    cx, cy, r = width // 2, height // 2, min(width, height) // 3
    for y in range(height):
        for x in range(width):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                px[x, y] = (200, 40, 60)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def draft(
    client: TestClient, data: bytes = SQUARE, name: str = "Gadget.svg", kind: str | None = None
) -> dict[str, Any]:
    r = client.post(
        "/api/assets/draft",
        params={"filename": name, **({"kind": kind} if kind else {})},
        content=data,
    )
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def keep(client: TestClient, did: str, **fields: Any) -> dict[str, Any]:
    body = {
        "name": "gadget",
        "kind": "object",
        "summary": "A gadget",
        "tags": ["gizmo", "ग़ैजेट"],
        "height": 300,
        **fields,
    }
    r = client.post(f"/api/assets/draft/{did}/commit", json=body)
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def listing(client: TestClient) -> dict[str, dict[str, Any]]:
    return {a["name"]: a for a in client.get("/api/assets").json()["assets"]}


def wait_job(client: TestClient, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if snap["status"] in ("done", "error", "cancelled"):
            return snap
        time.sleep(0.1)
    raise AssertionError("job did not finish")


# ======================================================================= the library
def test_the_library_is_listed_with_what_the_app_needs(client: TestClient, tmp_path: Path) -> None:
    j = client.get("/api/assets").json()
    rows = {a["name"]: a for a in j["assets"]}
    assert {"car", "tree", "cat", "beach"} <= set(rows)
    car = rows["car"]
    assert car["kind"] == "object" and car["origin"] == "builtin" and car["editable"] is False
    assert car["roles"] == ["body"] and car["version"] and car["file"] == "car.svg"
    assert rows["beach"]["place"]["slots"]["center"]
    assert j["folder"] == str(tmp_path / "ws" / "assets") or j["folder"].endswith("/ws/assets")
    assert j["problems"] == [] and j["max_bytes"] == MAX_ART_BYTES


def test_every_asset_route_needs_the_token(app: Any) -> None:
    anon = TestClient(app)
    for method, path in (
        ("get", "/api/assets"),
        ("post", "/api/assets/draft"),
        ("delete", "/api/assets/car"),
    ):
        r = getattr(anon, method)(path)
        assert r.status_code == 401, path


def test_built_in_assets_have_thumbnails_and_art(client: TestClient) -> None:
    for style in ("paper_cutout", "flat_vector", "stickman"):
        r = client.get("/api/assets/car/thumb", params={"style": style})
        assert (
            r.status_code == 200
            and r.headers["content-type"] == "image/webp"
            and len(r.content) > 500
        )
    assert client.get("/api/assets/car/thumb", params={"true_scale": "true"}).status_code == 200
    assert client.get("/api/assets/beach/thumb", params={"time_of_day": "night"}).status_code == 200
    art = client.get("/api/assets/car/art")
    assert art.status_code == 200 and art.headers["content-type"] == "image/svg+xml"
    assert "sandbox" in art.headers["content-security-policy"]
    assert client.get("/api/assets/nothing/thumb").status_code == 404
    assert client.get("/api/assets/car/thumb", params={"style": "nope"}).status_code == 400
    assert client.get("/api/assets/car/thumb", params={"time_of_day": "noon"}).status_code == 400


# ======================================================================= adding an asset
def test_an_upload_is_read_drawn_and_kept(client: TestClient, tmp_path: Path) -> None:
    d = draft(client, kind="object")
    assert d["format"] == "svg" and d["roles"] == ["body"] and d["suggested"]["name"] == "gadget"
    assert d["suggested"]["height"] == 300 and d["aspect"] == pytest.approx(1.0, abs=0.01)
    base = {"kind": "object", "height": 300}
    shots = {}
    for style in ("paper_cutout", "flat_vector", "stickman"):
        r = client.get(f"/api/assets/draft/{d['id']}/thumb", params={"style": style, **base})
        assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
        shots[style] = r.content
    assert len(set(shots.values())) == 3
    tiny = client.get(
        f"/api/assets/draft/{d['id']}/thumb",
        params={"style": "flat_vector", "kind": "object", "height": 80},
    ).content
    assert tiny != shots["flat_vector"]  # the settings chosen so far change the picture
    assert "gadget" not in listing(client)  # nothing is kept until it is saved

    row = keep(client, d["id"])
    assert row["origin"] == "user" and row["editable"] is True and row["roles"] == ["body"]
    assert row["tags"] == ["gadget", "gizmo", "ग़ैजेट"]
    art = tmp_path / "ws" / "assets" / "objects"
    assert (art / "gadget.svg").is_file() and json.loads(
        (art / "gadget.json").read_text(encoding="utf-8")
    )["height"] == 300
    assert listing(client)["gadget"]["editable"] is True
    assert client.get("/api/assets/gadget/thumb").status_code == 200
    assert (
        client.post(
            f"/api/assets/draft/{d['id']}/commit", json={"name": "gadget2", "kind": "object"}
        ).status_code
        == 404
    )  # the draft is spent


def test_the_new_asset_is_usable_at_once_by_the_editor_and_the_linter(client: TestClient) -> None:
    keep(client, draft(client)["id"], summary="A red gadget")
    cat = client.get("/api/catalog").json()
    row = next(o for o in cat["objects"] if o["name"] == "gadget")
    assert (
        row["library"] == "user"
        and row["roles"] == ["body"]
        and row["palette"] == {"body": "#e63946"}
    )
    assert row["size"] == pytest.approx(0.52, abs=0.01) and row["aspect"] == pytest.approx(
        1.0, abs=0.01
    )
    spec = {
        "version": "1.0",
        "meta": {"title": "t", "style": "flat_vector"},
        "characters": [],
        "scenes": [
            {
                "id": "a",
                "duration_sec": 4,
                "background": {"template": "abstract"},
                "objects": [
                    {"asset": "gadget", "position": [0.5, 0.8], "palette": {"body": "#2a6fdb"}}
                ],
                "captions": [{"text": "hi there", "t0": 0.5, "t1": 3, "style": "subtitle"}],
            }
        ],
    }
    report = client.post("/api/lint", json={"spec": spec}).json()
    assert not [
        i for i in report["issues"] if i["severity"] == "error" and i["code"] != "DURATION_BUDGET"
    ]
    frame = client.post("/api/preview", json={"spec": spec, "scale": 0.1})
    assert frame.status_code == 200


def test_a_picture_has_its_background_removed_and_can_be_a_character(client: TestClient) -> None:
    d = draft(client, png(), "My Dog.png", kind="character")
    assert d["format"] == "picture" and any("background" in n for n in d["notes"])
    assert (
        client.get(
            f"/api/assets/draft/{d['id']}/thumb",
            params={
                "style": "paper_cutout",
                "kind": "character",
                "true_scale": "true",
                "height": 300,
            },
        ).status_code
        == 200
    )
    row = keep(
        client,
        d["id"],
        name="my_dog",
        kind="character",
        height=300,
        anchor=[0.5, 0.94],
        tags=["pup"],
    )
    assert row["kind"] == "character" and row["format"] == "raster"
    cat = client.get("/api/catalog").json()
    assert any(a["name"] == "my_dog" and a["library"] == "user" for a in cat["archetypes"])


def test_a_place_keeps_its_ground_facts(client: TestClient) -> None:
    svg = f'<svg {NS} viewBox="0 0 1080 1920"><rect width="1080" height="1920" fill="#4aa8e8"/></svg>'.encode()
    d = draft(client, svg, "Plaza.svg", kind="place")
    row = keep(
        client,
        d["id"],
        name="plaza",
        kind="place",
        place={
            "ground_y": 0.7,
            "horizon": 0.5,
            "perspective": 1,
            "slots": {
                "left": [0.2, 0.7],
                "center": [0.5, 0.72],
                "right": [0.8, 0.7],
                "fountain": [0.4, 0.72],
            },
        },
    )
    assert row["place"]["slots"]["fountain"] == [0.4, 0.72] and row["origin"] == "user"
    bg = next(b for b in client.get("/api/catalog").json()["backgrounds"] if b["name"] == "plaza")
    assert bg["slots"]["fountain"] == [0.4, 0.72] and bg["ground_y"] == 0.7


@pytest.mark.parametrize(
    ("data", "status", "word"),
    [
        (b"", 400, "empty"),
        (b"just some text", 415, "picture or drawing"),
        (
            b'<?xml version="1.0"?><svg ' + NS.encode() + b"><text>hi</text></svg>",
            422,
            "cannot use",
        ),
        (
            b'<!DOCTYPE svg [<!ENTITY a "x">]><svg '
            + NS.encode()
            + b'><rect width="1" height="1"/></svg>',
            422,
            "entity",
        ),
        (b"\x89PNG\r\n\x1a\nnot really", 422, "cannot use"),
    ],
)
def test_files_that_cannot_be_used_are_refused_with_a_reason(
    client: TestClient, data: bytes, status: int, word: str
) -> None:
    r = client.post("/api/assets/draft", params={"filename": "x"}, content=data)
    assert r.status_code == status and word in json.dumps(r.json()).lower()


def test_a_file_over_the_limit_is_refused(client: TestClient) -> None:
    r = client.post(
        "/api/assets/draft",
        params={"filename": "big.svg"},
        content=SQUARE + b" " * (MAX_ART_BYTES + 5000),
    )
    assert r.status_code == 413 and "12 MB" in r.json()["detail"]


def test_a_name_that_is_not_usable_or_is_taken_is_said(client: TestClient) -> None:
    d = draft(client)
    r = client.post(
        f"/api/assets/draft/{d['id']}/commit", json={"name": "Bad Name!", "kind": "object"}
    )
    assert r.status_code == 422
    r = client.post(f"/api/assets/draft/{d['id']}/commit", json={"name": "car", "kind": "object"})
    assert (
        r.status_code == 409 and r.json()["conflict"] == "builtin" and "replace" in r.json()["hint"]
    )
    r = client.post(f"/api/assets/draft/{d['id']}/commit", json={"name": "forest", "kind": "place"})
    assert r.status_code == 422 and "taken by the engine" in r.json()["detail"]
    r = client.post(f"/api/assets/draft/{d['id']}/commit", json={"name": "ok", "kind": "weapon"})
    assert r.status_code == 422


def test_a_built_in_asset_can_be_replaced_by_yours_and_comes_back_when_it_goes(
    client: TestClient,
) -> None:
    keep(client, draft(client)["id"], name="car", summary="My car", replace=True)
    assert (
        listing(client)["car"]["origin"] == "user" and listing(client)["car"]["summary"] == "My car"
    )
    assert client.delete("/api/assets/car").status_code == 200
    assert listing(client)["car"]["origin"] == "builtin"


def test_a_draft_can_be_discarded_and_an_unknown_one_is_gone(
    client: TestClient, tmp_path: Path
) -> None:
    d = draft(client)
    assert client.delete(f"/api/assets/draft/{d['id']}").status_code == 200
    assert client.get(f"/api/assets/draft/{d['id']}/thumb").status_code == 404
    assert not list((tmp_path / "ws" / ".drafts").glob(f"{d['id']}*"))
    assert client.get("/api/assets/draft/nope/thumb").status_code == 404


# ======================================================================= changing and removing
def test_an_asset_of_yours_can_be_edited_renamed_and_deleted(
    client: TestClient, tmp_path: Path
) -> None:
    keep(client, draft(client)["id"])
    r = client.patch(
        "/api/assets/gadget", json={"tags": ["widget"], "summary": "A widget", "height": 150}
    )
    assert (
        r.status_code == 200
        and r.json()["tags"] == ["gadget", "widget"]
        and r.json()["height"] == 150
    )
    moved = client.patch("/api/assets/gadget", json={"name": "device", "kind": "character"}).json()
    assert moved["name"] == "device" and moved["kind"] == "character"
    files = sorted(
        str(p.relative_to(tmp_path / "ws" / "assets"))
        for p in (tmp_path / "ws" / "assets").rglob("*")
        if p.is_file()
    )
    assert files == ["characters/device.json", "characters/device.svg"]
    assert "gadget" not in listing(client) and "device" in listing(client)
    assert client.delete("/api/assets/device").status_code == 200
    assert "device" not in listing(client)
    assert not list((tmp_path / "ws" / "assets").rglob("device.*"))


def test_built_in_assets_are_read_only_here(client: TestClient) -> None:
    r = client.patch("/api/assets/car", json={"summary": "mine"})
    assert r.status_code == 403 and "read-only" in r.json()["hint"]
    r = client.delete("/api/assets/tree")
    assert r.status_code == 403 and "cannot be deleted" in r.json()["hint"]
    assert client.patch("/api/assets/nothing", json={"summary": "x"}).status_code == 404
    keep(client, draft(client)["id"])
    assert client.patch("/api/assets/gadget", json={"name": "car"}).status_code == 409
    assert client.patch("/api/assets/gadget", json={"height": -5}).status_code == 422


def test_files_added_by_hand_are_found_after_a_reload(
    client: TestClient, app: Any, tmp_path: Path
) -> None:
    folder = tmp_path / "ws" / "assets" / "objects"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "handmade.svg").write_bytes(SQUARE)
    (folder / "broken.svg").write_text("<svg", encoding="utf-8")
    app.state.ctx.assets.reload()
    j = client.get("/api/assets").json()
    assert "handmade" in {a["name"] for a in j["assets"]}
    assert any("broken.svg" in p["file"] for p in j["problems"])


def test_extra_asset_folders_given_to_the_server_are_read_but_read_only(tmp_path: Path) -> None:
    extra = tmp_path / "shared"
    (extra / "objects").mkdir(parents=True)
    (extra / "objects" / "shared_box.svg").write_bytes(SQUARE)
    application = make_app(tmp_path, asset_dirs=(str(extra),))
    try:
        c = TestClient(application, headers={"Authorization": f"Bearer {TOKEN}"})
        j = c.get("/api/assets").json()
        row = next(a for a in j["assets"] if a["name"] == "shared_box")
        assert (
            row["origin"] == "user"
            and row["editable"] is False
            and str(extra.resolve()) in j["other_folders"]
        )
        assert c.delete("/api/assets/shared_box").status_code == 403
        assert str(extra.resolve()) in application.state.ctx.assets.dirs()
    finally:
        application.state.ctx.jobs.shutdown()


# ======================================================================= scripts and specs
def test_a_script_is_read_for_what_the_library_can_and_cannot_draw(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("reel.assets.coverage.load_lexicon", lambda: LEX)
    r = client.post(
        "/api/script/assets",
        json={"script": "The dog ran past the car. A sword lay by the castle."},
    )
    assert r.status_code == 200
    j = r.json()
    assert {c["asset"] for c in j["covered"]} >= {"car", "dog"}
    names = {m["name"]: m for m in j["missing"]}
    assert set(names) == {"sword", "castle"} and names["castle"]["kind"] == "place"
    assert (
        "fort" in names["castle"]["tags"]
        and names["castle"]["snippet"] == "A sword lay by the castle"
    )
    assert client.post("/api/script/assets", json={"script": "x" * 200_001}).status_code == 413


def test_adding_the_missing_asset_fills_the_gap_in_a_spec(client: TestClient) -> None:
    spec = {
        "version": "1.0",
        "meta": {
            "title": "t",
            "style": "flat_vector",
            "library_gaps": [
                {"kind": "object", "name": "gadget", "scenes": ["a"]},
                {"kind": "object", "name": "castle", "scenes": ["a"]},
            ],
        },
        "characters": [],
        "scenes": [
            {
                "id": "a",
                "duration_sec": 6,
                "background": {"template": "abstract"},
                "captions": [{"text": "hi there", "t0": 0.5, "t1": 3, "style": "subtitle"}],
            }
        ],
    }
    before = client.post("/api/spec/fill-gaps", json={"spec": spec}).json()
    assert before["filled"] == [] and len(before["pending"]) == 2
    keep(client, draft(client)["id"])
    after = client.post("/api/spec/fill-gaps", json={"spec": spec}).json()
    assert [g["name"] for g in after["filled"]] == ["gadget"] and after["filled"][0][
        "asset"
    ] == "gadget"
    assert [g["name"] for g in after["pending"]] == ["castle"]
    assert [o["asset"] for o in after["spec"]["scenes"][0]["objects"]] == ["gadget"]
    assert [g["name"] for g in after["spec"]["meta"]["library_gaps"]] == ["castle"]
    assert any(i["code"] == "LIBRARY_GAP" for i in after["lint"]["issues"])
    only = client.post(
        "/api/spec/fill-gaps", json={"spec": spec, "only": [{"kind": "object", "name": "castle"}]}
    ).json()
    assert only["filled"] == []  # asked for the one that is still missing


def test_planning_and_rendering_see_the_workspace_library(
    client: TestClient, tmp_path: Path
) -> None:
    keep(client, draft(client)["id"], name="gizmo_box", tags=["gizmo box", "contraption"])
    script = "Mia looked at the contraption on the table. The contraption beeped. She laughed. The dog barked at it."
    job = client.post(
        "/api/generate", json={"script": script, "style": "flat_vector", "seed": 3}
    ).json()
    snap = wait_job(client, job["job_id"])
    assert snap["status"] == "done", snap.get("events", [])[-1:]
    spec = snap["result"]["spec"]
    assert any(o["asset"] == "gizmo_box" for sc in spec["scenes"] for o in sc.get("objects", []))
    assert snap["result"]["lint"]["ok"] is True


def test_job_workers_load_the_asset_folders_they_are_given(tmp_path: Path) -> None:
    from reel.core.catalog import CATALOG
    from reel.server.workers import _load_plugins

    folder = tmp_path / "lib"
    (folder / "objects").mkdir(parents=True)
    (folder / "objects" / "worker_thing.svg").write_bytes(SQUARE)
    assert "worker_thing" not in CATALOG.objects
    _load_plugins({"assets": [str(folder)]})
    assert "worker_thing" in CATALOG.objects


# ======================================================================= odd and hostile things
def test_replacing_an_asset_only_removes_its_own_art_and_sidecar(
    client: TestClient, tmp_path: Path
) -> None:
    assets = tmp_path / "ws" / "assets"
    loose = (
        assets / "ball.svg"
    )  # an asset somebody dropped into the folder itself, not in characters/objects/places
    loose.write_bytes(SQUARE)
    bystander = assets / "ball.psd"
    bystander.write_bytes(b"layers of work that are not Reel's")
    client.app.state.ctx.assets.reload()  # type: ignore[attr-defined]
    assert listing(client)["ball"]["editable"] is True
    did = draft(client, SQUARE, "ball.svg")["id"]
    keep(client, did, name="ball", replace=True)
    assert not loose.exists(), "the old art is gone, not shadowing the new one"
    assert bystander.exists() and bystander.read_bytes().startswith(b"layers")
    assert (
        listing(client)["ball"]["origin"] == "user"
        and not (assets / "objects" / "ball.psd").exists()
    )
    assert client.get("/api/assets").json()["problems"] == []


def test_a_symlinked_assets_folder_is_still_the_workspaces_own(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "assets").symlink_to(real, target_is_directory=True)
    application = make_app(tmp_path)
    try:
        c = TestClient(application, headers={"Authorization": f"Bearer {TOKEN}"})
        did = draft(c)["id"]
        row = keep(c, did)
        assert row["editable"] is True
        assert c.delete("/api/assets/gadget").status_code == 200
    finally:
        application.state.ctx.jobs.shutdown()


def test_reading_an_upload_does_not_hold_the_library_still(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A big picture takes a while to read: everyone else's requests (the thumbnails, the list) must go on meanwhile."""
    import threading

    from reel.server.assets_api import AssetStore

    store: AssetStore = client.app.state.ctx.assets  # type: ignore[attr-defined]
    started, release = threading.Event(), threading.Event()
    real = AssetStore._art_info

    def slow(a: Any) -> Any:
        started.set()
        release.wait(10)
        return real(a)

    monkeypatch.setattr(AssetStore, "_art_info", staticmethod(slow))
    worker = threading.Thread(target=lambda: store.create_draft("slow.svg", SQUARE, "object"))
    worker.start()
    try:
        assert started.wait(10)
        t0 = time.perf_counter()
        assert "car" in listing(
            client
        )  # takes the library's lock: it would wait for the whole read if it were held
        assert time.perf_counter() - t0 < 3.0
    finally:
        release.set()
        worker.join(15)
    assert len(store._drafts) == 1


def test_a_file_that_breaks_the_importer_leaves_no_draft_behind(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reel.server.assets_api import AssetStore

    def boom(a: Any) -> Any:
        raise OverflowError("cannot convert float infinity to integer")

    monkeypatch.setattr(AssetStore, "_art_info", staticmethod(boom))
    r = client.post("/api/assets/draft", params={"filename": "odd.svg"}, content=SQUARE)
    assert r.status_code == 422 and "OverflowError" in r.text
    assert list((tmp_path / "ws" / ".drafts").iterdir()) == []


def test_the_art_of_a_file_with_an_odd_name_is_still_served(
    client: TestClient, tmp_path: Path
) -> None:
    from reel.assets.model import slug

    (tmp_path / "ws" / "assets" / "objects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ws" / "assets" / "objects" / "कुत्ता.svg").write_bytes(SQUARE)
    client.app.state.ctx.assets.reload()  # type: ignore[attr-defined]
    name = slug(
        "कुत्ता"
    )  # nothing Latin in it: a name made from the text, a different one for a different text
    assert name.startswith("asset_") and name != slug("बिल्ली")
    row = listing(client)[name]
    r = client.get(f"/api/assets/{name}/art")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["content-disposition"].isascii() and row["file"] == "कुत्ता.svg"
    jpeg = tmp_path / "ws" / "assets" / "objects" / "pic.jpeg"
    Image.open(io.BytesIO(png())).convert("RGB").save(jpeg)
    client.app.state.ctx.assets.reload()  # type: ignore[attr-defined]
    assert client.get("/api/assets/pic/art").headers["content-type"] == "image/jpeg"


def test_a_changed_asset_is_never_shown_from_an_old_preview_or_thumbnail(
    client: TestClient,
) -> None:
    from reel.assets.library import library_fingerprint
    from reel.server.preview import preview_id_for

    spec = {"version": "1.0", "meta": {}, "characters": [], "scenes": []}
    before_fp, before_id = library_fingerprint(), preview_id_for(spec, 0.5, None)
    did = draft(client)["id"]
    keep(client, did)
    after_fp, after_id = library_fingerprint(), preview_id_for(spec, 0.5, None)
    assert after_fp != before_fp and after_id != before_id
    first = client.get("/api/assets/gadget/thumb").content
    red = SQUARE.replace(b"#e63946", b"#2a6fdb")  # the same asset, redrawn in blue
    did2 = draft(client, red)["id"]
    keep(client, did2, replace=True)
    assert library_fingerprint() != after_fp
    assert client.get("/api/assets/gadget/thumb").content != first


def test_the_catalog_is_whole_while_the_library_changes(client: TestClient) -> None:
    import threading

    stop = threading.Event()
    errors: list[str] = []

    def churn() -> None:
        for i in range(6):
            if stop.is_set():
                return
            try:
                did = draft(client)["id"]
                keep(client, did, name=f"churn{i}")
                client.delete(f"/api/assets/churn{i}")
            except AssertionError as exc:  # pragma: no cover - reported below
                errors.append(str(exc))
                return

    worker = threading.Thread(target=churn)
    worker.start()
    statuses = []
    while worker.is_alive():
        statuses.append(client.get("/api/catalog").status_code)
    worker.join()
    assert not errors and set(statuses) <= {200}, (errors, statuses)
