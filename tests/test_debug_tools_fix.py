import io
import os
import zipfile
from unittest.mock import MagicMock, patch

from tools.agent_debug_tools import analyze_agent_logs


def _build_debug_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260325_20260330_log/aeagent",
            "\n".join(
                [
                    "2026-03-30T16:07:01.000+05:30 [main] INFO Starting agent",
                    "2026-03-30T16:07:05.000+05:30 [main] ERROR Connection reset by peer",
                    "java.net.SocketException: Connection reset",
                    "at com.example.Agent.run(Agent.java:42)",
                ]
            ),
        )
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260325_20260330_log/aeagent_2026-03-29_1.log.gz",
            b"",
        )
    return buf.getvalue()


def test_analyze_agent_logs_reads_current_aeagent_and_returns_per_file_summary():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1512}
    mock_client.get_agent_debug_logs.return_value = {
        "status": "COMPLETE",
        "logFileLink": "1512_ag_logdownload.zip",
    }
    mock_client._authorized_request.return_value = {"is_zip": True, "log_zip_content": _build_debug_zip()}

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "false"}):
        with patch("tools.agent_debug_tools.get_ae_client", return_value=mock_client):
            with patch("time.sleep", return_value=None):
                with patch("tools.agent_debug_tools.llm_client.chat") as mock_llm_chat:
                    result = analyze_agent_logs(agent_id="2963")

    assert result["success"] is True
    assert result["error_found"] is True
    assert result["files_analyzed"] >= 1
    assert any(item["filename"].endswith("aeagent") for item in result["per_file_summaries"])
    assert any(item["had_errors"] for item in result["per_file_summaries"])
    assert "Agent Error Summary" in result["report"]
    assert "Summary: This log contains 1 error block(s)." in result["report"]
    assert any(
        "Connection reset by peer" in item["summary"]
        for item in result["per_file_summaries"]
        if item["had_errors"]
    )
    assert result["message"].startswith("Found ")
    assert "File Details" in result["report"]
    mock_llm_chat.assert_not_called()


def test_analyze_agent_logs_uses_list_poll_fallback_when_id_endpoint_is_stale():
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
                "logFileLink": "/agent/debuglogs/download?id=1510",
            }
        ]

    mock_client.get_agent_debug_logs.side_effect = _poll_side_effect
    mock_client._authorized_request.return_value = {"is_zip": True, "log_zip_content": _build_debug_zip()}

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "false"}):
        with patch("tools.agent_debug_tools.get_ae_client", return_value=mock_client):
            with patch("time.sleep", return_value=None):
                result = analyze_agent_logs(agent_id="2963")

    assert result["success"] is True
    assert result["files_analyzed"] >= 1
    assert result["error_found"] is True


def test_analyze_agent_logs_prefers_direct_request_id_download_endpoint():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1514}
    mock_client.get_agent_debug_logs.return_value = {
        "status": "COMPLETE",
        "logFileLink": "1514_ag_logdownload.zip",
    }
    mock_client._authorized_request.return_value = {"is_zip": True, "log_zip_content": _build_debug_zip()}

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "false"}):
        with patch("tools.agent_debug_tools.get_ae_client", return_value=mock_client):
            with patch("time.sleep", return_value=None):
                result = analyze_agent_logs(agent_id="2963")

    assert result["success"] is True
    mock_client._authorized_request.assert_called_with("GET", "/agent/debuglogs/1514", use_rest_prefix=True)
