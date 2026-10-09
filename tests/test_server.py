"""Reel Studio's server: the HTTP API over the engine (security, projects, previews, jobs, audio, renders)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from reel.llm.client import ENV_KEYS
from reel.server.app import create_app
from reel.server.config import ServerConfig
from reel.server.jobs import JobManager
from reel.server.workers import duck_curve, estimate_seconds, lane_peaks, resolve_preset
from reel.server.workspace import Workspace, WorkspaceError, slugify

TOKEN = "test-token-0123456789"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A private cache and home: no keys, .env files or cached speech from the machine running the tests."""
    for name in ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("REEL_ENV_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("REEL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)


def make_app(
    tmp_path: Path, *, inline: bool = True, static: Path | None = None, seed: bool = True
) -> Any:
    cfg = ServerConfig(
        workspace=tmp_path / "ws",
        token=TOKEN,
        extra_hosts=("testserver",),
        static_dir=static,
        examples_dir=REPO / "examples" if seed else None,
    )
    return create_app(cfg, inline_jobs=inline)


@pytest.fixture
def app(tmp_path: Path) -> Any:
    application = make_app(tmp_path)
    yield application
    application.state.ctx.jobs.shutdown()


@pytest.fixture
def client(app: Any) -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


def short_spec(seconds: float = 3.0, style: str = "flat_vector") -> dict[str, Any]:
    """A valid but short (outside the 45-60 s budget) spec: renders only leniently, which keeps tests quick."""
    return {
        "version": "1.0",
        "meta": {"title": "Short", "style": style, "seed": 3, "target_duration_sec": 50},
        "characters": [{"id": "mia", "archetype": "kid", "name": "Mia"}],
        "scenes": [
            {
                "id": "a",
                "duration_sec": seconds,
                "background": {"template": "abstract", "params": {}},
                "layers": [
                    {
                        "character": "mia",
                        "position": "center",
                        "actions": [{"name": "wave", "t0": 0.2, "t1": 2.0}],
                    }
                ],
                "captions": [
                    {
                        "text": "Hello there",
                        "t0": 0.3,
                        "t1": 2.2,
                        "style": "subtitle",
                        "speaker": "mia",
                    }
                ],
            }
        ],
        "audio": {"music": None, "voiceover": "tts"},
    }


def wait_job(client: TestClient, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if snap["status"] in ("done", "error", "cancelled"):
            return snap
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


# ------------------------------------------------------------------ who may talk to the server
def test_every_api_call_needs_the_token(app: Any) -> None:
    anon = TestClient(app)
    for path in ("/api/health", "/api/catalog", "/api/projects", "/api/doctor"):
        r = anon.get(path)
        assert r.status_code == 401 and "access token" in r.json()["hint"], path
    assert (
        TestClient(app, headers={"Authorization": "Bearer wrong"}).get("/api/health").status_code
        == 401
    )
    assert (
        TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}).get("/api/health").status_code
        == 200
    )
    cookie = TestClient(app, cookies={"reel_token": TOKEN})
    assert cookie.get("/api/health").status_code == 200  # what <img> and <video> use


def test_a_foreign_host_name_is_refused_against_dns_rebinding(app: Any) -> None:
    r = TestClient(
        app, headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.example:8765"}
    ).get("/api/health")
    assert r.status_code == 421 and "link" in r.json()["hint"]


def test_unsafe_requests_from_another_origin_are_refused(client: TestClient) -> None:
    body = {"spec": short_spec()}
    bad = client.post("/api/lint", json=body, headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403
    assert (
        client.post("/api/lint", json=body, headers={"Origin": "http://testserver"}).status_code
        == 200
    )
    assert (
        client.post("/api/lint", json=body).status_code == 200
    )  # no Origin header (not a browser): fine


def test_the_launch_link_sets_an_httponly_cookie_and_hides_the_token(tmp_path: Path) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    app = make_app(tmp_path, static=static)
    c = TestClient(app, follow_redirects=False)
    assert (
        c.get("/").status_code == 401 and "access token" in c.get("/").text
    )  # locked page, no app
    assert c.get("/assets/app.js").status_code == 200  # the app's code is public
    r = c.get(f"/?token={TOKEN}")
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and TOKEN in cookie
    assert c.get("/?token=wrong").status_code == 401
    authed = TestClient(app, cookies={"reel_token": TOKEN})
    page = authed.get("/p/some-project")  # a client-side route: the single page app answers
    assert page.status_code == 200 and "<title>app</title>" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert authed.get("/..%2f..%2fetc/passwd").status_code in (
        200,
        404,
    )  # falls back to the app, never a file
    assert "root:" not in authed.get("/..%2f..%2fetc/passwd").text
    app.state.ctx.jobs.shutdown()


def test_without_a_built_app_the_server_still_serves_the_api(client: TestClient, app: Any) -> None:
    r = TestClient(app, cookies={"reel_token": TOKEN}).get("/")
    assert r.status_code == 200 and "has not been built" in r.text
    assert client.get("/api/health").json()["ffmpeg_ok"] is True


# ------------------------------------------------------------------ catalog and health
def test_the_catalog_carries_what_the_editor_needs(client: TestClient) -> None:
    cat = client.get("/api/catalog").json()
    assert {a["name"] for a in cat["actions"]} >= {"walk", "wave", "talk", "idle"}
    walk = next(a for a in cat["actions"] if a["name"] == "walk")
    assert walk["moves_root"] is True and {p["name"] for p in walk["params"]} >= {
        "to",
        "style",
        "in_place",
    }
    room = next(b for b in cat["backgrounds"] if b["name"] == "room")
    assert (
        room["slots"]["center"][0] == 0.5 and "sofa" in room["slots"] and 0 < room["ground_y"] < 1
    )
    kid = next(a for a in cat["archetypes"] if a["name"] == "kid")
    assert kid["palette"]["shirt"].startswith("#")
    assert "upbeat" in cat["music_moods"] and cat["limits"]["min_total_sec"] == 45
    assert all(isinstance(e, dict) and "name" in e for e in cat["easings"])
    assert "skin" in cat["palette_roles"] and "left" in cat["universal_slots"]


def test_schema_and_examples(client: TestClient) -> None:
    schema = client.get("/api/schema").json()
    assert schema["required"] == ["meta", "scenes"] and "SceneSpec" in schema["$defs"]
    ex = client.get("/api/examples").json()
    assert {s["id"] for s in ex["specs"]} == {"story_50s", "explainer_45s"}
    hindi = next(s for s in ex["scripts"] if s["id"] == "hindi_khoya_chhata")
    assert hindi["language"] == "hi" and hindi["words"] > 20


def test_no_response_ever_contains_a_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = {
        "OPENAI_API_KEY": "sk-secret-openai-0123456789abcdef",
        "ANTHROPIC_API_KEY": "sk-ant-secret-0123456789abcdef",
        "ELEVENLABS_API_KEY": "xi-secret-eleven-0123456789",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    bodies = []
    for path in ("/api/health", "/api/doctor", "/api/tts/engines", "/api/catalog"):
        bodies.append(client.get(path).text)
    bodies.append(
        client.post("/api/doctor/test", json={"kind": "tts", "target": "elevenlabs"}).text
    )  # unreachable host in tests? see below
    est = client.post("/api/audio/estimate", json={"spec": short_spec(), "engine": "elevenlabs"})
    bodies.append(est.text)
    joined = "\n".join(bodies)
    for value in secrets.values():
        assert value not in joined
    doctor = client.get("/api/doctor").json()
    assert {k["name"]: k["source"] for k in doctor["llm"]["keys"]}["OPENAI_API_KEY"] == "shell"


# ------------------------------------------------------------------ projects
def test_a_new_workspace_starts_with_the_two_example_reels(client: TestClient) -> None:
    items = client.get("/api/projects").json()
    assert {p["id"] for p in items} == {"story-50s", "explainer-45s"}
    story = next(p for p in items if p["id"] == "story-50s")
    assert story["title"] == "The Lost Umbrella" and 45 <= story["duration_sec"] <= 60
    assert story["lint"] == {"errors": 0, "warnings": 0} and story["thumb_url"].startswith(
        "/api/projects/story-50s/thumb"
    )
    doc = client.get("/api/projects/story-50s").json()
    assert doc["script"].strip() and doc["spec"]["meta"]["title"] == "The Lost Umbrella"


def test_project_lifecycle_with_etags(client: TestClient, app: Any) -> None:
    created = client.post(
        "/api/projects", json={"title": "My First Reel", "style": "stickman"}
    ).json()
    assert created["id"] == "my-first-reel" and created["spec"]["meta"]["style"] == "stickman"
    assert (
        client.post("/api/lint", json={"spec": created["spec"]}).json()["ok"] is True
    )  # a blank reel is lint-clean
    again = client.post("/api/projects", json={"title": "My First Reel"}).json()
    assert again["id"] == "my-first-reel-2"  # never overwrites

    spec = created["spec"]
    spec["meta"]["title"] = "Renamed"
    saved = client.put(
        "/api/projects/my-first-reel",
        json={"spec": spec, "script": "A script."},
        headers={"If-Match": created["etag"]},
    )
    assert (
        saved.status_code == 200
        and saved.json()["etag"] != created["etag"]
        and saved.json()["script"] == "A script."
    )
    # a stale tab: the file changed since it was opened
    stale = client.put(
        "/api/projects/my-first-reel", json={"spec": spec}, headers={"If-Match": created["etag"]}
    )
    assert stale.status_code == 409
    body = stale.json()
    assert (
        body["current_etag"] == saved.json()["etag"]
        and body["current"]["spec"]["meta"]["title"] == "Renamed"
    )
    # the file on disk is the spec itself, so the command line reads it too
    on_disk = json.loads(
        (app.state.ctx.workspace.projects_dir / "my-first-reel.reel.json").read_text()
    )
    assert on_disk["meta"]["title"] == "Renamed" and "spec" not in on_disk

    assert client.delete("/api/projects/my-first-reel").json() == {"ok": True}
    assert client.get("/api/projects/my-first-reel").status_code == 404


def test_project_ids_cannot_escape_the_workspace(client: TestClient) -> None:
    for bad in ("..%2Fsecrets", "a b", "UPPER", ".hidden"):
        assert client.get(f"/api/projects/{bad}").status_code in (400, 404)
    ws = Workspace(Path("."))
    with pytest.raises(WorkspaceError):
        ws.read_project("../x")
    assert slugify("Ünïcode & Spaces!!") == "n-code-spaces" and slugify("日本語") == "reel"


def test_a_broken_project_file_does_not_break_the_list(client: TestClient, app: Any) -> None:
    (app.state.ctx.workspace.projects_dir / "oops.reel.json").write_text("{not json")
    items = {p["id"]: p for p in client.get("/api/projects").json()}
    assert "broken" in items["oops"] and "story-50s" in items
    r = client.get("/api/projects/oops")
    assert r.status_code == 422 and "not valid JSON" in r.json()["detail"]


def test_project_thumbnails_are_webp(client: TestClient) -> None:
    r = client.get("/api/projects/story-50s/thumb")
    assert r.status_code == 200 and r.content[:4] == b"RIFF" and r.content[8:12] == b"WEBP"


# ------------------------------------------------------------------ lint and previews
def test_lint_reports_problems_with_paths_and_hints(client: TestClient) -> None:
    r = client.post("/api/lint", json={"spec": short_spec(3)}).json()
    codes = {i["code"] for i in r["issues"]}
    assert r["ok"] is False and "DURATION_BUDGET" in codes
    budget = next(i for i in r["issues"] if i["code"] == "DURATION_BUDGET")
    assert budget["path"] == "scenes" and "add about" in budget["hint"]
    assert (
        client.post("/api/lint", json={"spec": "nonsense"}).json()["ok"] is False
    )  # still an answer, not a crash


def test_the_script_check_says_how_closely_the_captions_follow_the_script(
    client: TestClient,
) -> None:
    script = (REPO / "examples/scripts/hindi_khoya_chhata.txt").read_text(encoding="utf-8")
    spec = json.loads(
        (REPO / "tests/fixtures/openai_khoya_chhata.json").read_text(encoding="utf-8")
    )
    got = client.post("/api/script/check", json={"script": script, "spec": spec}).json()
    assert (
        got["empty"] is False and got["ok"] is False and got["labels"] >= 4 and got["fits"] is True
    )
    assert got["covered"] < 0.5 and got["missing"] and got["words"] > 50
    assert client.post("/api/script/check", json={"script": "  ", "spec": spec}).json() == {
        "empty": True
    }


def test_the_script_lock_puts_the_captions_back_to_the_script(client: TestClient) -> None:
    script = (REPO / "examples/scripts/hindi_khoya_chhata.txt").read_text(encoding="utf-8")
    spec = json.loads(
        (REPO / "tests/fixtures/openai_khoya_chhata.json").read_text(encoding="utf-8")
    )
    r = client.post("/api/script/lock", json={"script": script, "spec": spec})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["changed"] and any("word for word" in n for n in got["notes"])
    assert got["coverage"]["ok"] is True and got["lint"]["ok"] is True
    shown = [c["text"] for sc in got["spec"]["scenes"] for c in sc["captions"]]
    assert "बारिश में घूमना कितना मज़ेदार है!" in shown and not any(t.endswith(":") for t in shown)
    names = {c.get("name") for c in got["spec"]["characters"]}
    assert {"मिया", "पिप", "बोल्ट"} <= names
    again = client.post("/api/script/lock", json={"script": script, "spec": got["spec"]}).json()
    assert again["changed"] is False  # nothing left to put right


def test_the_script_lock_refuses_what_a_reel_cannot_hold_and_what_is_empty(
    client: TestClient,
) -> None:
    spec = short_spec()
    long_script = " ".join(["The quick brown fox jumps over the lazy dog."] * 30)
    r = client.post("/api/script/lock", json={"script": long_script, "spec": spec})
    assert r.status_code == 409 and "too long" in r.json()["detail"]
    assert client.post("/api/script/lock", json={"script": "", "spec": spec}).status_code == 400


def test_a_preview_can_take_the_voices_word_timings(client: TestClient) -> None:
    """The mouths follow the generated voice: the preview of the same spec with word timings is its own preview."""
    spec = short_spec()
    plain = client.post("/api/preview", json={"spec": spec}).json()
    timed = client.post(
        "/api/preview",
        json={
            "spec": spec,
            "word_timings": {"0": {"0": [[0.3, 0.8, "Hello"], [0.9, 1.4, "there"]]}},
        },
    )
    assert timed.status_code == 200 and timed.json()["preview_id"] != plain["preview_id"]
    again = client.post(
        "/api/preview",
        json={
            "spec": spec,
            "word_timings": {"0": {"0": [[0.3, 0.8, "Hello"], [0.9, 1.4, "there"]]}},
        },
    ).json()
    assert again["preview_id"] == timed.json()["preview_id"]  # same words, same preview
    # whatever cannot be read is left out: a preview never fails over its timings
    junk = client.post(
        "/api/preview", json={"spec": spec, "word_timings": {"x": 3, "0": {"0": "no"}}}
    )
    assert junk.status_code == 200 and junk.json()["preview_id"] == plain["preview_id"]


def test_preview_frames_are_live_and_cacheable(client: TestClient) -> None:
    doc = client.get("/api/projects/story-50s").json()
    info = client.post("/api/preview", json={"spec": doc["spec"], "scale": 0.25}).json()
    assert (
        info["fps"] == 30
        and info["total_frames"] > 1400
        and (info["width"], info["height"]) == (270, 480)
    )
    again = client.post("/api/preview", json={"spec": doc["spec"], "scale": 0.25}).json()
    assert again["preview_id"] == info["preview_id"]  # same spec, same preview
    t0 = time.perf_counter()
    f = client.get(f"/api/preview/{info['preview_id']}/frame/300")
    assert (
        f.status_code == 200
        and f.content[:4] == b"RIFF"
        and f.headers["x-total-frames"] == str(info["total_frames"])
    )
    assert "immutable" in f.headers["cache-control"]
    assert time.perf_counter() - t0 < 2.0
    assert client.get(f"/api/preview/{info['preview_id']}/frame/99999").headers["x-frame"] == str(
        info["total_frames"] - 1
    )
    assert client.get("/api/preview/0000/frame/1").status_code == 404


def test_an_invalid_spec_has_no_preview_but_says_why(client: TestClient) -> None:
    broken = short_spec()
    broken["scenes"][0]["duration_sec"] = "long"
    r = client.post("/api/preview", json={"spec": broken})
    assert r.status_code == 422 and r.json()["issues"][0]["path"].startswith("scenes[0]")


def test_library_thumbnails(client: TestClient) -> None:
    for url in (
        "/api/library/thumb/style/stickman",
        "/api/library/thumb/background/room?time_of_day=night",
        "/api/library/thumb/archetype/robot",
    ):
        r = client.get(url)
        assert r.status_code == 200 and r.content[8:12] == b"WEBP", url
    assert client.get("/api/library/thumb/planet/earth").status_code == 400
    assert client.get("/api/library/thumb/style/nope").status_code == 404
    assert client.get("/api/library/thumb/background/room?time_of_day=noon").status_code == 400


# ------------------------------------------------------------------ script -> spec
def test_a_script_becomes_a_lint_clean_spec(client: TestClient) -> None:
    script = (REPO / "examples/scripts/lost_umbrella.txt").read_text()
    job = client.post(
        "/api/generate", json={"script": script, "style": "stickman", "seed": 5}
    ).json()
    snap = wait_job(client, job["job_id"])
    assert snap["status"] == "done"
    res = snap["result"]
    assert (
        res["lint"]["ok"] is True
        and res["spec"]["meta"]["style"] == "stickman"
        and res["generator"]
    )
    notes = [e["message"] for e in snap["events"] if e["type"] == "note"]
    assert notes[0] == "Planning with the offline planner" and any("scene" in n for n in notes)


def test_generate_validates_and_a_hosted_planner_needs_its_key(client: TestClient) -> None:
    assert client.post("/api/generate", json={"script": "   "}).status_code == 400
    job = client.post(
        "/api/generate", json={"script": "A short script about nothing much.", "planner": "openai"}
    ).json()
    snap = wait_job(client, job["job_id"])
    assert snap["status"] == "error"
    assert "OPENAI_API_KEY" in snap["events"][-1]["message"]


# ------------------------------------------------------------------ voices and sound
def test_engines_report_status_without_exposing_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = client.get("/api/tts/engines").json()
    by = {e["name"]: e for e in data["engines"]}
    assert (
        by["babble"]["available"]
        and by["elevenlabs"]["online"]
        and by["elevenlabs"]["available"] is False
    )
    assert "ELEVENLABS_API_KEY" in by["elevenlabs"]["detail"]
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-fake-0123456789")
    by = {e["name"]: e for e in client.get("/api/tts/engines").json()["engines"]}
    assert by["elevenlabs"]["available"] and by["elevenlabs"]["key_source"] == "shell"
    assert "xi-fake" not in json.dumps(by)


def test_voices_and_audition_samples(client: TestClient) -> None:
    voices = client.get("/api/tts/voices", params={"engine": "babble"}).json()
    assert voices and {"id", "name", "traits"} <= set(voices[0])
    sample = client.post("/api/tts/sample", json={"engine": "babble", "text": "Hello there"})
    assert (
        sample.status_code == 200
        and sample.content[:4] == b"RIFF"
        and sample.headers["content-type"] == "audio/wav"
    )
    assert (
        client.get("/api/tts/voices", params={"engine": "elevenlabs"}).status_code == 409
    )  # no key: a clear refusal


def test_a_paid_audition_needs_explicit_confirmation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-fake-0123456789")
    r = client.post(
        "/api/tts/sample", json={"engine": "elevenlabs", "text": "Twenty six characters long"}
    )
    assert r.status_code == 409
    body = r.json()
    assert (
        body["confirm_required"] is True and body["billable_characters"] == 26
    )  # nothing was sent anywhere


def test_the_speech_estimate_counts_new_and_cached_lines(
    client: TestClient, tmp_path: Path
) -> None:
    spec = short_spec()
    first = client.post("/api/audio/estimate", json={"spec": spec, "engine": "babble"}).json()
    assert (
        first["lines"] == 1
        and first["new_lines"] == 1
        and first["billable_characters"] == 0
        and first["online"] is False
    )
    job = client.post("/api/audio/prepare", json={"spec": spec, "engine": "babble"}).json()
    assert wait_job(client, job["job_id"])["status"] == "done"
    after = client.post("/api/audio/estimate", json={"spec": spec, "engine": "babble"}).json()
    assert after["cached_lines"] == 1 and after["new_lines"] == 0


def test_online_estimates_count_billable_characters_without_a_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reel.audio.elevenlabs import ElevenLabsTTS

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("an estimate must never reach the network")

    monkeypatch.setattr(
        "reel.server.audio_api.get_tts_engine",
        lambda name=None: ElevenLabsTTS(
            env={"ELEVENLABS_API_KEY": "xi-fake"}, transport=httpx.MockTransport(no_network)
        ),
    )
    est = client.post(
        "/api/audio/estimate", json={"spec": short_spec(), "engine": "elevenlabs"}
    ).json()
    assert (
        est["online"] is True
        and est["billable_characters"] == len("Hello there")
        and est["destination"] == "api.elevenlabs.io"
    )
    assert est["new_lines"] == 1 and est["cached_lines"] == 0


def test_the_estimate_for_an_unavailable_engine_says_why(client: TestClient) -> None:
    est = client.post(
        "/api/audio/estimate", json={"spec": short_spec(), "engine": "elevenlabs"}
    ).json()
    assert est["available"] is False and "ELEVENLABS_API_KEY" in est["reason"]


def test_audio_prepare_returns_a_playable_mix_with_waveforms(client: TestClient) -> None:
    spec = short_spec()
    spec["audio"]["music"] = "procedural:calm"
    job = client.post("/api/audio/prepare", json={"spec": spec, "engine": "babble"}).json()
    snap = wait_job(client, job["job_id"])
    assert snap["status"] == "done", snap["events"][-1]
    res = snap["result"]
    assert res["report"]["n_clips"] >= 2 and -20 < res["report"]["approx_lufs"] < -12
    assert (
        set(res["peaks"]) == {"voice", "music", "sfx"}
        and max(res["peaks"]["voice"]) > 0.05
        and max(res["peaks"]["music"]) > 0.02
    )
    duck = res["duck"]  # the dip the mixer puts on the music under the voice, for the audio lane
    assert duck["per_sec"] == 40 and len(duck["gain"]) >= 40 * res["total_sec"] - 1
    assert min(duck["gain"]) < 0.4 and max(duck["gain"]) == 1.0
    wav = client.get(res["wav_url"])
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    assert res["word_timings"]["0"]["0"][0][2] == "Hello"
    assert res["spec"]["scenes"][0]["captions"][0]["text"] == "Hello there"


def test_sound_effect_and_music_auditions(client: TestClient) -> None:
    sfx = client.get("/api/sfx/pop.wav")
    assert sfx.status_code == 200 and sfx.content[:4] == b"RIFF" and len(sfx.content) > 1000
    assert client.get("/api/sfx/nope.wav").status_code == 404
    music = client.get("/api/music/calm.wav", params={"seconds": 3})
    assert music.status_code == 200 and len(music.content) > 100_000
    assert client.get("/api/music/jazz.wav").status_code == 404


def test_lane_peaks_place_each_clip_on_its_track() -> None:
    from reel.audio.mix import MixClip

    sr = 8000
    voice = MixClip(1.0, np.full(sr, 0.5, dtype=np.float32), sr, kind="voice")
    sfx = MixClip(3.0, np.full(sr // 2, 0.25, dtype=np.float32), sr, gain_db=-6.0, kind="sfx")
    peaks = lane_peaks([voice, sfx], total=4.0, bins=40)  # 10 bins per second
    assert (
        peaks["voice"][:10] == [0.0] * 10
        and peaks["voice"][10:20] == [0.5] * 10
        and peaks["voice"][20:] == [0.0] * 20
    )
    assert (
        peaks["sfx"][30:35] == [pytest.approx(0.125, abs=1e-3)] * 5 and max(peaks["music"]) == 0.0
    )
    assert lane_peaks([], 0.0, 4)["voice"] == [0.0] * 4


def test_duck_curve_dips_while_somebody_speaks_and_recovers() -> None:
    from reel.audio.mix import MixClip

    sr = 8000
    voice = MixClip(2.0, np.full(sr * 2, 0.4, dtype=np.float32), sr, kind="voice")
    sfx = MixClip(
        0.5, np.full(sr // 4, 0.9, dtype=np.float32), sr, kind="sfx"
    )  # sfx does not duck the bed
    curve = duck_curve([voice, sfx], total=8.0, depth_db=-12.0, per_sec=20)
    assert curve is not None and curve["per_sec"] == 20 and len(curve["gain"]) == 160
    g = curve["gain"]
    assert g[:20] == [1.0] * 20  # before the voice (and its 0.08 s look-ahead)
    assert min(g[45:75]) == pytest.approx(10 ** (-12 / 20), abs=0.01)  # fully dipped inside 2..4 s
    assert g[-1] == 1.0 and g[110] > 0.95  # recovered by ~5.5 s
    assert duck_curve([sfx], total=8.0) is None  # nobody speaks: nothing to duck under
    assert duck_curve([], total=0.0) is None


# ------------------------------------------------------------------ rendering
def test_render_presets_and_estimates() -> None:
    assert (
        resolve_preset("draft", {})["scale"] == pytest.approx(1 / 3)
        and resolve_preset("draft", {})["lenient"] is True
    )
    full = resolve_preset("full", {})
    assert full["scale"] == 1.0 and full["maxrate"] == "10M" and full["lenient"] is False
    custom = resolve_preset("custom", {"scale": 0.75, "crf": 18, "maxrate": "8m", "lenient": True})
    assert (
        custom["scale"] == 0.75 and custom["crf"] == 18 and custom["maxrate"] == "8M"
    )  # ffmpeg reads a lower-case m as milli
    assert resolve_preset("custom", {"maxrate": "none"})["maxrate"] is None
    assert (
        estimate_seconds("paper_cutout", 1.0, 35)
        > estimate_seconds("stickman", 1.0, 35)
        > estimate_seconds("stickman", 0.33, 35)
    )
    assert estimate_seconds("flat_vector", 1.0, 0) == 0.0


def test_render_plan_counts_cached_chunks(client: TestClient) -> None:
    plan = client.post(
        "/api/render/plan", json={"project_id": "story-50s", "preset": "draft"}
    ).json()
    assert (
        plan["segments_total"] > 20
        and plan["segments_cached"] == 0
        and (plan["width"], plan["height"]) == (360, 640)
    )
    assert plan["est_seconds"] > 0 and 49 < plan["duration_sec"] < 51
    assert client.post("/api/render/plan", json={"preset": "draft"}).status_code == 400


@pytest.mark.render
def test_the_plan_sees_through_the_voice_retiming(client: TestClient) -> None:
    """A spoken reel is retimed to its speech before it is drawn: the plan must count cached chunks on that timing."""
    body = {"spec": short_spec(), "preset": "draft", "tts": "babble"}
    before = client.post("/api/render/plan", json=body).json()
    assert before["voice"] == "pending"  # the line is not generated yet: the retiming is not known

    job = client.post("/api/render", json=body).json()
    assert wait_job(client, job["job_id"])["status"] == "done"

    after = client.post("/api/render/plan", json=body).json()
    assert after["voice"] == "ready"
    assert after["segments_total"] >= 1 and after["segments_cached"] == after["segments_total"]
    assert after["est_seconds"] == 0

    silent = client.post("/api/render/plan", json={**body, "no_audio": True}).json()
    assert silent["voice"] == "none"


@pytest.mark.render
def test_a_draft_render_runs_as_a_job_and_lands_in_the_workspace(client: TestClient) -> None:
    job = client.post(
        "/api/render", json={"spec": short_spec(), "preset": "draft", "tts": "babble"}
    ).json()
    snap = wait_job(client, job["job_id"])
    assert snap["status"] == "done", snap["events"][-1]
    res = snap["result"]
    assert (
        (res["width"], res["height"]) == (360, 640)
        and res["has_audio"] is True
        and res["size_bytes"] > 10_000
    )
    assert res["segments"]["total"] >= 1 and res["id"] == job["render_id"]
    phases = [e["phase"] for e in snap["events"] if e["type"] == "progress"]
    assert phases[0] == "plan" and "audio" in phases and "frames" in phases and phases[-1] == "mux"
    frames = [e for e in snap["events"] if e["type"] == "progress" and e["phase"] == "frames"]
    assert frames[-1]["done"] == frames[-1]["total"] > 0
    assert [e["seq"] for e in snap["events"]] == list(range(len(snap["events"])))

    video = client.get(res["video_url"], headers={"Range": "bytes=0-99"})
    assert (
        video.status_code == 206
        and video.headers["content-range"].startswith("bytes 0-99/")
        and video.content[4:8] == b"ftyp"
    )
    listed = client.get("/api/renders").json()
    assert listed[0]["id"] == res["id"] and listed[0]["title"] == "Short"
    assert client.delete(f"/api/renders/{res['id']}").json() == {"ok": True}
    assert client.get("/api/renders").json() == []


@pytest.mark.render
def test_a_strict_render_of_an_invalid_spec_is_refused_with_the_lint_report(
    client: TestClient,
) -> None:
    job = client.post("/api/render", json={"spec": short_spec(), "preset": "standard"}).json()
    snap = wait_job(client, job["job_id"])
    err = snap["events"][-1]
    assert snap["status"] == "error" and err["lint"]["counts"]["errors"] == 1
    assert "not rendered" in err["message"] and "lenient" in err["hint"]
    assert client.get("/api/renders").json() == []  # nothing half-made


@pytest.mark.render
def test_job_events_stream_over_sse_and_can_resume(client: TestClient) -> None:
    job = client.post(
        "/api/render", json={"spec": short_spec(), "preset": "draft", "no_audio": True}
    ).json()
    seen: list[dict[str, Any]] = []
    with client.stream("GET", f"/api/jobs/{job['job_id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                seen.append(json.loads(line[6:]))
                if seen[-1]["type"] == "done":
                    break
    assert seen[-1]["type"] == "done" and seen[0]["type"] == "status"
    resumed = []
    with client.stream(
        "GET", f"/api/jobs/{job['job_id']}/events", headers={"Last-Event-ID": str(seen[-3]["seq"])}
    ) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                resumed.append(json.loads(line[6:]))
    assert [e["seq"] for e in resumed] == [seen[-2]["seq"], seen[-1]["seq"]]


@pytest.mark.render
def test_cancel_stops_a_running_render_and_leaves_nothing_behind(tmp_path: Path) -> None:
    app = make_app(tmp_path, inline=False)  # real job processes: only they can be cancelled
    try:
        c = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
        story = c.get("/api/projects/story-50s").json()["spec"]
        job = c.post("/api/render", json={"spec": story, "preset": "full", "no_audio": True}).json()
        deadline = time.time() + 90
        while time.time() < deadline:
            snap = c.get(f"/api/jobs/{job['job_id']}").json()
            if any(
                e["type"] == "progress" and e.get("phase") == "frames" and e["done"] > 0
                for e in snap["events"]
            ):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("the render never started")
        assert c.delete(f"/api/jobs/{job['job_id']}").json()["id"] == job["job_id"]
        final = wait_job(c, job["job_id"], timeout=60)
        assert final["status"] == "cancelled" and final["events"][-1]["type"] == "cancelled"
        renders = tmp_path / "ws" / "renders"
        assert list(renders.glob("*.mp4")) == [] and list(renders.glob(".*partial*")) == []
        # and the server is still fine
        assert c.get("/api/health").status_code == 200
    finally:
        app.state.ctx.jobs.shutdown()


# ------------------------------------------------------------------ jobs
def test_jobs_run_one_at_a_time_in_order_and_queued_jobs_can_be_cancelled() -> None:
    from reel.server import workers

    order: list[str] = []

    def slow(payload: dict[str, Any], emit: Any) -> None:
        order.append(payload["name"])
        time.sleep(payload.get("sleep", 0.2))
        emit({"type": "done", "result": {"name": payload["name"]}})

    workers.WORKERS["slow"] = slow
    mgr = JobManager(inline=True)
    try:
        a = mgr.submit("slow", "a", {"name": "a", "sleep": 0.4})
        b = mgr.submit("slow", "b", {"name": "b"})
        c = mgr.submit("slow", "c", {"name": "c"})
        assert a.events[0]["status"] == "queued" and b.events[0]["position"] >= 1
        mgr.cancel(b.id)
        for job in (a, c):
            deadline = time.time() + 5
            while job.status not in ("done", "cancelled") and time.time() < deadline:
                time.sleep(0.02)
        assert (
            order == ["a", "c"]
            and b.status == "cancelled"
            and a.status == "done"
            and c.result == {"name": "c"}
        )
        events, finished = a.wait(0, 0.1)
        assert finished and [e["seq"] for e in events] == list(range(len(events)))
        a.emit({"type": "note", "message": "after the end"})  # nothing follows a terminal event
        assert len(a.events) == len(events)
    finally:
        del workers.WORKERS["slow"]
        mgr.shutdown()


def test_a_worker_that_forgets_its_end_event_is_reported() -> None:
    from reel.server import workers

    workers.WORKERS["quiet"] = lambda payload, emit: emit({"type": "note", "message": "hmm"})
    mgr = JobManager(inline=True)
    try:
        job = mgr.submit("quiet", "quiet", {})
        deadline = time.time() + 3
        while job.status != "error" and time.time() < deadline:
            time.sleep(0.02)
        assert job.status == "error" and "without a result" in job.events[-1]["message"]
    finally:
        del workers.WORKERS["quiet"]
        mgr.shutdown()


# ------------------------------------------------------------------ cache, doctor, command line
def test_cache_stats_and_clearing(client: TestClient) -> None:
    stats = client.get("/api/cache").json()
    assert {"frames", "segments", "tts", "tmp", "total_bytes"} <= set(stats)
    assert client.delete("/api/cache/everything").status_code == 400
    r = client.delete("/api/cache/tts").json()
    assert "removed" in r and r["stats"]["tts"]["files"] == 0


def test_doctor_is_structured(client: TestClient) -> None:
    d = client.get("/api/doctor").json()
    assert d["ffmpeg"]["ok"] and d["skia"]["ok"] and len(d["fonts"]) == 3
    assert {e["name"] for e in d["tts"]} == {"piper", "say", "babble", "elevenlabs"}
    assert {k["name"] for k in d["llm"]["keys"]} == {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
    }
    assert d["counts"]["actions"] == 16
    t = client.post("/api/doctor/test", json={"kind": "tts", "target": "elevenlabs"}).json()
    assert t["ok"] is False and "ELEVENLABS_API_KEY" in t["message"]
    assert (
        client.post("/api/doctor/test", json={"kind": "llm", "target": "openai"}).json()["ok"]
        is False
    )


def test_reel_serve_refuses_to_listen_beyond_this_machine_without_being_asked() -> None:
    from typer.testing import CliRunner

    from reel.cli.main import app as cli

    r = CliRunner().invoke(cli, ["serve", "--host", "0.0.0.0", "--port", "9"])
    assert r.exit_code == 2 and "--allow-remote" in r.output
    assert "serve" in CliRunner().invoke(cli, ["--help"]).output


def test_the_built_app_is_packaged_for_reel_serve() -> None:
    """`reel serve` must work from a checkout without Node: the production build is committed under server/static."""
    static = REPO / "src/reel/server/static"
    index = static / "index.html"
    if not index.exists():
        pytest.skip("the web app has not been built (make ui-build)")
    html = index.read_text()
    assets = sorted((static / "assets").glob("*"))
    assert (
        re.search(r'src="/assets/[^"]+\.js"', html)
        and any(a.suffix == ".js" for a in assets)
        and any(a.suffix == ".css" for a in assets)
    )
    assert (
        "http://" not in html and "https://" not in html
    )  # no external requests: fonts and icons are bundled
