
import os
import sys
from rag.engine import get_rag_engine

def check_maturity_claim():
    rag = get_rag_engine()
    # Search for the tool specifically
    results = rag.list_collection("tools")
    main_hit = None
    for hit in results:
        meta = hit.get("metadata", {})
        if meta.get("tool_name") == "Maturity_Claim" or meta.get("workflow_name") == "Maturity_Claim":
            main_hit = hit
            break
    
    if main_hit:
        print("MATCH FOUND:")
        print(main_hit.get("metadata"))
    else:
        print("Maturity_Claim NOT FOUND in collection 'tools'")

if __name__ == "__main__":
    check_maturity_claim()
