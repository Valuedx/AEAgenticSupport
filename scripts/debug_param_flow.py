
import os
import sys
import json
import io

# Force UTF-8 for stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState
from tools.automationedge_client import get_automationedge_client
from tools.registry import tool_registry

def debug_param_flow():
    orch = Orchestrator()
    state = ConversationState()
    state.conversation_id = "test_param_flow"
    
    msg = "Add employee"
    print(f"--- Debugging Parameter Flow for: '{msg}' ---\n")
    
    # 1. Check Tool Discovery
    client = get_automationedge_client()
    print("Step 1: Discovering Tools...")
    # This mimics what discover_tools does
    tools = tool_registry.list_tools()
    wf_matches = [t for t in tools if "WF_add_employee" in t]
    print(f"Found matches: {wf_matches}")
    
    for wf in wf_matches[:1]:
        clean_wf = wf.replace("tool-", "")
        print(f"\nAnalyzing Workflow: {clean_wf}")
        params = client.get_cached_workflow_parameters(clean_wf)
        print(f"Direct Client Parameters: {[p['name'] for p in params]}")
        
    # 2. Run Preflight
    print("\nStep 2: Running Preflight...")
    response = orch._preflight_workflow_param_collection(msg, state)
    
    print("\n--- Orchestrator Response (Preflight) ---")
    print(response)
    
    print("\n--- state.param_collection ---")
    print(json.dumps(state.param_collection, indent=2))
    
    # 3. Check SOP Hints directly if preflight found something
    if state.param_collection:
        wf_name = state.param_collection.get("workflow_name")
        req_params = state.param_collection.get("required_params", [])
        print(f"\nStep 3: Checking SOP hints for {wf_name}...")
        from rag.engine import get_rag_engine
        rag = get_rag_engine()
        sop_hits = rag.search_sops(f"{wf_name} employee", top_k=5)
        hints = orch._extract_sop_param_hints(
            workflow_name=wf_name,
            required_params=req_params,
            sop_hits=sop_hits
        )
        print("SOP Hints extracted:")
        for h in hints:
            print(f"  - {h}")

if __name__ == "__main__":
    debug_param_flow()
