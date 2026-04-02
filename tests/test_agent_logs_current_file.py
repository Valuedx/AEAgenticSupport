import asyncio
import io
import os
import zipfile
from unittest.mock import MagicMock, patch

from mcp_server.tools.agent_tools import agent_analyze_logs


def _build_zip_with_current_aeagent_file() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260325_20260330_log/aeagent",
            "INFO boot ok\nERROR Current day failure\nstack trace line\n",
        )
    return buf.getvalue()


def _build_zip_with_timestamped_current_aeagent_file() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260325_20260330_log/aeagent.log",
            (
                "2026-03-30T12:17:37.000+00:00 INFO boot ok\n"
                "2026-03-30T12:17:38.125+00:00 ERROR Failed to upload a file on AE server\n"
                "2026-03-30T12:17:38.127+00:00 INFO File upload attempt: 2\n"
            ),
        )
    return buf.getvalue()


def _build_zip_with_mixed_date_results() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260325_20260330_log/aeagent.log.20260330",
            (
                "2026-03-30T09:00:00.000+00:00 INFO boot ok\n"
                "2026-03-30T09:00:01.000+00:00 INFO health check passed\n"
            ),
        )
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260325_20260330_log/aeagent.log.20260329",
            (
                "2026-03-29T12:17:37.000+00:00 INFO boot ok\n"
                "2026-03-29T12:17:38.125+00:00 ERROR Failed to upload a file on AE server\n"
                "2026-03-29T12:17:38.127+00:00 INFO File upload attempt: 2\n"
            ),
        )
    return buf.getvalue()


def _build_zip_with_today_txt_log_no_errors() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260328_20260331_log/aeagent.txt",
            (
                "INFO Agent boot completed\n"
                "INFO Heartbeat sent successfully\n"
                "INFO Queue depth is normal\n"
                "INFO Worker pool initialized\n"
                "INFO License validation passed\n"
                "INFO Control channel healthy\n"
                "INFO Poll cycle completed\n"
                "INFO No pending retries\n"
                "INFO Session cleanup finished\n"
                "INFO Agent health is stable\n"
                "INFO Monitoring loop sleeping\n"
            ),
        )
    return buf.getvalue()


def _build_zip_with_out_of_range_today_txt_log() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260328_20260331_log/aeagent.txt",
            (
                "2026-03-31T09:00:00.000+00:00 INFO Agent boot completed\n"
                "2026-03-31T09:00:01.000+00:00 INFO Heartbeat sent successfully\n"
                "2026-03-31T09:00:02.000+00:00 INFO Monitoring loop sleeping\n"
            ),
        )
    return buf.getvalue()


def _build_zip_with_repeated_errors_same_day() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        lines = []
        for attempt in range(1, 7):
            lines.extend(
                [
                    f"2026-03-30T23:57:{attempt:02d}.986+00:00 [WorkflowHandler-9][Request::2580887] ERROR c.a.a.u.WorkflowUtil:192 - Failed to upload a file on AE server",
                    f"2026-03-30T23:57:{attempt:02d}.986+00:00 [WorkflowHandler-9][Request::2580887] ERROR c.a.a.u.WorkflowUtil:196 - Error details: {{\"message\":\"File with extension [.txt] is not allowed\"}}",
                    f"2026-03-30T23:57:{attempt:02d}.987+00:00 [WorkflowHandler-9][Request::2580887] INFO  c.a.a.u.WorkflowUtil:147 - FileId: null, File upload attempt: {attempt}",
                ]
            )
        zf.writestr(
            "adarsh@GPSR61UB-00105_20260325_20260330_log/aeagent.log.20260330",
            "\n".join(lines),
        )
    return buf.getvalue()


def test_agent_analyze_logs_includes_current_aeagent_file_without_log_extension():
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
    mock_client.get_agent_debug_logs.return_value = {
        "status": "COMPLETE",
        "logFileLink": "1507_ag_logdownload.zip",
    }
    mock_client.get.return_value = _build_zip_with_current_aeagent_file()

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "false"}):
        with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
            with patch("time.sleep", return_value=None):
                result = asyncio.run(agent_analyze_logs(agent_id="2963"))

    assert result["success"] is True
    assert any(entry["filename"].endswith("aeagent") for entry in result["logs"])
    assert result["error_found"] is True


def test_agent_analyze_logs_does_not_block_on_ai_summary_by_default():
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
    mock_client.get.return_value = _build_zip_with_current_aeagent_file()

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "false"}):
        with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
            with patch("mcp_server.tools.agent_tools.llm_client.chat") as mock_llm_chat:
                with patch("time.sleep", return_value=None):
                    result = asyncio.run(agent_analyze_logs(agent_id="2963"))

    assert result["success"] is True
    assert result["error_found"] is True
    mock_llm_chat.assert_not_called()


def test_agent_analyze_logs_prefers_direct_request_id_download_endpoint():
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
    mock_client.get.return_value = _build_zip_with_current_aeagent_file()

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "false"}):
        with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
            with patch("time.sleep", return_value=None):
                result = asyncio.run(agent_analyze_logs(agent_id="2963"))

    assert result["success"] is True
    mock_client.get.assert_called_once_with("/agent/debuglogs/1514", use_rest=True)


def test_agent_analyze_logs_groups_current_file_errors_by_log_line_date_and_uses_warning_icon():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1515}
    mock_client.get_agent_debug_logs.return_value = {
        "status": "COMPLETE",
        "logFileLink": "1515_ag_logdownload.zip",
    }
    mock_client.get.return_value = _build_zip_with_timestamped_current_aeagent_file()

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "false"}):
        with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
            with patch("time.sleep", return_value=None):
                result = asyncio.run(agent_analyze_logs(agent_id="2963"))

    assert result["success"] is True
    assert "Unknown Date" not in result["report"]
    assert "2026-03-30" in result["report"]
    assert "### 📅 Date-wise Log Review" in result["report"]
    assert "- ⚠️ Issues detected in 1 file(s) for this date (1 errors total)." in result["report"]


def test_agent_analyze_logs_mentions_dates_with_no_issues_detected():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1516}
    mock_client.get_agent_debug_logs.return_value = {
        "status": "COMPLETE",
        "logFileLink": "1516_ag_logdownload.zip",
    }
    mock_client.get.return_value = _build_zip_with_mixed_date_results()

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "false"}):
        with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
            with patch("mcp_server.tools.agent_tools.llm_client.chat", return_value="Healthy tail summary for this file.") as mock_llm:
                with patch("time.sleep", return_value=None):
                    result = asyncio.run(
                        agent_analyze_logs(
                            agent_id="2963",
                            from_date="2026-03-29T00:00:00",
                            to_date="2026-03-30T23:59:59",
                        )
                    )

    assert result["success"] is True
    assert "#### 📅 2026-03-30" in result["report"]
    assert "- ✅ No issues detected in 1 file(s) checked for this date." in result["report"]
    assert "- Good health summary from the last 10 log lines:" in result["report"]
    assert "Healthy tail summary for this file." in result["report"]
    assert "#### 📅 2026-03-29" in result["report"]
    assert "- ⚠️ Issues detected in 1 file(s) for this date (1 errors total)." in result["report"]
    mock_llm.assert_called_once()


def test_agent_analyze_logs_treats_aeagent_txt_as_today_and_shows_last_10_lines_summary_for_clean_date():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1517}
    mock_client.get_agent_debug_logs.return_value = {
        "status": "COMPLETE",
        "logFileLink": "1517_ag_logdownload.zip",
    }
    mock_client.get.return_value = _build_zip_with_today_txt_log_no_errors()

    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
        with patch("mcp_server.tools.agent_tools.llm_client.chat", return_value="Agent looks healthy based on the latest 10 rows.") as mock_llm:
            with patch("time.sleep", return_value=None):
                result = asyncio.run(
                    agent_analyze_logs(
                        agent_id="2963",
                        from_date="2026-03-28T00:00:00",
                        to_date="2026-03-31T23:59:59",
                    )
                )

    assert result["success"] is True
    assert result["error_found"] is False
    assert "Unknown Date" not in result["report"]
    assert "#### 📅 2026-03-31" in result["report"]
    assert "**adarsh@GPSR61UB-00105_20260328_20260331_log/aeagent.txt** summary:" in result["report"]
    assert "Agent looks healthy based on the latest 10 rows." in result["report"]
    mock_llm.assert_called_once()


def test_agent_analyze_logs_ignores_live_txt_lines_outside_requested_date_range():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1518}
    mock_client.get_agent_debug_logs.return_value = {
        "status": "COMPLETE",
        "logFileLink": "1518_ag_logdownload.zip",
    }
    mock_client.get.return_value = _build_zip_with_out_of_range_today_txt_log()

    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
        with patch("time.sleep", return_value=None):
            result = asyncio.run(
                agent_analyze_logs(
                    agent_id="2963",
                    from_date="2026-03-28T00:00:00",
                    to_date="2026-03-30T23:59:59",
                )
            )

    assert result["success"] is True
    assert result["error_found"] is False
    assert "2026-03-31" not in result["report"]
    assert "No log files were found" in result["report"]


def test_agent_analyze_logs_surfaces_recurring_error_summary_when_many_error_lines_exist():
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {
            "agentId": "2963",
            "uuid": "385f365f-deb0-4a02-ba81-e01936816786",
            "agentName": "adarsh@GPSR61UB-00105",
            "agentState": "RUNNING",
        }
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": 1519}
    mock_client.get_agent_debug_logs.return_value = {
        "status": "COMPLETE",
        "logFileLink": "1519_ag_logdownload.zip",
    }
    mock_client.get.return_value = _build_zip_with_repeated_errors_same_day()

    ai_markdown = (
        "Summary:\nRepeated file upload failures were detected across multiple attempts.\n\n"
        "Suggested Actions:\n"
        "- Validate the workflow file extension mapping in AutomationEdge.\n"
        "- Review the upload configuration and allowed file-type policy."
    )

    with patch.dict(os.environ, {"AE_AGENT_LOG_AI_SUMMARY_ENABLED": "true"}):
        with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
            with patch("mcp_server.tools.agent_tools.llm_client.chat", return_value=ai_markdown) as mock_llm:
                with patch("time.sleep", return_value=None):
                    result = asyncio.run(agent_analyze_logs(agent_id="2963"))

    assert result["success"] is True
    assert result["total_error_lines"] == 12
    assert "### Agent Error Summary" in result["report"]
    assert "### 🤖 AI Diagnostic Summary" in result["report"]
    assert "Suggested Actions:" in result["report"]
    assert "Total error lines detected: 12" in result["report"]
    assert "Summary: This log contains 12 error line(s)." in result["report"]
    assert "6x Failed to upload a file on AE server" in result["report"]
    assert "6x Error details: {\"message\":\"File with extension [.txt] is not allowed\"}" in result["report"]
    assert "Chronological error occurrences (12):" in result["report"]
    assert result["report"].count("`2026-03-30T23:57:") == 12
    assert result["message"].startswith("Found 12 error lines across 1 file(s).")
    assert "Top issues:" in result["message"]
    mock_llm.assert_called_once()
