
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def dump_all_employee_tools():
    rag = get_rag_engine()
    hits = rag.search_tools("employee", top_k=20)
    print(f"Dumping {len(hits)} hits:")
    for hit in hits:
        print(f"=== ID: {hit['id']} (Score: {hit.get('rrf_score') or hit.get('similarity')}) ===")
        print(f"Workflow Name: {hit['metadata'].get('workflow_name')}")
        print(f"Required Params: {hit['metadata'].get('required_params')}")
        # print(f"Description: {hit['content'][:100]}...")
        print("-" * 30)

if __name__ == "__main__":
    dump_all_employee_tools()
