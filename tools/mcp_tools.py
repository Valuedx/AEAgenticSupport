"""
Register AutomationEdge MCP tools with the main app tool registry.

For co-located deployments, the main app consumes the shared local MCP tool
specs from `mcp_server.tool_specs`. If `AE_MCP_SERVER_URL` is configured, the
app instead behaves as an MCP client: it discovers tools remotely via
`list_tools()` and executes them via `call_tool()`.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import logging
from typing import Any, Callable

from config.settings import CONFIG
from tools.base import ToolDefinition, ToolResult
from tools.catalog import ToolCatalogEntry
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.mcp_tools")

_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _get_mcp_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp_tool")
    return _executor


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    future = _get_mcp_executor().submit(asyncio.run, coro)
    return future.result()


def _normalize_mcp_transport(raw_transport: Any) -> str:
    normalized = str(raw_transport or "streamable-http").strip().lower().replace("_", "-")
    if normalized in {"streamable-http", "sse"}:
        return normalized
    logger.warning("Unsupported AE_MCP_SERVER_TRANSPORT=%r; defaulting to streamable-http", raw_transport)
    return "streamable-http"


def _get_remote_mcp_server_url() -> str:
    return str(CONFIG.get("AE_MCP_SERVER_URL", "") or "").strip()


def _get_remote_mcp_transport() -> str:
    return _normalize_mcp_transport(CONFIG.get("AE_MCP_SERVER_TRANSPORT", "streamable-http"))


def _parse_remote_mcp_headers() -> dict[str, str]:
    raw_headers = str(CONFIG.get("AE_MCP_SERVER_HEADERS_JSON", "") or "").strip()
    if not raw_headers:
        return {}
    try:
        parsed = json.loads(raw_headers)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AE_MCP_SERVER_HEADERS_JSON is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("AE_MCP_SERVER_HEADERS_JSON must decode to a JSON object")
    headers: dict[str, str] = {}
    for key, value in parsed.items():
        clean_key = str(key or "").strip()
        if not clean_key or value is None:
            continue
        headers[clean_key] = str(value)
    return headers


def _get_remote_mcp_timeout_seconds() -> float:
    raw_timeout = CONFIG.get(
        "AE_MCP_SERVER_TIMEOUT_SECONDS",
        CONFIG.get("AE_TIMEOUT_SECONDS", 30),
    )
    try:
        return float(raw_timeout or 30)
    except (TypeError, ValueError):
        logger.warning("Invalid AE_MCP_SERVER_TIMEOUT_SECONDS=%r; defaulting to 30", raw_timeout)
        return 30.0


def _serialize_annotations(annotations: Any) -> dict[str, Any]:
    if not annotations:
        return {}
    if hasattr(annotations, "model_dump"):
        return annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(annotations, dict):
        return dict(annotations)
    return {}


def _get_remote_tool_meta(remote_tool: Any) -> dict[str, Any]:
    meta = getattr(remote_tool, "meta", None)
    return dict(meta or {})


def _derive_remote_tool_tier(meta: dict[str, Any], annotations: dict[str, Any]) -> str:
    explicit = str(meta.get("tier", "") or "").strip()
    if explicit:
        return explicit
    if annotations.get("readOnlyHint") is True:
        return "read_only"
    if annotations.get("destructiveHint") is True:
        return "high_risk"
    return "low_risk"


def _derive_remote_tool_safety(meta: dict[str, Any], annotations: dict[str, Any]) -> str:
    explicit = str(meta.get("safety", "") or "").strip()
    if explicit:
        return explicit
    if annotations.get("readOnlyHint") is True:
        return "safe_read"
    if annotations.get("destructiveHint") is True:
        return "guarded"
    return ""


def _coerce_input_examples(raw_examples: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_examples, list):
        return []
    return [dict(example) for example in raw_examples[:2] if isinstance(example, dict)]


def _normalize_remote_content_block(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(block, dict):
        return dict(block)
    return {"type": "text", "text": str(block)}


def _extract_remote_error_message(payload: dict[str, Any]) -> str:
    explicit_error = str(payload.get("error", "") or "").strip()
    if explicit_error:
        return explicit_error
    if not payload.get("_mcp_is_error"):
        return ""
    for block in payload.get("_mcp_content", []) or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text", "") or "").strip()
            if text:
                return text
    return "Remote MCP server reported a tool error."


def _normalize_remote_call_result(raw_result: Any) -> dict[str, Any]:
    if hasattr(raw_result, "model_dump"):
        raw = raw_result.model_dump(mode="json", by_alias=True, exclude_none=True)
    elif isinstance(raw_result, dict):
        raw = dict(raw_result)
    else:
        return {"result": raw_result}

    structured = raw.get("structuredContent")
    if isinstance(structured, dict):
        payload = dict(structured)
    elif structured is not None:
        payload = {"result": structured}
    else:
        payload = {}

    if raw.get("content"):
        payload["_mcp_content"] = [
            _normalize_remote_content_block(block)
            for block in raw.get("content", []) or []
        ]
    if raw.get("_meta"):
        payload["_mcp_meta"] = dict(raw.get("_meta") or {})
    if "isError" in raw:
        payload["_mcp_is_error"] = bool(raw.get("isError"))
    return payload or raw


def _get_streamable_http_client():
    try:
        from mcp.client.streamable_http import streamable_http_client

        return streamable_http_client
    except ImportError:
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client


async def _with_remote_mcp_session(operation: Callable[[Any], Any]) -> Any:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    url = _get_remote_mcp_server_url()
    if not url:
        raise ValueError("AE_MCP_SERVER_URL must be set for remote MCP mode")

    headers = _parse_remote_mcp_headers()
    timeout = _get_remote_mcp_timeout_seconds()
    transport = _get_remote_mcp_transport()
    client_factory = _get_streamable_http_client() if transport == "streamable-http" else sse_client

    async with client_factory(url, headers=headers or None, timeout=timeout) as streams:
        read_stream = streams[0]
        write_stream = streams[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await operation(session)


async def _list_remote_mcp_tools() -> tuple[Any, ...]:
    async def _op(session: Any) -> tuple[Any, ...]:
        tools: list[Any] = []
        cursor: str | None = None
        while True:
            result = await session.list_tools(cursor=cursor)
            tools.extend(list(result.tools or []))
            cursor = result.nextCursor
            if not cursor:
                break
        return tuple(tools)

    return await _with_remote_mcp_session(_op)


def _discover_remote_mcp_tools() -> tuple[Any, ...]:
    return tuple(_run_async(_list_remote_mcp_tools()))


async def _call_remote_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    async def _op(session: Any) -> Any:
        return await session.call_tool(tool_name, arguments=arguments)

    return await _with_remote_mcp_session(_op)


def _run_mcp_tool(tool_name: str, async_fn: Callable[..., Any], **kwargs: Any) -> ToolResult:
    sig = inspect.signature(async_fn)
    param_names = set(sig.parameters)
    filtered = {k: v for k, v in kwargs.items() if k in param_names and v is not None}
    for k, v in kwargs.items():
        if k in param_names and k not in filtered and v is None:
            filtered[k] = v

    try:
        data = _run_async(async_fn(**filtered))
    except Exception as exc:
        logger.exception("MCP tool %s failed", tool_name)
        return ToolResult(success=False, error=str(exc), tool_name=tool_name)

    if isinstance(data, dict) and data.get("error"):
        return ToolResult(success=False, data=data, error=str(data["error"]), tool_name=tool_name)
    return ToolResult(success=True, data=data if isinstance(data, dict) else {"result": data}, tool_name=tool_name)


def _run_remote_mcp_tool(tool_name: str, **kwargs: Any) -> ToolResult:
    try:
        payload = _normalize_remote_call_result(
            _run_async(_call_remote_mcp_tool(tool_name, kwargs))
        )
    except Exception as exc:
        logger.exception("Remote MCP tool %s failed", tool_name)
        return ToolResult(success=False, error=str(exc), tool_name=tool_name)

    error = _extract_remote_error_message(payload)
    if error:
        return ToolResult(success=False, data=payload, error=error, tool_name=tool_name)
    return ToolResult(success=True, data=payload, tool_name=tool_name)


def _register_local_mcp_tools() -> None:
    try:
        from mcp_server.tool_specs import get_mcp_tool_specs
    except ImportError as exc:
        logger.warning("Local MCP tools not available (mcp_server or mcp package missing): %s", exc)
        return

    specs = get_mcp_tool_specs()
    eager_count = 0

    for spec in specs:
        hydration_mode = "eager" if spec.always_available else "lazy"
        definition = ToolDefinition(
            name=spec.name,
            description=spec.resolved_description,
            category=spec.app_category,
            tier=spec.tier,
            parameters=spec.parameter_properties,
            required_params=spec.required_params,
            always_available=spec.always_available,
            use_when=spec.use_when,
            avoid_when=spec.avoid_when,
            input_examples=spec.input_examples[:2],
            metadata={
                "source": "mcp",
                "title": spec.resolved_title,
                "tags": spec.tags,
                "mcp_category": spec.mcp_category,
                "safety": spec.safety,
                "hydration_mode": hydration_mode,
                "latency_class": spec.latency_class,
                "annotations": spec.serialized_annotations,
                "mcp_meta": spec.meta,
                "output_schema": spec.output_schema,
                "structured_output": True,
                "mcp_connection_mode": "local",
            },
        )

        if spec.always_available:
            eager_count += 1

        def _make_handler_factory(_spec):
            def _factory():
                def _handler(**kwargs):
                    return _run_mcp_tool(_spec.name, _spec.structured_handler, **kwargs)

                return _handler

            return _factory

        tool_registry.register_catalog_entry(
            ToolCatalogEntry.from_definition(
                definition,
                source_ref=spec.name,
                hydration_mode=hydration_mode,
                latency_class=spec.latency_class,
                mutating=spec.is_mutating,
            ),
            handler_factory=_make_handler_factory(spec),
            hydrate=spec.always_available,
        )

    logger.info(
        "Cataloged %d local MCP tools with main app registry (%d eager, %d lazy)",
        len(specs),
        eager_count,
        len(specs) - eager_count,
    )


def _register_remote_mcp_tools() -> None:
    try:
        remote_tools = _discover_remote_mcp_tools()
    except ImportError as exc:
        logger.warning("Remote MCP client support is unavailable (mcp package missing): %s", exc)
        return
    except Exception as exc:
        logger.warning("Could not discover remote MCP tools from %s: %s", _get_remote_mcp_server_url(), exc)
        return

    eager_count = 0
    transport = _get_remote_mcp_transport()

    for remote_tool in remote_tools:
        meta = _get_remote_tool_meta(remote_tool)
        annotations = _serialize_annotations(getattr(remote_tool, "annotations", None))
        tier = _derive_remote_tool_tier(meta, annotations)
        safety = _derive_remote_tool_safety(meta, annotations)
        always_available = bool(meta.get("always_available", False))
        hydration_mode = "eager" if always_available else "lazy"
        latency_class = str(meta.get("latency_class", "medium") or "medium")
        output_schema = dict(getattr(remote_tool, "outputSchema", None) or {})
        parameters = dict(getattr(remote_tool, "inputSchema", {}) or {})
        use_when = str(meta.get("use_when", meta.get("useWhen", "")) or "")
        avoid_when = str(meta.get("avoid_when", meta.get("avoidWhen", "")) or "")
        input_examples = _coerce_input_examples(
            meta.get("input_examples", meta.get("inputExamples", []))
        )
        definition = ToolDefinition(
            name=remote_tool.name,
            description=str(getattr(remote_tool, "description", "") or f"Remote MCP tool {remote_tool.name}"),
            category=str(meta.get("app_category", "status") or "status"),
            tier=tier,
            parameters=dict(parameters.get("properties", {}) or {}),
            required_params=list(parameters.get("required", []) or []),
            always_available=always_available,
            use_when=use_when,
            avoid_when=avoid_when,
            input_examples=input_examples,
            metadata={
                "source": "mcp",
                "title": str(getattr(remote_tool, "title", "") or remote_tool.name),
                "tags": list(meta.get("tags", []) or []),
                "mcp_category": str(meta.get("category", "") or ""),
                "safety": safety,
                "hydration_mode": hydration_mode,
                "latency_class": latency_class,
                "annotations": annotations,
                "mcp_meta": meta,
                "output_schema": output_schema,
                "structured_output": bool(meta.get("structured_output", bool(output_schema))),
                "mcp_connection_mode": "remote",
                "mcp_transport": transport,
            },
        )

        if always_available:
            eager_count += 1

        def _make_handler_factory(_tool_name: str):
            def _factory():
                def _handler(**kwargs):
                    return _run_remote_mcp_tool(_tool_name, **kwargs)

                return _handler

            return _factory

        tool_registry.register_catalog_entry(
            ToolCatalogEntry.from_definition(
                definition,
                source_ref=remote_tool.name,
                hydration_mode=hydration_mode,
                latency_class=latency_class,
                mutating=bool(meta.get("mutating", tier != "read_only")),
            ),
            handler_factory=_make_handler_factory(remote_tool.name),
            hydrate=always_available,
        )

    logger.info(
        "Cataloged %d remote MCP tools from %s with main app registry (%d eager, %d lazy, transport=%s)",
        len(remote_tools),
        _get_remote_mcp_server_url(),
        eager_count,
        len(remote_tools) - eager_count,
        transport,
    )


def _register_mcp_tools() -> None:
    if not CONFIG.get("AE_MCP_TOOLS_ENABLED", False):
        return

    if _get_remote_mcp_server_url():
        _register_remote_mcp_tools()
        return

    _register_local_mcp_tools()


_register_mcp_tools()
