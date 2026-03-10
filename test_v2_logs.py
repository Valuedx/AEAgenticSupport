
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

AE_URL = "https://t4.automationedge.com"
ID = "2501865"

def test_v2():
    username = os.getenv("T4_USERNAME")
    password = os.getenv("T4_PASSWORD")
    
    with httpx.Client(base_url=AE_URL, verify=False) as client:
        # Auth
        auth_resp = client.post("/aeengine/rest/authenticate", params={"username": username, "password": password})
        token = auth_resp.json().get("sessionToken")
        headers = {"X-session-token": token, "Content-Type": "application/json"}
        
        for p in [
            f"/aeengine/rest/workflowinstances/logs/v2/{ID}",
            f"/aeengine/rest/workflowinstances/{ID}/executionResults",
            f"/aeengine/rest/workflowinstances/{ID}/history",
            f"/aeengine/rest/workflowinstances/{ID}/log",
        ]:
            r = client.get(p, headers=headers)
            print(f"GET {p}: {r.status_code}")
            if r.status_code == 200:
                print(f"SUCCESS with {p}!")

if __name__ == "__main__":
    test_v2()
