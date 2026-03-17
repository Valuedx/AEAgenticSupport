
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def dump_target_tool():
    rag = get_rag_engine()
    # Attempt to get the tool directly by ID if possible, or search for exact match
    # Since search_tools returns hits, let's filter for the specific ID
    hits = rag.search_tools("WF_add_employee", top_k=5)
    for hit in hits:
        if hit['id'] == "tool-WF_add_employee":
            print(f"ID: {hit['id']}")
            print("METADATA:")
            print(json.dumps(hit['metadata'], indent=2))
            print("CONTENT:")
            print(hit['content'])
            break

if __name__ == "__main__":
    dump_target_tool()
