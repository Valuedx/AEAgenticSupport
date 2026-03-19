import pytest
from unittest.mock import MagicMock
from tools.remediation_tools import trigger_workflow

class MockClient:
    def __init__(self):
        self.get_running_instances = MagicMock(return_value=[])
        self.resolve_cached_workflow_name = MagicMock(return_value="License_bot")
        self.get_cached_workflow_parameters = MagicMock(return_value=[])
        self.get_required_parameters = MagicMock(return_value=[])
        self.get_workflow_agents = MagicMock(return_value=[
            {"workflow": {"name": "License_bot"}, "agents": [{"agentName": "Agent1", "agentState": "RUNNING"}]}
        ])
        self.get_cached_workflow_info = MagicMock(return_value=("123", None))
        self.execute_workflow = MagicMock(return_value={"id": "2541401"})

def test_trigger_blocked_when_running(monkeypatch):
    """trigger_workflow returns blocked message when an instance is already running."""
    client = MockClient()
    # Simulate a running instance
    client.get_running_instances.return_value = [{"id": "2541401", "status": "InProgress"}]
    
    monkeypatch.setattr("tools.remediation_tools.get_ae_client", lambda: client)
    
    result = trigger_workflow("License_bot", {})
    
    assert result["success"] is False
    assert result["blocked_reason"] == "concurrent_execution"
    assert "already running" in result["message"]
    assert "2541401" in result["message"]

def test_trigger_proceeds_when_not_running(monkeypatch):
    """trigger_workflow proceeds normally when no instance is running."""
    client = MockClient()
    client.get_running_instances.return_value = []
    
    monkeypatch.setattr("tools.remediation_tools.get_ae_client", lambda: client)
    
    result = trigger_workflow("License_bot", {})
    
    # It should reach the "Execute" phase and return success (assuming mocks for execution work)
    assert result["success"] is True
    assert "2541401" in result.get("message", "")
