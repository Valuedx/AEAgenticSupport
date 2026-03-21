
import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.getcwd())

# Mock metrics to avoid DB errors
import config.metrics
config.metrics.metrics_collector = type('MockMetrics', (), {'start_turn': lambda *a, **k: None})()

from agents.orchestrator import Orchestrator

def test_extraction():
    orch = Orchestrator()
    user_msg = "i want to apply leave"
    param_names = ["leave_start", "leave_end", "leave_type", "emp_id"]
    
    print(f"Testing extraction for message: '{user_msg}'")
    print(f"Target params: {param_names}")
    
    extracted = orch._extract_params_from_user_message(user_msg, param_names)
    print(f"Extracted: {json.dumps(extracted, indent=2)}")

if __name__ == "__main__":
    test_extraction()
