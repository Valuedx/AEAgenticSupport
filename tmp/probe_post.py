import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def probe_post_endpoints():
    client = get_automationedge_client()
    wf_id = 6975 # Death_Claim
    wf_name = "Death_Claim"
    
    # Try POST /workflowinstances with different filters
    filters = [
        {"workflowId": wf_id},
        {"workflowName": wf_name},
        {"workflow_name": wf_name},
        {"id": wf_id}
    ]
    
    print(f"Probing POST /workflowinstances for Workflow {wf_name} ({wf_id})...")
    
    for payload in filters:
        print(f"\nTesting POST with payload: {payload}")
        try:
            res = client._authorized_request(
                "POST", "/workflowinstances", 
                params={"size": 10},
                payload=payload,
                use_rest_prefix=True,
                silent_on_status=[]
            )
            items = client._extract_list(res)
            print(f"SUCCESS! Items found: {len(items)}")
            if items:
                print(f"First item WF Name: {items[0].get('workflowName')}")
        except Exception as e:
            print(f"FAILED: {e}")

if __name__ == "__main__":
    probe_post_endpoints()
