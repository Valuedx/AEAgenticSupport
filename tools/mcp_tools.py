"""
Register AutomationEdge MCP tools with the main app tool registry.

The main app now consumes the same shared MCP tool specs as the standalone
server, so schema, safety, and structured-output metadata stay aligned.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
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
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = _get_mcp_executor().submit(asyncio.run, coro)
            return future.result()
        return asyncio.run(coro)
    except RuntimeError:
        return asyncio.run(coro)


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


def _register_mcp_tools() -> None:
    if not CONFIG.get("AE_MCP_TOOLS_ENABLED", False):
        return

    try:
        from mcp_server.tool_specs import get_mcp_tool_specs
    except ImportError as exc:
        logger.warning("MCP tools not available (mcp_server or mcp package missing): %s", exc)
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
        "Cataloged %d MCP tools with main app registry (%d eager, %d lazy)",
        len(specs),
        eager_count,
        len(specs) - eager_count,
    )


_register_mcp_tools()
