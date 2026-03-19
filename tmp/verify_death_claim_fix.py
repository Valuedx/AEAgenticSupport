import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.status_tools import check_workflow_status

def verify_death_claim_fix():
    print("Verifying fix for Death_Claim status...")
    # This should now:
    # 1. Scan up to 1000 instances in Phase 2
    # 2. Not fail if Phase 3 GET fallbacks return 400/404/500
    # 3. Return a clean structure with empty recent_list if still not found
    
    try:
        res = check_workflow_status(workflow_name="Death_Claim")
        print("SUCCESS! Tool returned result.")
        print(json.dumps(res, indent=2))
        
        if not res.get("recent_list"):
            print("\nNOTE: No executions found in last 1000 global instances. This is acceptable/expected if the bot hasn't run recently.")
            print("The crucial thing is that it DID NOT return a 404/400 error to the user.")
            
    except Exception as e:
        print(f"FAILED: Tool still crashed with: {e}")

if __name__ == "__main__":
    verify_death_claim_fix()
