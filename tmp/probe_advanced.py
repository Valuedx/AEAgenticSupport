import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def probe_advanced_filter():
    client = get_automationedge_client()
    wf_id = 6975
    wf_name = "Death_Claim"
    
    print(f"Probing POST /workflowinstances with ADVANCED filters for {wf_name}...")
    
    # Try different advanced filter syntaxes
    payloads = [
        # Syntax 1: filters list
        {"filters": [{"field": "workflowName", "operator": "EQ", "value": wf_name}]},
        {"filters": [{"field": "workflowId", "operator": "EQ", "value": wf_id}]},
        
        # Syntax 2: criteria
        {"criteria": {"workflowName": wf_name}},
        
        # Syntax 3: simple but maybe case sensitive?
        {"workflowName": wf_name},
    ]
    
    for payload in payloads:
        print(f"\nTesting with payload: {payload}")
        try:
            res = client._authorized_request(
                "POST", "/workflowinstances", 
                params={"size": 1},
                payload=payload,
                use_rest_prefix=True,
                silent_on_status=[]
            )
            items = client._extract_list(res)
            print(f"Items: {len(items)}")
            if items:
                print(f"Found WF Name: {items[0].get('workflowName')}")
                if items[0].get("workflowName") == wf_name:
                    print("!!! FILTER WORKED !!!")
        except Exception as e:
            print(f"FAILED: {e}")

if __name__ == "__main__":
    probe_advanced_filter()
