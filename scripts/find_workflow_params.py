
import os
import sys
import json

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def find_workflow():
    engine = get_rag_engine()
    # Search in 'tools' collection
    tools = engine.list_collection("tools")
    
    target = "WF_add_employee"
    found = []
    
    for tool in tools:
        name = tool.get("metadata", {}).get("tool_name") or tool.get("metadata", {}).get("workflow_name")
        if name == target or target in tool.get("content", ""):
            found.append(tool)
            
    if not found:
        print(f"Workflow {target} not found in 'tools' collection.")
        # Try a broader search
        all_collections = ["tools", "kb_articles", "sops"]
        for coll in all_collections:
            hits = engine.search(target, collection=coll, top_k=5)
            for hit in hits:
                print(f"Match in {coll}: ID={hit['id']}, Metadata={hit['metadata']}")
    else:
        for f in found:
            print(f"Found match: ID={f['id']}")
            print(f"Content: {f['content']}")
            print(f"Metadata: {json.dumps(f['metadata'], indent=2)}")

if __name__ == "__main__":
    find_workflow()
