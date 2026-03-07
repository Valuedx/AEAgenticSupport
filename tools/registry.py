"""
Tool registry: central catalog of available tools.
"""
from __future__ import annotations

from dataclasses import asdict
import logging
from typing import Callable, Optional

from config.settings import CONFIG
from tools.ae_dynamic_tools import (
    DynamicToolMapping,
    extract_dynamic_tool_mappings_from_payload,
)
from tools.automationedge_client import get_automationedge_client
from tools.base import ToolDefinition, ToolResult
from tools.catalog import ToolCatalogEntry

logger = logging.getLogger("ops_agent.tools.registry")
audit = logging.getLogger("ops_agent.audit")

MAX_TOOLS_FOR_FULL_CATALOG = 30


class TurnToolSet:
    """Turn-local hydrated tool set for the orchestrator loop."""

    def __init__(self, registry: "ToolRegistry", tool_names: list[str]):
        self._registry = registry
        self._tool_names: list[str] = []
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}

        seen: set[str] = set()
        for name in tool_names:
            clean = str(name or "").removeprefix("tool-")
            if clean and clean not in seen and clean in registry._catalog_entries:
                self._tool_names.append(clean)
                seen.add(clean)

    def list_tool_names(self) -> list[str]:
        return list(self._tool_names)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        clean = str(name or "").removeprefix("tool-")
        if clean not in self._tool_names:
            return None
        if clean not in self._definitions:
            entry = self._registry._catalog_entries.get(clean)
            if not entry:
                return None
            self._definitions[clean] = entry.to_tool_definition()
        return self._definitions.get(clean)

    def _get_handler(self, name: str) -> Optional[Callable]:
        clean = str(name or "").removeprefix("tool-")
        if clean not in self._tool_names:
            return None
        if clean in self._handlers:
            return self._handlers[clean]
        if clean in self._registry._handlers:
            self._handlers[clean] = self._registry._handlers[clean]
            return self._handlers[clean]
        factory = self._registry._handler_factories.get(clean)
        if factory:
            self._handlers[clean] = factory()
        return self._handlers.get(clean)

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        handler = self._get_handler(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
            )
        return self._registry._execute_with_handler(tool_name, handler, kwargs)

    def to_vertex_tools(self) -> list[dict]:
        declarations = []
        for name in self._tool_names:
            tool_def = self.get_tool(name)
            if tool_def:
                declarations.append(tool_def.to_llm_schema())
        return [{"function_declarations": declarations}]


class ToolRegistry:
    """Central registry for all ops agent tools."""

    def __init__(self):
        self._catalog_entries: dict[str, ToolCatalogEntry] = {}
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}
        self._handler_factories: dict[str, Callable[[], Callable]] = {}
        self._meta_registered = False
        self._dynamic_tool_names: set[str] = set()
        self._dynamic_mappings: dict[str, dict] = {}

    def register(
        self,
        definition: ToolDefinition,
        handler: Callable,
        *,
        source_ref: str = "",
        hydration_mode: str = "eager",
        latency_class: str = "",
        mutating: bool | None = None,
        allowed_agents: list[str] | None = None,
        hydrate: bool = True,
    ):
        self.register_catalog_entry(
            ToolCatalogEntry.from_definition(
                definition,
                source_ref=source_ref,
                hydration_mode=hydration_mode,
                latency_class=latency_class,
                mutating=mutating,
                allowed_agents=allowed_agents,
            ),
            handler_factory=lambda _handler=handler: _handler,
            hydrate=hydrate,
        )

    def register_catalog_entry(
        self,
        entry: ToolCatalogEntry,
        *,
        handler_factory: Callable[[], Callable] | None = None,
        hydrate: bool = False,
    ):
        self._catalog_entries[entry.name] = entry
        if handler_factory:
            self._handler_factories[entry.name] = handler_factory
        if hydrate:
            self._hydrate_tool(entry.name)
        logger.debug(
            "Registered tool catalog entry: %s (mode=%s hydrate=%s)",
            entry.name,
            entry.hydration_mode,
            hydrate,
        )

    def _hydrate_tool(self, name: str) -> Optional[ToolDefinition]:
        if name in self._tools:
            return self._tools[name]
        entry = self._catalog_entries.get(name)
        if not entry:
            return None
        definition = entry.to_tool_definition()
        self._tools[name] = definition
        factory = self._handler_factories.get(name)
        if factory:
            self._handlers[name] = factory()
        logger.debug("Hydrated tool: %s", name)
        return definition

    def unregister(self, name: str):
        self._catalog_entries.pop(name, None)
        self._tools.pop(name, None)
        self._handlers.pop(name, None)
        self._handler_factories.pop(name, None)
        self._dynamic_tool_names.discard(name)
        self._dynamic_mappings.pop(name, None)

    def remove_dynamic_tools(self) -> int:
        names = list(self._dynamic_tool_names)
        for name in names:
            self.unregister(name)
        return len(names)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        if name in self._tools:
            return self._tools.get(name)
        entry = self._catalog_entries.get(name)
        return entry.to_tool_definition() if entry else None

    def get_handler(self, name: str) -> Optional[Callable]:
        if name not in self._handlers and name in self._catalog_entries:
            self._hydrate_tool(name)
        return self._handlers.get(name)

    def list_tools(self) -> list[str]:
        return list(self._catalog_entries.keys())

    def list_dynamic_tools(self) -> list[str]:
        return sorted(self._dynamic_tool_names)

    def _execute_with_handler(
        self,
        tool_name: str,
        handler: Callable,
        kwargs: dict,
    ) -> ToolResult:
        audit.info("TOOL_CALL tool=%s params=%s", tool_name, kwargs)
        try:
            result = handler(**kwargs)

            if isinstance(result, ToolResult):
                if result.tool_name == "":
                    result.tool_name = tool_name
                if result.success:
                    audit.info("TOOL_OK tool=%s", tool_name)
                else:
                    audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, result.error)
                self._log_interaction(tool_name, kwargs, result.success, result.error)
                return result

            if isinstance(result, dict) and isinstance(result.get("success"), bool):
                if result["success"]:
                    audit.info("TOOL_OK tool=%s", tool_name)
                    self._log_interaction(tool_name, kwargs, True, "")
                    return ToolResult(success=True, data=result, tool_name=tool_name)
                error = str(
                    result.get("error")
                    or f"Tool '{tool_name}' reported unsuccessful execution."
                )
                audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, error)
                self._log_interaction(tool_name, kwargs, False, error)
                return ToolResult(
                    success=False,
                    data=result,
                    error=error,
                    tool_name=tool_name,
                )

            audit.info("TOOL_OK tool=%s", tool_name)
            self._log_interaction(tool_name, kwargs, True, "")
            return ToolResult(success=True, data=result, tool_name=tool_name)
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc, exc_info=True)
            audit.warning("TOOL_FAIL tool=%s error=%s", tool_name, exc)
            self._log_interaction(tool_name, kwargs, False, str(exc))
            return ToolResult(success=False, error=str(exc), tool_name=tool_name)

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        handler = self.get_handler(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
            )
        return self._execute_with_handler(tool_name, handler, kwargs)

    def _log_interaction(self, tool_name: str, params: dict, success: bool, error: str):
        try:
            from state.agent_catalog import get_agent_catalog

            get_agent_catalog().log_tool_interaction(
                tool_name=tool_name,
                params=params,
                success=success,
                error=error,
            )
        except Exception:
            # Optional observability path; never block tool execution.
            logger.debug("Skipping agent interaction log for %s", tool_name)

    # -----------------------------------------------------------------
    # Discovery & enumeration
    # -----------------------------------------------------------------

    def get_all_definitions(self) -> list[ToolDefinition]:
        return [entry.to_tool_definition() for entry in self._catalog_entries.values()]

    def get_all_rag_documents(self) -> list[dict]:
        return [entry.to_rag_document() for entry in self._catalog_entries.values()]

    def get_all_llm_schemas(self) -> list[dict]:
        return [entry.to_tool_definition().to_llm_schema() for entry in self._catalog_entries.values()]

    def get_tools_by_category(self, category: str) -> list[ToolDefinition]:
        return [
            entry.to_tool_definition()
            for entry in self._catalog_entries.values()
            if entry.definition.category == category
        ]

    def get_tools_by_tier(self, tier: str) -> list[ToolDefinition]:
        return [
            entry.to_tool_definition()
            for entry in self._catalog_entries.values()
            if entry.definition.tier == tier
        ]

    def get_always_available(self) -> list[ToolDefinition]:
        return [
            entry.to_tool_definition()
            for entry in self._catalog_entries.values()
            if entry.definition.always_available
        ]

    def build_turn_toolset(
        self,
        tool_names: list[str],
        *,
        allowed_categories: list[str] | None = None,
        include_meta: bool = True,
    ) -> TurnToolSet:
        self._ensure_meta_tools()
        selected: list[str] = []
        seen: set[str] = set()

        def _maybe_add(name: str):
            clean = str(name or "").removeprefix("tool-")
            if not clean or clean in seen:
                return
            entry = self._catalog_entries.get(clean)
            if not entry:
                return
            tool_def = entry.definition
            if allowed_categories and tool_def.category not in allowed_categories:
                return
            selected.append(clean)
            seen.add(clean)

        for name in tool_names:
            _maybe_add(name)

        if include_meta:
            _maybe_add("discover_tools")

        return TurnToolSet(self, selected)

    def build_turn_toolset_filtered(
        self,
        rag_tool_names: list[str],
        *,
        max_rag_tools: int = 12,
        allowed_categories: list[str] | None = None,
    ) -> TurnToolSet:
        self._ensure_meta_tools()
        selected_names: list[str] = []

        # 1. Add 'always_available' tools (or ALL tools if catalog is small),
        # but ALWAYS apply the category filter if provided.
        if len(self._catalog_entries) <= MAX_TOOLS_FOR_FULL_CATALOG:
            for entry in self._catalog_entries.values():
                tool_def = entry.definition
                if allowed_categories and tool_def.category not in allowed_categories:
                    continue
                selected_names.append(tool_def.name)
        else:
            for entry in self._catalog_entries.values():
                tool_def = entry.definition
                if tool_def.always_available:
                    if allowed_categories and tool_def.category not in allowed_categories:
                        continue
                    selected_names.append(tool_def.name)

            for name in rag_tool_names[:max_rag_tools]:
                selected_names.append(str(name or "").removeprefix("tool-"))

        toolset = self.build_turn_toolset(
            selected_names,
            allowed_categories=allowed_categories,
            include_meta=True,
        )
        logger.info(
            "Built turn-local tool set: %s/%s (%s)",
            len(toolset.list_tool_names()),
            len(self._catalog_entries),
            ", ".join(toolset.list_tool_names()),
        )
        return toolset

    @staticmethod
    def _tool_card(
        tool_def: ToolDefinition,
        *,
        score: float | None = None,
        registered: bool = True,
    ) -> dict:
        md = tool_def.metadata or {}
        card = {
            "name": tool_def.name,
            "workflow_name": md.get("workflow_name", ""),
            "description": tool_def.description,
            "category": tool_def.category,
            "tier": tool_def.tier,
            "source": md.get("source", "static"),
            "registered": registered,
            "dynamic": bool(md.get("dynamic", False)),
            "active": bool(md.get("active", True)),
            "always_available": bool(tool_def.always_available),
            "required_params": list(tool_def.required_params),
            "tags": md.get("tags", []),
            "use_when": tool_def.use_when,
            "avoid_when": tool_def.avoid_when,
            "input_examples": tool_def.input_examples[:2],
        }
        if score is not None:
            card["score"] = round(score, 3)
        return card

    @classmethod
    def _tool_card_from_entry(
        cls,
        entry: ToolCatalogEntry,
        *,
        score: float | None = None,
        registered: bool = True,
    ) -> dict:
        return cls._tool_card(
            entry.to_tool_definition(),
            score=score,
            registered=registered,
        )

    def get_tool_inventory(
        self,
        agent_tool_map: Optional[dict[str, list[str]]] = None,
    ) -> list[dict]:
        lookup = agent_tool_map or {}
        inventory: list[dict] = []
        for tool_name, entry in self._catalog_entries.items():
            linked_agents = sorted(
                agent_id for agent_id, tools in lookup.items() if tool_name in tools
            )
            card = self._tool_card_from_entry(entry)
            inventory.append(
                {
                    "toolName": tool_name,
                    "workflowName": card.get("workflow_name", ""),
                    "description": card["description"],
                    "category": card["category"],
                    "tier": card["tier"],
                    "source": card["source"],
                    "dynamic": card["dynamic"],
                    "active": card["active"],
                    "tags": card["tags"],
                    "parameters": entry.definition.parameters,
                    "required": entry.definition.required_params,
                    "useWhen": card["use_when"],
                    "avoidWhen": card["avoid_when"],
                    "inputExamples": card["input_examples"],
                    "linkedAgents": linked_agents,
                }
            )
        inventory.sort(key=lambda t: (t.get("source", ""), t["toolName"]))
        return inventory

    # -----------------------------------------------------------------
    # Dynamic AutomationEdge tools
    # -----------------------------------------------------------------

    def reload_automationedge_tools(self, include_inactive: bool = False) -> dict:
        if not CONFIG.get("AE_ENABLE_DYNAMIC_TOOLS", True):
            removed = self.remove_dynamic_tools()
            return {
                "enabled": False,
                "removed": removed,
                "registered": 0,
                "skipped": 0,
                "collisions": [],
            }

        removed = self.remove_dynamic_tools()
        client = get_automationedge_client()
        try:
            workflows = client.list_workflows()
        except Exception as exc:
            logger.warning("AE dynamic tool discovery failed: %s", exc)
            return {
                "enabled": True,
                "removed": removed,
                "registered": 0,
                "skipped": 0,
                "collisions": [],
                "total_workflows": 0,
                "error": str(exc),
            }

        details_by_workflow: dict[str, dict] = {}
        for wf in workflows:
            wf_name = str(wf.get("workflowName") or wf.get("name") or "").strip()
            wf_id = str(wf.get("id") or wf.get("workflowId") or wf_name).strip()
            if not wf_id:
                continue
            try:
                details = client.get_workflow_details(wf_id)
                if wf_name:
                    details_by_workflow[wf_name] = details
            except Exception as exc:
                logger.warning("Could not load workflow details for %s: %s", wf_id, exc)

        mappings = extract_dynamic_tool_mappings_from_payload(
            workflows,
            details_by_workflow=details_by_workflow,
        )

        registered = 0
        skipped = 0
        collisions: list[str] = []

        for mapping in mappings:
            if not include_inactive and not mapping.active:
                skipped += 1
                continue
            if (
                mapping.tool_name in self._catalog_entries
                and mapping.tool_name not in self._dynamic_tool_names
            ):
                collisions.append(mapping.tool_name)
                continue

            definition = mapping.to_tool_definition()
            self.register_catalog_entry(
                ToolCatalogEntry.from_definition(
                    definition,
                    source_ref=mapping.workflow_name,
                    hydration_mode="lazy",
                    latency_class="medium",
                ),
                handler_factory=lambda _mapping=mapping, _client=client: self._make_dynamic_tool_handler(
                    _mapping,
                    _client,
                ),
                hydrate=False,
            )
            self._dynamic_tool_names.add(mapping.tool_name)
            self._dynamic_mappings[mapping.tool_name] = asdict(mapping)
            registered += 1

        logger.info(
            "AE dynamic tool reload complete: removed=%s registered=%s skipped=%s collisions=%s",
            removed,
            registered,
            skipped,
            len(collisions),
        )

        # Best-effort sync/index: keep dynamic tool reload compatible with
        # test doubles and older clients that may not expose this helper.
        sync_result: dict = {}
        sync_fn = getattr(client, "sync_and_index_workflows", None)
        if callable(sync_fn):
            try:
                sync_result = sync_fn(workflows) or {}
            except Exception as exc:
                logger.warning("AE workflow sync/index failed (non-fatal): %s", exc)

        return {
            "enabled": True,
            "removed": removed,
            "registered": registered,
            "skipped": skipped,
            "collisions": collisions,
            "total_workflows": len(workflows),
            "db_synced": sync_result.get("db_synced", 0),
            "rag_indexed": sync_result.get("rag_indexed", 0),
        }


    def _make_dynamic_tool_handler(
        self,
        mapping: DynamicToolMapping,
        ae_client=None,
    ) -> Callable:
        client = ae_client or get_automationedge_client()

        def _handler(**kwargs):
            # ── "Ask Again" pattern from code_ref.py remediation_agent_ask_params ──
            # If required params are missing, return a structured request for them
            # instead of a silent failure. The orchestrator will see needs_user_input=True
            # and re-prompt the user with the question.
            missing = [p for p in mapping.required_params if not kwargs.get(p)]
            if missing:
                # Build a friendly, conversational question (mirrors code_ref.py pattern)
                param_bullets = "\n".join(f"  • {p}" for p in missing)
                friendly_name = mapping.workflow_name.replace("_", " ").replace("-", " ").title()
                question = (
                    f"I'm ready to help with **{friendly_name}**! Just need a few details first:\n"
                    f"{param_bullets}\n\n"
                    f"Please share these and I'll take care of the rest."
                )
                return {
                    "success": False,
                    "needs_user_input": True,
                    "missing_params": missing,
                    "question": question,
                    "tool_name": mapping.tool_name,
                    "workflow_name": mapping.workflow_name,
                }

            payload_args = dict(kwargs)
            org_code = str(payload_args.pop("orgCode", "") or payload_args.pop("org_code", ""))
            user_id = str(payload_args.pop("userId", "") or payload_args.pop("user_id", ""))
            source = str(payload_args.pop("source", "ae-dynamic-tool"))

            raw = client.execute_workflow(
                workflow_name=mapping.workflow_name,
                org_code=org_code,
                user_id=user_id,
                source=source,
                params=payload_args,
            )

            status = str(
                raw.get("status")
                or raw.get("requestStatus")
                or raw.get("state")
                or "QUEUED"
            ).upper()
            request_id = raw.get("requestId") or raw.get("id") or raw.get("executionId")
            message = raw.get("message") or raw.get("details") or ""

            return {
                "success": True,
                "status": status,
                "requestId": request_id,
                "message": message,
                "toolName": mapping.tool_name,
                "workflowName": mapping.workflow_name,
                "raw": raw,
            }

        return _handler


    # -----------------------------------------------------------------
    # Vertex AI tool objects
    # -----------------------------------------------------------------

    def get_vertex_tools(self) -> list:
        return self.build_turn_toolset(self.list_tools()).to_vertex_tools()

    def get_vertex_tools_filtered(
        self,
        rag_tool_names: list[str],
        max_rag_tools: int = 12,
        allowed_categories: list[str] | None = None,
    ) -> list:
        return self.build_turn_toolset_filtered(
            rag_tool_names,
            max_rag_tools=max_rag_tools,
            allowed_categories=allowed_categories,
        ).to_vertex_tools()

    # -----------------------------------------------------------------
    # discover_tools meta-tool
    # -----------------------------------------------------------------

    def _ensure_meta_tools(self):
        if self._meta_registered:
            return
        self._meta_registered = True

        def _discover_tools(query: str, category: str = "", top_k: int = 8) -> dict:
            from rag.engine import get_rag_engine

            results = []

            if query and query.strip():
                rag_hits = get_rag_engine().search_tools(query, top_k=top_k)
                for hit in rag_hits:
                    metadata = hit.get("metadata", {}) or {}
                    tool_name = metadata.get(
                        "tool_name", hit.get("id", "").removeprefix("tool-")
                    )
                    entry = self._catalog_entries.get(tool_name)
                    if entry:
                        results.append(
                            self._tool_card_from_entry(
                                entry,
                                score=hit.get("rrf_score", hit.get("similarity", 0)),
                            )
                        )
                    else:
                        # RAG-only workflow/tool hit (not currently registered as executable tool).
                        # Return it with explicit execution guidance so the LLM can use trigger_workflow.
                        wf_name = (
                            metadata.get("workflow_name")
                            or metadata.get("workflowName")
                            or tool_name
                        )
                        results.append(
                            {
                                "name": tool_name,
                                "workflow_name": wf_name,
                                "description": metadata.get("description", ""),
                                "category": metadata.get("category", "automationedge"),
                                "tier": metadata.get("tier", "medium_risk"),
                                "source": metadata.get("source", "automationedge"),
                                "score": round(hit.get("rrf_score", hit.get("similarity", 0)), 3),
                                "registered": False,
                                "required_params": metadata.get("required_params", []),
                                "tags": metadata.get("tags", []),
                                "use_when": metadata.get("use_when", ""),
                                "avoid_when": metadata.get("avoid_when", ""),
                                "input_examples": metadata.get("input_examples", [])[:2],
                                "use_tool": "trigger_workflow",
                                "hint": (
                                    "Tool not registered directly. Use trigger_workflow "
                                    "with workflow_name and parameters."
                                ),
                            }
                        )

            if category:
                present = {r["name"] for r in results}
                for entry in self._catalog_entries.values():
                    td = entry.definition
                    if td.category == category and td.name not in present:
                        results.append(self._tool_card_from_entry(entry))

            if not results:
                cats = sorted({entry.definition.category for entry in self._catalog_entries.values()})
                return {
                    "tools": [],
                    "total_catalog_size": len(self._catalog_entries),
                    "available_categories": cats,
                    "hint": "No matching tools found. Try a different query or browse by category.",
                }

            return {"tools": results[:top_k], "total_catalog_size": len(self._catalog_entries)}

        meta_def = ToolDefinition(
            name="discover_tools",
            description=(
                "Search the tool catalog to find tools matching a query or category. "
                "Use when currently loaded tools are insufficient. Returns "
                "category, risk tier, required parameters, and example arguments "
                "when available."
            ),
            category="meta",
            tier="read_only",
            parameters={
                "query": {
                    "type": "string",
                    "description": "Semantic query describing capability needed.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default 8).",
                },
            },
            required_params=[],
            always_available=True,
            metadata={"source": "meta"},
            use_when=(
                "You need a capability that is not in the currently loaded subset "
                "of tools, or you need to search by intent instead of exact name."
            ),
            avoid_when="You already have a specific tool loaded that clearly fits the task.",
            input_examples=[
                {"query": "find tools to diagnose failed request", "top_k": 5},
                {"query": "workflow permissions", "category": "dependency", "top_k": 3},
            ],
        )
        self.register(meta_def, _discover_tools)

    # -----------------------------------------------------------------
    # Dynamic tool expansion (mid-conversation)
    # -----------------------------------------------------------------

    def resolve_discovered_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        return self.get_tool(tool_name)


tool_registry = ToolRegistry()
