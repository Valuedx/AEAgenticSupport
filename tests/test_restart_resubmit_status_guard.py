from tools.remediation_tools import restart_execution, resubmit_execution
from unittest.mock import patch


def _timesheet_workflow_response():
    return (
        '{"message":"The timesheet file has been shared with you. Kindly check your mailbox.",'
        '"error":null,"currentStatus":null,"outputParameters":[]}'
    )


def test_restart_execution_blocks_completed_status_and_does_not_call_restart():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "COMPLETED", "workflowName": "Claims_Processing_Daily"}

        def restart_request(self, execution_id, reason=""):
            raise AssertionError("restart_request should not be called for completed executions")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = restart_execution(execution_id="22024")

    assert result["success"] is False
    assert result["status"] == "COMPLETED"
    assert "completed" in result["error"].lower()
    assert "use fresh run" in result["hint"].lower()


def test_resubmit_execution_blocks_completed_status_and_does_not_call_resubmit():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "Complete", "workflowName": "Claims_Processing_Daily"}

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            raise AssertionError("resubmit_request should not be called for completed executions")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = resubmit_execution(execution_id="22024")

    assert result["success"] is False
    assert result["status"] == "COMPLETED"
    assert "completed" in result["error"].lower()
    assert "use fresh run" in result["hint"].lower()


def test_restart_execution_allows_failed_status():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILURE", "workflowName": "Claims_Processing_Daily"}

        def restart_request(self, execution_id, reason=""):
            return {"success": True, "message": "restart accepted"}

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = restart_execution(execution_id="22024")

    assert result["success"] is True


def test_resubmit_execution_allows_failed_status():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Processing_Daily"}

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            return {"message": "resubmitted"}

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = resubmit_execution(execution_id="22024")

    assert result["success"] is True


def test_resubmit_execution_accepts_scoped_user_context():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Processing_Daily"}

        def get_workflow_agents(self):
            return []

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            return {"message": "resubmitted", "success": True, "automationRequestId": execution_id}

        def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
            return {"status": "QUEUED", "raw": {"status": "QUEUED"}}

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = resubmit_execution(
            execution_id="22024",
            user_id="webchat:kirtibala.gujar",
            org_code="AEGEMS",
        )

    assert result["success"] is True


def test_resubmit_execution_surfaces_new_request_id_from_ae_response():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "timesheet_report_generation_v5"}

        def get_workflow_agents(self):
            return []

        def list_agents(self):
            return [{"agentName": "agent-timesheet-01", "agentState": "RUNNING"}]

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            return {
                "source": "AutomationEdge HelpDesk",
                "automationRequestId": 2611436,
                "success": True,
                "responseCode": "RequestCreated",
                "oldRequestId": 2611283,
            }

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={"workflowName": "timesheet_report_generation_v5", "agentName": "agent-timesheet-01"},
    ):
        result = resubmit_execution(execution_id="2611283")

    assert result["success"] is True
    assert result["execution_id"] == "2611436"
    assert result["request_id"] == "2611436"
    assert result["source_execution_id"] == "2611283"
    assert result["new_execution_id"] == "2611436"
    assert "New Request ID: `2611436`" in result["message"]


def test_restart_execution_surfaces_completed_workflow_response_message_after_poll():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "timesheet_report_generation_v5"}

        def restart_request(self, execution_id, reason=""):
            return {"success": True, "message": "restart accepted"}

        def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
            return {
                "status": "Complete",
                "raw": {
                    "status": "Complete",
                    "workflowName": "timesheet_report_generation_v5",
                    "workflowResponse": _timesheet_workflow_response(),
                },
            }

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = restart_execution(execution_id="2611283")

    assert result["success"] is True
    assert result["status"] == "Complete"
    assert result["message"] == "The timesheet file has been shared with you. Kindly check your mailbox."
    assert result["workflow_response_message"] == "The timesheet file has been shared with you. Kindly check your mailbox."


def test_resubmit_execution_surfaces_completed_workflow_response_message_after_poll():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "timesheet_report_generation_v5"}

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            return {
                "success": True,
                "automationRequestId": 2611436,
                "oldRequestId": 2611283,
            }

        def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
            return {
                "status": "Complete",
                "raw": {
                    "status": "Complete",
                    "workflowName": "timesheet_report_generation_v5",
                    "workflowResponse": _timesheet_workflow_response(),
                },
            }

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = resubmit_execution(execution_id="2611283")

    assert result["success"] is True
    assert result["execution_id"] == "2611436"
    assert result["status"] == "Complete"
    assert result["message"] == "The timesheet file has been shared with you. Kindly check your mailbox."
    assert result["workflow_response_message"] == "The timesheet file has been shared with you. Kindly check your mailbox."


def test_restart_execution_returns_failure_when_polled_status_is_still_failed():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Daily_claim_report_bot"}

        def restart_request(self, execution_id, reason=""):
            return {"success": True, "message": "restart accepted"}

        def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
            return {
                "status": "Failure",
                "raw": {
                    "status": "Failure",
                    "workflowName": "Daily_claim_report_bot",
                    "workflowResponse": '{"message":null,"error":"Login issue Life Asia Portal","currentStatus":null,"outputParameters":[]}',
                },
            }

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = restart_execution(execution_id="2616407")

    assert result["success"] is False
    assert result["status"] == "Failure"
    assert "after restart is **Failure**" in result["error"]
    assert "Login issue Life Asia Portal" in result["error"]


def test_restart_execution_blocks_when_assigned_agents_are_not_running():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Processing_Daily"}

        def get_workflow_agents(self):
            return [
                {
                    "workflow": {"name": "Claims_Processing_Daily"},
                    "agents": [
                        {"agentName": "claims-agent-01", "agentState": "STOPPED"},
                        {"agentName": "claims-agent-02", "agentState": "DISCONNECTED"},
                    ],
                }
            ]

        def restart_request(self, execution_id, reason=""):
            raise AssertionError("restart_request should not be called when assigned agents are down")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = restart_execution(execution_id="22024")

    assert result["success"] is False
    assert result["status"] == "AGENT_UNAVAILABLE"
    assert result["workflow_name"] == "Claims_Processing_Daily"
    assert "start at least one assigned agent first" in result["error"].lower()
    assert "claims-agent-01 (STOPPED)" in result["error"]
    assert len(result["assigned_agents"]) == 2


def test_resubmit_execution_blocks_when_assigned_agents_are_not_running():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Processing_Daily"}

        def get_workflow_agents(self):
            return [
                {
                    "workflow": {"name": "Claims_Processing_Daily"},
                    "agents": [
                        {"agentName": "claims-agent-01", "agentState": "STOPPED"},
                    ],
                }
            ]

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            raise AssertionError("resubmit_request should not be called when assigned agents are down")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = resubmit_execution(execution_id="22024")

    assert result["success"] is False
    assert result["status"] == "AGENT_UNAVAILABLE"
    assert result["workflow_name"] == "Claims_Processing_Daily"
    assert "start the assigned agent first" in result["hint"].lower()
    assert "claims-agent-01 (STOPPED)" in result["error"]


def test_restart_execution_blocks_from_execution_agent_fallback_when_mapping_missing():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {
                "status": "FAILED",
                "workflowName": "Claims_Processing_Daily",
                "agentName": "claims-agent-01",
                "agentId": "2987",
            }

        def get_workflow_agents(self):
            return []

        def list_agents(self):
            return [{"agentName": "claims-agent-01", "agentId": "2987", "agentState": "STOPPED"}]

        def restart_request(self, execution_id, reason=""):
            raise AssertionError("restart_request should not be called when fallback agent is stopped")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ):
        result = restart_execution(execution_id="22024")

    assert result["success"] is False
    assert result["status"] == "AGENT_UNAVAILABLE"
    assert "claims-agent-01 (STOPPED)" in result["error"]


def test_resubmit_execution_blocks_from_request_agent_fallback_when_mapping_missing():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {
                "status": "FAILED",
                "workflowName": "Claims_Processing_Daily",
            }

        def get_workflow_agents(self):
            return []

        def list_agents(self):
            return [{"agentName": "claims-agent-01", "agentId": "2987", "agentState": "STOPPED"}]

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            raise AssertionError("resubmit_request should not be called when fallback agent is stopped")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={"workflowName": "Claims_Processing_Daily", "agentName": "claims-agent-01", "agentId": "2987"},
    ):
        result = resubmit_execution(execution_id="22024")

    assert result["success"] is False
    assert result["status"] == "AGENT_UNAVAILABLE"
    assert "claims-agent-01 (STOPPED)" in result["error"]
