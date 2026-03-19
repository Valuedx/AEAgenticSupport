import unittest
import sys
import os
from unittest.mock import MagicMock, patch

# Add the project root to sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.append(root_dir)

# Mock side_effect for get_runtime_value to provide correct types
def mock_get_runtime_value(key, default=None):
    if "timeout" in key.lower():
        return 30
    if "verify" in key.lower() or "ssl" in key.lower():
        return False
    if "url" in key.lower():
        return "https://t4.example.com"
    return default or "mock_value"

with patch('config.settings.CONFIG', MagicMock()):
    with patch('state.app_config.get_runtime_value', side_effect=mock_get_runtime_value):
        from tools.automationedge_client import AutomationEdgeClient

class TestStatusDeepAudit(unittest.TestCase):
    
    def test_get_workflow_instances_paging_get_consistency(self):
        """Test Phase 3 GET paging has status, size=50 and order=desc."""
        client = AutomationEdgeClient()
        client._authorized_request = MagicMock()
        client._extract_list = MagicMock()
        
        # Mock Phase 1 & 2 to return empty to fall through
        client._authorized_request.side_effect = [
            [], [], # Phase 1 Modern (GET)
            [], [], # Phase 2 POST (T4)
            [{"id": 1}], # Phase 3 GET Page 1
        ]
        client._extract_list.side_effect = lambda x: x if isinstance(x, list) else []
        
        client.get_workflow_instances(workflow_name="TestBot", limit=50, status_filter="Complete")
        
        # Check the last call (Phase 3 GET)
        calls = [c for c in client._authorized_request.call_args_list if c[0][0] == "GET"]
        phase3_call = calls[-1] # The one that succeeded
        
        params = phase3_call[1].get('params', {})
        self.assertEqual(params.get('order'), "desc")
        self.assertEqual(params.get('status'), "Complete")
        self.assertEqual(params.get('size'), 50)
        self.assertEqual(params.get('offset'), 0)

    def test_global_page_size_limits(self):
        """Test that list_agents etc use size=50 by default."""
        client = AutomationEdgeClient()
        client._authorized_request = MagicMock(return_value=[])
        
        # list_agents should use size=50
        client.list_agents()
        self.assertEqual(client._authorized_request.call_args[1].get('params', {}).get('size'), 50)
        
        # check_agent_status should use size=50 in its T4 POST call
        client.check_agent_status(org_code="test_org")
        monitoring_call = [c for c in client._authorized_request.call_args_list if "monitoring" in c[0][1]][0]
        self.assertEqual(monitoring_call[1].get('params', {}).get('size'), 50)

if __name__ == "__main__":
    unittest.main()
