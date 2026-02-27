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
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.rag.indexer")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _load_json_files(directory: str) -> list[dict]:
    docs = []
    pattern = os.path.join(directory, "**", "*.json")
    for filepath in glob.glob(pattern, recursive=True):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                docs.extend(data)
            elif isinstance(data, dict):
                docs.append(data)
        except Exception as e:
            logger.warning(f"Failed to load {filepath}: {e}")
    return docs


def _load_markdown_files(directory: str) -> list[dict]:
    docs = []
    pattern = os.path.join(directory, "**", "*.md")
    for filepath in glob.glob(pattern, recursive=True):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            doc_id = os.path.splitext(os.path.basename(filepath))[0]
            docs.append({
                "id": doc_id,
                "content": content,
                "metadata": {"source": filepath, "type": "markdown"},
            })
        except Exception as e:
            logger.warning(f"Failed to load {filepath}: {e}")
    return docs


def index_kb_articles():
    kb_dir = os.path.join(DATA_DIR, "kb_articles")
    if not os.path.isdir(kb_dir):
        logger.info("No kb_articles directory found, skipping.")
        return
    docs = _load_json_files(kb_dir) + _load_markdown_files(kb_dir)
    if docs:
        get_rag_engine().index_documents(docs, collection="kb_articles")
        logger.info(f"Indexed {len(docs)} KB articles")


def index_sops():
    sop_dir = os.path.join(DATA_DIR, "sops")
    if not os.path.isdir(sop_dir):
        logger.info("No sops directory found, skipping.")
        return
    docs = _load_json_files(sop_dir) + _load_markdown_files(sop_dir)
    if docs:
        get_rag_engine().index_documents(docs, collection="sops")
        logger.info(f"Indexed {len(docs)} SOPs")


def index_tool_docs():
    tool_doc_dir = os.path.join(DATA_DIR, "tool_docs")
    extra_docs = []
    if os.path.isdir(tool_doc_dir):
        extra_docs = (
            _load_json_files(tool_doc_dir)
            + _load_markdown_files(tool_doc_dir)
        )

    registry_docs = tool_registry.get_all_rag_documents()
    all_docs = registry_docs + extra_docs
    if all_docs:
        get_rag_engine().index_documents(all_docs, collection="tools")
        logger.info(
            f"Indexed {len(all_docs)} tool documents "
            f"({len(registry_docs)} from registry, "
            f"{len(extra_docs)} from files)"
        )


def index_past_incidents():
    incident_dir = os.path.join(DATA_DIR, "past_incidents")
    if not os.path.isdir(incident_dir):
        logger.info("No past_incidents directory found, skipping.")
        return
    docs = _load_json_files(incident_dir) + _load_markdown_files(incident_dir)
    if docs:
        get_rag_engine().index_documents(docs, collection="past_incidents")
        logger.info(f"Indexed {len(docs)} past incidents")


def index_all():
    logger.info("Starting full RAG index build...")
    index_kb_articles()
    index_sops()
    index_tool_docs()
    index_past_incidents()
    logger.info("RAG index build complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    index_all()
