"""
Extension-level settings injected into AI Studio's Django settings.
These are merged with the base AI Studio settings at startup.

NOTE: Environment variables are read directly in each module via
os.environ.get().  This file provides a single reference for all
Extension-specific env vars and their defaults.
"""
from __future__ import annotations

import os

TOOL_BASE_URL = os.environ.get("TOOL_BASE_URL", "http://localhost:9999")
TOOL_AUTH_TOKEN = os.environ.get("TOOL_AUTH_TOKEN", "")
POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "postgresql://localhost/ops_agent")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
VERTEX_AI_MODEL = os.environ.get("VERTEX_AI_MODEL", "gemini-2.0-flash")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-004")
AE_BASE_URL = os.environ.get("AE_BASE_URL", "https://localhost:8443")
AE_API_KEY = os.environ.get("AE_API_KEY", "")
