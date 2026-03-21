
import sys
import os
import json
from unittest.mock import MagicMock, patch

# Force UTF-8 for stdout
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Mock llm_client
captured_prompts = []
class MockLLM:
    def chat(self, prompt, **kwargs):
        captured_prompts.append(prompt)
        if "CRITICAL" in prompt:
            # Failure prompt
            return "- Check the error logs for more details.\n- Raise a support ticket for further investigation."
        else:
            # Success prompt
            return "- View the results in the portal.\n- Start another request."

import config.llm_client
config.llm_client.llm_client = MockLLM()

from agents.orchestrator import Orchestrator

def test_suggestions():
    orch = Orchestrator()
    
    print("Scenario 1: Successful Workflow")
    success_data = {
        "status": "Complete",
        "workflow_name": "WF_test",
        "message": "The process finished successfully."
    }
    resp_success = orch._format_completion_message("trigger_workflow", success_data)
    print("\n--- RESPONSE (SUCCESS) ---")
    print(resp_success)
    print("--------------------------")
    
    print("\nScenario 2: Failed Workflow")
    captured_prompts.clear()
    fail_data = {
        "status": "Failed",
        "workflow_name": "WF_test",
        "message": "Automation terminated due to a missing file."
    }
    resp_fail = orch._format_completion_message("trigger_workflow", fail_data)
    print("\n--- RESPONSE (FAILURE) ---")
    print(resp_fail)
    print("--------------------------")
    
    # Check if the instruction was present in the fail prompt
    if captured_prompts and "CRITICAL" in captured_prompts[0]:
        print("\nSUCCESS: LLM prompt for failure contained the critical ticket instruction.")
    else:
        print("\nFAILURE: LLM prompt for failure did NOT contain the instruction.")

    if "ticket" in resp_fail.lower() or "support" in resp_fail.lower():
        print("SUCCESS: Response contains ticket/support suggestion.")
    else:
        print("FAILURE: Response missing ticket/support suggestion.")

if __name__ == "__main__":
    test_suggestions()
