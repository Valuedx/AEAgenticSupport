"""
Configuration for the AutomationEdge MCP Server.
Loads from environment variables with sensible defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    for candidate in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ):
        if candidate.exists():
            load_dotenv(candidate)
            break
except ImportError:
    pass


def _bool(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes")


MCP_CONFIG = {
    "AE_BASE_URL": os.environ.get("AE_BASE_URL", "https://localhost:8443"),
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
    "AE_DEFAULT_USERID": os.environ.get("AE_DEFAULT_USERID", "mcp_server"),
    "AE_REST_BASE_PATH": os.environ.get("AE_REST_BASE_PATH", "/aeengine/rest"),
    "AE_AUTH_ENDPOINT": os.environ.get("AE_AUTH_ENDPOINT", "/authenticate"),
    "AE_SESSION_HEADER": os.environ.get("AE_SESSION_HEADER", "X-session-token"),
    "AE_TOKEN_FIELD": os.environ.get("AE_TOKEN_FIELD", "token"),
    "AE_TOKEN_TTL_SECONDS": int(os.environ.get("AE_TOKEN_TTL_SECONDS", "1800")),
    "AE_TIMEOUT_SECONDS": int(os.environ.get("AE_TIMEOUT_SECONDS", "30")),
    "AE_VERIFY_SSL": _bool(os.environ.get("AE_VERIFY_SSL", "false")),
    "MCP_TRANSPORT": os.environ.get("MCP_TRANSPORT", "stdio"),
    "MCP_HOST": os.environ.get("MCP_HOST", "0.0.0.0"),
    "MCP_PORT": int(os.environ.get("MCP_PORT", "8000")),
    "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
}
