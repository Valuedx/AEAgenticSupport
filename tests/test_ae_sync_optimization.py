import unittest
from unittest.mock import MagicMock, patch
import httpx
import logging
import sys

# Import the actual classes/functions to test
from tools.registry import ToolRegistry
from tools.automationedge_client import AutomationEdgeClient

class TestAESyncOptimization(unittest.TestCase):
    def setUp(self):
        # We pass a mock client to avoid real network calls
        self.mock_httpx = MagicMock(spec=httpx.Client)
        self.client = AutomationEdgeClient(client=self.mock_httpx)
        self.registry = ToolRegistry()

    @patch("tools.registry.get_automationedge_client")
    @patch("tools.registry.CONFIG")
    def test_reload_tools_skips_details_if_metadata_present(self, mock_config, mock_get_client):
        # Setup config mock
        mock_config.get.side_effect = lambda k, v=None: True if k == "AE_ENABLE_DYNAMIC_TOOLS" else v
        
        # Setup client mock
        mock_ae_client = MagicMock()
        mock_get_client.return_value = mock_ae_client
        
        # Scenario: T4 Catalogue returns rich metadata in the list
        workflows = [
            {
                "workflowName": "WF_Test_Rich",
                "id": "123",
                "params": [{"name": "param1", "type": "String", "optional": False}],
                "AgenticToolConfiguration": {
                    "toolName": "rich_tool", 
                    "active": True,
                    "status": "active"
                }
            }
        ]
        mock_ae_client.list_workflows.return_value = workflows
        
        # When
        self.registry.reload_automationedge_tools()
        
        # Then
        # Should NOT have called get_workflow_details because rich metadata was present in 'workflows'
        mock_ae_client.get_workflow_details.assert_not_called()
        self.assertIn("rich_tool", self.registry.list_dynamic_tools())

    @patch("tools.registry.get_automationedge_client")
    @patch("tools.registry.CONFIG")
    def test_reload_tools_calls_details_if_metadata_missing(self, mock_config, mock_get_client):
        # Setup config mock
        mock_config.get.side_effect = lambda k, v=None: True if k == "AE_ENABLE_DYNAMIC_TOOLS" else v
        
        # Setup client mock
        mock_ae_client = MagicMock()
        mock_get_client.return_value = mock_ae_client
        
        # Scenario: Standard list (no config)
        workflows = [{"workflowName": "WF_Standard", "id": "456"}]
        mock_ae_client.list_workflows.return_value = workflows
        mock_ae_client.get_workflow_details.return_value = {
            "AgenticToolConfiguration": {
                "toolName": "standard_tool", 
                "active": True,
                "status": "active"
            }
        }
        
        # When
        self.registry.reload_automationedge_tools()
        
        # Then
        # Should HAVE called get_workflow_details
        mock_ae_client.get_workflow_details.assert_called_with("456")
        self.assertIn("standard_tool", self.registry.list_dynamic_tools())

    @patch("tools.automationedge_client.logger")
    def test_authorized_request_silences_logged_errors(self, mock_logger):
        # Setup mock 400 response
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = '{"message": "Bad Request"}'
        # raise_for_status should raise an exception for 400
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=mock_response
        )
        
        # Mock the underlying httpx client's request method
        self.mock_httpx.request.return_value = mock_response
        
        # Case 1: Silent on 400
        with self.assertRaises(httpx.HTTPStatusError):
            self.client._authorized_request("GET", "/test", silent_on_status=[400])
        
        # Should NOT log error when status matches silent list
        mock_logger.error.assert_not_called()

        # Case 2: Not silent (default)
        with self.assertRaises(httpx.HTTPStatusError):
            self.client._authorized_request("GET", "/test")
        
        # SHOULD log error for non-silent status codes
        mock_logger.error.assert_called()

if __name__ == "__main__":
    unittest.main()
