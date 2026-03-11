import httpx
import os
from dotenv import load_dotenv

load_dotenv()

base_url = os.environ.get("AE_BASE_URL", "https://t4.automationedge.com")
rest_path = os.environ.get("AE_REST_BASE_PATH", "/aeengine/rest")
org = os.environ.get("AE_ORG_CODE", "OMKAR_PATIL_3666")
username = os.environ.get("AE_USERNAME", "omkar.patil@valuedx.com")
password = os.environ.get("AE_PASSWORD", "Vdx@07")

print(f"Base: {base_url}, Rest: {rest_path}, Org: {org}")

client = httpx.Client(base_url=base_url, verify=False)

# 1. Authenticate
auth_url = f"{rest_path}/authenticate"
resp = client.post(auth_url, params={"username": username, "password": password})
print(f"Auth status: {resp.status_code}")
if resp.status_code != 200:
    print(f"Auth failed: {resp.text}")
    exit(1)

token = resp.json().get("token")
headers = {"X-session-token": token, "Accept": "application/json"}

# 2. Try schedule list
url = f"{rest_path}/tenants/{org}/workflows/schedules"
print(f"Trying url: {url}")

# Try with workflowId in query (as it is now)
params = {"offset": 0, "size": 10, "workflowId": "7801"}
resp = client.post(url, headers=headers, params=params)
print(f"POST with workflowId in params: {resp.status_code}")

# Try without any workflowId
params_no_wf = {"offset": 0, "size": 10}
resp = client.post(url, headers=headers, params=params_no_wf)
print(f"POST without workflowId: {resp.status_code}")
if resp.status_code == 200:
    print(f"Response: {resp.json().get('metadata')}")

# Try with workflowId in body
print("Trying with workflowId in body...")
resp = client.post(url, headers=headers, params=params_no_wf, json={"workflowId": 7801})
print(f"POST with workflowId in body: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json().get("data", [])
    print(f"Success! Found {len(data)} schedules")
    if data:
        print(f"First schedule: {data[0].get('scheduleName')}")

# Try alternate path
alt_url = f"{rest_path}/{org}/schedules"
print(f"Trying alt URL: {alt_url}")
resp = client.get(alt_url, headers=headers)
print(f"GET {alt_url} status: {resp.status_code}")
