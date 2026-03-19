import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def probe_post_query():
    client = get_automationedge_client()
    wf_id = 6975
    wf_name = "Death_Claim"
    
    # Try POST /workflowinstances with filters in QUERY PARAMS
    print(f"Probing POST /workflowinstances with QUERY filters for {wf_name}...")
    
    tests = [
        {"workflowId": wf_id},
        {"workflowName": wf_name},
    ]
    
    for q_params in tests:
        q_params["size"] = 1
        print(f"\nTesting with query params: {q_params}")
        try:
            res = client._authorized_request(
                "POST", "/workflowinstances", 
                params=q_params,
                payload={},
                use_rest_prefix=True,
                silent_on_status=[]
            )
            items = client._extract_list(res)
            print(f"SUCCESS! Items: {len(items)}")
            if items:
                print(f"Found WF Name: {items[0].get('workflowName')}")
        except Exception as e:
            print(f"FAILED: {e}")

if __name__ == "__main__":
    probe_post_query()
