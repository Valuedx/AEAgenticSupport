import json
import os
import sys
from datetime import datetime, timezone

# Add project root to path
sys.path.append(os.getcwd())

from tools.status_tools import check_workflow_status

def test_natural_language_matching():
    # Test cases for natural language matching
    test_cases = [
        "email bot jd",
        "maturity claim",
        "license bot",
        "email bot"
    ]
    
    for query in test_cases:
        print(f"\n--- Testing query: '{query}' ---")
        try:
            result = check_workflow_status(workflow_name=query)
            print(f"Resolved Name: {result.get('workflow_name')}")
            print(f"Latest Status: {result.get('latest_status')}")
            print(f"Message: {result.get('message')}")
            recent = result.get("recent_executions", [])
            print(f"Recent Executions Count: {len(recent)}")
            if recent:
                print(f"Latest Execution ID: {recent[0].get('id')}")
        except Exception as e:
            print(f"Error checking status for '{query}': {e}")

if __name__ == "__main__":
    test_natural_language_matching()
