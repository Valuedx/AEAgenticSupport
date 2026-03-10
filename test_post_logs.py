
import os
import httpx
from dotenv import load_dotenv
import json

load_dotenv()

AE_URL = "https://t4.automationedge.com"
ID = "2501865"

def test_post_logs():
    username = os.getenv("T4_USERNAME")
    password = os.getenv("T4_PASSWORD")
    
    with httpx.Client(base_url=AE_URL, verify=False) as client:
        # Auth
        auth_resp = client.post("/aeengine/rest/authenticate", params={"username": username, "password": password})
        token = auth_resp.json().get("sessionToken")
        headers = {"X-session-token": token, "Content-Type": "application/json"}
        
        bodies = [
            {"workflowInstanceId": int(ID)},
            {"executionId": int(ID)},
            {"id": int(ID)},
        ]
        
        paths = [
            "/aeengine/rest/workflowlogs",
            "/aeengine/rest/workflowinstances/logs",
            "/aeengine/rest/executions/logs",
        ]
        
        for path in paths:
            for body in bodies:
                r = client.post(path, json=body, headers=headers)
                print(f"POST {path} with {body}: {r.status_code}")
                if r.status_code == 200:
                    print(f"SUCCESS with {path}!")
                    # return

if __name__ == "__main__":
    test_post_logs()
