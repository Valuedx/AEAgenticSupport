
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def inspect_rag():
    rag = get_rag_engine()
    # Search for it to get the content
    hits = rag.search_tools("WF_add_employee", top_k=1)
    if hits:
        hit = hits[0]
        print(f"ID: {hit['id']}")
        print(f"Content:\n{hit['content']}")
        print(f"Metadata: {json.dumps(hit['metadata'], indent=2)}")
    else:
        print("Not found in RAG.")

if __name__ == "__main__":
    inspect_rag()
