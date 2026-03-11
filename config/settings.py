"""
Central configuration — all settings loaded from environment variables
with sensible defaults for local development.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

_t4_base_url = os.environ.get("T4_BASE_URL", "").strip()
_ae_base_url_env = os.environ.get("AE_BASE_URL", "").strip()
_ae_rest_base_path_env = os.environ.get("AE_REST_BASE_PATH", "").strip()

_derived_ae_base_url = _ae_base_url_env
_derived_rest_path = _ae_rest_base_path_env or "/aeengine/rest"

if _t4_base_url:
    parsed = urlparse(_t4_base_url)
    if not _ae_base_url_env:
        if parsed.scheme and parsed.netloc:
            _derived_ae_base_url = f"{parsed.scheme}://{parsed.netloc}"
        else:
            _derived_ae_base_url = _t4_base_url
    if not _ae_rest_base_path_env and parsed.path and parsed.path != "/":
        _derived_rest_path = parsed.path.rstrip("/")

if not _derived_ae_base_url:
    _derived_ae_base_url = "https://localhost:8443"

CONFIG = {
    # AutomationEdge API
    "AE_BASE_URL": _derived_ae_base_url,
    "AE_API_KEY": os.environ.get("AE_API_KEY", ""),
    "AE_USERNAME": os.environ.get(
        "AE_USERNAME", os.environ.get("T4_USERNAME", "")
    ),
    "AE_PASSWORD": os.environ.get(
        "AE_PASSWORD", os.environ.get("T4_PASSWORD", "")
    ),
    "AE_ORG_CODE": os.environ.get(
        "AE_ORG_CODE", os.environ.get("T4_ORG_CODE", "")
    ),
    "AE_DEFAULT_USERID": os.environ.get("AE_DEFAULT_USERID", "ops_agent"),
    "AE_REST_BASE_PATH": _derived_rest_path,
    "AE_AUTH_ENDPOINT": os.environ.get("AE_AUTH_ENDPOINT", "/authenticate"),
    "AE_EXECUTE_ENDPOINT": os.environ.get("AE_EXECUTE_ENDPOINT", "/execute"),
    "AE_WORKFLOWS_ENDPOINT": os.environ.get("AE_WORKFLOWS_ENDPOINT", "/workflows/catalogue"),
    "AE_WORKFLOWS_METHOD": os.environ.get("AE_WORKFLOWS_METHOD", "GET"),
    "AE_WORKFLOW_DETAILS_ENDPOINT": os.environ.get(
        "AE_WORKFLOW_DETAILS_ENDPOINT", "/{org_code}/workflows/{workflow_identifier}/config"
    ),
    "AE_WORKFLOW_DETAILS_METHOD": os.environ.get(
        "AE_WORKFLOW_DETAILS_METHOD", "GET"
    ),
    "AE_SESSION_HEADER": os.environ.get("AE_SESSION_HEADER", "X-session-token"),
    "AE_TOKEN_FIELD": os.environ.get("AE_TOKEN_FIELD", "token"),
    "AE_TOKEN_TTL_SECONDS": int(os.environ.get("AE_TOKEN_TTL_SECONDS", "1800")),
    "AE_ENABLE_DYNAMIC_TOOLS": os.environ.get(
        "AE_ENABLE_DYNAMIC_TOOLS", "true"
    ).lower() in ("1", "true", "yes"),
    "AE_DYNAMIC_DIRECT_TOOL_NAMES": [
        name.strip()
        for name in os.environ.get("AE_DYNAMIC_DIRECT_TOOL_NAMES", "").split(",")
        if name.strip()
    ],
    "AE_MCP_TOOLS_ENABLED": os.environ.get(
        "AE_MCP_TOOLS_ENABLED", "false"
    ).lower() in ("1", "true", "yes"),
    "AE_MCP_SERVER_URL": os.environ.get("AE_MCP_SERVER_URL", "").strip(),
    "AE_MCP_SERVER_TRANSPORT": os.environ.get(
        "AE_MCP_SERVER_TRANSPORT", "streamable-http"
    ).strip(),
    "AE_MCP_SERVER_HEADERS_JSON": os.environ.get(
        "AE_MCP_SERVER_HEADERS_JSON", ""
    ).strip(),
    "AE_MCP_SERVER_TIMEOUT_SECONDS": os.environ.get(
        "AE_MCP_SERVER_TIMEOUT_SECONDS",
        os.environ.get("AE_TIMEOUT_SECONDS", os.environ.get("T4_TIMEOUT_SECONDS", "30")),
    ),
    "AE_TIMEOUT_SECONDS": int(
        os.environ.get("AE_TIMEOUT_SECONDS", os.environ.get("T4_TIMEOUT_SECONDS", "30"))
    ),

    # Google Cloud / Vertex AI
    "GOOGLE_CLOUD_PROJECT": os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
    "GOOGLE_CLOUD_LOCATION": os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    "VERTEX_AI_MODEL": os.environ.get("VERTEX_AI_MODEL", "gemini-2.0-flash"),

    # PostgreSQL + pgvector
    "POSTGRES_DSN": os.environ.get(
        "POSTGRES_DSN", "postgresql://localhost/ops_agent"
    ),
    "DB_POOL_MAX_CONN": int(os.environ.get("DB_POOL_MAX_CONN", "10")),

    # Embeddings (set EMBEDDING_DIMENSION to avoid cold-start probe, e.g. 768 for text-embedding-004)
    "EMBEDDING_MODEL": os.environ.get("EMBEDDING_MODEL", "text-embedding-004"),
    "EMBEDDING_DIMENSION": int(os.environ["EMBEDDING_DIMENSION"]) if os.environ.get("EMBEDDING_DIMENSION") else None,

    # Tool gateway
    "TOOL_BASE_URL": os.environ.get("TOOL_BASE_URL", "http://localhost:9999"),
    "TOOL_AUTH_TOKEN": os.environ.get("TOOL_AUTH_TOKEN", ""),
    "COGNIBOT_BASE_URL": os.environ.get("COGNIBOT_BASE_URL", "http://localhost:3978"),
    "COGNIBOT_DIRECTLINE_SECRET": os.environ.get("COGNIBOT_DIRECTLINE_SECRET", ""),
    "COGNIBOT_TIMEOUT_SECONDS": int(os.environ.get("COGNIBOT_TIMEOUT_SECONDS", "60")),

    # Agent behaviour limits
    "MAX_AGENT_ITERATIONS": int(os.environ.get("MAX_AGENT_ITERATIONS", "15")),
    "MAX_RESTARTS_PER_WORKFLOW": int(
        os.environ.get("MAX_RESTARTS_PER_WORKFLOW", "3")
    ),
    "MAX_BULK_OPERATIONS": int(os.environ.get("MAX_BULK_OPERATIONS", "10")),
    "STALE_ISSUE_MINUTES": int(os.environ.get("STALE_ISSUE_MINUTES", "30")),
    "RECURRENCE_ESCALATION_THRESHOLD": int(
        os.environ.get("RECURRENCE_ESCALATION_THRESHOLD", "3")
    ),
    "MAX_RAG_TOOLS": int(os.environ.get("MAX_RAG_TOOLS", "12")),
    "TOOL_FEEDBACK_HALF_LIFE_DAYS": float(
        os.environ.get("TOOL_FEEDBACK_HALF_LIFE_DAYS", "7")
    ),

    # Safety — workflows that must never be auto-restarted
    "PROTECTED_WORKFLOWS": [
        wf.strip()
        for wf in os.environ.get(
            "PROTECTED_WORKFLOWS", "regulatory_report_irdai"
        ).split(",")
        if wf.strip()
    ],

    # Logging
    "LOG_DIR": os.environ.get("LOG_DIR", "logs"),
    "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
    "LOG_JSON": os.environ.get("LOG_JSON", "true").lower() == "true",

    # Agent management UI / catalog
    "AGENT_CATALOG_PATH": os.environ.get(
        "AGENT_CATALOG_PATH", "state/agent_catalog.json"
    ),
    "APP_CONTROL_CENTER_PATH": os.environ.get(
        "APP_CONTROL_CENTER_PATH", "state/app_control_center.json"
    ),
    "TOOL_OVERRIDE_PATH": os.environ.get(
        "TOOL_OVERRIDE_PATH", "state/tool_overrides.json"
    ),
    "SCHEDULER_CATALOG_PATH": os.environ.get(
        "SCHEDULER_CATALOG_PATH", "state/scheduler_catalog.json"
    ),
    "DOCS_CATALOG_PATH": os.environ.get(
        "DOCS_CATALOG_PATH", "state/docs_catalog.json"
    ),
    "AGENT_ADMIN_TOKEN": os.environ.get("AGENT_ADMIN_TOKEN", ""),
    "AGENT_INTERACTION_LOG_LIMIT": int(
        os.environ.get("AGENT_INTERACTION_LOG_LIMIT", "500")
    ),
    "SESSION_TTL_DAYS": int(os.environ.get("SESSION_TTL_DAYS", "30")),

    # RBAC & Security (Feature 2.6)
    "RBAC_ENABLED": os.environ.get("RBAC_ENABLED", "true").lower() == "true",
    "ROLE_RANK": {
        "admin": 100,
        "dev": 50,
        "technical": 50,
        "support": 10,
        "readonly": 0
    },
    "TIER_RANK": {
        "high_risk": 50,
        "medium_risk": 20,
        "low_risk": 10,
        "read_only": 0
    },

    # Proactive Monitoring & Scheduling (Feature 2.2)
    "ENABLE_PROACTIVE_MONITORING": os.environ.get("ENABLE_PROACTIVE_MONITORING", "true").lower() == "true",
    "HEALTH_CHECK_INTERVAL_SECONDS": int(os.environ.get("HEALTH_CHECK_INTERVAL_SECONDS", "300")),
    "ENABLE_DAILY_SUMMARY": os.environ.get("ENABLE_DAILY_SUMMARY", "true").lower() == "true",
    "DAILY_SUMMARY_HOUR": int(os.environ.get("DAILY_SUMMARY_HOUR", "8")),
    "MONITORED_WORKFLOWS": [
        w.strip() for w in os.environ.get("MONITORED_WORKFLOWS", "").split(",") if w.strip()
    ],
}
