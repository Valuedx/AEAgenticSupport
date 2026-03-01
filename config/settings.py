"""
Central configuration — all settings loaded from environment variables
with sensible defaults for local development.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

CONFIG = {
    # AutomationEdge API
    "AE_BASE_URL": os.environ.get("AE_BASE_URL", "https://localhost:8443"),
    "AE_API_KEY": os.environ.get("AE_API_KEY", ""),
    "AE_TIMEOUT_SECONDS": int(os.environ.get("AE_TIMEOUT_SECONDS", "30")),

    # Google Cloud / Vertex AI
    "GOOGLE_CLOUD_PROJECT": os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
    "GOOGLE_CLOUD_LOCATION": os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    "VERTEX_AI_MODEL": os.environ.get("VERTEX_AI_MODEL", "gemini-2.0-flash"),

    # PostgreSQL + pgvector
    "POSTGRES_DSN": os.environ.get(
        "POSTGRES_DSN", "postgresql://localhost/ops_agent"
    ),
    "DB_POOL_MAX_CONN": int(os.environ.get("DB_POOL_MAX_CONN", "10")),

    # Embeddings
    "EMBEDDING_MODEL": os.environ.get("EMBEDDING_MODEL", "text-embedding-004"),

    # Tool gateway
    "TOOL_BASE_URL": os.environ.get("TOOL_BASE_URL", "http://localhost:9999"),
    "TOOL_AUTH_TOKEN": os.environ.get("TOOL_AUTH_TOKEN", ""),

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
}
