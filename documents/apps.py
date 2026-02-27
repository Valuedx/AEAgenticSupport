"""
Document management for the AutomationEdge AI Studio extension.
Handles loading and watching knowledge base documents for RAG indexing.
"""

import os
import logging

from documents.settings import DOCUMENTS_SETTINGS

logger = logging.getLogger("ops_agent.documents")


class DocumentManager:
    """Manages document storage directories and auto-indexing for the RAG engine."""

    def __init__(self):
        self.settings = DOCUMENTS_SETTINGS
        self._ensure_directories()

    def _ensure_directories(self):
        for key in ("KB_ARTICLES_DIR", "SOP_DIR", "TOOL_DOCS_DIR",
                     "PAST_INCIDENTS_DIR"):
            path = self.settings.get(key, "")
            if path and not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
                logger.info(f"Created directory: {path}")

    def list_documents(self, directory_key: str) -> list[str]:
        path = self.settings.get(directory_key, "")
        if not path or not os.path.isdir(path):
            return []
        supported = set(self.settings.get("SUPPORTED_FORMATS", []))
        files = []
        for fname in os.listdir(path):
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ext in supported:
                files.append(os.path.join(path, fname))
        return sorted(files)

    def get_all_document_paths(self) -> dict[str, list[str]]:
        return {
            "kb_articles": self.list_documents("KB_ARTICLES_DIR"),
            "sops": self.list_documents("SOP_DIR"),
            "tool_docs": self.list_documents("TOOL_DOCS_DIR"),
            "past_incidents": self.list_documents("PAST_INCIDENTS_DIR"),
        }
