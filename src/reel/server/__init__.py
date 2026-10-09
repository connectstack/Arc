"""Reel Studio's local server: the engine behind a small HTTP API and the built web UI.

``reel serve`` (see :mod:`reel.cli.main`) starts it; :func:`reel.server.app.create_app` builds the ASGI app.
It needs the optional web packages (``pip install -e ".[ui]"``: FastAPI + uvicorn); everything else in reel does not.

Design rules (they are the reason for most of the code here):

* **local only**: it listens on 127.0.0.1, every API call needs a per-launch token (a cookie set by the launch link or a
  bearer header), the ``Host`` header must be a loopback name and unsafe requests must come from this origin;
* **the engine is the source of truth**: lint, previews, audio and renders call the same library functions the command
  line does, so a spec behaves identically in the UI and in ``reel render``;
* **long work is a job**: renders and voice generation run in their own process (so Cancel works and the server stays
  responsive), report progress as server-sent events, and survive a page reload;
* **keys never travel**: API keys stay in the environment / ``.env`` files read by the engine; no route accepts, returns
  or logs one.
"""

from __future__ import annotations
