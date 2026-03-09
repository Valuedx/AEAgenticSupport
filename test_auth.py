import requests

URL = "http://localhost:3978/v3/directline/conversations"
SECRET = "secret.secret.secret"

TESTS = [
    {"name": "Bearer Protocol", "headers": {"Authorization": f"Bearer {SECRET}"}},
    {"name": "Plain Secret", "headers": {"Authorization": SECRET}},
    {"name": "BotConnector Protocol", "headers": {"Authorization": f"BotConnector {SECRET}"}},
    {"name": "X-Admin-Token", "headers": {"X-Admin-Token": SECRET}},
]

for test in TESTS:
    print(f"Testing {test['name']}...")
    try:
        r = requests.post(URL, headers=test['headers'], json={"user": {"id": "test_user"}}, timeout=5)
        print(f"  Status: {r.status_code}")
        if r.status_code < 400:
            print(f"  SUCCESS! Response: {r.text}")
        else:
            print(f"  FAILED: {r.text}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print("-" * 20)
