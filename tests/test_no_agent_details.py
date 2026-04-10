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


def _timesheet_workflow_response():
    return (
        '{"message":"The timesheet file has been shared with you. Kindly check your mailbox.",'
        '"error":null,"currentStatus":null,"outputParameters":[]}'
    )


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
    assert "support ticket" in response.lower()
    assert "create_support_ticket" not in response


def test_get_execution_status_reports_other_process_running_when_new():
    mock_client = MagicMock()
    mock_client.get_execution_status.return_value = {
        "id": "2590291",
        "status": "New",
        "workflowName": "timesheet_report_generation_v5",
    }
    mock_client.diagnose_new_execution.return_value = {
        "reason": "other_process_running",
        "summary": (
            "Execution `2590291` for **timesheet_report_generation_v5** is still **New** "
            "because another process is currently running: **Payroll_Process** "
            "(Execution ID: `2590200`, status: `InProgress`). Please wait some time and check again."
        ),
        "other_execution_id": "2590200",
        "other_workflow_name": "Payroll_Process",
    }

    with patch("tools.status_tools.get_ae_client", return_value=mock_client):
        result = status_tools.get_execution_status("2590291")

    assert result["status"] == "New"
    assert result["diagnosis"] == "other_process_running"
    assert "another process is currently running" in result["message"]
    assert "Payroll_Process" in result["message"]
    assert "wait some time" in result["recommendation"].lower()


def test_get_execution_status_asks_restart_when_new_and_agent_is_down():
    mock_client = MagicMock()
    mock_client.get_execution_status.return_value = {
        "id": "2590291",
        "status": "New",
        "workflowName": "timesheet_report_generation_v5",
    }
    mock_client.diagnose_new_execution.return_value = {
        "reason": "agent_unavailable",
        "summary": (
            "Execution `2590291` for **timesheet_report_generation_v5** is still **New** "
            "because its assigned agent is not running (agent-timesheet-01 (STOPPED)). "
            "Please restart the agent and try again."
        ),
        "assigned_agents": [
            {"agentName": "agent-timesheet-01", "agentState": "STOPPED"},
        ],
    }

    with patch("tools.status_tools.get_ae_client", return_value=mock_client):
        result = status_tools.get_execution_status("2590291")

    assert result["status"] == "New"
    assert result["diagnosis"] == "agent_unavailable"
    assert "restart the agent" in result["message"].lower()
    assert len(result["assigned_agents"]) == 1


def test_trigger_workflow_reports_other_process_running_when_poll_detects_it():
    mock_client = MagicMock()
    mock_client.resolve_cached_workflow_name.return_value = "timesheet_report_generation_v5"
    mock_client.get_cached_workflow_info.return_value = ("9109", [])
    mock_client.get_cached_workflow_parameters.return_value = []
    mock_client.get_required_parameters.return_value = []
    mock_client.get_workflow_agents.return_value = [
        {
            "workflow": {"name": "timesheet_report_generation_v5"},
            "agents": [
                {"agentName": "agent-timesheet-01", "agentState": "RUNNING"},
            ],
        }
    ]
    mock_client.get_running_instances.return_value = []
    mock_client.execute_workflow.return_value = {"id": "2590291", "status": "New"}
    mock_client.poll_execution_status.return_value = {
        "status": "waiting_other_process",
        "raw": {
            "newExecutionDiagnosis": {
                "reason": "other_process_running",
                "summary": (
                    "Execution `2590291` for **timesheet_report_generation_v5** is still **New** "
                    "because another process is currently running: **Payroll_Process** "
                    "(Execution ID: `2590200`, status: `InProgress`). Please wait some time and check again."
                ),
                "other_execution_id": "2590200",
                "other_workflow_name": "Payroll_Process",
            }
        },
    }

    with patch("tools.remediation_tools.get_ae_client", return_value=mock_client):
        result = remediation_tools.trigger_workflow("timesheet_report_generation_v5", {})

    assert result["success"] is True
    assert result["status"] == "New"
    assert result["diagnosis"] == "other_process_running"
    assert "already triggered" in result["message"]
    assert "another process is currently running" in result["message"]
    assert result["other_execution_id"] == "2590200"


def test_trigger_workflow_surfaces_completed_workflow_response_message():
    mock_client = MagicMock()
    mock_client.resolve_cached_workflow_name.return_value = "timesheet_report_generation_v5"
    mock_client.get_cached_workflow_info.return_value = ("9109", [])
    mock_client.get_cached_workflow_parameters.return_value = []
    mock_client.get_required_parameters.return_value = []
    mock_client.get_workflow_agents.return_value = []
    mock_client.get_running_instances.return_value = []
    mock_client.execute_workflow.return_value = {"id": "2609419", "status": "New"}
    mock_client.poll_execution_status.return_value = {
        "status": "Complete",
        "raw": {
            "status": "Complete",
            "workflowName": "timesheet_report_generation_v5",
            "workflowResponse": _timesheet_workflow_response(),
        },
    }

    with patch("tools.remediation_tools.get_ae_client", return_value=mock_client):
        result = remediation_tools.trigger_workflow("timesheet_report_generation_v5", {})

    assert result["success"] is True
    assert result["message"] == "The timesheet file has been shared with you. Kindly check your mailbox."
    assert result["workflow_response_message"] == "The timesheet file has been shared with you. Kindly check your mailbox."


def test_trigger_workflow_refreshes_completed_payload_when_poll_message_missing():
    mock_client = MagicMock()
    mock_client.resolve_cached_workflow_name.return_value = "timesheet_report_generation_v5"
    mock_client.get_cached_workflow_info.return_value = ("9109", [])
    mock_client.get_cached_workflow_parameters.return_value = []
    mock_client.get_required_parameters.return_value = []
    mock_client.get_workflow_agents.return_value = []
    mock_client.get_running_instances.return_value = []
    mock_client.execute_workflow.return_value = {"id": "2609419", "status": "New"}
    mock_client.poll_execution_status.return_value = {
        "status": "Complete",
        "raw": {
            "status": "Complete",
            "workflowName": "timesheet_report_generation_v5",
        },
    }
    mock_client.refresh_execution_payload.return_value = {
        "id": "2609419",
        "status": "Complete",
        "workflowName": "timesheet_report_generation_v5",
        "workflowResponse": _timesheet_workflow_response(),
    }

    with patch("tools.remediation_tools.get_ae_client", return_value=mock_client):
        result = remediation_tools.trigger_workflow("timesheet_report_generation_v5", {})

    assert result["success"] is True
    assert result["message"] == "The timesheet file has been shared with you. Kindly check your mailbox."
    assert result["workflow_response_message"] == "The timesheet file has been shared with you. Kindly check your mailbox."
    mock_client.refresh_execution_payload.assert_called_once()


def test_get_execution_status_surfaces_completed_workflow_response_message():
    mock_client = MagicMock()
    mock_client.get_execution_status.return_value = {
        "id": "2609419",
        "status": "Complete",
        "workflowName": "timesheet_report_generation_v5",
        "workflowResponse": _timesheet_workflow_response(),
    }

    with patch("tools.status_tools.get_ae_client", return_value=mock_client):
        result = status_tools.get_execution_status("2609419")

    assert result["status"] == "Complete"
    assert result["message"] == "The timesheet file has been shared with you. Kindly check your mailbox."
    assert result["workflow_response_message"] == "The timesheet file has been shared with you. Kindly check your mailbox."


def test_get_execution_status_surfaces_related_app_failure_summary():
    mock_client = MagicMock()
    mock_client.get_execution_status.return_value = {
        "id": "22887",
        "status": "Failure",
        "workflowName": "Life_Asia_Workflow",
        "createdDate": "2026-04-08T13:45:47+00:00",
    }

    with patch("tools.status_tools.get_ae_client", return_value=mock_client), patch(
        "tools.status_tools._maybe_add_related_issue_health_check",
        return_value={
            "issue_type": "life_asia",
            "issue_label": "Life Asia",
            "health_check_label": "Life Asia health check",
            "workflow_name": "Life_Asia_Health_Check",
            "status": "NOT_CHECKED",
            "request_id": "",
            "message": "Current Life Asia health has not been checked yet. If you want to retry this workflow, I will first verify the current Life Asia health and only then proceed.",
            "failure_reason": "Life Asia connection timeout.",
            "application_status": "not_checked",
            "health_check_run": False,
        },
    ):
        result = status_tools.get_execution_status("22887")

    assert result["status"] == "Failure"
    assert "Status: Failed." in result["message"]
    assert "Likely cause: Life Asia connection timeout." in result["message"]
    assert "Current Life Asia health has not been checked yet" in result["message"]
    assert result["failure_reason"] == "Life Asia connection timeout."
    assert result["related_issue_check"]["application_status"] == "not_checked"


def test_t4_execute_and_poll_surfaces_completed_workflow_response_message():
    mock_client = MagicMock()
    mock_client.resolve_cached_workflow_name.return_value = "timesheet_report_generation_v5"
    mock_client.get_cached_workflow_parameters.return_value = []
    mock_client.get_required_parameters.return_value = []
    mock_client.execute_workflow.return_value = {"id": "2609419", "status": "New"}
    mock_client.poll_execution_status.return_value = {
        "status": "Complete",
        "raw": {
            "status": "Complete",
            "workflowName": "timesheet_report_generation_v5",
            "workflowResponse": _timesheet_workflow_response(),
        },
    }

    with patch("tools.status_tools.get_ae_client", return_value=mock_client):
        result = status_tools.t4_execute_and_poll(
            workflow_name="timesheet_report_generation_v5",
            workflow_id="9109",
            params={},
        )

    assert result["success"] is True
    assert "The timesheet file has been shared with you. Kindly check your mailbox." in result["message"]
    assert result["workflow_response_message"] == "The timesheet file has been shared with you. Kindly check your mailbox."


def test_t4_execute_and_poll_waiting_other_process_is_not_reported_as_trigger_failure():
    mock_client = MagicMock()
    mock_client.resolve_cached_workflow_name.return_value = "timesheet_report_generation_v5"
    mock_client.get_cached_workflow_parameters.return_value = []
    mock_client.get_required_parameters.return_value = []
    mock_client.execute_workflow.return_value = {"id": "2590291", "status": "New"}
    mock_client.poll_execution_status.return_value = {
        "status": "waiting_other_process",
        "raw": {
            "newExecutionDiagnosis": {
                "reason": "other_process_running",
                "summary": (
                    "Execution `2590291` for **timesheet_report_generation_v5** is still **New** "
                    "because another process is currently running: **Payroll_Process** "
                    "(Execution ID: `2590200`, status: `InProgress`). Please wait some time and check again."
                ),
                "other_execution_id": "2590200",
                "other_workflow_name": "Payroll_Process",
            }
        },
    }

    with patch("tools.status_tools.get_ae_client", return_value=mock_client):
        result = status_tools.t4_execute_and_poll(
            workflow_name="timesheet_report_generation_v5",
            workflow_id="9109",
            params={},
        )

    assert result["success"] is True
    assert result["status"] == "New"
    assert "already triggered" in result["message"]
    assert result["diagnosis"] == "other_process_running"
    assert result["other_execution_id"] == "2590200"
