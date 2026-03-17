
import sys
import os
import json
from unittest.mock import MagicMock

# Force UTF-8 for stdout
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Mock llm_client
class MockLLM:
    def chat(self, prompt, **kwargs):
        # Simulate a long response that would be cut off by 80 tokens 
        # but fit in 256.
        return "- Investigate the root cause of the salary discrepancy by checking the payroll database directly for VDX432.\n- Download the detailed payslip PDF from the ERP system to verify components."

import config.llm_client
config.llm_client.llm_client = MockLLM()

from agents.orchestrator import Orchestrator

def test_completion_message():
    orch = Orchestrator()
    data = {
        "message": "Payslip for VDX432 | Net Salary: ₹NaN",
        "execution_id": "2530101",
        "status": "Complete",
        "workflow_name": "WF_generate_payslip"
    }
    
    resp = orch._format_completion_message("WF_generate_payslip", data)
    print("--- ASSISTANT RESPONSE ---")
    print(resp)
    print("--------------------------")
    
    if "Investigate the root cause" in resp and "Download the detailed" in resp:
        print("SUCCESS: Suggestions are fully included and not truncated!")
    else:
        print("FAILURE: Suggestions are missing or truncated!")

if __name__ == "__main__":
    test_completion_message()
