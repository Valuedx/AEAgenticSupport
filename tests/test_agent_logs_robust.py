"""Robust tests for MCP agent_analyze_logs — multi-agent and edge cases."""

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_discovery_multiple_running_agents():
    """Empty ID with multiple running agents should list all of them."""
    client = MagicMock()
    client.list_agents.return_value = [
        {"agentId": "agent-1", "agentName": "Agent One", "agentState": "CONNECTED", "uuid": "u1"},
        {"agentId": "agent-2", "agentName": "Agent Two", "agentState": "STOPPED", "uuid": "u2"},
        {"agentId": "agent-3", "agentName": "Agent Three", "agentState": "ACTIVE", "uuid": "u3"},
    ]

    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=client):
        from mcp_server.tools.agent_tools import agent_analyze_logs

        result = await agent_analyze_logs(agent_id="")

    assert result["discovery"] is True
    names = [a["agent_name"] for a in result["running_agents"]]
    assert "Agent One" in names
    assert "Agent Three" in names
    assert "Agent Two" not in names


@pytest.mark.asyncio
async def test_case_insensitive_name_resolution():
    """Agent names should resolve case-insensitively."""
    client = MagicMock()
    client.list_agents.return_value = [
        {"agentId": "ID-123", "agentName": "TestAgent", "agentState": "CONNECTED", "uuid": "uu-123"},
    ]
    client.request_agent_debug_logs.return_value = {"id": "req-1"}
    client.get_agent_debug_logs.return_value = {"status": "FAILED"}

    with (
        patch("mcp_server.tools.agent_tools.get_ae_client", return_value=client),
        patch("time.sleep", return_value=None),
    ):
        from mcp_server.tools.agent_tools import agent_analyze_logs

        result = await agent_analyze_logs(agent_id="testagent")

    # Should have resolved the agent and attempted log extraction (with any epoch-ms values)
    client.request_agent_debug_logs.assert_called_once()
    args = client.request_agent_debug_logs.call_args[0]
    assert args[0] == "uu-123"
    assert isinstance(args[1], int) and args[1] > 0
    assert isinstance(args[2], int) and args[2] > 0
