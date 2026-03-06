
import os
import logging
from rag.engine import get_rag_engine
from rag.index_all import index_all
from config.db import get_conn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_rag")

def test_hybrid_search():
    engine = get_rag_engine()
    
    # 1. Setup - Ensure table exists with new columns
    engine._ensure_tables()
    
    # 2. Index some test docs
    test_docs = [
        {
            "id": "doc1",
            "content": "The quick brown fox jumps over the lazy dog. Version 2.0 deployment guide.",
            "metadata": {"type": "test"}
        },
        {
            "id": "doc2",
            "content": "PostgreSQL vector similarity search with pgvector extension. HNSW index configuration.",
            "metadata": {"type": "test"}
        },
        {
            "id": "doc3",
            "content": "Enterprise RAG with hybrid search and Reciprocal Rank Fusion (RRF).",
            "metadata": {"type": "test"}
        }
    ]
    
    engine.index_documents(test_docs, collection="test_hybrid")
    
    # 3. Search - Keyword exact match (should favor RRF/TS)
    logger.info("--- Searching for 'pgvector' ---")
    results = engine.search("pgvector", collection="test_hybrid")
    for r in results:
        logger.info(f"ID: {r['id']}, Score: {r.get('rrf_score')}, Content: {r['content'][:50]}...")
    
    # Check if doc2 is top
    assert results[0]['id'] == "doc2"

    # 4. Search - Semantic match
    logger.info("--- Searching for 'fast similarity search' ---")
    results = engine.search("fast similarity search", collection="test_hybrid")
    for r in results:
         logger.info(f"ID: {r['id']}, Score: {r.get('rrf_score')}, Content: {r['content'][:50]}...")

if __name__ == "__main__":
    try:
        test_hybrid_search()
        print("RAG Hybrid Test PASSED")
    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
