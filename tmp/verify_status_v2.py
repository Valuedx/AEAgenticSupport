import logging
from tools.base import get_ae_client
from tools.status_tools import check_workflow_status

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_status")

def verify():
    client = get_ae_client()
    
    # 1. Test natural name status for Maturity Claim
    logger.info("Testing status for 'Maturity Claim'...")
    res_name = check_workflow_status(workflow_name="Maturity Claim")
    logger.info(f"Result for 'Maturity Claim': {res_name.get('message')}")
    logger.info(f"Latest ID: {res_name.get('latest_execution_id')}")
    
    # 2. Test specific Request ID from user's logs
    test_id = "2536463" # From the screenshot snippet in history
    logger.info(f"Testing direct status for Request ID {test_id}...")
    res_id = check_workflow_status(workflow_name=test_id)
    logger.info(f"Result for ID {test_id}: {res_id.get('message')}")
    logger.info(f"Bot Name: {res_id.get('bot_name')}")
    logger.info(f"Status: {res_id.get('status')}")

if __name__ == "__main__":
    verify()
