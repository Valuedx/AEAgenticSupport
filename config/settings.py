"""
Central configuration — all settings loaded from environment variables
with sensible defaults for local development.
"""

import os

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

    # Embeddings
    "EMBEDDING_MODEL": os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),

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
