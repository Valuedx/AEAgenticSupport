import logging
import sys
import os
import json
from datetime import datetime, timezone, timedelta

# Set up paths to include current directory
sys.path.append(os.getcwd())

from tools.status_tools import check_workflow_status

def main():
    wf_name = "License_bot"
    results = {}
    
    try:
        # 1. Normal check
        results["normal_check"] = check_workflow_status(wf_name)
        
        # 2. Filtered check (Complete)
        results["complete_filter"] = check_workflow_status(wf_name, status="Complete")

        # 3. Filtered check (Failure)
        results["failure_filter"] = check_workflow_status(wf_name, status="Failure")
            
        with open("status_verification_results.json", "w") as f:
            json.dump(results, f, indent=2)
            
    except Exception as e:
        with open("status_verification_error.txt", "w") as f:
            f.write(str(e))

if __name__ == "__main__":
    main()
