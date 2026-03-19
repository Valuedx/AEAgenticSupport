"""Tests for the MCP agent_analyze_logs tool — discovery and name resolution."""

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_ae_client():
    client = MagicMock()
    client.list_agents.return_value = [
        {"agentId": "agent-1", "agentName": "Agent One", "agentState": "CONNECTED", "uuid": "uuid-1"},
        {"agentId": "agent-2", "agentName": "Agent Two", "agentState": "STOPPED", "uuid": "uuid-2"},
    ]
    return client


@pytest.mark.asyncio
async def test_empty_agent_id_returns_running_agents(mock_ae_client):
    """Empty agent_id should list running agents for discovery."""
    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_ae_client):
        from mcp_server.tools.agent_tools import agent_analyze_logs

        result = await agent_analyze_logs(agent_id="")

    assert result["success"] is True
    assert result["discovery"] is True
    running = result["running_agents"]
    assert len(running) == 1
    assert running[0]["agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_nonexistent_agent_returns_error(mock_ae_client):
    """Non-existent agent name should return an error."""
    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_ae_client):
        from mcp_server.tools.agent_tools import agent_analyze_logs

        result = await agent_analyze_logs(agent_id="NonExistent")

    assert "not found" in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_stopped_agent_returns_state_error(mock_ae_client):
    """STOPPED agent should return a 'restart first' error."""
    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_ae_client):
        from mcp_server.tools.agent_tools import agent_analyze_logs

        result = await agent_analyze_logs(agent_id="agent-2")

    assert "restart" in result.get("error", "").lower()
    assert result["agent_state"] == "STOPPED"


@pytest.mark.asyncio
async def test_agent_without_uuid_returns_error():
    """Agent resolved but with no uuid should return a clear error."""
    client = MagicMock()
    client.list_agents.return_value = [
        {"agentId": "agent-x", "agentName": "No UUID Agent", "agentState": "RUNNING"},
    ]
    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=client):
        from mcp_server.tools.agent_tools import agent_analyze_logs

        result = await agent_analyze_logs(agent_id="agent-x")

    assert result["success"] is False
    assert "UUID" in result["error"]


@pytest.mark.asyncio
async def test_reversed_dates_returns_error(mock_ae_client):
    """from_date > to_date should return a validation error."""
    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_ae_client):
        from mcp_server.tools.agent_tools import agent_analyze_logs

        result = await agent_analyze_logs(
            agent_id="agent-1",
            from_date="2026-03-20T00:00:00",
            to_date="2026-03-15T00:00:00",
        )

    assert result["success"] is False
    assert "after" in result["error"].lower() or "swap" in result["error"].lower()
