from unittest.mock import patch

from agents.orchestrator import Orchestrator
from tools.mcp_tools import _extract_remote_error_message, _normalize_remote_call_result
from state.conversation_state import ConversationState


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


def test_orchestrator_format_completion_message_recovers_local_mcp_json_string_result():
    orchestrator = Orchestrator()
    data = {
        "result": (
            '{'
            '"success": true, '
            '"action": "restart_failed", '
            '"request_id": "22880", '
            '"new_request_id": null, '
            '"status": null, '
            '"reason": "Input file placed, restarting workflow", '
            '"raw": {'
            '"message": "Request [22880] has been restarted", '
            '"success": true'
            '}'
            '}'
        )
    }

    response = orchestrator._format_completion_message("ae.request.restart_failed", data)

    assert "Action Completed" in response
    assert "22880" in response
    assert "restarted" in response.lower()
    assert '"action": "restart_failed"' not in response


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


def test_format_completion_message_shows_related_system_check_separately():
    orchestrator = Orchestrator()

    with patch("agents.orchestrator.llm_client.chat", return_value=""):
        response = orchestrator._format_completion_message(
            "restart_execution",
            {
                "success": True,
                "message": "Execution 2615124 has been restarted successfully.",
                "execution_id": "2615124",
                "workflow_name": "Daily_claim_report_bot",
                "status": "QUEUED",
                "health_gate": {
                    "issue_label": "Life Asia",
                    "health_gate_passed": True,
                },
            },
        )

    assert "Execution 2615124 has been restarted successfully." in response
    assert "Related system check" in response
    assert "Life Asia is healthy." in response


def test_format_completion_message_skips_suggestions_after_ticket_creation():
    orchestrator = Orchestrator()

    with patch("agents.orchestrator.llm_client.chat") as mock_chat:
        response = orchestrator._format_completion_message(
            "create_support_ticket",
            {
                "success": True,
                "ticket_id": "HDFC-1001",
                "message": "Support ticket HDFC-1001 has been created successfully.",
            },
        )

    assert "Action Completed" in response
    assert "Support ticket HDFC-1001 has been created successfully." in response
    assert "Here are a few options" not in response
    assert "Suggested next actions" not in response
    mock_chat.assert_not_called()


def test_format_completion_message_prompt_blocks_unavailable_ticket_status_suggestions():
    orchestrator = Orchestrator()
    captured = {}

    def fake_chat(prompt, **kwargs):
        captured["prompt"] = prompt
        return "- Review the workflow outcome.\n- Share the result with the requester."

    with patch("agents.orchestrator.llm_client.chat", side_effect=fake_chat):
        response = orchestrator._format_completion_message(
            "trigger_workflow",
            {
                "success": True,
                "workflow_name": "timesheet_report_generation_v5",
                "request_id": "2609419",
                "status": "Complete",
                "message": "The timesheet file has been shared with you. Kindly check your mailbox.",
            },
        )

    assert "Action Completed" in response
    assert "Do not hardcode workflow names" in captured["prompt"]
    assert "Never suggest checking ticket status" in captured["prompt"]
    assert "After a ticket is created, do not suggest checking status or updates" in captured["prompt"]


def test_format_completion_message_failure_suggests_support_ticket_in_plain_language():
    orchestrator = Orchestrator()

    response = orchestrator._format_completion_message(
        "t4_execute_and_poll",
        {
            "success": False,
            "error": "No automation agent was available to start the workflow.",
            "status": "NO_AGENT",
            "assigned_agents": [
                {"agentName": "agent-timesheet-01", "agentState": "STOPPED"},
            ],
        },
    )

    assert "Unable to Complete Action" in response
    assert "support ticket" in response.lower()
    assert "create_support_ticket" not in response


def test_recent_tool_result_fallback_prefers_new_request_id_over_old_request_id():
    state = ConversationState()
    state.log_tool_call(
        "resubmit_execution",
        {"execution_id": "2611582"},
        {
            "success": True,
            "message": "Execution has been resubmitted.",
            "request_id": "2611582",
            "new_request_id": "2611600",
            "workflow_name": "timesheet_report_generation_v5",
            "status": "New",
        },
        True,
    )

    response = Orchestrator._build_recent_tool_result_fallback_response(state)

    assert "2611600" in response
    assert "2611582" not in response


def test_has_hold_resume_reminder_detects_hold_message():
    text = (
        "The request is on hold.\n"
        "Reply **continue** to review it again, or **drop it** to cancel."
    )
    assert Orchestrator._has_hold_resume_reminder(text) is True


def test_has_hold_resume_reminder_false_for_regular_text():
    text = "I can create a support ticket now."
    assert Orchestrator._has_hold_resume_reminder(text) is False
