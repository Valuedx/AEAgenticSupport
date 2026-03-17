
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def dump_employee_tools():
    rag = get_rag_engine()
    # Search very broadly
    hits = rag.search_tools("employee", top_k=20)
    print(f"Found {len(hits)} hits for 'employee':")
    for hit in hits:
        print(f"---")
        print(f"ID: {hit['id']}")
        print(f"Workflow: {hit['metadata'].get('workflow_name')}")
        print(f"Required Params: {hit['metadata'].get('required_params')}")
        print(f"Description: {hit['content'].splitlines()[2] if len(hit['content'].splitlines()) > 2 else ''}")

if __name__ == "__main__":
    dump_employee_tools()
