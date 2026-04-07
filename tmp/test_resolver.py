import sys
import os
import logging

# Setup basic logging to see the "Fuzzy match" logs
logging.basicConfig(level=logging.INFO)

# Add current directory to path
sys.path.append(os.path.abspath("."))

from tools.automationedge_client import AutomationEdgeClient

def test_resolution():
    client = AutomationEdgeClient()
    
    # Test cases:
    # 1. Exact match (already working)
    # 2. Name with (ID: 123) suffix
    # 3. Name with spaces/hyphens
    # 4. Partial match
    
    test_names = [
        "Cashier Receipting Report Download -MG22P1W25", # The name from user logs
        "Cashier Receipting Report Download -MG22P1W25 (ID: 12345)", # Mocked with ID
        "Cashier_Receipting_Report_Download", # Slugified
    ]
    
    print("--- Testing Workflow Resolution ---")
    for name in test_names:
        resolved = client.resolve_cached_workflow_name(name)
        print(f"Input: '{name}' -> Resolved: '{resolved}'")

if __name__ == "__main__":
    test_resolution()
