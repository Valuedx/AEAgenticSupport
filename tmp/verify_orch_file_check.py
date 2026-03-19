
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

# Mock dependencies that might touch DB or external services on import
sys.modules['config.db'] = MagicMock()
sys.modules['config.metrics'] = MagicMock()

from agents.orchestrator import Orchestrator

class TestFileUploadCheck(unittest.TestCase):
    @patch('agents.orchestrator.get_ae_client')
    @patch('agents.orchestrator.tool_registry')
    @patch('agents.orchestrator.llm_client')
    def test_preflight_maturity_claim(self, mock_llm, mock_registry, mock_ae_func):
        # Mock discover_tools to return Maturity_Claim
        mock_discover_result = MagicMock()
        mock_discover_result.success = True
        mock_discover_result.data = {
            "tools": [{
                "name": "Maturity_Claim",
                "score": 0.9,
                "metadata": {"source": "automationedge", "workflow_name": "Maturity_Claim"}
            }]
        }
        mock_registry.execute.return_value = mock_discover_result
        
        # Mock execution intent
        mock_llm.chat.return_value = "EXECUTE"
        
        # Mock AE client parameters
        mock_ae_client = MagicMock()
        mock_ae_func.return_value = mock_ae_client
        mock_ae_client.get_cached_workflow_parameters.return_value = [
            {"name": "Input_Path", "type": "File"}
        ]
        
        orch = Orchestrator()
        state = MagicMock()
        state.messages = []
        state.affected_workflows = []
        
        # We want to test _preflight_workflow_param_collection
        response = orch._preflight_workflow_param_collection(
            "run maturity claim",
            state
        )
        
        print(f"Response: {response}")
        self.assertEqual(response, "This feature is not present please go to AE server trigger bot manually")

    @patch('agents.orchestrator.get_ae_client')
    @patch('agents.orchestrator.llm_client')
    def test_continue_collection_maturity_claim(self, mock_llm, mock_ae_func):
        # Mock AE client parameters
        mock_ae_client = MagicMock()
        mock_ae_func.return_value = mock_ae_client
        mock_ae_client.get_cached_workflow_parameters.return_value = [
            {"name": "Input_Path", "type": "File"}
        ]
        
        orch = Orchestrator()
        state = MagicMock()
        state.param_collection = {
            "workflow_name": "Maturity_Claim",
            "required_params": ["Input_Path"],
            "collected_params": {}
        }
        state.messages = []
        
        # Mock param extraction
        mock_llm.chat.return_value = '{"Input_Path": null}'
        
        # We want to test _continue_param_collection
        response = orch._continue_param_collection(
            "some input",
            state
        )
        
        print(f"Continue Response: {response}")
        self.assertEqual(response, "This feature is not present please go to AE server trigger bot manually")

if __name__ == "__main__":
    unittest.main()
