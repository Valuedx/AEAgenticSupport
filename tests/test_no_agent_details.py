from unittest.mock import MagicMock, patch

from agents.orchestrator import Orchestrator
from tools import remediation_tools, status_tools


def _assigned_agents_payload():
    return [
        {
            "workflow": {"name": "timesheet_report_generation_v5"},
            "agents": [
                {"agentName": "agent-timesheet-01", "agentState": "STOPPED"},
                {"agentName": "agent-timesheet-02", "agentState": "DISCONNECTED"},
            ],
        }
    ]


def test_trigger_workflow_surfaces_assigned_agents_when_no_agent_available():
    mock_client = MagicMock()
    mock_client.resolve_cached_workflow_name.return_value = "timesheet_report_generation_v5"
    mock_client.get_cached_workflow_info.return_value = ("9109", [])
    mock_client.get_cached_workflow_parameters.return_value = []
    mock_client.get_required_parameters.return_value = []
    mock_client.get_workflow_agents.side_effect = [[], _assigned_agents_payload()]
    mock_client.get_running_instances.return_value = []
    mock_client.execute_workflow.return_value = {"id": "2590291", "status": "New"}
    mock_client.poll_execution_status.return_value = {"status": "NO_AGENT", "raw": {}}

    with patch("tools.remediation_tools.get_ae_client", return_value=mock_client):
        result = remediation_tools.trigger_workflow("timesheet_report_generation_v5", {})

    assert result["success"] is False
    assert result["request_id"] == "2590291"
    assert "agent-timesheet-01 (STOPPED)" in result["error"]
    assert "agent-timesheet-02 (DISCONNECTED)" in result["error"]
    assert len(result["assigned_agents"]) == 2


def test_t4_execute_and_poll_surfaces_assigned_agents_when_no_agent_available():
    mock_client = MagicMock()
    mock_client.resolve_cached_workflow_name.return_value = "timesheet_report_generation_v5"
    mock_client.get_cached_workflow_parameters.return_value = []
    mock_client.get_required_parameters.return_value = []
    mock_client.execute_workflow.return_value = {"id": "2590291", "status": "New"}
    mock_client.poll_execution_status.return_value = {"status": "no_agent", "raw": {}}
    mock_client.get_workflow_agents.return_value = _assigned_agents_payload()

    with patch("tools.status_tools.get_ae_client", return_value=mock_client):
        result = status_tools.t4_execute_and_poll(
            workflow_name="timesheet_report_generation_v5",
            workflow_id="9109",
            params={},
        )

    assert result["success"] is False
    assert result["request_id"] == "2590291"
    assert "agent-timesheet-01 (STOPPED)" in result["message"]
    assert "agent-timesheet-02 (DISCONNECTED)" in result["message"]
    assert len(result["assigned_agents"]) == 2


def test_orchestrator_failure_message_lists_assigned_agents():
    orchestrator = Orchestrator()

    response = orchestrator._format_completion_message(
        "t4_execute_and_poll",
        {
            "success": False,
            "error": "No automation agent was available.",
            "request_id": "2590291",
            "assigned_agents": [
                {"agentName": "agent-timesheet-01", "agentState": "STOPPED"},
                {"agentName": "agent-timesheet-02", "agentState": "DISCONNECTED"},
            ],
        },
    )

    assert "Assigned agents" in response
    assert "agent-timesheet-01 (STOPPED)" in response
    assert "agent-timesheet-02 (DISCONNECTED)" in response
