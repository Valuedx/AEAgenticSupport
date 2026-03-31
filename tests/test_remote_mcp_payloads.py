from agents.orchestrator import Orchestrator
from tools.mcp_tools import _extract_remote_error_message, _normalize_remote_call_result


def test_normalize_remote_call_result_unwraps_nested_result_dict():
    raw = {
        "structuredContent": {
            "result": {
                "success": True,
                "report": "### Log Analysis\nDetailed findings from the agent logs.",
                "message": "analysis complete",
            }
        },
        "content": [{"type": "text", "text": "fallback text"}],
        "isError": False,
    }

    payload = _normalize_remote_call_result(raw)

    assert payload["success"] is True
    assert payload["report"].startswith("### Log Analysis")
    assert payload["message"] == "analysis complete"
    assert payload["_mcp_content"][0]["text"] == "fallback text"


def test_extract_remote_error_message_reads_nested_result_error():
    payload = {
        "result": {
            "success": False,
            "error": "Agent is not running",
            "agent_name": "adarsh@GPSR61UB-00105",
        },
        "_mcp_is_error": False,
    }

    assert _extract_remote_error_message(payload) == "Agent is not running"


def test_normalize_remote_call_result_parses_content_only_json_report():
    raw = {
        "content": [
            {
                "type": "text",
                "text": (
                    '{'
                    '"success": true, '
                    '"agent_name": "adarsh@GPSR61UB-00105", '
                    '"request_id": 1510, '
                    '"report": "### Log Analysis for Agent: adarsh@GPSR61UB-00105\\n'
                    '**Files Analyzed:** 5\\n'
                    '### ✅ Clean Trace", '
                    '"message": "Log analysis completed."'
                    '}'
                ),
            }
        ],
        "isError": False,
    }

    payload = _normalize_remote_call_result(raw)

    assert payload["success"] is True
    assert payload["agent_name"] == "adarsh@GPSR61UB-00105"
    assert payload["request_id"] == 1510
    assert payload["report"].startswith("### Log Analysis for Agent")
    assert payload["_mcp_content"][0]["type"] == "text"


def test_orchestrator_format_completion_message_recovers_content_only_json_report():
    orchestrator = Orchestrator()
    data = {
        "_mcp_content": [
            {
                "type": "text",
                "text": (
                    '{'
                    '"success": true, '
                    '"report": "### Log Analysis\\nDetailed findings from the agent logs.", '
                    '"message": "done"'
                    '}'
                ),
            }
        ]
    }

    response = orchestrator._format_completion_message("ae.agent.analyze_logs", data)

    assert response.startswith("### Log Analysis")


def test_build_action_failure_response_preserves_structured_agent_log_timeout_details():
    orchestrator = Orchestrator()

    response = orchestrator._build_action_failure_response(
        action_tool="ae.agent.analyze_logs",
        action_args={"agent_id": "2963"},
        error_text="Timed out waiting for logs to be ready",
        error_data={
            "success": False,
            "error": "Timed out waiting for logs to be ready",
            "agent_name": "adarsh@GPSR61UB-00105",
            "agent_state": "RUNNING",
            "request_id": 1512,
            "last_status": "NEW",
            "waited_seconds": 180,
        },
    )

    assert "Timed out waiting for logs to be ready" in response
    assert "Request ID" in response
    assert "1512" in response
    assert "Server Extraction Status" in response
    assert "Waited" in response
    assert "Recommended troubleshooting steps" not in response


def test_format_completion_message_includes_hint_for_completed_restart_rejection():
    orchestrator = Orchestrator()

    response = orchestrator._format_completion_message(
        "restart_execution",
        {
            "success": False,
            "error": "Execution `22024` for **Claims_Processing_Daily** is already completed, so restart is not allowed.",
            "hint": "Use Fresh Run to run this workflow again.",
            "execution_id": "22024",
            "workflow_name": "Claims_Processing_Daily",
            "status": "COMPLETED",
        },
    )

    assert "Unable to Complete Action" in response
    assert "Fresh Run" in response
    assert "22024" in response


def test_build_action_failure_response_for_completed_restart_skips_retry_prompt():
    orchestrator = Orchestrator()

    response = orchestrator._build_action_failure_response(
        action_tool="restart_execution",
        action_args={"workflow_name": "Claims_Processing_Daily"},
        error_text="Execution `22024` for **Claims_Processing_Daily** is already completed, so restart is not allowed.",
    )

    assert "Fresh Run" in response
    assert "Would you like me to retry" not in response
