import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.status_tools import check_workflow_status

def test_tool():
    print("--- Testing check_workflow_status('License_bot') ---")
    result = check_workflow_status("License_bot")
    print(f"Bot Name: {result.get('bot_name')}")
    recent = result.get("recent_executions") or []
    print(f"Recent Executions Count (limit 50): {len(recent)}")
    if len(recent) > 0:
        for i, r in enumerate(recent[:5]):
            print(f"Run {i}: ID={r.get('id')}, Status={r.get('status')}, Time={r.get('time')}")
    print(f"Message: {result.get('message')}")

if __name__ == "__main__":
    test_tool()
