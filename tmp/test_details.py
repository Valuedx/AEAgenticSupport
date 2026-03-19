
import sys
import os
import httpx

# Add project root to path
sys.path.insert(0, os.getcwd())

from tools.automationedge_client import get_automationedge_client

def test_details():
    client = get_automationedge_client()
    org = "OMKAR_PATIL_3666"
    
    # Test cases: (name/id, use_prefix)
    tests = [
        ("7787", True),
        ("7787", False),
        ("WF_apply_leave", True),
        ("WF_apply_leave", False),
    ]
    
    for ident, prefix in tests:
        path = f"/{org}/workflows/{ident}"
        print(f"\nTesting path: {path} (prefix={prefix})")
        try:
            res = client._authorized_request("GET", path, use_rest_prefix=prefix)
            print("SUCCESS!")
            # print(res)
        except Exception as e:
            print(f"FAILED: {e}")

if __name__ == "__main__":
    test_details()
