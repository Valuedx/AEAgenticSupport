
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def dump_employee_data():
    rag = get_rag_engine()
    
    print("--- TOOLS ---")
    tool_hits = rag.search_tools("employee", top_k=20)
    for hit in tool_hits:
        print(f"ID: {hit['id']}")
        print(f"Score: {hit.get('score') or hit.get('similarity')}")
        print(f"Required Params: {hit['metadata'].get('required_params')}")
        print(f"Parameters Metadata: {hit['metadata'].get('parameters')}")
        print("-" * 10)

    print("\n--- SOPS ---")
    sop_hits = rag.search_sops("employee add", top_k=10)
    for hit in sop_hits:
        print(f"ID: {hit['id']}")
        print(f"Content: {hit['content']}")
        print("-" * 10)

if __name__ == "__main__":
    dump_employee_data()
