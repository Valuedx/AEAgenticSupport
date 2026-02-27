"""
App-specific settings for the documents module.
Configures document storage, indexing, and retrieval paths
for the AutomationEdge support assistant.
"""

import os

DOCUMENTS_SETTINGS = {
    "KB_ARTICLES_DIR": os.environ.get("KB_ARTICLES_DIR", "rag/data/kb_articles"),
    "SOP_DIR": os.environ.get("SOP_DIR", "rag/data/sops"),
    "TOOL_DOCS_DIR": os.environ.get("TOOL_DOCS_DIR", "rag/data/tool_docs"),
    "PAST_INCIDENTS_DIR": os.environ.get(
        "PAST_INCIDENTS_DIR", "rag/data/past_incidents"
    ),
    "MAX_DOCUMENT_SIZE_MB": int(os.environ.get("MAX_DOCUMENT_SIZE_MB", "10")),
    "SUPPORTED_FORMATS": ["json", "md", "txt", "csv"],
    "AUTO_INDEX_ON_UPLOAD": True,
}
