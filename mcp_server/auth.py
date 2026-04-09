"""
MCP server authentication and authorization.

BearerTokenMiddleware
    ASGI middleware that enforces a static bearer token on all SSE /
    streamable-HTTP requests when MCP_BEARER_TOKEN is configured.  If the
    token is not set the middleware passes all traffic through and logs a
    one-time startup WARNING so the operator is aware.

make_mutate_guard(safety)
    Returns a coroutine decorator that blocks guarded / privileged /
    safe_mutation tool handlers when MCP_MUTATE_ENABLED or
    MCP_PRIVILEGED_ENABLED is false.  dry_run=True calls are always allowed
    since they make no backend changes.
"""
from __future__ import annotations

import hmac
import json
import logging
from functools import wraps
from typing import Any, Callable

from mcp_server.config import MCP_CONFIG

logger = logging.getLogger("ae_mcp.auth")

# ── static response bodies ──────────────────────────────────────────────────

_UNAUTHORIZED: bytes = json.dumps({
    "error": "unauthorized",
    "message": (
        "Missing or invalid Authorization header. "
        "Expected: Authorization: Bearer <MCP_BEARER_TOKEN>"
    ),
}).encode()

# Shared headers for both HTTP and WebSocket rejections (tuple — never mutated).
_UNAUTHORIZED_HEADERS = (
    (b"content-type", b"application/json"),
    (b"www-authenticate", b'Bearer realm="ae-mcp"'),
    (b"content-length", str(len(_UNAUTHORIZED)).encode()),
)

_DENY_MUTATE: str = json.dumps({
    "error": "forbidden",
    "message": (
        "Mutating operations are disabled on this server. "
        "Set MCP_MUTATE_ENABLED=true to enable."
    ),
})

_DENY_PRIVILEGED: str = json.dumps({
    "error": "forbidden",
    "message": (
        "Privileged operations are disabled on this server. "
        "Set MCP_PRIVILEGED_ENABLED=true to enable."
    ),
})


# ── ASGI bearer-token middleware ─────────────────────────────────────────────

class BearerTokenMiddleware:
    """Validate ``Authorization: Bearer <token>`` on every HTTP/WS request.

    If *token* is empty the middleware is effectively a no-op and logs a
    WARNING at startup so the operator knows the surface is unprotected.
    Token comparison uses :func:`hmac.compare_digest` to prevent timing attacks.
    """

    def __init__(self, app, *, token: str) -> None:
        self._app = app
        self._token = token.strip() if token else ""
        if not self._token:
            logger.warning(
                "MCP_BEARER_TOKEN is not configured — the HTTP MCP surface is "
                "unauthenticated.  Set MCP_BEARER_TOKEN in your environment to "
                "require a bearer token on every request."
            )

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        # Non-HTTP scopes (lifespan, etc.) are always passed through.
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        # No token configured — pass through (warned at startup).
        if not self._token:
            await self._app(scope, receive, send)
            return

        headers: dict[bytes, bytes] = dict(scope.get("headers", []))
        raw_auth = headers.get(b"authorization", b"").decode("latin-1")
        # Auth scheme names are case-insensitive (RFC 7235 §2.1).
        presented = raw_auth[7:].strip() if raw_auth.lower().startswith("bearer ") else ""

        if presented and hmac.compare_digest(presented, self._token):
            await self._app(scope, receive, send)
            return

        logger.warning(
            "MCP bearer auth rejected: method=%s path=%s",
            scope.get("method", "?"),
            scope.get("path", "?"),
        )

        # WebSocket and HTTP rejections use different ASGI message types.
        if scope["type"] == "websocket":
            await send({
                "type": "websocket.http.response.start",
                "status": 401,
                "headers": _UNAUTHORIZED_HEADERS,
            })
            await send({"type": "websocket.http.response.body", "body": _UNAUTHORIZED})
        else:
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": _UNAUTHORIZED_HEADERS,
            })
            await send({"type": "http.response.body", "body": _UNAUTHORIZED})


# ── per-tool mutate / privileged gate ───────────────────────────────────────

def make_mutate_guard(safety: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Return a coroutine decorator that gates a tool handler on server-side
    kill-switches.

    *safety* should be ``"safe_mutation"``, ``"guarded"``, or ``"privileged"``.

    * ``MCP_MUTATE_ENABLED=false`` blocks all three tiers.
    * ``MCP_PRIVILEGED_ENABLED=false`` additionally blocks ``"privileged"``
      even when ``MCP_MUTATE_ENABLED`` is true.
    * ``dry_run=True`` calls are always passed through regardless of either
      flag, because they make no backend changes.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        async def _guarded(*args: Any, **kwargs: Any) -> Any:
            # dry_run calls are read-only introspection — never block them.
            if kwargs.get("dry_run"):
                return await fn(*args, **kwargs)

            if safety == "privileged" and not MCP_CONFIG["MCP_PRIVILEGED_ENABLED"]:
                logger.warning(
                    "Blocked privileged tool call (MCP_PRIVILEGED_ENABLED=false): %s",
                    fn.__name__,
                )
                return _DENY_PRIVILEGED

            if not MCP_CONFIG["MCP_MUTATE_ENABLED"]:
                logger.warning(
                    "Blocked mutating tool call (MCP_MUTATE_ENABLED=false): %s",
                    fn.__name__,
                )
                return _DENY_MUTATE

            return await fn(*args, **kwargs)

        # Preserve an explicit __signature__ if _make_structured_handler set one,
        # so FastMCP can still derive the correct input schema from the wrapper.
        if hasattr(fn, "__signature__"):
            _guarded.__signature__ = fn.__signature__  # type: ignore[attr-defined]

        return _guarded

    return decorator
