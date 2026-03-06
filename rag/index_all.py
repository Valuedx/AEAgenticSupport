"""
Index builder script.
Indexes knowledge base articles, SOPs, tool documentation,
and past incidents into pgvector for RAG retrieval.

Usage:
    python -m rag.index_all
"""

import glob
import json
import logging
import os

from rag.engine import get_rag_engine
from rag.document_processor import DocumentProcessor
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.rag.indexer")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
processor = DocumentProcessor()

def _load_files_from_dir(directory: str) -> list[dict]:
    """Uniformly load and process files using DocumentProcessor."""
    all_chunks = []
    # Support multiple extensions: pdf, md, json
    extensions = ["*.pdf", "*.md", "*.json"]
    
    for ext in extensions:
        pattern = os.path.join(directory, "**", ext)
        for filepath in glob.glob(pattern, recursive=True):
            logger.info(f"Processing: {filepath}")
            chunks = processor.process_file(filepath)
            for chunk in chunks:
                all_chunks.append({
                    "id": chunk.id,
                    "content": chunk.content,
                    "metadata": chunk.metadata
                })
    return all_chunks


def index_kb_articles():
    kb_dir = os.path.join(DATA_DIR, "kb_articles")
    if not os.path.isdir(kb_dir):
        logger.info("No kb_articles directory found, skipping.")
        return
    docs = _load_files_from_dir(kb_dir)
    if docs:
        get_rag_engine().index_documents(docs, collection="kb_articles")
        logger.info(f"Indexed {len(docs)} segments from KB articles")


def index_sops():
    sop_dir = os.path.join(DATA_DIR, "sops")
    if not os.path.isdir(sop_dir):
        logger.info("No sops directory found, skipping.")
        return
    docs = _load_files_from_dir(sop_dir)
    if docs:
        get_rag_engine().index_documents(docs, collection="sops")
        logger.info(f"Indexed {len(docs)} segments from SOPs")


def index_tool_docs():
    tool_doc_dir = os.path.join(DATA_DIR, "tool_docs")
    extra_docs = []
    if os.path.isdir(tool_doc_dir):
        extra_docs = _load_files_from_dir(tool_doc_dir)

    registry_docs = tool_registry.get_all_rag_documents()
    all_docs = registry_docs + extra_docs
    if all_docs:
        get_rag_engine().index_documents(all_docs, collection="tools")
        logger.info(
            f"Indexed {len(all_docs)} tool document segments "
            f"({len(registry_docs)} from registry, "
            f"{len(extra_docs)} from files)"
        )


def index_past_incidents():
    incident_dir = os.path.join(DATA_DIR, "past_incidents")
    if not os.path.isdir(incident_dir):
        logger.info("No past_incidents directory found, skipping.")
        return
    docs = _load_files_from_dir(incident_dir)
    if docs:
        get_rag_engine().index_documents(docs, collection="past_incidents")
        logger.info(f"Indexed {len(docs)} segments from past incidents")


def index_t4_workflows():
    """Fetch T4 workflows from the live API and index them into both:
    - workflow_catalog (Postgres table) — searchable by name/ID
    - rag_documents (collection='tools') — embedded for semantic RAG search

    Uses sync_and_index_workflows() on the client so only one API call is made.
    Skips silently if T4 is not configured or unreachable.
    """
    try:
        from tools.automationedge_client import get_automationedge_client
        from config.settings import CONFIG

        # Only attempt if T4 credentials are configured
        if not CONFIG.get("AE_USERNAME") and not CONFIG.get("AE_API_KEY"):
            logger.info("T4 credentials not configured — skipping workflow index.")
            return

        client = get_automationedge_client()
        result = client.sync_and_index_workflows()
        logger.info(
            "T4 workflow sync complete: db_synced=%d rag_indexed=%d",
            result.get("db_synced", 0),
            result.get("rag_indexed", 0),
        )
    except Exception as exc:
        logger.warning("T4 workflow indexing failed (non-fatal): %s", exc)


def index_all():
    logger.info("Starting full RAG index build...")
    index_kb_articles()
    index_sops()
    index_tool_docs()
    index_past_incidents()
    index_t4_workflows()   # T4 workflow catalog → DB + RAG embeddings
    logger.info("RAG index build complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    index_all()

