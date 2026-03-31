import asyncio
from unittest.mock import MagicMock, patch

from mcp_server.tools.agent_tools import agent_analyze_logs


def test_agent_analyze_logs_waits_beyond_old_75_second_limit():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1507}

    poll_responses = [{"status": "NEW", "logFileLink": None} for _ in range(20)]
    poll_responses.append({"status": "COMPLETE", "logFileLink": "1507_ag_logdownload.zip"})
    mock_client.get_agent_debug_logs.side_effect = poll_responses
    mock_client.get.return_value = b"not-a-real-zip"

    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
        with patch("time.sleep", return_value=None):
            result = asyncio.run(agent_analyze_logs(agent_id="2963"))

    assert result["success"] is False
    assert "Failed to process log ZIP" in result["error"]
    assert mock_client.get_agent_debug_logs.call_count == 21
