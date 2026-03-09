from rag.engine import get_rag_engine
from config.logging_setup import setup_logging
import json

setup_logging()
rag = get_rag_engine()

query = "can you check status of request id 2501865"
print(f"Query: {query}\n")

print("--- TOOL HITS ---")
tool_hits = rag.search_tools(query, top_k=5)
if not tool_hits:
    print("No tool hits found.")
else:
    for h in tool_hits:
        meta = h.get('metadata', {})
        score = h.get('score', 0.0) or 0.0
        print(f"Score: {score:.4f} | Tool: {meta.get('tool_name')} | Desc: {h.get('content')[:100]}...")

print("\n--- SOP HITS ---")
sop_hits = rag.search_sops(query, top_k=3)
if not sop_hits:
    print("No SOP hits found.")
else:
    for h in sop_hits:
        score = h.get('score', 0.0) or 0.0
        print(f"Score: {score:.4f} | Content: {h.get('content')[:100]}...")
