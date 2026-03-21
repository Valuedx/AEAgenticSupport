
import sys
import os
import json
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.getcwd())

# Mock AE client
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

# Mock llm_client
class MockLLM:
    def chat(self, prompt, **kwargs):
        print(f"LLM PROMPT RECEIVED:\n{prompt}\n")
        return "NATURAL_PROMPT_FROM_LLM"

import agents.orchestrator
agents.orchestrator.llm_client = MockLLM()

from agents.orchestrator import Orchestrator

def test_fix():
    orch = Orchestrator()
    workflow_name = "WF_apply_leave"
    remaining = ["leave_type", "leave_start", "leave_end", "emp_id"]
    
    print(f"Testing fix for remaining params: {remaining}")
    
    # Simulate the logic I just added to _handle_parameter_gathering
    pretty_items = []
    schema_list = MockAEClient().get_cached_workflow_parameters(workflow_name)
    schema_map = {p.get("name"): p for p in schema_list if isinstance(p, dict) and p.get("name")}
    
    for name in remaining:
        p_schema = schema_map.get(name, {})
        p_name = orch._prettify_param_name(name)
        desc = p_schema.get("description") or p_schema.get("displayName") or ""
        clean_desc = orch._clean_param_description(desc, name)
        pretty_items.append((p_name, clean_desc))
    
    print(f"Results in pretty_items: {pretty_items}")
    found_names = [x[0] for x in pretty_items] # Prettify doesn't change these much in this case
    
    # We need to check if 'leave_type' is in the list (prettified version)
    expected_prettified = orch._prettify_param_name("leave_type")
    if expected_prettified in found_names:
        print(f"SUCCESS: '{expected_prettified}' is present in pretty_items!")
    else:
        print(f"FAILURE: '{expected_prettified}' is still missing!")

    # Test the LLM-based prompting
    msg = orch._build_param_request_message(
        workflow_name=workflow_name,
        items=pretty_items,
        intro="Got it."
    )
    print(f"FINAL MESSAGE OUTPUT: {msg}")

if __name__ == "__main__":
    test_fix()
