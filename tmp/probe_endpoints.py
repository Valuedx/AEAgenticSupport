import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def probe_endpoints():
    client = get_automationedge_client()
    wf_id = "6975" # Death_Claim
    wf_name = "Death_Claim"
    org = "OMKAR_PATIL_3666"
    
    paths_to_test = [
        # Standard instances
        (f"/workflows/{wf_id}/instances", True),
        (f"/workflows/{wf_name}/instances", True),
        (f"/{org}/workflows/{wf_id}/instances", True),
        
        # Standard executions
        (f"/workflows/{wf_id}/executions", True),
        (f"/workflows/{wf_name}/executions", True),
        (f"/{org}/workflows/{wf_id}/executions", True),
        
        # Non-prefix variants
        (f"/api/v1/workflows/{wf_id}/executions", False),
        (f"/api/v2/workflows/{wf_id}/instances", False),
        
        # Global with filter in params
        ("/workflowinstances", True),
    ]
    
    print(f"Probing endpoints for Workflow {wf_name} ({wf_id})...")
    
    for path, use_prefix in paths_to_test:
        print(f"\nTesting: {path} (prefix={use_prefix})")
        try:
            params = {}
            if path == "/workflowinstances":
                params = {"workflowId": wf_id, "size": 1}
                
            res = client._authorized_request(
                "GET", path, 
                params=params,
                use_rest_prefix=use_prefix,
                silent_on_status=[]
            )
            print(f"SUCCESS! Path worked.")
            print(f"Result type: {type(res)}")
            if isinstance(res, list):
                print(f"Items found: {len(res)}")
            else:
                print(f"Keys: {list(res.keys()) if isinstance(res, dict) else 'N/A'}")
        except Exception as e:
            print(f"FAILED: {e}")

if __name__ == "__main__":
    probe_endpoints()
