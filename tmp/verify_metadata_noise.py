import logging
import sys
import os
import httpx

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import AutomationEdgeClient
from tools.registry import ToolRegistry

# Configure logging to see DEBUG logs
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger("verify_discovery")

def test_discovery_noise():
    print("\n=== Testing Discovery Noise and Negative Caching ===")
    registry = ToolRegistry()
    
    from tools.automationedge_client import get_automationedge_client
    ae_client = get_automationedge_client()
    
    print(f"Initial fail cache size: {len(ae_client._metadata_fail_cache)}")
    
    # Simulate a few AE-1005 failures
    test_id = "NON_EXISTENT_WF_123"
    print(f"\n1. First attempt for {test_id} (should log DEBUG silence if it fails with 400/AE-1005)")
    try:
        ae_client.get_workflow_details(test_id)
    except Exception as e:
        print(f"Caught expected error: {e}")

    print(f"Fail cache size after 1st attempt: {len(ae_client._metadata_fail_cache)}")
    
    print(f"\n2. Second attempt for {test_id} (should be INSTANTLY skipped via cache)")
    try:
        ae_client.get_workflow_details(test_id)
    except Exception as e:
        print(f"Caught error: {e}")

    print(f"Fail cache size after 2nd attempt: {len(ae_client._metadata_fail_cache)}")
    
    # Reload tools
    print("\n3. Testing reload_automationedge_tools noise suppression")
    try:
        results = registry.reload_automationedge_tools()
        print(f"Reload results: {results}")
    except Exception as e:
        print(f"Reload failed (likely auth): {e}")

if __name__ == "__main__":
    test_discovery_noise()
