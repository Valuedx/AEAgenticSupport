import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def get_wf_details():
    client = get_automationedge_client()
    wf_id = "6975"
    print(f"Fetching details for Workflow {wf_id}...")
    try:
        # Standard catalogue-style detail path
        res = client._authorized_request(
            "GET", f"/workflows/{wf_id}/config",
            use_rest_prefix=True
        )
        print("Detail Data:")
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    get_wf_details()
