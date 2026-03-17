import logging
import sys
import os

# Set up paths to include current directory
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check_wf")

def main():
    client = get_automationedge_client()
    logger.info("Fetching workflows...")
    try:
        workflows = client.list_workflows(all_pages=True)
        logger.info(f"Found {len(workflows)} workflows.")
        
        matches = [w for w in workflows if "License" in (w.get("workflowName") or w.get("name") or "")]
        if matches:
            logger.info("Matches for 'License':")
            for w in matches:
                logger.info(f" - Name: {w.get('workflowName') or w.get('name')}, ID: {w.get('workflowId') or w.get('id')}")
        else:
            logger.info("No workflows found matching 'License'.")
            # Log first 5 workflows to see structure
            if workflows:
                logger.info("First 5 workflows:")
                for w in workflows[:5]:
                    logger.info(f" - {w}")
                    
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()
