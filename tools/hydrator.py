"""
Turn-local hydration helpers for the tool catalog.
"""
from __future__ import annotations

from typing import Callable, Optional

from tools.base import ToolDefinition, ToolResult
from tools.catalog import ToolCatalog, ToolCatalogEntry
from tools.executor import ToolExecutor


class TurnToolSet:
    """Turn-local hydrated tool set for the orchestrator loop."""

    def __init__(
        self,
        hydrator: "ToolHydrator",
        tool_names: list[str],
        *,
        feedback_agent_id: str = "",
    ):
        self._hydrator = hydrator
        self._feedback_agent_id = str(feedback_agent_id or "").strip()
        self._tool_names: list[str] = []
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}

        seen: set[str] = set()
        for name in tool_names:
            clean = str(name or "").removeprefix("tool-")
            if clean and clean not in seen and clean in hydrator.catalog:
                self._tool_names.append(clean)
                seen.add(clean)

    def list_tool_names(self) -> list[str]:
        return list(self._tool_names)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        clean = str(name or "").removeprefix("tool-")
        if clean not in self._tool_names:
            return None
        if clean not in self._definitions:
            definition = self._hydrator.get_tool_definition(clean)
            if not definition:
                return None
            self._definitions[clean] = definition
        return self._definitions.get(clean)

    def _get_handler(self, name: str) -> Optional[Callable]:
        clean = str(name or "").removeprefix("tool-")
        if clean not in self._tool_names:
            return None
        if clean in self._handlers:
            return self._handlers[clean]
        handler = self._hydrator.get_handler(clean, persist=False)
        if handler:
            self._handlers[clean] = handler
        return self._handlers.get(clean)

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        handler = self._get_handler(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
            )
        if (
            str(tool_name or "").removeprefix("tool-") == "discover_tools"
            and self._feedback_agent_id
            and "_agent_id" not in kwargs
        ):
            kwargs = dict(kwargs)
            kwargs["_agent_id"] = self._feedback_agent_id
        return self._hydrator.executor.execute(tool_name, handler, kwargs)

    def to_vertex_tools(self) -> list[dict]:
        declarations = []
        for name in self._tool_names:
            tool_def = self.get_tool(name)
            if tool_def:
                declarations.append(tool_def.to_llm_schema())
        return [{"function_declarations": declarations}]


class ToolHydrator:
    """Hydrates catalog entries into runtime definitions and handlers."""

    def __init__(
        self,
        *,
        catalog: ToolCatalog,
        tools_cache: dict[str, ToolDefinition],
        handlers_cache: dict[str, Callable],
        handler_factories: dict[str, Callable[[], Callable]],
        executor: ToolExecutor,
        is_llm_callable: Callable[[ToolCatalogEntry], bool],
    ):
        self.catalog = catalog
        self._tools_cache = tools_cache
        self._handlers_cache = handlers_cache
        self._handler_factories = handler_factories
        self.executor = executor
        self._is_llm_callable = is_llm_callable

    def hydrate_tool(self, name: str) -> Optional[ToolDefinition]:
        clean = str(name or "").removeprefix("tool-")
        if clean in self._tools_cache:
            return self._tools_cache[clean]
        entry = self.catalog.get(clean)
        if not entry:
            return None
        definition = entry.to_tool_definition()
        self._tools_cache[clean] = definition
        factory = self._handler_factories.get(clean)
        if factory:
            self._handlers_cache[clean] = factory()
        return definition

    def get_tool_definition(self, name: str) -> Optional[ToolDefinition]:
        clean = str(name or "").removeprefix("tool-")
        if clean in self._tools_cache:
            return self._tools_cache.get(clean)
        entry = self.catalog.get(clean)
        return entry.to_tool_definition() if entry else None

    def get_handler(self, name: str, *, persist: bool = True) -> Optional[Callable]:
        clean = str(name or "").removeprefix("tool-")
        if clean in self._handlers_cache:
            return self._handlers_cache.get(clean)
        if clean not in self.catalog:
            return None
        factory = self._handler_factories.get(clean)
        if not factory:
            return None
        handler = factory()
        if persist:
            entry = self.catalog.get(clean)
            if entry:
                self._tools_cache.setdefault(clean, entry.to_tool_definition())
            self._handlers_cache[clean] = handler
        return handler

    def build_turn_toolset(
        self,
        tool_names: list[str],
        *,
        allowed_categories: list[str] | None = None,
        include_meta: bool = True,
        feedback_agent_id: str = "",
    ) -> TurnToolSet:
        selected: list[str] = []
        seen: set[str] = set()

        def _maybe_add(name: str):
            clean = str(name or "").removeprefix("tool-")
            if not clean or clean in seen:
                return
            entry = self.catalog.get(clean)
            if not entry:
                return
            if not self._is_llm_callable(entry):
                return
            tool_def = entry.definition
            if not bool((tool_def.metadata or {}).get("active", True)):
                return
            if allowed_categories and tool_def.category not in allowed_categories:
                return
            selected.append(clean)
            seen.add(clean)

        for name in tool_names:
            _maybe_add(name)

        if include_meta:
            _maybe_add("discover_tools")

        return TurnToolSet(self, selected, feedback_agent_id=feedback_agent_id)
