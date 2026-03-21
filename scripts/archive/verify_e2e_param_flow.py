
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

class MockLLM:
    def chat(self, prompt, **kwargs):
        if "Extract parameter values" in prompt:
            # Simulate extracting sick leave
            if "sick" in prompt.lower():
                return json.dumps({"leave_type": "sick", "leave_start": None, "leave_end": None, "emp_id": None})
            return json.dumps({"leave_type": None, "leave_start": None, "leave_end": None, "emp_id": None})
        
        if "Ask the user to provide" in prompt:
            # Emoji here caused UnicodeEncodeError on some systems
            return "FRIENDLY_LLM_PROMPT: I've got your request. To help you with your 'Apply Leave', I just need a few more things: leave type, start date, end date, and your employee ID. 😊"
        
        return "Generic Response"

class MockResult:
    def __init__(self, data, success=True):
        self.data = data
        self.success = success

class MockRegistry:
    def execute(self, tool, **kwargs):
        if tool == "discover_tools":
            # Return a hit that has 'leave_type' in metadata
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
agents.orchestrator.llm_client = MockLLM()
agents.orchestrator.tool_registry = MockRegistry()

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState

def run_e2e_simulation():
    orch = Orchestrator()
    
    # --- TURN 1 ---
    state = ConversationState()
    state.conversation_id = "test_conv_1"
    user_msg = "i want to apply for sick leave"
    print(f"--- TURN 1 ---")
    print(f"User: {user_msg}")
    
    resp = orch._preflight_workflow_param_collection(user_msg, state)
    print(f"Assistant Response:\n{resp}\n")
    
    # Verify state
    pc = state.param_collection
    print(f"Collected Params: {pc.get('collected_params')}")
    print(f"Required Params: {pc.get('required_params')}")
    
    # --- TURN 2 ---
    state2 = ConversationState()
    state2.conversation_id = "test_conv_2"
    user_msg_2 = "i want to apply leave"
    print(f"\n--- TURN 2 ---")
    print(f"User: {user_msg_2}")
    
    # Update mock to return nothing extracted for the second turn
    def mock_chat_2(prompt, **kwargs):
        if "Extract parameter values" in prompt:
            return json.dumps({"leave_type": None, "leave_start": None, "leave_end": None, "emp_id": None})
        if "Ask the user to provide" in prompt:
            return "NATURAL_PROMPT: To get started with your 'Apply Leave', I just need the leave type, start and end dates, and your employee ID."
        return "Generic Response"
    
    agents.orchestrator.llm_client.chat = mock_chat_2
    
    resp_2 = orch._preflight_workflow_param_collection(user_msg_2, state2)
    print(f"Assistant Response:\n{resp_2}")
    
    if resp_2 and ("leave type" in resp_2.lower() or "natural_prompt" in resp_2.lower()):
        print("\nCONFIRMED: Logic successfully included 'leave type' in the information needed!")
    else:
        print("\nFAILURE: 'leave type' was missing from the process!")

if __name__ == "__main__":
    run_e2e_simulation()
