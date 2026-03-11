import sys
import os

# Ensure we can import from the current directory
sys.path.append(os.getcwd())

from mcp_server.ae_client import get_ae_client
import httpx

client = get_ae_client()
url = f"/tenants/{client.org}/workflows/schedules"
params = {"offset": 0, "size": 10, "workflowId": "7801"}

# Try WITHOUT body (None)
try:
    print(f"Testing POST to {url} with json=None")
    # Using private _request to simulate exactly what happens
    resp = client._request("POST", url, params=params, json_body=None)
    print(f"Success with json=None")
except Exception as exc:
    print(f"Failed with json=None: {exc}")

# Try WITH empty body ({})
try:
    print(f"Testing POST to {url} with json={{}}")
    resp = client._request("POST", url, params=params, json_body={})
    print(f"Success with json={{}}")
except Exception as exc:
    print(f"Failed with json={{}}: {exc}")
