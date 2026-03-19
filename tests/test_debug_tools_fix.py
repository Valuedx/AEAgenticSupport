"""Tests for the in-app analyze_agent_logs tool — discovery + extraction contract."""

import pytest
from unittest.mock import MagicMock, patch, ANY


def test_empty_agent_id_returns_running_agents():
    """Empty agent_id should list running agents for discovery."""
    client = MagicMock()
    client.list_agents.return_value = [
        {"agentId": "123", "agentName": "Test Agent", "agentState": "RUNNING", "uuid": "uuid-123"},
    ]

    with patch("tools.agent_debug_tools.get_ae_client", return_value=client):
        from tools.agent_debug_tools import analyze_agent_logs

        result = analyze_agent_logs(agent_id="")

    assert result["success"] is True
    assert result["discovery"] is True
    assert result["count"] == 1
    assert result["running_agents"][0]["agent_id"] == "123"


def test_agent_without_uuid_returns_error():
    """Agent with no uuid field should return a clear error."""
    client = MagicMock()
    client.list_agents.return_value = [
        {"agentId": "456", "agentName": "No UUID", "agentState": "RUNNING"},
    ]

    with patch("tools.agent_debug_tools.get_ae_client", return_value=client):
        from tools.agent_debug_tools import analyze_agent_logs

        result = analyze_agent_logs(agent_id="456")

    assert result["success"] is False
    assert "UUID" in result["error"]


def test_reversed_dates_returns_error():
    """from_date after to_date should return a validation error."""
    client = MagicMock()
    client.list_agents.return_value = [
        {"agentId": "789", "agentName": "Agent", "agentState": "RUNNING", "uuid": "uuid-789"},
    ]

    with patch("tools.agent_debug_tools.get_ae_client", return_value=client):
        from tools.agent_debug_tools import analyze_agent_logs

        result = analyze_agent_logs(
            agent_id="789",
            from_date="2026-03-20T00:00:00",
            to_date="2026-03-15T00:00:00",
        )

    assert result["success"] is False
    assert "after" in result["error"].lower() or "swap" in result["error"].lower()
