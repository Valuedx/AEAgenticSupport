import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def list_all_workflows():
    client = get_automationedge_client()
    try:
        # client.get() doesn't take use_rest_prefix, client.request() does
        resp = client.request("GET", "/workflows/catalogue", use_rest_prefix=True)
        workflows = []
        if isinstance(resp, list):
            workflows = resp
        elif isinstance(resp, dict):
            workflows = resp.get("workflows") or resp.get("data") or []
            
        print(f"Total workflows found: {len(workflows)}")
        for wf in workflows:
            wf_id = wf.get("id") or wf.get("workflowId")
            wf_name = wf.get("name") or wf.get("workflowName")
            print(f"ID: {wf_id} | Name: {wf_name}")
            
    except Exception as e:
        print(f"Error fetching: {e}")

if __name__ == "__main__":
    list_all_workflows()
