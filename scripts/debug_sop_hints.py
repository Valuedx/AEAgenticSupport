
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.orchestrator import Orchestrator
from rag.engine import get_rag_engine

def debug_sop_hints():
    orch = Orchestrator()
    rag = get_rag_engine()
    
    workflow_name = "WF_add_employee"
    required_params = ["emp_id", "name", "designation", "department"]
    
    print(f"--- Debugging SOP hints for {workflow_name} ---")
    
    # Simulate RAG search for SOPs
    sop_hits = rag.search_sops("employee add", top_k=5)
    print(f"Found {len(sop_hits)} SOP hits.")
    
    hints = orch._extract_sop_param_hints(
        workflow_name=workflow_name,
        required_params=required_params,
        sop_hits=sop_hits
    )
    
    print("\n--- Extracted Hints ---")
    for hint in hints:
        print(f"- {hint}")

if __name__ == "__main__":
    debug_sop_hints()
