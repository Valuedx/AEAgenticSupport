import logging
import sys
import os
import json
from datetime import datetime, timezone, timedelta

# Set up paths to include current directory
sys.path.append(os.getcwd())

from tools.status_tools import check_workflow_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_status")

def main():
    wf_name = "License_bot"
    logger.info(f"Checking status for {wf_name}...")
    
    try:
        # 1. Normal check
        result = check_workflow_status(wf_name)
        logger.info("Normal Check Result:")
        logger.info(json.dumps(result, indent=2))
        
        # 2. Filtered check (Complete)
        logger.info(f"\nChecking status for {wf_name} with filter 'Complete'...")
        result_complete = check_workflow_status(wf_name, status="Complete")
        logger.info("Filtered (Complete) Result:")
        logger.info(json.dumps(result_complete, indent=2))

        # 3. Filtered check (Failure)
        logger.info(f"\nChecking status for {wf_name} with filter 'Failure'...")
        result_failure = check_workflow_status(wf_name, status="Failure")
        logger.info("Filtered (Failure) Result:")
        logger.info(json.dumps(result_failure, indent=2))
            
    except Exception as e:
        logger.error(f"Error during verification: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
