from tools import dependency_tools


class _StubClient:
    def __init__(self, payload):
        self.payload = payload

    def get_workflow_details(self, workflow_name):
        return self.payload


def test_get_workflow_config_handles_none_payload(monkeypatch):
    monkeypatch.setattr(
        dependency_tools,
        "get_ae_client",
        lambda: _StubClient(None),
    )

    result = dependency_tools.get_workflow_config("timesheet_report_generation_v5")

    assert result == {
        "workflow_name": "timesheet_report_generation_v5",
        "input_paths": [],
        "output_paths": [],
        "timeout_minutes": None,
        "retry_count": None,
        "parameters": {},
    }


def test_get_schedule_info_handles_none_payload(monkeypatch):
    monkeypatch.setattr(
        dependency_tools,
        "get_ae_client",
        lambda: _StubClient(None),
    )

    result = dependency_tools.get_schedule_info("timesheet_report_generation_v5")

    assert result == {
        "workflow_name": "timesheet_report_generation_v5",
        "cron_expression": None,
        "next_run": None,
        "last_run": None,
        "timezone": None,
        "enabled": True,
    }
