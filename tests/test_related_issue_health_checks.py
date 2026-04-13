import json

from tools import status_tools


def _registry_entries():
    return [
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
    ]


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

    def get_workflow_instance_by_id(self, execution_id):
        return {
            "id": execution_id,
            "automationRequestId": execution_id,
            "workflowName": "Claims_Process",
            "status": "Failure",
            "createdDate": "2026-04-02T08:00:00+00:00",
        }

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


def _mock_related_app_llm(responses: dict[str, dict]):
    def _fake_chat(prompt, system="", temperature=0.0, max_tokens=None):
        lowered = str(prompt or "").lower()
        for query, payload in responses.items():
            if str(query).lower() in lowered:
                return json.dumps(payload)
        return json.dumps(
            {
                "decision": "uncertain",
                "issue_type": "",
                "confidence": 0.0,
                "reason": "no_stubbed_response",
            }
        )

    return _fake_chat


def test_related_application_query_detection_matches_generic_process_and_workflow_phrases(monkeypatch):
    monkeypatch.setattr(
        status_tools.related_app_registry,
        "get_related_applications",
        _registry_entries,
    )
    monkeypatch.setattr(
        status_tools.related_app_registry.llm_client,
        "chat",
        _mock_related_app_llm(
            {
                "may i know the status of life asia process": {
                    "decision": "alias",
                    "issue_type": "life_asia",
                    "confidence": 0.98,
                    "reason": "generic application alias query",
                },
                "may i know the status of life asia workflow": {
                    "decision": "alias",
                    "issue_type": "life_asia",
                    "confidence": 0.98,
                    "reason": "generic application alias query",
                },
                "status of life asia surrender process": {
                    "decision": "not_alias",
                    "issue_type": "",
                    "confidence": 0.94,
                    "reason": "specific workflow phrase present",
                },
            }
        ),
    )

    process_match = status_tools.related_app_registry.classify_direct_related_application_query(
        "may i know the status of life asia process"
    )
    workflow_match = status_tools.related_app_registry.classify_direct_related_application_query(
        "may i know the status of life asia workflow"
    )
    specific_workflow = status_tools.related_app_registry.classify_direct_related_application_query(
        "status of life asia surrender process"
    )

    assert process_match["decision"] == "alias"
    assert process_match["related_application"]["issue_label"] == "Life Asia"
    assert workflow_match["decision"] == "alias"
    assert workflow_match["related_application"]["issue_label"] == "Life Asia"
    assert specific_workflow["decision"] == "not_alias"


def test_check_workflow_status_describes_life_asia_issue_without_running_health_check(monkeypatch):
    client = _StatusClient("Failure")

    monkeypatch.setitem(status_tools.CONFIG, "ENABLE_RELATED_ISSUE_HEALTH_CHECK", True)
    monkeypatch.setitem(status_tools.CONFIG, "RELATED_ISSUE_HEALTH_CHECK_USE_ADMIN_SCOPE", True)
    monkeypatch.setattr(status_tools, "get_ae_client", lambda: client)
    monkeypatch.setattr(
        status_tools.related_app_registry,
        "get_related_applications",
        _registry_entries,
    )
    monkeypatch.setattr(
        "tools.log_tools.get_execution_logs",
        lambda execution_id, tail=0, user_id="", org_code="": {
            "report": "Process failed due to Life Asia connection timeout.",
            "primary_error": {"error_message": "Life Asia connection issue"},
        },
    )

    result = status_tools.check_workflow_status("Claims_Process")

    assert result["latest_status"] == "Failure"
    assert "Status: Failed." in result["message"]
    assert "Likely cause: Life Asia connection issue." in result["message"]
    assert "Current Life Asia health has not been checked yet." in result["message"]
    assert "If you want to retry this workflow" in result["message"]
    assert "Triggered" not in result["message"]
    assert result["related_issue_check"]["workflow_name"] == "TEBT_Health_Check"
    assert result["related_issue_check"]["used_admin_scope"] is False
    assert result["related_issue_check"]["failure_reason"] == "Life Asia connection issue."
    assert result["related_issue_check"]["application_status"] == "not_checked"
    assert result["related_issue_check"]["health_check_run"] is False
    assert client.executed == []


def test_check_workflow_status_related_application_alias_uses_latest_matching_failure(monkeypatch):
    class AliasClient:
        default_org_code = "AEGEMS"

        def resolve_cached_workflow_name(self, workflow_name, user_id="", org_code=""):
            return "MG300W3_Surrender_Process"

        def request(self, method, path, **kwargs):
            assert method == "GET"
            assert path == "/api/v1/failures/recent"
            return {
                "failures": [
                    {
                        "execution_id": "22916",
                        "workflow_name": "Daily_claim_report_bot",
                        "status": "Failure",
                        "error_message": "Life Asia application unavailable",
                        "failure_time": "2026-04-10T09:08:00+00:00",
                    }
                ]
            }

        def get_workflow_instance_by_id(self, execution_id):
            return {
                "id": execution_id,
                "automationRequestId": execution_id,
                "workflowName": "Daily_claim_report_bot",
                "status": "Failure",
                "createdDate": "2026-04-10T09:08:00+00:00",
            }

    client = AliasClient()

    monkeypatch.setattr(status_tools, "get_ae_client", lambda: client)
    monkeypatch.setattr(
        status_tools.related_app_registry,
        "get_related_applications",
        _registry_entries,
    )
    monkeypatch.setattr(
        status_tools.related_app_registry.llm_client,
        "chat",
        _mock_related_app_llm(
            {
                "may i know the status of life asia process": {
                    "decision": "alias",
                    "issue_type": "life_asia",
                    "confidence": 0.99,
                    "reason": "generic application alias query",
                }
            }
        ),
    )
    monkeypatch.setattr(
        "tools.log_tools.get_execution_logs",
        lambda execution_id, tail=0, user_id="", org_code="": {
            "workflow_name": "Daily_claim_report_bot",
            "report": "Life Asia application unavailable.",
            "primary_error": {"error_message": "Life Asia application unavailable"},
        },
    )

    result = status_tools.check_workflow_status("may i know the status of life asia process")

    assert result["issue_label"] == "Life Asia"
    assert result["latest_execution_id"] == "22916"
    assert result["workflow_name"] == "Daily_claim_report_bot"
    assert "MG300W3_Surrender_Process" not in result["message"]
    assert "Life Asia" in result["message"]


def test_check_workflow_status_related_application_alias_fails_closed_without_safe_match(monkeypatch):
    class AliasClient:
        default_org_code = "AEGEMS"

        def resolve_cached_workflow_name(self, workflow_name, user_id="", org_code=""):
            return "MG300W3_Surrender_Process"

        def request(self, method, path, **kwargs):
            assert method == "GET"
            assert path == "/api/v1/failures/recent"
            return {
                "failures": [
                    {
                        "execution_id": "22916",
                        "workflow_name": "Daily_claim_report_bot",
                        "status": "Failure",
                        "error_message": "Upstream portal issue",
                        "failure_time": "2026-04-10T09:08:00+00:00",
                    }
                ]
            }

        def get_workflow_instance_by_id(self, execution_id):
            return {
                "id": execution_id,
                "automationRequestId": execution_id,
                "workflowName": "Daily_claim_report_bot",
                "status": "Failure",
                "createdDate": "2026-04-10T09:08:00+00:00",
            }

    client = AliasClient()

    monkeypatch.setattr(status_tools, "get_ae_client", lambda: client)
    monkeypatch.setattr(
        status_tools.related_app_registry,
        "get_related_applications",
        _registry_entries,
    )
    monkeypatch.setattr(
        status_tools.related_app_registry.llm_client,
        "chat",
        _mock_related_app_llm(
            {
                "life asia workflow": {
                    "decision": "alias",
                    "issue_type": "life_asia",
                    "confidence": 0.99,
                    "reason": "generic application alias query",
                }
            }
        ),
    )
    monkeypatch.setattr(
        "tools.log_tools.get_execution_logs",
        lambda execution_id, tail=0, user_id="", org_code="": {
            "workflow_name": "Daily_claim_report_bot",
            "report": "Portal issue without application marker.",
            "primary_error": {"error_message": "Portal issue"},
        },
    )

    result = status_tools.check_workflow_status("life asia workflow")

    assert result["status"] == "AMBIGUOUS_APPLICATION_QUERY"
    assert result["issue_label"] == "Life Asia"
    assert "did not guess" in result["message"]
    assert "MG300W3_Surrender_Process" not in result["message"]


def test_check_workflow_status_describes_tebt_issue_without_running_health_check(monkeypatch):
    client = _StatusClient("Complete")

    monkeypatch.setitem(status_tools.CONFIG, "ENABLE_RELATED_ISSUE_HEALTH_CHECK", True)
    monkeypatch.setitem(status_tools.CONFIG, "RELATED_ISSUE_HEALTH_CHECK_USE_ADMIN_SCOPE", True)
    monkeypatch.setattr(status_tools, "get_ae_client", lambda: client)
    monkeypatch.setattr(
        status_tools.related_app_registry,
        "get_related_applications",
        _registry_entries,
    )
    monkeypatch.setattr(
        "tools.log_tools.get_execution_logs",
        lambda execution_id, tail=0, user_id="", org_code="": {
            "report": "Process failed due to TEBT portal login failure.",
            "primary_error": {"error_message": "TEBT login issue"},
        },
    )

    result = status_tools.check_workflow_status("Claims_Process")

    assert "Status: Failed." in result["message"]
    assert "Likely cause: TEBT login issue." in result["message"]
    assert "Current TEBT health has not been checked yet." in result["message"]
    assert result["related_issue_check"]["workflow_name"] == "Life_Asia_Health_Check"
    assert result["related_issue_check"]["application_status"] == "not_checked"
    assert result["related_issue_check"]["health_check_run"] is False
    assert client.executed == []


def test_check_workflow_status_numeric_request_id_uses_related_issue_summary_without_running_health_check(monkeypatch):
    client = _StatusClient("Complete")

    monkeypatch.setitem(status_tools.CONFIG, "ENABLE_RELATED_ISSUE_HEALTH_CHECK", True)
    monkeypatch.setitem(status_tools.CONFIG, "RELATED_ISSUE_HEALTH_CHECK_USE_ADMIN_SCOPE", True)
    monkeypatch.setattr(status_tools, "get_ae_client", lambda: client)
    monkeypatch.setattr(
        status_tools.related_app_registry,
        "get_related_applications",
        _registry_entries,
    )
    monkeypatch.setattr(
        "tools.log_tools.get_execution_logs",
        lambda execution_id, tail=0, user_id="", org_code="": {
            "report": "Process failed due to TEBT portal login failure.",
            "primary_error": {"error_message": "TEBT login issue"},
        },
    )

    result = status_tools.check_workflow_status("2506738")

    assert result["is_single_search"] is True
    assert "Status: Failed." in result["message"]
    assert "Likely cause: TEBT login issue." in result["message"]
    assert "Current TEBT health has not been checked yet." in result["message"]
    assert result["related_issue_check"]["application_status"] == "not_checked"
    assert result["related_issue_check"]["health_check_run"] is False
    assert client.executed == []
