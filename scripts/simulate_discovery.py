
import os
import sys
import json
import sqlite3

# Set up environment
PROJECT_ROOT = "D:\\AEAgenticSupport"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def simulate_discovery():
    rag = get_rag_engine()
    query = "Add employee"
    print(f"--- Simulating RAG search for: '{query}' ---")
    
    # 1. Search tools
    tool_hits = rag.search_tools(query, top_k=5)
    print(f"\nTool Hits ({len(tool_hits)}):")
    for hit in tool_hits:
        print(f"- ID: {hit['id']}, Score: {hit.get('similarity', hit.get('rrf_score'))}")
        metadata = hit.get('metadata', {})
        print(f"  WF Name: {metadata.get('workflow_name')}")
        params = metadata.get('parameters', [])
        if params:
            print(f"  Params: {[p.get('name') for p in params]}")
        else:
            print("  Params: None")

    # 2. Search SOPs
    sop_hits = rag.search_sops(query, top_k=3)
    print(f"\nSOP Hits ({len(sop_hits)}):")
    for hit in sop_hits:
        print(f"- ID: {hit['id']}")
        print(f"  Content Snippet: {hit['content'][:200]}...")

if __name__ == "__main__":
    simulate_discovery()
