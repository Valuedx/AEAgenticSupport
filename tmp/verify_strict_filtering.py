import unittest
from unittest.mock import MagicMock, patch
import sys
import os

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.append(root_dir)

# Mock side_effect for get_runtime_value to provide correct types
def mock_get_runtime_value(key, default=None):
    if "timeout" in key.lower(): return 30
    if "verify" in key.lower() or "ssl" in key.lower(): return False
    if "url" in key.lower(): return "https://t4.example.com"
    return default or "mock_value"

with patch('config.settings.CONFIG', MagicMock()):
    with patch('state.app_config.get_runtime_value', side_effect=mock_get_runtime_value):
        from tools.automationedge_client import AutomationEdgeClient

class TestStrictFiltering(unittest.TestCase):
    def test_strict_filtering_discards_mismatches(self):
        """Test that get_workflow_instances discards mismatched results and eventually raises."""
        client = AutomationEdgeClient()
        client._authorized_request = MagicMock()
        
        # Simulating a server that always returns Wrong_Bot regardless of the query
        client._authorized_request.return_value = {"data": [{"workflowName": "Wrong_Bot", "id": 123}]}
        
        # It should filter everything out and eventually raise RuntimeError 
        # because no candidates matched.
        with self.assertRaises(RuntimeError) as cm:
            client.get_workflow_instances(workflow_name="Right_Bot", limit=5)
        
        self.assertIn("Could not fetch instances for workflow 'Right_Bot'", str(cm.exception))

    def test_no_filtering_when_global(self):
        """Test that global listing (no name) still returns everything."""
        client = AutomationEdgeClient()
        client._authorized_request = MagicMock()
        client._authorized_request.return_value = [{"workflowName": "BotA"}, {"workflowName": "BotB"}]
        
        results = client.get_workflow_instances(workflow_name="", limit=5)
        self.assertEqual(len(results), 2)

if __name__ == "__main__":
    unittest.main()
