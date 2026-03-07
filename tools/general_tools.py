"""
General-purpose escape-hatch tools.

These are always-available, Cursor-style tools that let the LLM
reach any AE API endpoint or run any read-only database query even
when no specific typed tool has been registered for it.

Safety:
  - call_ae_api is tier medium_risk (write methods require approval)
  - query_database is read_only (SELECT only, enforced)
  - search_knowledge_base is read_only (RAG search wrapper)
"""
from __future__ import annotations

import json
import logging
import re

from psycopg2.extras import RealDictCursor

from config.db import get_readonly_conn
from config.settings import CONFIG
from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.general")

_BLOCKED_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY)\b",
    re.IGNORECASE,
)

_MAX_ROWS = 50


# =====================================================================
# call_ae_api — generic REST escape hatch
# =====================================================================

def call_ae_api(method: str, endpoint: str,
                params: str = "", body: str = "") -> dict:
    """Call any AutomationEdge REST API endpoint.

    Handles both GET (read) and POST/PUT/DELETE (write) methods.
    The approval gate will intercept write methods before execution.
    """
    method = method.strip().upper()
    endpoint = endpoint.strip()
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    client = get_ae_client()

    parsed_params = _safe_json(params) if params else None
    parsed_body = _safe_json(body) if body else None

    if method == "GET":
        data = client.get(endpoint, params=parsed_params)
    elif method == "POST":
        data = client.post(endpoint, payload=parsed_body)
    elif method == "PUT":
        data = client.request("PUT", endpoint, payload=parsed_body or {})
    elif method == "DELETE":
        data = client.request("DELETE", endpoint, params=parsed_params)
    else:
        raise ValueError(f"Unsupported HTTP method: {method}")

    return {
        "method": method,
        "endpoint": endpoint,
        "response": _truncate(data),
    }


# =====================================================================
# query_database — read-only SQL escape hatch
# =====================================================================

def query_database(sql: str, params: str = "") -> dict:
    """Execute a read-only SQL query against the ops_agent database.

    Only SELECT statements are allowed. Mutations are blocked.
    Results are capped at _MAX_ROWS rows.
    """
    sql = sql.strip().rstrip(";")

    if _BLOCKED_SQL.search(sql):
        return {
            "error": (
                "Only SELECT queries are allowed. "
                "Mutations (INSERT/UPDATE/DELETE/DROP/etc.) are blocked."
            ),
        }

    if not sql.upper().startswith("SELECT"):
        return {
            "error": "Query must start with SELECT.",
        }

    parsed_params = None
    if params:
        parsed_params = _safe_json(params)
        if isinstance(parsed_params, dict):
            parsed_params = tuple(parsed_params.values())
        elif not isinstance(parsed_params, (list, tuple)):
            parsed_params = (parsed_params,)

    try:
        with get_readonly_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, parsed_params)
                rows = cur.fetchmany(_MAX_ROWS)
                total = cur.rowcount
    except Exception as e:
        return {"error": str(e), "sql": sql[:200]}

    serializable_rows = [
        {k: _make_serializable(v) for k, v in row.items()}
        for row in rows
    ]

    result = {
        "rows": serializable_rows,
        "row_count": len(serializable_rows),
    }
    if total > _MAX_ROWS:
        result["truncated"] = True
        result["total_rows"] = total
    return result


# =====================================================================
# search_knowledge_base — RAG search escape hatch
# =====================================================================

def search_knowledge_base(query: str, collection: str = "",
                          top_k: int = 5) -> dict:
    """Search the RAG knowledge base by semantic query.

    Collections: kb_articles, sops, tools, past_incidents.
    If collection is empty, searches across all collections.
    """
    from rag.engine import get_rag_engine
    rag = get_rag_engine()

    results = []
    collections = (
        [collection] if collection
        else ["kb_articles", "sops", "tools", "past_incidents"]
    )

    errors = []
    for coll in collections:
        try:
            hits = rag.search(query, collection=coll, top_k=top_k)
            for h in hits:
                score = h.get("rrf_score", h.get("similarity", 0)) or 0
                results.append({
                    "id": h.get("id", ""),
                    "collection": coll,
                    "content": h.get("content", "")[:300],
                    "similarity": round(float(score), 3),
                })
        except Exception as exc:
            logger.warning("RAG search failed for collection %s: %s", coll, exc)
            errors.append(f"{coll}: {exc}")

    results.sort(key=lambda r: r.get("similarity", 0), reverse=True)
    result = {
        "results": results[:top_k],
        "collections_searched": collections,
    }
    if errors:
        result["warnings"] = errors
    return result


# =====================================================================
# Helpers
# =====================================================================

def _safe_json(raw: str):
    """Parse a JSON string, returning the raw string if it fails."""
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _make_serializable(val):
    """Coerce DB values to JSON-serializable types."""
    if isinstance(val, (str, int, float, bool, type(None))):
        return val
    return str(val)


def _truncate(data, max_len: int = 4000) -> dict | list | str:
    """Prevent huge API responses from blowing up the context."""
    s = json.dumps(data, default=str)
    if len(s) <= max_len:
        return data
    if isinstance(data, str):
        return data[:max_len] + "..."
    return {
        "_truncated": True,
        "_preview": s[:max_len],
    }


# =====================================================================
# Registration
# =====================================================================

tool_registry.register(
    ToolDefinition(
        name="call_ae_api",
        description=(
            "Call any AutomationEdge REST API endpoint directly. "
            "Use this as a fallback when no specific tool exists for "
            "the operation you need. Supports GET, POST, PUT, DELETE. "
            "Write methods (POST/PUT/DELETE) will trigger the approval "
            "gate. Prefer specific typed tools when available — they "
            "have better parameter validation and audit logging."
        ),
        category="general",
        tier="medium_risk",
        parameters={
            "method": {
                "type": "string",
                "description": "HTTP method: GET, POST, PUT, or DELETE",
            },
            "endpoint": {
                "type": "string",
                "description": (
                    "API path, e.g. /api/v1/workflows/my_workflow/status "
                    "or /api/v1/queues/my_queue/items"
                ),
            },
            "params": {
                "type": "string",
                "description": (
                    "Query parameters as JSON string, "
                    "e.g. '{\"hours\": 24, \"limit\": 10}'. "
                    "Used for GET requests."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Request body as JSON string for POST/PUT, "
                    "e.g. '{\"reason\": \"cascade failure\"}'"
                ),
            },
        },
        required_params=["method", "endpoint"],
        always_available=True,
        use_when=(
            "No specific typed tool covers the exact AutomationEdge endpoint "
            "or operation you need."
        ),
        avoid_when=(
            "A typed tool already exists for the task, or the action is risky "
            "and you have not gathered the exact endpoint and payload yet."
        ),
        input_examples=[
            {
                "method": "GET",
                "endpoint": "/api/v1/workflows/Claims_Processing_Daily/status",
                "params": "{\"limit\": 1}",
            }
        ],
    ),
    call_ae_api,
)

tool_registry.register(
    ToolDefinition(
        name="query_database",
        description=(
            "Run a read-only SQL query against the operations database. "
            "Only SELECT queries are allowed - mutations are blocked. "
            "Useful for checking conversation state, issue history, "
            "RAG document counts, or any custom diagnostic query. "
            "Tables: rag_documents, issue_registry, "
            "conversation_state. Results are capped at 50 rows."
        ),
        category="general",
        tier="read_only",
        parameters={
            "sql": {
                "type": "string",
                "description": (
                    "SQL SELECT query, e.g. "
                    "'SELECT * FROM issue_registry WHERE "
                    "conversation_id = %s'"
                ),
            },
            "params": {
                "type": "string",
                "description": (
                    "Query parameters as JSON string for %s placeholders, "
                    "e.g. '[\"session-123\"]'"
                ),
            },
        },
        required_params=["sql"],
        always_available=True,
        use_when=(
            "You need a read-only diagnostic lookup against internal ops tables "
            "that no typed tool exposes."
        ),
        avoid_when=(
            "A workflow/API/status tool already provides the answer, or the user "
            "is asking to change data."
        ),
        input_examples=[
            {
                "sql": "SELECT workflow_name, status FROM workflow_catalog WHERE workflow_name = %s",
                "params": "[\"Claims_Processing_Daily\"]",
            }
        ],
    ),
    query_database,
)

tool_registry.register(
    ToolDefinition(
        name="search_knowledge_base",
        description=(
            "Search the RAG knowledge base by semantic similarity. "
            "Finds relevant KB articles, SOPs, tool documentation, "
            "and past incident records. Use when you need background "
            "context about a workflow, error pattern, or resolution "
            "approach. Collections: kb_articles, sops, tools, "
            "past_incidents."
        ),
        category="general",
        tier="read_only",
        parameters={
            "query": {
                "type": "string",
                "description": "Semantic search query",
            },
            "collection": {
                "type": "string",
                "description": (
                    "Limit to one collection (empty = search all): "
                    "kb_articles, sops, tools, past_incidents"
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Max results to return (default 5)",
            },
        },
        required_params=["query"],
        always_available=True,
        use_when=(
            "You need SOPs, prior incident context, or background knowledge "
            "before deciding what operational action to take."
        ),
        avoid_when=(
            "You already know the exact workflow/request/entity and need current "
            "state from a live system tool."
        ),
        input_examples=[
            {"query": "dns resolution failure overnight batch", "collection": "sops", "top_k": 3}
        ],
    ),
    search_knowledge_base,
)
