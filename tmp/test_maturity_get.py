import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def test_maturity_get():
    client = get_automationedge_client()
    wf_id = "6976" # Maturity_Claim
    path = f"/workflows/{wf_id}/instances"
    
    print(f"Testing GET {path} (prefix=True) for Maturity_Claim...")
    try:
        res = client._authorized_request(
            "GET", path, 
            use_rest_prefix=True,
            silent_on_status=[]
        )
        print("SUCCESS!")
        print(f"Count: {len(res) if isinstance(res, list) else 'N/A'}")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    test_maturity_get()
