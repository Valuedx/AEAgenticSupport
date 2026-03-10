
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

AE_URL = "https://t4.automationedge.com"
ID = "2501865"

def test_exotic():
    username = os.getenv("T4_USERNAME")
    password = os.getenv("T4_PASSWORD")
    
    with httpx.Client(base_url=AE_URL, verify=False) as client:
        # Auth
        auth_resp = client.post("/aeengine/rest/authenticate", params={"username": username, "password": password})
        token = auth_resp.json().get("sessionToken")
        headers = {"X-session-token": token, "Content-Type": "application/json"}
        
        for sub in ["executionlogs", "workflowlogs", "executionLogs", "workflowLogs", "logDetails"]:
            path = f"/aeengine/rest/workflowinstances/{ID}/{sub}"
            r = client.get(path, headers=headers)
            print(f"GET {path}: {r.status_code}")
            if r.status_code == 200:
                print(f"SUCCESS with {sub}!")

if __name__ == "__main__":
    test_exotic()
