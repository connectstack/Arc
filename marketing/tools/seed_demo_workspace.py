"""Fill a Reel Studio workspace with a few real reels (examples + scripts planned by the offline planner) for the screenshots and the video.

Usage: python seed_demo_workspace.py [http://127.0.0.1:8768]
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8768"


def call(path, body=None, method=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method or ("POST" if body is not None else "GET"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def plan(script, title, style, seed):
    job = call("/api/generate", {"script": script, "style": style, "target_duration": 50, "seed": seed, "planner": "offline", "enrich": True, "repairs": 3, "verbatim": True})["job_id"]
    for _ in range(200):
        snap = call(f"/api/jobs/{job}")
        if snap["status"] in ("done", "error"):
            break
        time.sleep(0.3)
    if snap["status"] != "done":
        raise SystemExit(f"planning '{title}' failed: {snap.get('error')}")
    spec = snap["result"]["spec"]
    spec["meta"]["title"] = title
    spec["meta"]["style"] = style
    doc = call("/api/projects", {"spec": spec, "script": script, "title": title})
    print("created", doc["id"], title, style)


ex = call("/api/examples")
for sid in ("story_50s", "explainer_45s"):
    doc = call("/api/projects", {"from_example": sid})
    print("created", doc["id"], doc["spec"]["meta"]["title"], doc["spec"]["meta"]["style"])

scripts = {s["id"]: s for s in ex["scripts"]}
plan(scripts["hindi_khoya_chhata"]["text"], "खोया हुआ छाता", "flat_vector", 7)
plan(scripts["script_story"]["text"], "The Midnight Cookie Mystery", "stickman", 11)
