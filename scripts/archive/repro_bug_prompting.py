
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

from agents.orchestrator import Orchestrator

def test_prompting():
    orch = Orchestrator()
    workflow_name = "WF_apply_leave"
    # Orchestrator thinks these are required (e.g. from RAG)
    required = ["leave_type", "leave_start", "leave_end", "emp_id"]
    # User hasn't provided anything yet
    remaining = ["leave_type", "leave_start", "leave_end", "emp_id"]
    
    print(f"Testing prompting for remaining params: {remaining}")
    
    # Simulate _handle_parameter_gathering's loop
    pretty_items = []
    schema = MockAEClient().get_cached_workflow_parameters(workflow_name)
    for p in schema:
        name = p.get("name")
        if name in remaining:
            pretty_items.append((name, p.get("displayName", "")))
    
    print(f"Results in pretty_items: {pretty_items}")
    found_names = [x[0] for x in pretty_items]
    if "leave_type" not in found_names:
        print("BUG REPRODUCED: 'leave_type' is missing from pretty_items!")
    else:
        print("Bug not reproduced.")

if __name__ == "__main__":
    test_prompting()
