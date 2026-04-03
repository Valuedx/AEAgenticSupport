from tools.remediation_tools import restart_execution, resubmit_execution
from unittest.mock import patch


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
