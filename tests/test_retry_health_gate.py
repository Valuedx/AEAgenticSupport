from unittest.mock import patch

from tools import remediation_tools
import json


def test_restart_execution_blocks_when_related_app_health_check_fails():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Process"}

        def get_workflow_agents(self):
            return []

        def restart_request(self, execution_id, reason=""):
            raise AssertionError("restart_request should not be called when health gate blocks")

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ), patch(
        "tools.log_tools.get_execution_logs",
        return_value={
            "report": "Process failed due to Life Asia connection timeout.",
            "primary_error": {"error_message": "Life Asia connection issue"},
        },
    ), patch(
        "tools.status_tools._run_related_health_check",
        return_value={
            "issue_type": "life_asia",
            "issue_label": "Life Asia",
            "health_check_label": "Life Asia health check",
            "workflow_name": "TEBT_Health_Check",
            "status": "FAILED",
            "message": "Life Asia is currently unavailable.",
        },
    ):
        result = remediation_tools.restart_execution(execution_id="22024")

    assert result["success"] is False
    assert result["blocked_reason"] == "health_check_failed"
    assert "Life Asia" in result["message"]
    assert "currently unavailable" in result["message"]


def test_resubmit_execution_allows_retry_when_related_app_health_check_passes():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Process"}

        def get_workflow_agents(self):
            return []

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            return {"success": True, "automationRequestId": 2611436, "oldRequestId": execution_id}

        def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
            return {"status": "QUEUED", "raw": {"status": "QUEUED"}}

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ), patch(
        "tools.log_tools.get_execution_logs",
        return_value={
            "report": "Process failed due to TEBT portal login failure.",
            "primary_error": {"error_message": "TEBT login issue"},
        },
    ), patch(
        "tools.status_tools._run_related_health_check",
        return_value={
            "issue_type": "tebt",
            "issue_label": "TEBT",
            "health_check_label": "TEBT health check",
            "workflow_name": "Life_Asia_Health_Check",
            "status": "COMPLETE",
            "message": "TEBT is currently up and running.",
        },
    ):
        result = remediation_tools.resubmit_execution(execution_id="22024")

    assert result["success"] is True
    assert result["health_gate"]["health_gate_passed"] is True
    assert "Retrying workflow now" in result["message"]


def test_restart_execution_uses_explicit_org_code_for_retry_health_gate():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Process"}

        def get_workflow_agents(self):
            return []

        def restart_request(self, execution_id, reason=""):
            return {"success": True, "automationRequestId": execution_id}

        def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
            return {"status": "QUEUED", "raw": {"status": "QUEUED"}}

    captured: dict[str, str] = {}

    def fake_pre_retry_health_gate(client, execution_id, workflow_name, org_code):
        captured["org_code"] = org_code
        return {
            "health_gate_passed": True,
            "issue_type": "tebt",
            "issue_label": "TEBT",
            "health_summary": "TEBT is currently up and running.\n\nTEBT is healthy. Retrying workflow now...",
            "health_result": {"status": "COMPLETE", "passed": True},
        }

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ), patch(
        "tools.remediation_tools._pre_retry_health_gate",
        side_effect=fake_pre_retry_health_gate,
    ), patch(
        "tools.remediation_tools.default_org_code",
        return_value="DEFAULT",
    ):
        result = remediation_tools.restart_execution(execution_id="22024", org_code="AEGEMS")

    assert result["success"] is True
    assert captured["org_code"] == "AEGEMS"


def test_restart_execution_fallback_to_resubmit_reuses_single_health_check():
    class StubClient:
        def get_execution_status(self, execution_id):
            return {"status": "FAILED", "workflowName": "Claims_Process"}

        def get_workflow_agents(self):
            return []

        def restart_request(self, execution_id, reason=""):
            raise Exception("AE-2624 maximum limit of 10 restarts reached")

        def resubmit_request(self, execution_id, reason="", from_failure_point=True):
            return {"success": True, "automationRequestId": "2611437", "oldRequestId": execution_id}

        def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
            return {"status": "QUEUED", "raw": {"status": "QUEUED"}}

    call_count = {"value": 0}

    def fake_pre_retry_health_gate(client, execution_id, workflow_name, org_code):
        call_count["value"] += 1
        return {
            "health_gate_passed": True,
            "issue_type": "life_asia",
            "issue_label": "Life Asia",
            "health_summary": "Life Asia is currently up and running.\n\nLife Asia is healthy. Retrying workflow now...",
            "health_result": {"status": "COMPLETE", "passed": True},
        }

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.remediation_tools._get_request_payload",
        return_value={},
    ), patch(
        "tools.remediation_tools._pre_retry_health_gate",
        side_effect=fake_pre_retry_health_gate,
    ):
        result = remediation_tools.restart_execution(execution_id="22024")

    assert result["success"] is True
    assert result["fallback"] == "resubmit"
    assert call_count["value"] == 1


def test_run_related_health_check_uses_execution_status_error_when_logs_do_not_identify_issue():
    workflow_response = json.dumps(
        {
            "message": None,
            "error": "Login issue Life Asia Portal",
            "currentStatus": None,
            "outputParameters": None,
        }
    )

    class StubClient:
        def get_execution_status(self, execution_id):
            return {
                "id": execution_id,
                "status": "FAILED",
                "workflowName": "Daily_claim_report_bot",
                "workflowResponse": workflow_response,
            }

        def refresh_execution_payload(self, execution_id, record=None, workflow_name="", recent_limit=25):
            return dict(record or {})

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()), patch(
        "tools.status_tools.related_app_registry.get_related_applications",
        return_value=[
            {
                "issue_type": "life_asia",
                "issue_label": "Life Asia",
                "health_check_label": "Life Asia health check",
                "health_check_workflow": "TEBT_Health_Check",
                "markers": ["life asia", "life_asia", "lifeasia"],
                "contexts": ["connect", "connection", "timeout", "down", "unavailable", "socket", "host", "service"],
            },
            {
                "issue_type": "tebt",
                "issue_label": "TEBT",
                "health_check_label": "TEBT health check",
                "health_check_workflow": "Life_Asia_Health_Check",
                "markers": ["tebt"],
                "contexts": ["login", "portal", "credential", "password", "auth", "authentication", "session", "sign in", "signin"],
            },
        ],
    ), patch(
        "tools.log_tools.get_execution_logs",
        return_value={"report": "Generic failure with no app details."},
    ), patch(
        "tools.status_tools._run_related_health_check",
        return_value={
            "issue_type": "life_asia",
            "issue_label": "Life Asia",
            "health_check_label": "Life Asia health check",
            "workflow_name": "TEBT_Health_Check",
            "status": "COMPLETE",
            "message": "Life Asia is currently up and running.",
        },
    ):
        result = remediation_tools.run_related_health_check(
            execution_id="2615124",
            workflow_name="Daily_claim_report_bot",
        )

    assert result["success"] is True
    assert result["health_gate_required"] is True
    assert result["health_check_workflow"] == "TEBT_Health_Check"
    assert result["issue_label"] == "Life Asia"
