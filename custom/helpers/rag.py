"""
RAG search helpers.
Bridges the Extension layer to the standalone rag/engine.py module.
Falls back to REST stubs if direct pgvector access is unavailable.
"""

import logging
from typing import Dict, List

logger = logging.getLogger("support_agent.rag")

try:
    from rag.engine import get_rag_engine
    _USE_DIRECT = True
except ImportError:
    _USE_DIRECT = False
    logger.info("Direct RAG engine unavailable, using REST stubs")


def rag_search_sop(client, query: str, top_k: int = 6) -> List[Dict]:
    if _USE_DIRECT:
        return get_rag_engine().search_sops(query, top_k=top_k)
    return client.call(
        "/rag/sop/search", {"query": query, "top_k": top_k}
    ).get("results", [])


def rag_search_tools(client, query: str, top_k: int = 8) -> List[Dict]:
    if _USE_DIRECT:
        return get_rag_engine().search_tools(query, top_k=top_k)
    return client.call(
        "/rag/tools/search", {"query": query, "top_k": top_k}
    ).get("results", [])


def rag_search_kb(client, query: str, top_k: int = 5) -> List[Dict]:
    if _USE_DIRECT:
        return get_rag_engine().search_kb(query, top_k=top_k)
    return client.call(
        "/rag/kb/search", {"query": query, "top_k": top_k}
    ).get("results", [])


def rag_search_past_incidents(client, query: str,
                              top_k: int = 3) -> List[Dict]:
    if _USE_DIRECT:
        return get_rag_engine().search_past_incidents(query, top_k=top_k)
    return client.call(
        "/rag/incidents/search", {"query": query, "top_k": top_k}
    ).get("results", [])
