import pytest
from unittest.mock import MagicMock, patch
from tools.remediation_tools import trigger_workflow

def test_trigger_blocked_when_running():
    """trigger_workflow returns blocked message when an instance is already running."""
    
    # 1. Setup Mocks
    mock_client = MagicMock()
    
    # Mock resolution to 'License_bot'
    mock_client.resolve_cached_workflow_name.return_value = "License_bot"
    
    # Mock get_running_instances to return a fake running instance
    mock_client.get_running_instances.return_value = [{
        "id": "2541401",
        "status": "InProgress",
        "workflowName": "License_bot",
        "startTime": "2026-03-19T10:00:00Z"
    }]
    
    # Mock check_agent_status to return success (so it passes IMPROVEMENT 6)
    mock_client.check_agent_status.return_value = [{"agentState": "Running"}]

    # 2. Execute
    with patch("tools.remediation_tools.get_ae_client", return_value=mock_client):
        # We don't need real params/schema for this guard check
        result = trigger_workflow("License_bot", {})

    # 3. Assertions
    assert result["success"] is False
    assert result["blocked_reason"] == "concurrent_execution"
    assert "already running" in result["message"]
    assert "2541401" in result["message"]
    assert "License Bot" in result["message"] # Should be title-cased
    
    # Verify the client was called
    mock_client.get_running_instances.assert_called_with("License_bot")
    # Verify it didn't proceed to actual trigger
    mock_client.execute_workflow.assert_not_called()

def test_trigger_proceeds_when_not_running():
    """trigger_workflow proceeds normally when no instances are running."""
    
    mock_client = MagicMock()
    mock_client.resolve_cached_workflow_name.return_value = "License_bot"
    mock_client.get_running_instances.return_value = [] # Nothing running
    mock_client.check_agent_status.return_value = [{"agentState": "Running"}]
    
    mock_client.get_cached_workflow_id.return_value = "999"
    mock_client.get_cached_workflow_info.return_value = ("999", "License_bot")
    mock_client.execute_workflow.return_value = {"id": "999", "status": "New"}
    mock_client.poll_execution_status.return_value = {"status": "Complete", "execution_id": "999"}

    with patch("tools.remediation_tools.get_ae_client", return_value=mock_client):
        result = trigger_workflow("License_bot", {})

    # Assertions
    assert result["success"] is True
    mock_client.execute_workflow.assert_called()
