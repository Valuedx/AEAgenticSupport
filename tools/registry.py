"""
Tool registry — central catalog of all available tools.
Discovers tools, provides them for RAG indexing, and supports
RAG-filtered tool selection for scalable LLM function calling.

When the catalog is small (<30 tools) all tools can be sent to the
LLM.  When it grows to 100s+, `get_vertex_tools_filtered()` sends
only the RAG-matched subset + always-available tools + the
`discover_tools` meta-tool so the LLM can pull in more on demand.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from tools.base import ToolDefinition, ToolResult

logger = logging.getLogger("ops_agent.tools.registry")
audit = logging.getLogger("ops_agent.audit")

MAX_TOOLS_FOR_FULL_CATALOG = 30


class ToolRegistry:
    """Central registry for all ops agent tools."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}
        self._meta_registered = False

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

            # Allow handlers to return ToolResult directly.
            if isinstance(result, ToolResult):
                if result.tool_name == "":
                    result.tool_name = tool_name
                if result.success:
                    audit.info(f"TOOL_OK tool={tool_name}")
                else:
                    audit.warning(
                        f"TOOL_FAIL tool={tool_name} error={result.error}"
                    )
                return result

            # Backward compatibility for dict payloads that embed success/error.
            if isinstance(result, dict) and isinstance(
                result.get("success"), bool
            ):
                if result["success"]:
                    audit.info(f"TOOL_OK tool={tool_name}")
                    return ToolResult(
                        success=True,
                        data=result,
                        tool_name=tool_name,
                    )
                error = str(
                    result.get("error")
                    or f"Tool '{tool_name}' reported unsuccessful execution."
                )
                audit.warning(f"TOOL_FAIL tool={tool_name} error={error}")
                return ToolResult(
                    success=False,
                    data=result,
                    error=error,
                    tool_name=tool_name,
                )

            audit.info(f"TOOL_OK tool={tool_name}")
            return ToolResult(success=True, data=result, tool_name=tool_name)
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            audit.warning(f"TOOL_FAIL tool={tool_name} error={e}")
            return ToolResult(
                success=False, error=str(e), tool_name=tool_name
            )

    # -----------------------------------------------------------------
    # Discovery & enumeration
    # -----------------------------------------------------------------

    def get_all_definitions(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get_all_rag_documents(self) -> list[dict]:
        return [t.to_rag_document() for t in self._tools.values()]

    def get_all_llm_schemas(self) -> list[dict]:
        """Return plain dict schemas (for serialization/logging)."""
        return [t.to_llm_schema() for t in self._tools.values()]

    def get_tools_by_category(self, category: str) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.category == category]

    def get_tools_by_tier(self, tier: str) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.tier == tier]

    def get_always_available(self) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.always_available]

    # -----------------------------------------------------------------
    # Vertex AI tool objects
    # -----------------------------------------------------------------

    def get_vertex_tools(self) -> list:
        """Return a Vertex AI Tool object with ALL FunctionDeclarations."""
        from vertexai.generative_models import Tool
        declarations = [
            t.to_vertex_function_declaration()
            for t in self._tools.values()
        ]
        return [Tool(function_declarations=declarations)]

    def get_vertex_tools_filtered(
        self,
        rag_tool_names: list[str],
        max_rag_tools: int = 12,
    ) -> list:
        """Return Vertex AI Tool with a filtered subset of declarations.

        Includes:
          1. Always-available tools (marked with always_available=True)
          2. RAG-matched tools (up to *max_rag_tools*)
          3. The ``discover_tools`` meta-tool (so the LLM can search for more)

        If total catalog size is <= MAX_TOOLS_FOR_FULL_CATALOG, falls
        back to the full catalog to avoid unnecessary filtering overhead.
        """
        self._ensure_meta_tools()

        if len(self._tools) <= MAX_TOOLS_FOR_FULL_CATALOG:
            return self.get_vertex_tools()

        from vertexai.generative_models import Tool

        selected: dict[str, ToolDefinition] = {}

        for t in self._tools.values():
            if t.always_available:
                selected[t.name] = t

        for name in rag_tool_names[:max_rag_tools]:
            clean = name.removeprefix("tool-")
            if clean in self._tools and clean not in selected:
                selected[clean] = self._tools[clean]

        if "discover_tools" in self._tools:
            selected["discover_tools"] = self._tools["discover_tools"]

        declarations = [t.to_vertex_function_declaration()
                        for t in selected.values()]
        logger.info(
            f"Filtered tools: {len(selected)}/{len(self._tools)} "
            f"({', '.join(selected.keys())})"
        )
        return [Tool(function_declarations=declarations)]

    # -----------------------------------------------------------------
    # discover_tools meta-tool
    # -----------------------------------------------------------------

    def _ensure_meta_tools(self):
        """Register the discover_tools meta-tool once."""
        if self._meta_registered:
            return
        self._meta_registered = True

        def _discover_tools(query: str, category: str = "",
                            top_k: int = 8) -> dict:
            """Search the tool catalog by semantic query and/or category."""
            from rag.engine import get_rag_engine
            results = []

            if query and query.strip():
                rag_hits = get_rag_engine().search_tools(query, top_k=top_k)
                for hit in rag_hits:
                    tool_name = hit.get("metadata", {}).get(
                        "tool_name", hit.get("id", "").removeprefix("tool-")
                    )
                    td = self._tools.get(tool_name)
                    if td:
                        results.append({
                            "name": td.name,
                            "description": td.description,
                            "category": td.category,
                            "tier": td.tier,
                            "similarity": round(hit.get("similarity", 0), 3),
                        })

            if category:
                for td in self._tools.values():
                    if td.category == category and td.name not in {
                        r["name"] for r in results
                    }:
                        results.append({
                            "name": td.name,
                            "description": td.description,
                            "category": td.category,
                            "tier": td.tier,
                        })

            if not results:
                cats = sorted({t.category for t in self._tools.values()})
                return {
                    "tools": [],
                    "total_catalog_size": len(self._tools),
                    "available_categories": cats,
                    "hint": "No matching tools found. Try a different "
                            "query or browse by category.",
                }

            return {
                "tools": results[:top_k],
                "total_catalog_size": len(self._tools),
            }

        meta_def = ToolDefinition(
            name="discover_tools",
            description=(
                "Search the tool catalog to find tools that match a query "
                "or category. Use when the currently available tools are "
                "insufficient. Returns tool names, descriptions, and "
                "categories. You can then call the discovered tools directly."
            ),
            category="meta",
            tier="read_only",
            parameters={
                "query": {
                    "type": "string",
                    "description": (
                        "Semantic search query describing the capability "
                        "you need, e.g. 'retry failed queue items' or "
                        "'disable a scheduled workflow'"
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Filter by category: status, logs, file, "
                        "remediation, dependency, config, notification"
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default 8)",
                },
            },
            required_params=[],
            always_available=True,
        )
        self.register(meta_def, _discover_tools)

    # -----------------------------------------------------------------
    # Dynamic tool expansion (mid-conversation)
    # -----------------------------------------------------------------

    def resolve_discovered_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        """Look up a tool the LLM found via discover_tools.

        When the catalog is filtered, the LLM may try to call a tool that
        wasn't in its initial function declarations.  The orchestrator
        calls this to check if the tool actually exists before returning
        an 'unknown tool' error.
        """
        return self._tools.get(tool_name)


tool_registry = ToolRegistry()
