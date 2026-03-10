
import os
import httpx
from dotenv import load_dotenv
import json

load_dotenv()

AE_URL = "https://t4.automationedge.com"
LOG_ENTRY_ID = "1210" # From previous test

def test_download_log():
    username = os.getenv("T4_USERNAME")
    password = os.getenv("T4_PASSWORD")
    
    with httpx.Client(base_url=AE_URL, verify=False) as client:
        # Auth
        auth_resp = client.post("/aeengine/rest/authenticate", params={"username": username, "password": password})
        token = auth_resp.json().get("sessionToken")
        headers = {"X-session-token": token}
        
        # Test download endpoint
        # Pattern: /aeengine/rest/agent/debuglogs/download?id={id}
        url = f"/aeengine/rest/agent/debuglogs/download"
        params = {"id": LOG_ENTRY_ID}
        
        print(f"GET {url}?id={LOG_ENTRY_ID}")
        resp = client.get(url, params=params, headers=headers)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            print(f"Success! Content-Type: {resp.headers.get('Content-Type')}")
            # print(f"First 100 bytes of content: {resp.content[:100]}")
            # If it's a zip, we'd need to unzip it to see the text.
            with open("/tmp/debug_log.zip", "wb") as f:
                f.write(resp.content)
            print("Saved to /tmp/debug_log.zip")
        else:
            print(f"Download failed: {resp.text}")

if __name__ == "__main__":
    test_download_log()
