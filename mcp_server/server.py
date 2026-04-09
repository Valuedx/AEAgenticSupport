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
        "investigating, diagnosing, and remediating automation request issues. "
        "IMPORTANT: Prioritize tool execution over asking clarification questions. "
        "If an ID (like agent_id) is missing, FIRST call the relevant listing/discovery tool "
        "to show the user the available options. NEVER ask 'which agent' if you haven't "
        "successfully listed the running agents yet."
    ),
)


def _register_tools() -> int:
    count = 0
    for spec in get_mcp_tool_specs():
        # gated_handler applies the MCP_MUTATE_ENABLED / MCP_PRIVILEGED_ENABLED
        # kill-switches for mutating tools; read-only tools return structured_handler
        # unchanged.  Guard logic lives in MCPToolSpec.gated_handler so it is shared
        # with the co-located local bridge path in tools/mcp_tools.py.
        mcp.add_tool(
            spec.gated_handler,
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
