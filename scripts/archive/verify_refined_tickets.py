
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
        if "create_hdfc_ticket" in prompt:
            if "Incident" in prompt:
                return "- Raise an Incident ticket using create_hdfc_ticket.\n- Check the workflow logs."
            else:
                return "- Create a Request ticket via create_hdfc_ticket.\n- Review the process documentation."
        elif "create_incident_ticket" in prompt:
            return "- Raise a support incident using create_incident_ticket.\n- Contact technical support."
        return "- Suggestion 1\n- Suggestion 2"

import config.llm_client
config.llm_client.llm_client = MockLLM()

from agents.orchestrator import Orchestrator

def test_refined_suggestions():
    orch = Orchestrator()
    
    print("Scenario 1: HDFC Workflow Failure")
    captured_prompts.clear()
    fail_data_hdfc = {
        "status": "Failed",
        "workflow_name": "WF_HDFC_Demat_Process",
        "message": "API Timeout occurred."
    }
    resp_fail_hdfc = orch._format_completion_message("trigger_workflow", fail_data_hdfc)
    print("\n--- RESPONSE (HDFC FAILURE) ---")
    print(resp_fail_hdfc)
    if "create_hdfc_ticket" in captured_prompts[0] and "Incident" in captured_prompts[0]:
        print("SUCCESS: Prompt correctly identified HDFC context and Incident bias.")
    else:
        print("FAILURE: Prompt did not identify HDFC/Incident context.")

    print("\nScenario 2: Generic Workflow Failure")
    captured_prompts.clear()
    fail_data_gen = {
        "status": "Error",
        "workflow_name": "WF_Generic_Process",
        "message": "Resource not found."
    }
    _ = orch._format_completion_message("trigger_workflow", fail_data_gen)
    if "create_incident_ticket" in captured_prompts[0]:
        print("SUCCESS: Prompt correctly identified Generic context.")
    else:
        print("FAILURE: Prompt did not identify Generic context.")

    print("\nScenario 3: HDFC Workflow Success (Request bias)")
    captured_prompts.clear()
    success_data_hdfc = {
        "status": "Complete",
        "workflow_name": "WF_HDFC_Onboarding",
        "message": "Onboarding complete."
    }
    _ = orch._format_completion_message("trigger_workflow", success_data_hdfc)
    if "create_hdfc_ticket" in captured_prompts[0] and "Request" in captured_prompts[0]:
        print("SUCCESS: Prompt correctly biased toward Request for success follow-up.")
    else:
        print("FAILURE: Prompt did not bias toward Request for success.")

if __name__ == "__main__":
    test_refined_suggestions()
