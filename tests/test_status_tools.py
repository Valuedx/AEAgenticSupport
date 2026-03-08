from __future__ import annotations

from unittest.mock import patch

from tools import status_tools


def test_get_system_health_uses_modern_api_fallback():
    class StubClient:
        default_org_code = ""

        def __init__(self):
            self.calls = []

        def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs.get("use_rest_prefix", False)))
            if path == "/api/v1/system/health":
                return {
                    "status": "healthy",
                    "agents": [
                        {"name": "agent-prod-01", "status": "online"},
                        {"name": "agent-prod-02", "status": "offline"},
                    ],
                    "queue_depth": 7,
                    "active_executions": 3,
                }
            raise AssertionError(f"Unexpected path: {path}")

    client = StubClient()
    with patch("tools.status_tools.get_ae_client", return_value=client):
        result = status_tools.get_system_health()

    assert client.calls == [("GET", "/api/v1/system/health", False)]
    assert result["status"] == "healthy"
    assert result["agents_online"] == 1
    assert result["agents_offline"] == 1
    assert result["queue_depth"] == 7


def test_list_recent_failures_accepts_workflow_name_and_normalizes_modern_items():
    class StubClient:
        def get_workflow_instances(self, workflow_name, limit):
            assert workflow_name == "Policy_Renewal_Batch"
            assert limit == 20
            return [
                {
                    "execution_id": "EX-0042",
                    "workflow_name": workflow_name,
                    "status": "failed",
                    "agentName": "agent-prod-01",
                    "error": "FileNotFoundError: batch missing",
                    "completed_at": "2026-03-08T04:00:00+00:00",
                }
            ]

        def request(self, method, path, **kwargs):
            raise AssertionError(f"Unexpected fallback call: {method} {path}")

    client = StubClient()
    with patch("tools.status_tools.get_ae_client", return_value=client):
        result = status_tools.list_recent_failures(
            workflow_name="Policy_Renewal_Batch",
            limit=5,
        )

    assert result["total_count"] == 1
    assert result["failures"][0]["execution_id"] == "EX-0042"
    assert result["failures"][0]["workflow_name"] == "Policy_Renewal_Batch"
    assert result["failures"][0]["error_message"] == "FileNotFoundError: batch missing"


def test_list_recent_failures_handles_naive_iso_timestamps_from_recent_failures():
    class StubClient:
        default_org_code = ""

        def request(self, method, path, **kwargs):
            assert method == "GET"
            assert path == "/api/v1/failures/recent"
            return {
                "failures": [
                    {
                        "execution_id": "EX-1001",
                        "workflow_name": "Claims_Processing_Daily",
                        "status": "failed",
                        "error": "File missing",
                        "completed_at": "2026-03-08T10:00:00",
                    }
                ]
            }

    client = StubClient()
    with patch("tools.status_tools.get_ae_client", return_value=client):
        result = status_tools.list_recent_failures(limit=5)

    assert result["total_count"] == 1
    assert result["failures"][0]["execution_id"] == "EX-1001"
