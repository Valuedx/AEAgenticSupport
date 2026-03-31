import asyncio
import io
import zipfile
from unittest.mock import MagicMock, patch

from mcp_server.tools.agent_tools import agent_analyze_logs


def _build_minimal_log_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("aeagent", "INFO ok\nERROR from fallback poll path\n")
    return buf.getvalue()


def test_agent_analyze_logs_uses_list_poll_fallback_when_id_endpoint_is_stale():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1510}

    def _poll_side_effect(request_id=""):
        if request_id:
            return {"id": 1510, "status": "NEW", "logFileLink": None}
        return [
            {
                "id": 1510,
                "status": "COMPLETE",
                "logFileLink": "1510_ag_logdownload13943812384840715511.zip",
                "agentInfoDto": {
                    "id": 2963,
                    "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
                    "agentName": "adarsh@GPSR61UB-00105",
                    "agentState": "RUNNING",
                },
            }
        ]

    mock_client.get_agent_debug_logs.side_effect = _poll_side_effect
    mock_client.get.return_value = _build_minimal_log_zip()

    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
        with patch("time.sleep", return_value=None):
            result = asyncio.run(
                agent_analyze_logs(
                    agent_id="2963",
                    from_date="2026-03-26T15:52:00",
                    to_date="2026-03-30T15:52:00",
                )
            )

    assert result["success"] is True
    assert result["request_id"] == 1510
    assert result["zip_file_name"] == "1510_ag_logdownload13943812384840715511.zip"
    assert result["error_found"] is True
