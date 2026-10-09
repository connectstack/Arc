"""Who may talk to the server: the person who launched it, from this machine, and nobody else.

Three independent checks, all in one small ASGI middleware (no framework features, so the rules are easy to read):

1. **Host header**: only the loopback names + the configured port.  A web page on another site that resolves its own
   domain to 127.0.0.1 ("DNS rebinding") sends *its* host name, so it is refused (421).
2. **Token**: every ``/api`` call must carry the per-launch token, as ``Authorization: Bearer <token>`` or as the
   ``reel_token`` cookie.  The cookie is set by the launch link (``/?token=...``) as ``HttpOnly; SameSite=Strict``, which is
   what lets ``<img>`` and ``<video>`` elements load frames and renders without any script handling the secret.
3. **Origin header** on unsafe methods: if a browser sends one, it must be this server's own origin.

Static files (the web app's code) are public; the app's entry page and every API route need the token.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, MutableMapping
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlparse

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

COOKIE = "reel_token"
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: the page the web app is served with: everything it loads comes from this server, nothing from anywhere else
CSP = (
    "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


def _header(scope: Scope, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return str(v.decode("latin-1"))
    return ""


def token_from_request(scope: Scope) -> str | None:
    """The token a request presents: the bearer header, else the cookie."""
    auth = _header(scope, b"authorization")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    raw = _header(scope, b"cookie")
    if raw:
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(COOKIE)
        return morsel.value if morsel else None
    return None


def token_ok(expected: str | None, presented: str | None) -> bool:
    if expected is None:
        return True
    return presented is not None and hmac.compare_digest(expected.encode(), presented.encode())


async def _json_response(send: Send, status: int, body: dict[str, Any]) -> None:
    raw = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})


class SecurityMiddleware:
    def __init__(self, app: Any, *, token: str | None, allowed_hosts: frozenset[str]) -> None:
        self.app = app
        self.token = token
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        host = _header(scope, b"host").lower()
        if host not in self.allowed_hosts:
            await _json_response(
                send,
                421,
                {
                    "detail": "wrong host",
                    "hint": "open Reel Studio from the link `reel serve` printed (http://127.0.0.1:<port>/...)",
                },
            )
            return
        path: str = scope["path"]
        method: str = scope["method"]
        if method in UNSAFE:
            origin = _header(scope, b"origin")
            if (
                origin
                and origin != "null"
                and urlparse(origin).netloc.lower() not in self.allowed_hosts
            ):
                await _json_response(send, 403, {"detail": "cross-origin request refused"})
                return
        if path.startswith("/api/") and not token_ok(self.token, token_from_request(scope)):
            await _json_response(
                send,
                401,
                {
                    "detail": "not authorised",
                    "hint": "open Reel Studio with the link printed by `reel serve` (it carries the access token)",
                },
            )
            return

        api = path.startswith("/api/")

        async def send_with_headers(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                have = {k.lower() for k, _ in headers}
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                if api and b"cache-control" not in have:
                    extra.append((b"cache-control", b"no-store"))
                content_type = dict(headers).get(b"content-type", b"")
                if content_type.startswith(b"text/html"):
                    extra.append((b"content-security-policy", CSP.encode()))
                message = {**message, "headers": headers + extra}
            await send(message)

        await self.app(scope, receive, send_with_headers)
