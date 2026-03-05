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

logger = logging.getLogger("ops_agent.tools.registry")
audit = logging.getLogger("ops_agent.audit")

MAX_TOOLS_FOR_FULL_CATALOG = 30


class ToolRegistry:
    """Central registry for all ops agent tools."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}
        self._meta_registered = False
        self._dynamic_tool_names: set[str] = set()
        self._dynamic_mappings: dict[str, dict] = {}

    def register(self, definition: ToolDefinition, handler: Callable):
        self._tools[definition.name] = definition
        self._handlers[definition.name] = handler
        logger.debug("Registered tool: %s", definition.name)

    def unregister(self, name: str):
        self._tools.pop(name, None)
        self._handlers.pop(name, None)
        self._dynamic_tool_names.discard(name)
        self._dynamic_mappings.pop(name, None)

    def remove_dynamic_tools(self) -> int:
        names = list(self._dynamic_tool_names)
        for name in names:
            self.unregister(name)
        return len(names)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def get_handler(self, name: str) -> Optional[Callable]:
        return self._handlers.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def list_dynamic_tools(self) -> list[str]:
        return sorted(self._dynamic_tool_names)

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
            )
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
        return list(self._tools.values())

    def get_all_rag_documents(self) -> list[dict]:
        return [t.to_rag_document() for t in self._tools.values()]

    def get_all_llm_schemas(self) -> list[dict]:
        return [t.to_llm_schema() for t in self._tools.values()]

    def get_tools_by_category(self, category: str) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.category == category]

    def get_tools_by_tier(self, tier: str) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.tier == tier]

    def get_always_available(self) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.always_available]

    def get_tool_inventory(
        self,
        agent_tool_map: Optional[dict[str, list[str]]] = None,
    ) -> list[dict]:
        lookup = agent_tool_map or {}
        inventory: list[dict] = []
        for tool_name, tool_def in self._tools.items():
            md = tool_def.metadata or {}
            linked_agents = sorted(
                agent_id for agent_id, tools in lookup.items() if tool_name in tools
            )
            inventory.append(
                {
                    "toolName": tool_name,
                    "workflowName": md.get("workflow_name", ""),
                    "description": tool_def.description,
                    "category": tool_def.category,
                    "tier": tool_def.tier,
                    "source": md.get("source", "static"),
                    "dynamic": bool(md.get("dynamic", False)),
                    "active": bool(md.get("active", True)),
                    "tags": md.get("tags", []),
                    "parameters": tool_def.parameters,
                    "required": tool_def.required_params,
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
            if mapping.tool_name in self._tools and mapping.tool_name not in self._dynamic_tool_names:
                collisions.append(mapping.tool_name)
                continue

            handler = self._make_dynamic_tool_handler(mapping, client)
            definition = mapping.to_tool_definition()
            self.register(definition, handler)
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

        # Sync all fetched workflows to Postgres workflow_catalog AND index embeddings in RAG.
        # Single call re-uses the already-fetched `workflows` list — no extra API call.
        sync_result = client.sync_and_index_workflows(workflows)

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
        declarations = [t.to_llm_schema() for t in self._tools.values()]
        return [{"function_declarations": declarations}]

    def get_vertex_tools_filtered(
        self,
        rag_tool_names: list[str],
        max_rag_tools: int = 12,
    ) -> list:
        self._ensure_meta_tools()

        if len(self._tools) <= MAX_TOOLS_FOR_FULL_CATALOG:
            return self.get_vertex_tools()

        selected: dict[str, ToolDefinition] = {}
        for tool_def in self._tools.values():
            if tool_def.always_available:
                selected[tool_def.name] = tool_def

        for name in rag_tool_names[:max_rag_tools]:
            clean = name.removeprefix("tool-")
            if clean in self._tools and clean not in selected:
                selected[clean] = self._tools[clean]

        if "discover_tools" in self._tools:
            selected["discover_tools"] = self._tools["discover_tools"]

        declarations = [t.to_llm_schema() for t in selected.values()]
        logger.info(
            "Filtered tools: %s/%s (%s)",
            len(selected),
            len(self._tools),
            ", ".join(selected.keys()),
        )
        return [{"function_declarations": declarations}]

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
                    td = self._tools.get(tool_name)
                    if td:
                        md = td.metadata or {}
                        results.append(
                            {
                                "name": td.name,
                                "description": td.description,
                                "category": td.category,
                                "tier": td.tier,
                                "source": md.get("source", "static"),
                                "similarity": round(hit.get("similarity", 0), 3),
                            }
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
                                "similarity": round(hit.get("similarity", 0), 3),
                                "registered": False,
                                "use_tool": "trigger_workflow",
                                "hint": (
                                    "Tool not registered directly. Use trigger_workflow "
                                    "with workflow_name and parameters."
                                ),
                            }
                        )

            if category:
                present = {r["name"] for r in results}
                for td in self._tools.values():
                    if td.category == category and td.name not in present:
                        md = td.metadata or {}
                        results.append(
                            {
                                "name": td.name,
                                "description": td.description,
                                "category": td.category,
                                "tier": td.tier,
                                "source": md.get("source", "static"),
                            }
                        )

            if not results:
                cats = sorted({t.category for t in self._tools.values()})
                return {
                    "tools": [],
                    "total_catalog_size": len(self._tools),
                    "available_categories": cats,
                    "hint": "No matching tools found. Try a different query or browse by category.",
                }

            return {"tools": results[:top_k], "total_catalog_size": len(self._tools)}

        meta_def = ToolDefinition(
            name="discover_tools",
            description=(
                "Search the tool catalog to find tools matching a query or category. "
                "Use when currently loaded tools are insufficient."
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
        )
        self.register(meta_def, _discover_tools)

    # -----------------------------------------------------------------
    # Dynamic tool expansion (mid-conversation)
    # -----------------------------------------------------------------

    def resolve_discovered_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        return self._tools.get(tool_name)


tool_registry = ToolRegistry()
