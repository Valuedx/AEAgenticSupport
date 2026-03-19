import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def check_methods():
    client = get_automationedge_client()
    for method in ["GET", "POST"]:
        for size in [50, 100]:
            print(f"--- Testing {method} /aeengine/rest/workflowinstances with size={size} ---")
            params = {"offset": 0, "size": size, "order": "desc"}
            try:
                resp = client.request(method, "/workflowinstances", params=params, use_rest_prefix=True)
                items = []
                if isinstance(resp, list):
                    items = resp
                elif isinstance(resp, dict):
                    items = resp.get("data") or resp.get("instances") or resp.get("executions") or []
                
                print(f"Result for {method} (size={size}): Found {len(items)} items")
                if items:
                     names = set()
                     for it in items:
                          n = it.get("workflowName") or (it.get("workflowConfiguration") or {}).get("name") or "Unknown"
                          names.add(n)
                     print(f"Distinct Bots in list: {list(names)}")
            except Exception as e:
                print(f"Error {method} {size}: {e}")

if __name__ == "__main__":
    check_methods()
