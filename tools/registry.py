"""
Tool registry — central catalog of all available tools.
Discovers tools, provides them for RAG indexing and LLM tool selection.
"""

import logging
from typing import Callable, Optional

from tools.base import ToolDefinition, ToolResult

logger = logging.getLogger("ops_agent.tools.registry")
audit = logging.getLogger("ops_agent.audit")


class ToolRegistry:
    """Central registry for all ops agent tools."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}

    def register(self, definition: ToolDefinition, handler: Callable):
        self._tools[definition.name] = definition
        self._handlers[definition.name] = handler
        logger.debug(f"Registered tool: {definition.name}")

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def get_handler(self, name: str) -> Optional[Callable]:
        return self._handlers.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
            )
        audit.info(f"TOOL_CALL tool={tool_name} params={kwargs}")
        try:
            result = handler(**kwargs)
            audit.info(f"TOOL_OK tool={tool_name}")
            return ToolResult(success=True, data=result, tool_name=tool_name)
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            audit.warning(f"TOOL_FAIL tool={tool_name} error={e}")
            return ToolResult(
                success=False, error=str(e), tool_name=tool_name
            )

    def get_all_definitions(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get_all_rag_documents(self) -> list[dict]:
        return [t.to_rag_document() for t in self._tools.values()]

    def get_all_llm_schemas(self) -> list[dict]:
        """Return plain dict schemas (for serialization/logging)."""
        return [t.to_llm_schema() for t in self._tools.values()]

    def get_vertex_tools(self) -> list:
        """Return a Vertex AI Tool object with all FunctionDeclarations."""
        from vertexai.generative_models import Tool
        declarations = [
            t.to_vertex_function_declaration()
            for t in self._tools.values()
        ]
        return [Tool(function_declarations=declarations)]

    def get_tools_by_category(self, category: str) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.category == category]

    def get_tools_by_tier(self, tier: str) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.tier == tier]


tool_registry = ToolRegistry()
