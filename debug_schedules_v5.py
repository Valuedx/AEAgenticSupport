import sys
import os

# Ensure we can import from the current directory
sys.path.append(os.getcwd())

from mcp_server.ae_client import get_ae_client
import httpx

client = get_ae_client()
url = f"/tenants/{client.org}/workflows/schedules"

# 1. TEST: POST with workflowId in query params (what we think fails)
params_only = {"offset": 0, "size": 10, "workflowId": "7801"}
try:
    print(f"--- Test 1: POST to {url} with workflowId in query params ---")
    resp = client._request("POST", url, params=params_only, json_body={})
    print(f"Success with query params! Total: {resp.get('metadata', {}).get('totalRecords')}")
except Exception as exc:
    print(f"FAILED with query params: {exc}")

# 2. TEST: POST with workflowId in body (what we think works)
params_base = {"offset": 0, "size": 10}
body = {"workflowId": 7801}
try:
    print(f"\n--- Test 2: POST to {url} with workflowId in json body ---")
    resp = client._request("POST", url, params=params_base, json_body=body)
    data = resp.get("data", [])
    print(f"SUCCESS with json body! Found {len(data)} schedules.")
except Exception as exc:
    print(f"FAILED with json body: {exc}")

# 3. TEST: POST with no filters at all
try:
    print(f"\n--- Test 3: POST to {url} with NO filters ---")
    resp = client._request("POST", url, params=params_base, json_body={})
    print(f"SUCCESS with no filters! Total: {resp.get('metadata', {}).get('totalRecords')}")
except Exception as exc:
    print(f"FAILED with no filters: {exc}")
