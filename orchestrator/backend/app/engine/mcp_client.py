"""MCP client using Streamable HTTP transport.

Connects to the parent project's MCP server via the standard MCP protocol
over Streamable HTTP, replacing the previous raw httpx REST bridge.

The MCP server must be running with ``--transport streamable-http``.
Default endpoint: ``http://localhost:8000/mcp``
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_tool_defs_cache: list[dict[str, Any]] | None = None


async def _call_tool_async(
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url=settings.mcp_server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)

    content_parts = []
    for block in result.content:
        if hasattr(block, "text"):
            content_parts.append(block.text)

    raw_text = "\n".join(content_parts)
    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        return {"result": raw_text}


async def _list_tools_async() -> list[dict[str, Any]]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url=settings.mcp_server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()

    tools = []
    for tool in result.tools:
        tools.append({
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema if hasattr(tool, "inputSchema") else {"type": "object", "properties": {}},
        })
    return tools


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Get the running event loop or create a new one for sync contexts."""
    try:
        loop = asyncio.get_running_loop()
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Synchronous wrapper: call an MCP tool via Streamable HTTP."""
    try:
        loop = _get_or_create_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _call_tool_async(tool_name, arguments))
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(_call_tool_async(tool_name, arguments))
    except Exception as exc:
        logger.error("MCP call_tool(%s) failed: %s", tool_name, exc)
        return {"error": str(exc)}


def list_tools() -> list[dict[str, Any]]:
    """Synchronous wrapper: list available MCP tools."""
    global _tool_defs_cache
    if _tool_defs_cache is not None:
        return _tool_defs_cache

    try:
        loop = _get_or_create_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _list_tools_async())
                result = future.result(timeout=30)
        else:
            result = loop.run_until_complete(_list_tools_async())

        _tool_defs_cache = result
        logger.info("Loaded %d tool definitions from MCP server", len(result))
        return result
    except Exception as exc:
        logger.error("MCP list_tools failed: %s", exc)
        return []


def get_openai_style_tool_defs(tool_names: list[str]) -> list[dict[str, Any]]:
    """Load tool definitions from MCP and return in OpenAI function-calling format.

    Used by the ReAct loop to feed tool schemas to LLM providers.
    """
    all_tools = list_tools()
    tool_map = {t["name"]: t for t in all_tools}

    result = []
    for name in tool_names:
        tool = tool_map.get(name)
        if not tool:
            logger.warning("Tool '%s' not found in MCP registry", name)
            continue
        result.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        })
    return result
