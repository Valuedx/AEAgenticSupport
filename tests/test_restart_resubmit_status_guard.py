from tools.remediation_tools import restart_execution, resubmit_execution
from unittest.mock import patch


def test_restart_execution_blocks_completed_status_and_does_not_call_restart():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "COMPLETED", "workflowName": "Claims_Processing_Daily"}

        def restart_request(self, execution_id, reason=""):
            raise AssertionError("restart_request should not be called for completed executions")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()):
        result = restart_execution(execution_id="22024")

    assert result["success"] is False
    assert result["status"] == "COMPLETED"
    assert "completed" in result["error"].lower()
    assert "trigger a new execution instead" in result["hint"].lower()


def test_resubmit_execution_blocks_completed_status_and_does_not_call_resubmit():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "Complete", "workflowName": "Claims_Processing_Daily"}

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            raise AssertionError("resubmit_request should not be called for completed executions")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()):
        result = resubmit_execution(execution_id="22024")

    assert result["success"] is False
    assert result["status"] == "COMPLETED"
    assert "completed" in result["error"].lower()
    assert "trigger a new execution instead" in result["hint"].lower()


def test_restart_execution_allows_failed_status():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILURE", "workflowName": "Claims_Processing_Daily"}

        def restart_request(self, execution_id, reason=""):
            return {"success": True, "message": "restart accepted"}

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()):
        result = restart_execution(execution_id="22024")

    assert result["success"] is True


def test_resubmit_execution_allows_failed_status():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Processing_Daily"}

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            return {"message": "resubmitted"}

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()):
        result = resubmit_execution(execution_id="22024")

    assert result["success"] is True
