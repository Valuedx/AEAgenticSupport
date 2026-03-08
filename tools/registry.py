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
from tools.catalog import ToolCatalog, ToolCatalogEntry
from tools.executor import ToolExecutor
from tools.hydrator import ToolHydrator, TurnToolSet
from tools.ranker import ToolRanker

logger = logging.getLogger("ops_agent.tools.registry")
audit = logging.getLogger("ops_agent.audit")

MAX_TOOLS_FOR_FULL_CATALOG = 30


class ToolRegistry:
    """Central registry for all ops agent tools."""

    def __init__(self):
        self._catalog = ToolCatalog()
        self._catalog_entries = self._catalog.entries
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}
        self._handler_factories: dict[str, Callable[[], Callable]] = {}
        self._ranker = ToolRanker()
        self._executor = ToolExecutor(
            app_logger=logger,
            audit_logger=audit,
            interaction_logger=self._log_interaction,
        )
        self._hydrator = ToolHydrator(
            catalog=self._catalog,
            tools_cache=self._tools,
            handlers_cache=self._handlers,
            handler_factories=self._handler_factories,
            executor=self._executor,
            is_llm_callable=self._is_llm_callable_entry,
        )
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
        self._catalog.register(entry)
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

    @staticmethod
    def _is_llm_callable_entry(entry: ToolCatalogEntry) -> bool:
        return entry.hydration_mode != "execute_via_generic_runner"

    def _hydrate_tool(self, name: str) -> Optional[ToolDefinition]:
        definition = self._hydrator.hydrate_tool(name)
        if definition:
            logger.debug("Hydrated tool: %s", name)
        return definition

    def unregister(self, name: str):
        self._catalog.unregister(name)
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
        entry = self._catalog.get(name)
        return entry.to_tool_definition() if entry else None

    def get_handler(self, name: str) -> Optional[Callable]:
        return self._hydrator.get_handler(name, persist=True)

    def list_tools(self) -> list[str]:
        return self._catalog.list_names()

    def list_dynamic_tools(self) -> list[str]:
        return sorted(self._dynamic_tool_names)

    def _execute_with_handler(
        self,
        tool_name: str,
        handler: Callable,
        kwargs: dict,
    ) -> ToolResult:
        return self._executor.execute(tool_name, handler, kwargs)

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        handler = self.get_handler(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
            )
        return self._executor.execute(tool_name, handler, kwargs)

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
        return self._catalog.get_all_definitions()

    def get_all_rag_documents(self) -> list[dict]:
        return self._catalog.get_all_rag_documents()

    def get_all_llm_schemas(self) -> list[dict]:
        return self._catalog.get_all_llm_schemas()

    def get_tools_by_category(self, category: str) -> list[ToolDefinition]:
        return self._catalog.get_tools_by_category(category)

    def get_tools_by_tier(self, tier: str) -> list[ToolDefinition]:
        return self._catalog.get_tools_by_tier(tier)

    def get_always_available(self) -> list[ToolDefinition]:
        return self._catalog.get_always_available()

    @staticmethod
    def _coerce_tool_score(raw_score) -> float:
        try:
            return max(0.0, float(raw_score or 0.0))
        except Exception:
            return 0.0

    def _entry_from_rag_hit(self, hit: dict) -> tuple[ToolCatalogEntry | None, bool]:
        metadata = hit.get("metadata", {}) or {}
        tool_name = str(
            metadata.get("tool_name") or hit.get("id", "").removeprefix("tool-")
        ).strip()
        if not tool_name:
            return None, False
        entry = self._catalog.get(tool_name)
        if entry:
            return entry, True

        workflow_name = str(
            metadata.get("workflow_name")
            or metadata.get("workflowName")
            or ""
        ).strip()
        hydration_mode = str(metadata.get("hydration_mode", "lazy") or "lazy")
        if metadata.get("llm_callable") is False and hydration_mode != "execute_via_generic_runner":
            hydration_mode = "execute_via_generic_runner"

        catalog_metadata = dict(metadata.get("catalog", {}) or {})
        if metadata.get("use_tool"):
            catalog_metadata["use_tool"] = metadata.get("use_tool")

        definition = ToolDefinition(
            name=tool_name,
            description=str(metadata.get("description", "") or ""),
            category=str(metadata.get("category", "automationedge") or "automationedge"),
            tier=str(metadata.get("tier", "medium_risk") or "medium_risk"),
            parameters=metadata.get("parameters", {}) or {},
            required_params=list(metadata.get("required_params", []) or []),
            always_available=bool(metadata.get("always_available", False)),
            metadata={
                "source": metadata.get("source", "automationedge"),
                "workflow_name": workflow_name,
                "tags": list(metadata.get("tags", []) or []),
            },
            use_when=str(metadata.get("use_when", "") or ""),
            avoid_when=str(metadata.get("avoid_when", "") or ""),
            input_examples=list(metadata.get("input_examples", []) or [])[:2],
        )
        return (
            ToolCatalogEntry.from_definition(
                definition,
                source=str(metadata.get("source", "automationedge") or "automationedge"),
                source_ref=workflow_name or tool_name,
                hydration_mode=hydration_mode,
                latency_class=str(metadata.get("latency_class", "medium") or "medium"),
                mutating=bool(
                    metadata.get(
                        "mutating",
                        str(metadata.get("tier", "read_only") or "read_only") != "read_only",
                    )
                ),
                allowed_agents=list(metadata.get("allowed_agents", []) or []),
                metadata=catalog_metadata,
            ),
            False,
        )

    def rank_tool_candidates(
        self,
        query: str,
        *,
        rag_hits: list[dict] | None = None,
        allowed_categories: list[str] | None = None,
        top_k: int = 8,
        include_category_fallback: bool = False,
        feedback_agent_id: str = "",
    ) -> list[dict]:
        candidates: dict[str, dict] = {}
        retrieval_scores: dict[str, float] = {}
        retrieval_ranks: dict[str, int] = {}

        for idx, hit in enumerate(rag_hits or []):
            if not isinstance(hit, dict):
                continue
            entry, registered = self._entry_from_rag_hit(hit)
            if not entry:
                continue
            if allowed_categories and entry.definition.category not in allowed_categories:
                continue
            score = self._coerce_tool_score(
                hit.get("rrf_score", hit.get("similarity", 0.0))
            )
            existing = candidates.get(entry.name)
            if existing and existing["raw_score"] >= score:
                continue
            candidates[entry.name] = {
                "entry": entry,
                "registered": registered,
                "raw_score": score,
            }
            retrieval_scores[entry.name] = score
            retrieval_ranks[entry.name] = idx

        if include_category_fallback:
            for entry in self._catalog.values():
                if allowed_categories and entry.definition.category not in allowed_categories:
                    continue
                candidates.setdefault(
                    entry.name,
                    {
                        "entry": entry,
                        "registered": True,
                        "raw_score": 0.0,
                    },
                )

        feedback_stats = self._get_tool_feedback(
            list(candidates),
            feedback_agent_id=feedback_agent_id,
        )
        ranked = self._ranker.rank(
            query,
            [candidate["entry"] for candidate in candidates.values()],
            retrieval_scores=retrieval_scores,
            retrieval_ranks=retrieval_ranks,
            feedback_stats=feedback_stats,
        )

        cards: list[dict] = []
        for candidate in ranked[:top_k]:
            record = candidates.get(candidate.entry.name, {})
            cards.append(
                self._tool_card_from_entry(
                    candidate.entry,
                    score=candidate.score,
                    registered=bool(record.get("registered", False)),
                )
            )
        return cards

    @staticmethod
    def _get_tool_feedback(
        tool_names: list[str],
        *,
        feedback_agent_id: str = "",
    ) -> dict[str, dict]:
        if not tool_names:
            return {}
        try:
            from state.agent_catalog import get_agent_catalog

            limit = min(int(CONFIG.get("AGENT_INTERACTION_LOG_LIMIT", 500) or 500), 200)
            half_life_days = float(CONFIG.get("TOOL_FEEDBACK_HALF_LIFE_DAYS", 7.0) or 7.0)
            catalog = get_agent_catalog()
            global_feedback = catalog.summarize_tool_feedback(
                tool_names,
                limit=limit,
                half_life_days=half_life_days,
            )
            if not feedback_agent_id:
                return global_feedback

            scoped_feedback = catalog.summarize_tool_feedback(
                tool_names,
                agent_id=feedback_agent_id,
                limit=limit,
                half_life_days=half_life_days,
            )
            merged: dict[str, dict] = {}
            for tool_name in tool_names:
                scoped = scoped_feedback.get(tool_name, {})
                scoped_weight = float(
                    scoped.get("weighted_total_count", scoped.get("total_count", 0.0))
                    or 0.0
                )
                merged[tool_name] = scoped if scoped_weight > 0.0 else global_feedback.get(tool_name, {})
            return merged
        except Exception:
            return {}

    def build_turn_toolset(
        self,
        tool_names: list[str],
        *,
        allowed_categories: list[str] | None = None,
        include_meta: bool = True,
        feedback_agent_id: str = "",
    ) -> TurnToolSet:
        self._ensure_meta_tools()
        return self._hydrator.build_turn_toolset(
            tool_names,
            allowed_categories=allowed_categories,
            include_meta=include_meta,
            feedback_agent_id=feedback_agent_id,
        )

    def build_turn_toolset_filtered(
        self,
        rag_tool_names: list[str],
        *,
        query: str = "",
        rag_hits: list[dict] | None = None,
        max_rag_tools: int = 12,
        allowed_categories: list[str] | None = None,
        feedback_agent_id: str = "",
    ) -> TurnToolSet:
        self._ensure_meta_tools()
        selected_names: list[str] = []

        if rag_hits:
            for entry in self._catalog.values():
                tool_def = entry.definition
                if not tool_def.always_available:
                    continue
                if allowed_categories and tool_def.category not in allowed_categories:
                    continue
                selected_names.append(tool_def.name)

            ranked_cards = self.rank_tool_candidates(
                query,
                rag_hits=rag_hits,
                allowed_categories=allowed_categories,
                top_k=max_rag_tools,
                feedback_agent_id=feedback_agent_id,
            )
            selected_names.extend(card["name"] for card in ranked_cards)
            if not ranked_cards and len(self._catalog) <= MAX_TOOLS_FOR_FULL_CATALOG:
                for entry in self._catalog.values():
                    tool_def = entry.definition
                    if allowed_categories and tool_def.category not in allowed_categories:
                        continue
                    selected_names.append(tool_def.name)
        elif len(self._catalog) <= MAX_TOOLS_FOR_FULL_CATALOG:
            for entry in self._catalog.values():
                tool_def = entry.definition
                if allowed_categories and tool_def.category not in allowed_categories:
                    continue
                selected_names.append(tool_def.name)
        else:
            for entry in self._catalog.values():
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
            feedback_agent_id=feedback_agent_id,
        )
        logger.info(
            "Built turn-local tool set: %s/%s (%s)",
            len(toolset.list_tool_names()),
            len(self._catalog),
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
            "title": md.get("title", ""),
            "workflow_name": md.get("workflow_name", ""),
            "description": tool_def.description,
            "category": tool_def.category,
            "tier": tool_def.tier,
            "safety": md.get("safety", ""),
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
            "hydration_mode": md.get("hydration_mode", "eager"),
            "structured_output": bool(md.get("structured_output", False)),
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
        card = cls._tool_card(
            entry.to_tool_definition(),
            score=score,
            registered=registered and cls._is_llm_callable_entry(entry),
        )
        card["hydration_mode"] = entry.hydration_mode
        card["latency_class"] = entry.latency_class
        card["mutating"] = entry.mutating
        card["llm_callable"] = cls._is_llm_callable_entry(entry)
        if not card["llm_callable"]:
            use_tool = str(
                entry.metadata.get("use_tool")
                or entry.definition.metadata.get("use_tool")
                or "trigger_workflow"
            )
            card["registered"] = False
            card["use_tool"] = use_tool
            card["hint"] = (
                f"Tool is cataloged but not exposed as a direct LLM function. "
                f"Use {use_tool} with workflow_name and parameters."
            )
        return card

    def get_tool_inventory(
        self,
        agent_tool_map: Optional[dict[str, list[str]]] = None,
    ) -> list[dict]:
        lookup = agent_tool_map or {}
        inventory: list[dict] = []
        for tool_name, entry in self._catalog.items():
            linked_agents = sorted(
                agent_id for agent_id, tools in lookup.items() if tool_name in tools
            )
            card = self._tool_card_from_entry(entry)
            inventory.append(
                {
                    "toolName": tool_name,
                    "toolTitle": card.get("title", ""),
                    "workflowName": card.get("workflow_name", ""),
                    "description": card["description"],
                    "category": card["category"],
                    "tier": card["tier"],
                    "safety": card.get("safety", ""),
                    "source": card["source"],
                    "dynamic": card["dynamic"],
                    "active": card["active"],
                    "tags": card["tags"],
                    "parameters": entry.definition.parameters,
                    "required": entry.definition.required_params,
                    "useWhen": card["use_when"],
                    "avoidWhen": card["avoid_when"],
                    "inputExamples": card["input_examples"],
                    "hydrationMode": card["hydration_mode"],
                    "latencyClass": card["latency_class"],
                    "mutating": card["mutating"],
                    "llmCallable": card.get("llm_callable", True),
                    "structuredOutput": card.get("structured_output", False),
                    "outputSchema": entry.definition.metadata.get("output_schema", {}),
                    "annotations": entry.definition.metadata.get("annotations", {}),
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
        direct_dynamic = {
            str(name).strip().lower()
            for name in CONFIG.get("AE_DYNAMIC_DIRECT_TOOL_NAMES", [])
            if str(name).strip()
        }

        registered = 0
        skipped = 0
        collisions: list[str] = []

        for mapping in mappings:
            if not include_inactive and not mapping.active:
                skipped += 1
                continue
            if (
                mapping.tool_name in self._catalog
                and mapping.tool_name not in self._dynamic_tool_names
            ):
                collisions.append(mapping.tool_name)
                continue

            definition = mapping.to_tool_definition()
            is_direct = (
                mapping.tool_name.lower() in direct_dynamic
                or mapping.workflow_name.lower() in direct_dynamic
            )
            self.register_catalog_entry(
                ToolCatalogEntry.from_definition(
                    definition,
                    source_ref=mapping.workflow_name,
                    hydration_mode="lazy" if is_direct else "execute_via_generic_runner",
                    latency_class="medium",
                    metadata={
                        "use_tool": "trigger_workflow",
                    },
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
        query: str = "",
        rag_hits: list[dict] | None = None,
        max_rag_tools: int = 12,
        allowed_categories: list[str] | None = None,
        feedback_agent_id: str = "",
    ) -> list:
        return self.build_turn_toolset_filtered(
            rag_tool_names,
            query=query,
            rag_hits=rag_hits,
            max_rag_tools=max_rag_tools,
            allowed_categories=allowed_categories,
            feedback_agent_id=feedback_agent_id,
        ).to_vertex_tools()

    # -----------------------------------------------------------------
    # discover_tools meta-tool
    # -----------------------------------------------------------------

    def _ensure_meta_tools(self):
        if self._meta_registered:
            return
        self._meta_registered = True

        def _discover_tools(query: str, category: str = "", top_k: int = 8, _agent_id: str = "") -> dict:
            from rag.engine import get_rag_engine

            results: list[dict] = []
            rag_hits = []

            if query and query.strip():
                rag_hits = get_rag_engine().search_tools(query, top_k=max(top_k * 3, top_k))

            results = self.rank_tool_candidates(
                query,
                rag_hits=rag_hits,
                allowed_categories=[category] if category else None,
                top_k=top_k,
                include_category_fallback=bool(category),
                feedback_agent_id=_agent_id,
            )

            if not results:
                cats = sorted({entry.definition.category for entry in self._catalog.values()})
                return {
                    "tools": [],
                    "total_catalog_size": len(self._catalog),
                    "available_categories": cats,
                    "hint": "No matching tools found. Try a different query or browse by category.",
                }

            return {"tools": results[:top_k], "total_catalog_size": len(self._catalog)}

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
