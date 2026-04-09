"""
Tests for mcp_server.auth — BearerTokenMiddleware and make_mutate_guard.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch


# ── helpers ──────────────────────────────────────────────────────────────────

def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _passthrough_app(scope, receive, send):
    """Minimal ASGI app that records it was called."""
    send._called = True
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _http_scope(token: str | None = None, path: str = "/mcp") -> dict:
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {"type": "http", "method": "POST", "path": path, "headers": headers}


def _ws_scope(token: str | None = None) -> dict:
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {"type": "websocket", "path": "/mcp", "headers": headers}


def _collect_responses() -> tuple[list[dict], Any]:
    responses: list[dict] = []

    async def _send(msg: dict) -> None:
        responses.append(msg)

    return responses, _send


# ── BearerTokenMiddleware ────────────────────────────────────────────────────

class TestBearerTokenMiddleware:

    def _make(self, token: str = "secret"):
        from mcp_server.auth import BearerTokenMiddleware
        return BearerTokenMiddleware(_passthrough_app, token=token)

    # -- token present and correct -------------------------------------------

    def test_correct_token_passes(self):
        mw = self._make("secret")
        responses, send = _collect_responses()
        run(mw(_http_scope("secret"), None, send))
        assert responses[0]["status"] == 200

    def test_scheme_case_insensitive(self):
        """RFC 7235 §2.1: auth scheme names are case-insensitive."""
        from mcp_server.auth import BearerTokenMiddleware

        async def _app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        mw = BearerTokenMiddleware(_app, token="secret")
        headers_lower = [(b"authorization", b"bearer secret")]
        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers_lower}
        responses, send = _collect_responses()
        run(mw(scope, None, send))
        assert responses[0]["status"] == 200

    def test_mixed_case_scheme_passes(self):
        from mcp_server.auth import BearerTokenMiddleware

        async def _app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        mw = BearerTokenMiddleware(_app, token="secret")
        scope = {
            "type": "http", "method": "POST", "path": "/mcp",
            "headers": [(b"authorization", b"BEARER secret")],
        }
        responses, send = _collect_responses()
        run(mw(scope, None, send))
        assert responses[0]["status"] == 200

    # -- token missing or wrong -----------------------------------------------

    def test_wrong_token_rejected(self):
        mw = self._make("secret")
        responses, send = _collect_responses()
        run(mw(_http_scope("wrong"), None, send))
        assert responses[0]["status"] == 401
        assert responses[0]["type"] == "http.response.start"

    def test_missing_token_rejected(self):
        mw = self._make("secret")
        responses, send = _collect_responses()
        run(mw(_http_scope(None), None, send))
        assert responses[0]["status"] == 401

    def test_empty_bearer_value_rejected(self):
        from mcp_server.auth import BearerTokenMiddleware

        mw = BearerTokenMiddleware(_passthrough_app, token="secret")
        scope = {
            "type": "http", "method": "POST", "path": "/mcp",
            "headers": [(b"authorization", b"Bearer ")],
        }
        responses, send = _collect_responses()
        run(mw(scope, None, send))
        assert responses[0]["status"] == 401

    def test_401_body_is_json(self):
        mw = self._make("secret")
        responses, send = _collect_responses()
        run(mw(_http_scope("wrong"), None, send))
        body = json.loads(responses[1]["body"])
        assert body["error"] == "unauthorized"

    def test_401_includes_content_length(self):
        mw = self._make("secret")
        responses, send = _collect_responses()
        run(mw(_http_scope("wrong"), None, send))
        header_dict = dict(responses[0]["headers"])
        assert b"content-length" in header_dict

    def test_401_includes_www_authenticate(self):
        mw = self._make("secret")
        responses, send = _collect_responses()
        run(mw(_http_scope("wrong"), None, send))
        header_dict = dict(responses[0]["headers"])
        assert b"www-authenticate" in header_dict

    # -- no token configured (fail-open) --------------------------------------

    def test_no_token_configured_passes_all(self):
        from mcp_server.auth import BearerTokenMiddleware
        mw = BearerTokenMiddleware(_passthrough_app, token="")
        responses, send = _collect_responses()
        run(mw(_http_scope(None), None, send))
        assert responses[0]["status"] == 200

    # -- non-HTTP scopes -------------------------------------------------------

    def test_lifespan_scope_always_passes(self):
        mw = self._make("secret")
        called = []

        async def _app(scope, receive, send):
            called.append(True)

        mw2 = type(mw)(_app, token="secret")
        run(mw2({"type": "lifespan"}, None, None))
        assert called

    # -- WebSocket scope -------------------------------------------------------

    def test_websocket_wrong_token_uses_ws_message_types(self):
        mw = self._make("secret")
        responses, send = _collect_responses()
        run(mw(_ws_scope("wrong"), None, send))
        assert responses[0]["type"] == "websocket.http.response.start"
        assert responses[0]["status"] == 401
        assert responses[1]["type"] == "websocket.http.response.body"

    def test_websocket_correct_token_passes(self):
        from mcp_server.auth import BearerTokenMiddleware

        async def _ws_app(scope, receive, send):
            await send({"type": "websocket.accept"})

        mw = BearerTokenMiddleware(_ws_app, token="secret")
        responses, send = _collect_responses()
        run(mw(_ws_scope("secret"), None, send))
        assert responses[0]["type"] == "websocket.accept"

    # -- timing-safe comparison -----------------------------------------------

    def test_comparison_uses_hmac_compare_digest(self):
        """Verify the comparison is timing-safe (implementation check)."""
        import inspect
        from mcp_server import auth
        source = inspect.getsource(auth.BearerTokenMiddleware.__call__)
        assert "hmac.compare_digest" in source


# ── make_mutate_guard ────────────────────────────────────────────────────────

class TestMakeMutateGuard:

    def _dummy(self, name: str = "dummy_tool"):
        async def _fn(request_id: str, reason: str = "", dry_run: bool = False) -> str:
            return "executed"
        _fn.__name__ = name
        return _fn

    # -- kill-switches off ----------------------------------------------------

    def test_guarded_blocked_when_mutate_disabled(self):
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("guarded")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": False, "MCP_PRIVILEGED_ENABLED": True}):
            result = run(wrapped(request_id="1", reason="test"))
        assert json.loads(result)["error"] == "forbidden"
        assert "MCP_MUTATE_ENABLED" in json.loads(result)["message"]

    def test_privileged_blocked_when_mutate_disabled(self):
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("privileged")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": False, "MCP_PRIVILEGED_ENABLED": True}):
            result = run(wrapped(request_id="1", reason="test"))
        assert json.loads(result)["error"] == "forbidden"

    def test_privileged_blocked_when_privileged_disabled(self):
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("privileged")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": True, "MCP_PRIVILEGED_ENABLED": False}):
            result = run(wrapped(request_id="1", reason="test"))
        body = json.loads(result)
        assert body["error"] == "forbidden"
        assert "MCP_PRIVILEGED_ENABLED" in body["message"]

    def test_guarded_not_blocked_by_privileged_flag_alone(self):
        """MCP_PRIVILEGED_ENABLED=false should NOT block guarded tools."""
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("guarded")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": True, "MCP_PRIVILEGED_ENABLED": False}):
            result = run(wrapped(request_id="1", reason="test"))
        assert result == "executed"

    def test_safe_mutation_blocked_when_mutate_disabled(self):
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("safe_mutation")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": False, "MCP_PRIVILEGED_ENABLED": True}):
            result = run(wrapped(request_id="1", reason="test"))
        assert json.loads(result)["error"] == "forbidden"

    # -- kill-switches on (normal operation) ----------------------------------

    def test_guarded_executes_when_enabled(self):
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("guarded")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": True, "MCP_PRIVILEGED_ENABLED": True}):
            result = run(wrapped(request_id="1", reason="test"))
        assert result == "executed"

    def test_privileged_executes_when_both_enabled(self):
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("privileged")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": True, "MCP_PRIVILEGED_ENABLED": True}):
            result = run(wrapped(request_id="1", reason="test"))
        assert result == "executed"

    # -- dry_run passthrough --------------------------------------------------

    def test_dry_run_bypasses_mutate_kill_switch(self):
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("guarded")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": False, "MCP_PRIVILEGED_ENABLED": False}):
            result = run(wrapped(request_id="1", reason="test", dry_run=True))
        assert result == "executed"

    def test_dry_run_bypasses_privileged_kill_switch(self):
        from mcp_server.auth import make_mutate_guard
        fn = self._dummy()
        wrapped = make_mutate_guard("privileged")(fn)
        with patch("mcp_server.auth.MCP_CONFIG", {"MCP_MUTATE_ENABLED": True, "MCP_PRIVILEGED_ENABLED": False}):
            result = run(wrapped(request_id="1", reason="test", dry_run=True))
        assert result == "executed"

    # -- signature preservation -----------------------------------------------

    def test_signature_preserved(self):
        """gated_handler must expose the same __signature__ as structured_handler."""
        import inspect
        from mcp_server.auth import make_mutate_guard

        async def _tool(request_id: str, reason: str, dry_run: bool = False) -> str:
            return "ok"

        import inspect as _inspect
        sig = _inspect.signature(_tool)
        _tool.__signature__ = sig  # simulate what _make_structured_handler does

        wrapped = make_mutate_guard("guarded")(_tool)
        assert wrapped.__signature__ == sig

    # -- MCPToolSpec.gated_handler integration --------------------------------

    def test_spec_gated_handler_wraps_mutating_tools(self):
        from mcp_server.tool_specs import get_mcp_tool_specs
        specs = {s.name: s for s in get_mcp_tool_specs()}
        mutating_names = [
            "ae.request.add_support_comment",   # safe_mutation
            "ae.request.tag_case_reference",    # safe_mutation
            "ae.request.terminate_running",     # privileged
            "ae.request.restart_failed",        # guarded
        ]
        for name in mutating_names:
            spec = specs[name]
            assert spec.gated_handler is not spec.structured_handler, (
                f"{name} (safety={spec.safety}) gated_handler should differ from structured_handler"
            )

    def test_spec_gated_handler_passthrough_for_read_only(self):
        from mcp_server.tool_specs import get_mcp_tool_specs
        specs = {s.name: s for s in get_mcp_tool_specs()}
        read_spec = specs["ae.request.get_summary"]
        assert read_spec.gated_handler is read_spec.structured_handler
