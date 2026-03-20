"""MCP Tool bridge endpoint.

Reads the existing tool_specs registry from the parent AEAgenticSupport
project and exposes it as a JSON API for the frontend node palette.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from app.security.tenant import get_tenant_id
from app.api.schemas import ToolOut

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])
logger = logging.getLogger(__name__)

_TOOL_CACHE: list[dict[str, Any]] | None = None


def _load_tool_specs() -> list[dict[str, Any]]:
    """Attempt to import tool_specs from the parent MCP server."""
    global _TOOL_CACHE
    if _TOOL_CACHE is not None:
        return _TOOL_CACHE

    mcp_path = Path(__file__).resolve().parents[3] / "mcp_server"
    if not mcp_path.exists():
        logger.warning("MCP server directory not found at %s", mcp_path)
        _TOOL_CACHE = []
        return _TOOL_CACHE

    sys.path.insert(0, str(mcp_path.parent))
    try:
        mod = importlib.import_module("mcp_server.tool_specs")
        registry = getattr(mod, "TOOL_SPECS", {})

        tools = []
        for name, spec in registry.items():
            tools.append({
                "name": name,
                "title": spec.get("title", name),
                "description": spec.get("description", ""),
                "category": spec.get("category", "misc"),
                "safety_tier": spec.get("safety_tier", "safe_read"),
                "tags": spec.get("tags", []),
            })

        _TOOL_CACHE = tools
        logger.info("Loaded %d tool specs from MCP server", len(tools))
    except Exception:
        logger.exception("Failed to load tool_specs from MCP server")
        _TOOL_CACHE = []
    finally:
        if str(mcp_path.parent) in sys.path:
            sys.path.remove(str(mcp_path.parent))

    return _TOOL_CACHE


@router.get("", response_model=list[ToolOut])
def list_tools(tenant_id: str = Depends(get_tenant_id)):
    """Return the full MCP tool registry for the node palette."""
    return _load_tool_specs()
