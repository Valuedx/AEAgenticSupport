
import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.getcwd())

from agents.orchestrator import Orchestrator

def test_extraction_hallucination():
    orch = Orchestrator()
    user_msg = "i want to apply leave"
    param_names = ["leave_type", "leave_start", "leave_end", "emp_id"]
    
    print(f"Testing extraction with message: '{user_msg}'")
    print(f"Parameters requested: {param_names}")
    
    extracted = orch._extract_params_from_user_message(user_msg, param_names)
    print(f"Extracted result: {json.dumps(extracted, indent=2)}")
    
    if extracted.get("leave_type") == "leave":
        print("CONFIRMED: LLM is incorrectly extracting 'leave' as the value for 'leave_type'!")
    elif extracted.get("leave_type"):
        print(f"LLM extracted '{extracted.get('leave_type')}' for 'leave_type'.")
    else:
        print("LLM correctly did not extract anything for 'leave_type'.")

if __name__ == "__main__":
    test_extraction_hallucination()
