# How the AutomationEdge MCP Server Works

This document explains the internal mechanics of the MCP server from startup through tool execution. It is intended for developers maintaining the server or extending its tool surface.

---

## Contents

1. [Startup sequence](#1-startup-sequence)
2. [Transport modes](#2-transport-modes)
3. [HTTP authentication layer](#3-http-authentication-layer)
4. [Tool registration pipeline](#4-tool-registration-pipeline)
5. [Safety tiers and mutate kill-switches](#5-safety-tiers-and-mutate-kill-switches)
6. [Request execution flow](#6-request-execution-flow)
7. [AutomationEdge backend client](#7-automationedge-backend-client)
8. [Co-located bridge mode](#8-co-located-bridge-mode)
9. [Schema and metadata flow](#9-schema-and-metadata-flow)
10. [Configuration reference](#10-configuration-reference)

---

## 1. Startup sequence

```
python -m mcp_server [--transport ...] [--host ...] [--port ...]
```

`__main__.py` is the entry point. In order:

1. Parse CLI args (transport, host, port, log-level), falling back to `MCP_CONFIG` values.
2. Configure `logging`.
3. Import `mcp_server.server` — this triggers tool registration as a module-level side effect (see §4).
4. **stdio** — call `mcp.run(transport="stdio")` directly; FastMCP reads from stdin and writes to stdout.
5. **SSE / streamable-HTTP** — call `mcp.sse_app()` or `mcp.streamable_http_app()` to get the Starlette ASGI app, wrap it with `BearerTokenMiddleware`, then hand the wrapped app to `uvicorn.run()`.

The distinction between stdio and HTTP is deliberate: stdio never passes through network code, so the bearer middleware is never instantiated for it.

---

## 2. Transport modes

| Mode | Use case | Auth |
|------|----------|------|
| `stdio` | Cursor, Claude Desktop, local CLI | No HTTP — process-level trust |
| `streamable-http` | Remote MCP clients, main app bridge over network | `MCP_BEARER_TOKEN` |
| `sse` | Older MCP clients that require SSE | `MCP_BEARER_TOKEN` |

The MCP endpoint URL for HTTP modes is `http://<host>:<port>/mcp`.

---

## 3. HTTP authentication layer

**File:** `mcp_server/auth.py` — `BearerTokenMiddleware`

```
HTTP client
    │  Authorization: Bearer <token>
    ▼
BearerTokenMiddleware.__call__()
    ├─ scope type not http/websocket  →  pass through (lifespan etc.)
    ├─ MCP_BEARER_TOKEN not set       →  pass through + startup WARNING
    ├─ token matches (hmac.compare_digest)  →  pass through
    └─ token missing / wrong          →  401 JSON immediately
                                          (websocket scope: websocket.http.response.*)
    ▼
FastMCP Starlette app
```

Key properties:

- **Timing-safe:** `hmac.compare_digest` prevents token enumeration via response timing.
- **Case-insensitive scheme:** `bearer`, `Bearer`, `BEARER` are all accepted (RFC 7235 §2.1); the token value itself is compared as-is.
- **Fail-open by design:** if `MCP_BEARER_TOKEN` is empty the middleware warns at startup and lets all traffic through. This keeps stdio and trusted-LAN deployments zero-config. Set the token for any network-exposed deployment.
- **Scope-aware rejection:** HTTP uses `http.response.*` messages; WebSocket uses `websocket.http.response.*`.

---

## 4. Tool registration pipeline

Every tool goes through three transformation steps before FastMCP registers it.

```
tool function (e.g. request_restart_failed)
        │
        │  _make_structured_handler()                     tool_specs.py
        │  • captures original signature
        │  • wraps with normalize_tool_result()
        │  • sets return annotation → dict[str, Any]
        ▼
MCPToolSpec.structured_handler   (cached_property)
        │
        │  make_mutate_guard(safety)                       auth.py
        │  • only for safe_mutation / guarded / privileged
        │  • reads MCP_MUTATE_ENABLED, MCP_PRIVILEGED_ENABLED
        │  • dry_run=True bypasses the gate entirely
        │  • preserves __signature__ for schema inference
        ▼
MCPToolSpec.gated_handler        (cached_property)
        │
        │  mcp.add_tool(spec.gated_handler, ...)          server.py
        │  registered.parameters = spec.input_schema       ← schema override
        │  registered.annotations = spec.annotations       ← MCP annotations
        ▼
FastMCP tool registry
```

### Why the schema is overridden after `add_tool`

FastMCP derives the JSON schema from the handler's Python signature. The `input_schema` property on `MCPToolSpec` enriches that base schema with:

- Human-readable `description` strings per parameter (from `_COMMON_PARAMETER_DESCRIPTIONS` and per-tool overrides)
- `examples` array (from `input_examples` on the spec)

FastMCP doesn't accept these enrichments during `add_tool`, so they are patched directly onto the registered `Tool` object immediately after registration.

### Tool spec definition

`get_mcp_tool_specs()` in `tool_specs.py` returns all 116 specs as a single `@lru_cache`-d tuple. Each spec is built with `_spec(name, handler, mcp_category, safety)`, optionally merged with entries from `_CURATED_TOOL_OVERRIDES` (title, description, use_when, avoid_when, input_examples, parameter_docs, extra_tags).

---

## 5. Safety tiers and mutate kill-switches

### Tiers

| Safety value | Tier | MCP `destructiveHint` | Gate |
|---|---|---|---|
| `safe_read` | read-only | `false` | none |
| `safe_mutation` | low-risk | `false` | `MCP_MUTATE_ENABLED` |
| `guarded` | medium-risk | `true` | `MCP_MUTATE_ENABLED` |
| `privileged` | high-risk | `true` | `MCP_MUTATE_ENABLED` + `MCP_PRIVILEGED_ENABLED` |

### Gate logic (`make_mutate_guard`)

```python
if kwargs.get("dry_run"):          # always allow — no backend state changes
    return await fn(...)

if safety == "privileged" and not MCP_CONFIG["MCP_PRIVILEGED_ENABLED"]:
    return DENY_PRIVILEGED         # {"error": "forbidden", ...}

if not MCP_CONFIG["MCP_MUTATE_ENABLED"]:
    return DENY_MUTATE             # {"error": "forbidden", ...}

return await fn(...)               # execute
```

`dry_run=True` calls are always passed through regardless of either flag. This lets operators leave `MCP_MUTATE_ENABLED=false` while still allowing read-only preflight checks.

### Where the gate lives

The guard is applied in `MCPToolSpec.gated_handler`, not in `server.py`. This means it is shared across **all registration paths** — the standalone FastMCP server and the co-located local bridge in `tools/mcp_tools.py` both call `spec.gated_handler`, so the kill-switches work identically in both deployment modes.

---

## 6. Request execution flow

### Standalone HTTP server

```
MCP client  →  HTTP POST /mcp
              Authorization: Bearer <token>
    │
    ▼
BearerTokenMiddleware                auth.py
    ├─ 401 if token invalid
    └─ pass through
    ▼
FastMCP Starlette app                (mcp.streamable_http_app / sse_app)
    │  deserialise tools/call request
    ▼
spec.gated_handler(**kwargs)         auth.py + tool_specs.py
    ├─ MCP_MUTATE_ENABLED / MCP_PRIVILEGED_ENABLED check
    └─ pass through
    ▼
spec.structured_handler(**kwargs)    tool_specs.py
    │  normalize_tool_result()
    ▼
raw tool function(**kwargs)          tools/*.py
    │  get_ae_client().some_method()
    ▼
AEClient._request()                  ae_client.py
    │  session-token or API-key auth
    │  TLS verification (AE_VERIFY_SSL)
    ▼
AutomationEdge REST API
```

### Co-located local bridge

When `AE_MCP_TOOLS_ENABLED=true` and `AE_MCP_SERVER_URL` is not set, `tools/mcp_tools.py` registers the same specs directly into the main app's tool registry. The handler factory at registration time is:

```python
def _handler(**kwargs):
    return _run_mcp_tool(spec.name, spec.gated_handler, **kwargs)
```

`_run_mcp_tool` filters kwargs to the handler's signature then calls `spec.gated_handler(**filtered)` in a thread executor (the main app is sync; the tool handlers are async).

### Remote bridge

When `AE_MCP_SERVER_URL` is set, the main app acts as an MCP client: it discovers tools via `list_tools()` and executes them via `call_tool()` over HTTP. In this case `spec.gated_handler` is not involved — enforcement is entirely on the server receiving the remote call.

---

## 7. AutomationEdge backend client

**File:** `mcp_server/ae_client.py` — `AEClient`

One singleton `AEClient` is shared across all tool calls (`get_ae_client()`). It handles:

- **Session-token auth** (username + password): `POST /authenticate` → session token cached with TTL, auto-refreshed on 401.
- **API-key auth**: `Authorization: Bearer <AE_API_KEY>` header, no session management.
- **TLS:** controlled by `AE_VERIFY_SSL` (defaults `true`; set `false` for self-signed certs).
- **Endpoint fallback:** `_try_paths()` tries an ordered list of URL variants (org-scoped, global, with/without rest base) and caches the first successful path per `(method, paths_tuple)` key.

The client is not aware of the MCP caller's identity. All AE operations run under the single configured backend credential. `requested_by` and `case_id` parameters accepted by mutating tools are passed as comment text or included in the JSON body — they are not verified against an authenticated MCP principal.

---

## 8. Co-located bridge mode

`tools/mcp_tools.py` runs at main app startup. It checks `AE_MCP_TOOLS_ENABLED` then branches:

```
AE_MCP_TOOLS_ENABLED=false  →  no-op

AE_MCP_TOOLS_ENABLED=true
    AE_MCP_SERVER_URL set    →  _register_remote_mcp_tools()
                                 discover tools via MCP list_tools()
                                 register remote call wrappers
    AE_MCP_SERVER_URL blank  →  _register_local_mcp_tools()
                                 iterate get_mcp_tool_specs()
                                 register spec.gated_handler wrappers
```

Both paths create entries in the main app's `ToolCatalog`. Tools marked `always_available` are hydrated eagerly; the rest are lazy and activated by RAG retrieval or `discover_tools`.

---

## 9. Schema and metadata flow

Each `MCPToolSpec` exposes several computed properties. The relationship is:

```
handler.__signature__
    │
    └─ FastMCPTool.from_function(structured_handler)   →  _base_input_schema
                                                            (raw JSON schema from signature)
    │
    └─ input_schema property
        • injects parameter descriptions
        • injects input_examples
        • returned as registered Tool.parameters

annotations property (ToolAnnotations)
    • readOnlyHint, destructiveHint, idempotentHint, openWorldHint
    • title, appCategory
    • returned as registered Tool.annotations

meta cached_property
    • source, category, safety, tier, mutating, always_available
    • tags, use_when, avoid_when, input_examples, latency_class
    • serialised into mcp_meta on ToolCatalogEntry (bridge path)
    • exposed as Tool._meta over MCP protocol (remote bridge path)
```

Tags are derived from the tool name, mcp_category, and safety value by splitting on `.` and `_`, lowercasing, and removing stopwords (`ae`, `get`, `list`, `by`, …). Per-spec `extra_tags` are appended after deduplication.

---

## 10. Configuration reference

All values are read from environment variables at process startup via `mcp_server/config.py`. The `.env` file in the project root is loaded automatically if present.

### AutomationEdge connection

| Variable | Default | Description |
|---|---|---|
| `AE_BASE_URL` | `https://localhost:8443` | AE server base URL |
| `AE_USERNAME` | — | Session-auth username (also `T4_USERNAME`) |
| `AE_PASSWORD` | — | Session-auth password (also `T4_PASSWORD`) |
| `AE_ORG_CODE` | — | Organisation code (also `T4_ORG_CODE`) |
| `AE_API_KEY` | — | API key (alternative to username/password) |
| `AE_VERIFY_SSL` | `true` | Set `false` for self-signed certs only |
| `AE_TIMEOUT_SECONDS` | `30` | HTTP timeout for AE API calls |
| `AE_TOKEN_TTL_SECONDS` | `1800` | Session token validity window |
| `AE_REST_BASE_PATH` | `/aeengine/rest` | REST API path prefix |

### MCP server

| Variable | Default | Description |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | Default transport (`stdio`, `sse`, `streamable-http`) |
| `MCP_HOST` | `127.0.0.1` | Bind host for HTTP/SSE transport |
| `MCP_PORT` | `8000` | Bind port for HTTP/SSE transport |

### Authentication and authorisation

| Variable | Default | Description |
|---|---|---|
| `MCP_BEARER_TOKEN` | — | Required `Authorization: Bearer` token for HTTP transports; empty = unauthenticated (startup WARNING) |
| `MCP_MUTATE_ENABLED` | `true` | `false` blocks all `safe_mutation`, `guarded`, and `privileged` tool calls (dry_run exempt) |
| `MCP_PRIVILEGED_ENABLED` | `true` | `false` blocks only `privileged`-tier tools; lower tiers unaffected |
