from rag.engine import get_rag_engine
import logging

logging.basicConfig(level=logging.INFO)

def test_rag():
    rag = get_rag_engine()
    query = "Which workflows are having issues?"
    print(f"Testing RAG with query: '{query}'")
    
    # Test vector search
    hits = rag.search_tools(query, top_k=5)
    print(f"\nFound {len(hits)} hits:")
    for i, hit in enumerate(hits):
        print(f"{i+1}. {hit.get('id')} - {hit.get('rrf_score', hit.get('similarity')):.4f}")
        print(f"   Content: {hit.get('content')[:100]}...")

if __name__ == "__main__":
    test_rag()
