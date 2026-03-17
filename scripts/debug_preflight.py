
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

def debug_preflight():
    orch = Orchestrator()
    state = ConversationState()
    state.conversation_id = "debug_conv"
    
    msg = "Add employee"
    print(f"--- Debugging preflight for message: '{msg}' ---")
    
    # We want to see what _preflight_workflow_param_collection does
    # Since it returns a string (the prompt), we can just call it
    response = orch._preflight_workflow_param_collection(msg, state)
    
    print("\n--- Orchestrator Response ---")
    print(response)
    
    print("\n--- Captured state.param_collection ---")
    print(json.dumps(state.param_collection, indent=2))

if __name__ == "__main__":
    debug_preflight()
