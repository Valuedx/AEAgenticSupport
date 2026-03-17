
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def dump_employee_tools():
    rag = get_rag_engine()
    hits = rag.search_tools("employee", top_k=20)
    print(f"Found {len(hits)} hits for 'employee':")
    results = []
    for hit in hits:
        results.append({
            "id": hit['id'],
            "workflow": hit['metadata'].get('workflow_name'),
            "required_params": hit['metadata'].get('required_params'),
            "content_preview": hit['content'][:200].replace('\n', ' ')
        })
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    dump_employee_tools()
