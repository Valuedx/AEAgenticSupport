
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine
from tools.registry import tool_registry

def test_discover():
    query = "Add employee"
    rag = get_rag_engine()
    hits = rag.search_tools(query, top_k=5)
    
    print(f"Results for '{query}':")
    for hit in hits:
        print(f"---")
        print(f"ID: {hit['id']}")
        print(f"Score: {hit.get('rrf_score') or hit.get('similarity')}")
        print(f"Metadata: {json.dumps(hit.get('metadata'), indent=2)}")
        print(f"Content: {hit.get('content')}")

if __name__ == "__main__":
    test_discover()
