import sys
import os
import json
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.orchestrator_client import get_orchestrator_client

client = get_orchestrator_client()
print(f"Base URL: {client.base_url}")
print(f"Tenant ID: {client.tenant_id}")

url = client._url("/workflows")
headers = client._headers()
print(f"Requesting: {url} with headers {headers}")

try:
    with httpx.Client() as c:
        resp = c.get(url, headers=headers)
        print(f"Status Code: {resp.status_code}")
        print(f"Response Content: {resp.text[:500]}")
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
except Exception as e:
    print(f"Error: {e}")
