from tools import status_tools


class _StatusClient:
    default_org_code = "AEGEMS"

    def __init__(self, poll_status: str):
        self.poll_status = poll_status
        self.executed: list[dict] = []

    def resolve_cached_workflow_name(self, name, user_id="", org_code=""):
        return name

    def get_workflow_instances(self, workflow_name, limit=300, status_filter=None):
        return [
            {
                "id": "2506738",
                "automationRequestId": "2506738",
                "workflowName": workflow_name,
                "status": "Failure",
                "createdDate": "2026-04-02T08:00:00+00:00",
            }
        ]

    def get_cached_workflow_id(self, workflow_name, user_id="", org_code=""):
        return f"id-{workflow_name}"

    def execute_workflow(self, **kwargs):
        self.executed.append(dict(kwargs))
        return {
            "automationRequestId": "HC-001",
            "requestId": "HC-001",
            "id": "HC-001",
            "status": "QUEUED",
        }

    def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=10):
        return {"status": self.poll_status, "raw": {"status": self.poll_status}}


def test_check_workflow_status_appends_life_asia_health_check(monkeypatch):
    client = _StatusClient("Failure")

    monkeypatch.setitem(status_tools.CONFIG, "ENABLE_RELATED_ISSUE_HEALTH_CHECK", True)
    monkeypatch.setitem(status_tools.CONFIG, "RELATED_ISSUE_HEALTH_CHECK_USE_ADMIN_SCOPE", True)
    monkeypatch.setitem(status_tools.CONFIG, "LIFE_ASIA_HEALTH_CHECK_WORKFLOW", "TEBT_Health_Check")
    monkeypatch.setitem(status_tools.CONFIG, "TEBT_HEALTH_CHECK_WORKFLOW", "Life_Asia_Health_Check")
    monkeypatch.setattr(status_tools, "get_ae_client", lambda: client)
    monkeypatch.setattr(
        "tools.log_tools.get_execution_logs",
        lambda execution_id, tail=0, user_id="", org_code="": {
            "report": "Process failed due to Life Asia connection timeout.",
            "primary_error": {"error_message": "Life Asia connection issue"},
        },
    )

    result = status_tools.check_workflow_status("Claims_Process")

    assert result["latest_status"] == "Failure"
    assert "The absolute latest execution for bot" in result["message"]
    assert "Latest failure logs suggest a **Life Asia** issue." in result["message"]
    assert "Triggered **Life Asia health check** via admin scope." in result["message"]
    assert "Process failed due to Life Asia issue. System is currently unavailable. Please try again later." in result["message"]
    assert result["related_issue_check"]["workflow_name"] == "TEBT_Health_Check"
    assert result["related_issue_check"]["used_admin_scope"] is True
    assert client.executed[0]["workflow_name"] == "TEBT_Health_Check"
    assert client.executed[0]["user_id"] == ""


def test_check_workflow_status_appends_tebt_health_check(monkeypatch):
    client = _StatusClient("Complete")

    monkeypatch.setitem(status_tools.CONFIG, "ENABLE_RELATED_ISSUE_HEALTH_CHECK", True)
    monkeypatch.setitem(status_tools.CONFIG, "RELATED_ISSUE_HEALTH_CHECK_USE_ADMIN_SCOPE", True)
    monkeypatch.setitem(status_tools.CONFIG, "LIFE_ASIA_HEALTH_CHECK_WORKFLOW", "TEBT_Health_Check")
    monkeypatch.setitem(status_tools.CONFIG, "TEBT_HEALTH_CHECK_WORKFLOW", "Life_Asia_Health_Check")
    monkeypatch.setattr(status_tools, "get_ae_client", lambda: client)
    monkeypatch.setattr(
        "tools.log_tools.get_execution_logs",
        lambda execution_id, tail=0, user_id="", org_code="": {
            "report": "Process failed due to TEBT portal login failure.",
            "primary_error": {"error_message": "TEBT login issue"},
        },
    )

    result = status_tools.check_workflow_status("Claims_Process")

    assert "Latest failure logs suggest a **TEBT** issue." in result["message"]
    assert "Triggered **TEBT health check** via admin scope." in result["message"]
    assert "Process failed due to TEBT issue. System is operational. Please retry the workflow." in result["message"]
    assert result["related_issue_check"]["workflow_name"] == "Life_Asia_Health_Check"
    assert client.executed[0]["workflow_name"] == "Life_Asia_Health_Check"
