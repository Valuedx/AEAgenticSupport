"""
Startup bootstrap for tool registration, dynamic reload, and RAG indexing.
"""
from __future__ import annotations

import importlib
import logging

from rag.engine import get_rag_engine
from state.agent_catalog import get_agent_catalog
from tools.registry import tool_registry

_STATIC_TOOL_MODULES = [
    "tools.status_tools",
    "tools.log_tools",
    "tools.file_tools",
    "tools.remediation_tools",
    "tools.dependency_tools",
    "tools.notification_tools",
    "tools.general_tools",
    "tools.mcp_tools",
]


def import_static_tool_modules() -> list[str]:
    loaded: list[str] = []
    for module_name in _STATIC_TOOL_MODULES:
        importlib.import_module(module_name)
        loaded.append(module_name)
    return loaded


def initialize_tooling(app_logger: logging.Logger | None = None) -> dict:
    logger = app_logger or logging.getLogger("ops_agent.tools.bootstrap")
    summary = {
        "modules_loaded": [],
        "dynamic_reload": {},
        "agent_links_updated": False,
        "rag_indexed": False,
        "catalog_size": 0,
    }

    summary["modules_loaded"] = import_static_tool_modules()

    try:
        reload_summary = tool_registry.reload_automationedge_tools()
        summary["dynamic_reload"] = reload_summary
        logger.info(
            "Dynamic AE tools reload: enabled=%s registered=%s removed=%s skipped=%s collisions=%s",
            reload_summary.get("enabled"),
            reload_summary.get("registered"),
            reload_summary.get("removed"),
            reload_summary.get("skipped"),
            len(reload_summary.get("collisions", [])),
        )
    except Exception as exc:
        summary["dynamic_reload"] = {"error": str(exc)}
        logger.warning("Dynamic AE tool sync skipped: %s", exc)

    try:
        get_agent_catalog().ensure_default_agent_links(tool_registry.list_tools())
        summary["agent_links_updated"] = True
    except Exception as exc:
        logger.warning("Agent catalog initialization skipped: %s", exc)

    try:
        rag = get_rag_engine()
        tool_docs = tool_registry.get_all_rag_documents()
        rag.index_tools(tool_docs)
        summary["rag_indexed"] = True
        summary["catalog_size"] = len(tool_docs)
        logger.info("Ops Agent initialized - tools indexed into RAG")
    except Exception as exc:
        summary["rag_error"] = str(exc)
        summary["catalog_size"] = len(tool_registry.list_tools())
        logger.warning("RAG indexing deferred (DB may not be ready): %s", exc)

    return summary
