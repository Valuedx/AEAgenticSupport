import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.ae_dynamic_tools import extract_dynamic_tool_mapping
from tools.automationedge_client import get_automationedge_client

def test_dynamic_tool_avoid_when():
    # Mock some workflow metadata
    wf_summary = {
        "workflowId": 6526,
        "workflowName": "Email_Bot_JD",
        "description": "Email Bot for JD processing"
    }
    
    # Minimal config to trigger tool generation
    wf_details = {
        "agenticToolConfiguration": {
            "toolName": "Email_Bot_JD",
            "active": True,
            "description": "Process JDs via Email"
        },
        "configurationParameters": []
    }
    
    mapping = extract_dynamic_tool_mapping(wf_summary, wf_details)
    if mapping:
        tool_def = mapping.to_tool_definition()
        print(f"Tool Name: {tool_def.name}")
        print(f"Avoid When: {tool_def.avoid_when}")
        
        # Verify it contains our special phrase
        expected = "the user is only asking for the status, history, or last run of this bot"
        if expected in tool_def.avoid_when:
            print("SUCCESS: Avoid When contains the status check steering hint.")
        else:
            print("FAILURE: Avoid When MISSING the steering hint.")
    else:
        print("FAILURE: Mapping could not be created.")

if __name__ == "__main__":
    test_dynamic_tool_avoid_when()
