import sys
import os

# Ensure we can import from the current directory
sys.path.append(os.getcwd())

from mcp_server.ae_client import get_ae_client
import json

client = get_ae_client()
print(f"Client org: {client.org}")

# 1. Try Path 0 with POST and workflowId in params
url = f"/tenants/{client.org}/workflows/schedules"
params = {"offset": 0, "size": 10, "workflowId": "7801"}

try:
    print(f"Testing POST to {url} with params {params}")
    resp = client.post(url, params=params)
    print(f"Status: 200 (Success)")
    print(f"Metadata: {resp.get('metadata')}")
except Exception as exc:
    print(f"Failed with params in query: {exc}")

# 2. Try Path 0 with POST and workflowId in body
try:
    print(f"Testing POST to {url} with workflowId in body")
    resp = client.post(url, json_body={"workflowId": 7801})
    print(f"Status: 200 (Success)")
    data = resp.get("data", [])
    print(f"Found {len(data)} schedules")
except Exception as exc:
    print(f"Failed with workflowId in body: {exc}")

# 3. Try Path 0 with POST and order=desc ONLY (no filter)
try:
    print(f"Testing POST to {url} with no filters")
    resp = client.post(url, params={"offset": 0, "size": 10})
    print(f"Status: 200 (Success)")
    print(f"Metadata: {resp.get('metadata')}")
except Exception as exc:
    print(f"Failed with no filters: {exc}")
