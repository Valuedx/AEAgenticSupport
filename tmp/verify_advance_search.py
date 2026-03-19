import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def verify_advance_search():
    client = get_automationedge_client()
    bots_to_test = ["Death_Claim", "License_bot", "Maturity_Claim"]
    
    for wf_name in bots_to_test:
        print(f"\n--- Verifying advanceSearch for {wf_name} ---")
        try:
            instances = client.get_workflow_instances(workflow_name=wf_name, limit=5)
            print(f"SUCCESS! Found {len(instances)} instances.")
            for inst in instances:
                print(f"ID: {inst.get('id')} | Name: {inst.get('workflowName')} | Status: {inst.get('status')} | Date: {inst.get('startTime')}")
        except Exception as e:
            print(f"FAILED for {wf_name}: {e}")

if __name__ == "__main__":
    verify_advance_search()
