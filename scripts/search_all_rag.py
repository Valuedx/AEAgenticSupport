
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def search_everywhere():
    rag = get_rag_engine()
    queries = ["Start Date", "Any other relevant information"]
    
    for q in queries:
        print(f"\n=== Searching for '{q}' ===")
        
        print("--- Knowledge Base ---")
        kb_hits = rag.search_kb(q, top_k=5)
        for h in kb_hits:
            print(f"ID: {h['id']} | Content: {h['content'][:200]}...")
            
        print("--- SOPs ---")
        sop_hits = rag.search_sops(q, top_k=5)
        for h in sop_hits:
            print(f"ID: {h['id']} | Content: {h['content'][:200]}...")
            
        print("--- Tools ---")
        tool_hits = rag.search_tools(q, top_k=5)
        for h in tool_hits:
            print(f"ID: {h['id']} | Metadata: {json.dumps(h['metadata'].get('required_params'))}")

if __name__ == "__main__":
    search_everywhere()
