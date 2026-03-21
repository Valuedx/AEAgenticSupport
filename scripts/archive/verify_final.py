
import sys
import os
import json
from unittest.mock import MagicMock

# Force UTF-8 for stdout to handle emojis
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add project root to path
sys.path.insert(0, os.getcwd())

# 1. Mock infrastructure
class MockAEClient:
    def get_cached_workflow_parameters(self, name):
        # Database schema is missing 'leave_type'
        return [
            {"name": "leave_start", "displayName": "Start Date"},
            {"name": "leave_end", "displayName": "End Date"},
            {"name": "emp_id", "displayName": "Employee ID"}
        ]

import tools.automationedge_client
tools.automationedge_client.get_ae_client = lambda: MockAEClient()

class MockResult:
    def __init__(self, data, success=True):
        self.data = data
        self.success = success

class MockRegistry:
    def execute(self, tool, **kwargs):
        if tool == "discover_tools":
            return MockResult({
                "tools": [{
                    "id": "tool-WF_apply_leave",
                    "name": "WF_apply_leave",
                    "score": 1.0,
                    "metadata": {
                        "workflow_name": "WF_apply_leave",
                        "required_params": ["leave_type", "leave_start", "leave_end", "emp_id"],
                        "parameters": {
                            "leave_type": {"optional": False},
                            "leave_start": {"optional": False},
                            "leave_end": {"optional": False},
                            "emp_id": {"optional": False}
                        }
                    }
                }]
            })
        return MockResult({})

import agents.orchestrator
agents.orchestrator.tool_registry = MockRegistry()

# We use the REAL Orchestrator and REAL llm_client (which is already configured in the environment)
# to see the actual effect of our prompt hardening.
from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState

def run_e2e_simulation():
    orch = Orchestrator()
    
    # --- TURN: User says "i want to apply leave" ---
    state = ConversationState()
    state.conversation_id = "test_conv_final"
    user_msg = "i want to apply leave"
    print(f"User: {user_msg}")
    
    resp = orch._preflight_workflow_param_collection(user_msg, state)
    print(f"Assistant Response:\n{resp}\n")
    
    if resp and "leave type" in resp.lower():
        print("CONFIRMED: Logic successfully identified and prompted for 'leave type'!")
    else:
        print("FAILURE: 'leave type' was filtered out or incorrectly marked as collected!")

if __name__ == "__main__":
    run_e2e_simulation()
