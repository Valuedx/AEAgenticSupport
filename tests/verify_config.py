import requests
import json

try:
    r = requests.get("http://localhost:5050/api/ui-config/chat")
    print(f"Status: {r.status_code}")
    print(f"Data: {json.dumps(r.json(), indent=2)}")
except Exception as e:
    print(f"Error checking config: {e}")

try:
    r = requests.post("http://localhost:5050/chat", json={
        "message": "test health check",
        "session_id": "test-session",
        "user_id": "test-user",
        "user_role": "technical"
    })
    print(f"Chat Status: {r.status_code}")
    print(f"Chat Response: {json.dumps(r.json(), indent=2)}")
except Exception as e:
    print(f"Chat Error: {e}")
