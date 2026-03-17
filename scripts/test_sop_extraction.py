
import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor

# Force UTF-8 for stdout
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PROJECT_ROOT = "D:\\AEAgenticSupport"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState

DSN = "postgresql://postgres:root@localhost:5432/ops_agent"

def debug_sop_extraction():
    orch = Orchestrator()
    workflow_name = "WF_add_employee"
    required_params = ["emp_id", "name", "designation", "department"]
    
    print(f"--- Debugging SOP Extraction for {workflow_name} ---")
    
    # We suspect a RAG hit for 'employee' might be pulling a Leave SOP
    # that mentions 'Start Date'
    mock_sop_content = """
    To add an employee, ensure you have the employee ID and department.
    Note: For leave requests, the Start Date is mandatory.
    Any other relevant information should be noted in the comments.
    """
    
    sop_hits = [
        {"id": "sop-mock", "content": mock_sop_content, "score": 0.8}
    ]
    
    print("\nMock SOP Content:")
    print(mock_sop_content)
    
    hints = orch._extract_sop_param_hints(
        workflow_name=workflow_name,
        required_params=required_params,
        sop_hits=sop_hits
    )
    
    print("\nExtracted Hints:")
    for h in hints:
        print(f"  - {h}")

    if any("Start Date" in h for h in hints):
        print("\n[!] FOUND IT: The SOP extraction logic is grabbing lines from unrelated sections of the SOP.")

if __name__ == "__main__":
    debug_sop_extraction()
