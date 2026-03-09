"""
AutomationEdge MCP Server.

Registers the full AutomationEdge tool surface with richer MCP metadata:
titles, annotations, structured output, and per-tool meta payloads.
"""
from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from mcp_server.tool_specs import get_mcp_tool_specs

logger = logging.getLogger("ae_mcp.server")

mcp = FastMCP(
    "AutomationEdge Support",
    instructions=(
        "AutomationEdge IT Operations MCP Server. Provides tools for "
        "investigating, diagnosing, and remediating automation request issues, "
        "managing workflows, agents, schedules, credential pools, users, and "
        "permissions on the AutomationEdge platform. All mutating tools expose "
        "safety metadata, require reason fields where applicable, and return "
        "structured output."
    ),
)


def _register_tools() -> int:
    count = 0
    for spec in get_mcp_tool_specs():
        mcp.add_tool(
            spec.structured_handler,
            name=spec.name,
            description=spec.resolved_description,
            annotations=spec.annotations,
        )
        registered = mcp._tool_manager._tools.get(spec.name)
        if registered:
            registered.parameters = spec.input_schema
            registered.annotations = spec.annotations
            registered.description = spec.resolved_description
        count += 1
    return count


_REGISTERED_TOOL_COUNT = _register_tools()
logger.info("Registered %d AutomationEdge MCP tools", _REGISTERED_TOOL_COUNT)
