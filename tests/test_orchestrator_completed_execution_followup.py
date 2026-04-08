from unittest.mock import patch

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState


def test_completed_execution_fresh_run_request_rewrites_resubmit_to_trigger_workflow():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "resubmit_execution",
        {"execution_id": "22624"},
        {
            "success": False,
            "execution_id": "22624",
            "workflow_name": "Claim Process Chatbot",
            "status": "COMPLETED",
            "error": "Execution 22624 for Claim Process Chatbot is already completed, so resubmit is not allowed.",
            "hint": "Use Fresh Run to run this workflow again.",
        },
        False,
    )

    with patch(
        "agents.orchestrator.get_ae_client",
        return_value=type(
            "StubClient",
            (),
            {
                "get_cached_workflow_parameters": lambda self, workflow_name: [
                    {"name": "request_id", "required": True}
                ]
            },
        )(),
    ):
        with patch.object(
            orchestrator,
            "_extract_params_from_user_message",
            return_value={"request_id": "22624"},
        ):
            tool_name, tool_args = orchestrator._rewrite_completed_execution_followup(
                user_message="trigger new process",
                state=state,
                tool_name="ae.request.resubmit_from_start",
                tool_args={"request_id": "22624", "reason": "run again"},
            )

    assert tool_name == "trigger_workflow"
    assert tool_args == {
        "workflow_name": "Claim Process Chatbot",
        "parameters": {"request_id": "22624"},
    }


def test_completed_execution_resubmit_request_is_also_rewritten_to_trigger_workflow():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "restart_execution",
        {"execution_id": "22624"},
        {
            "success": False,
            "execution_id": "22624",
            "workflow_name": "Claim Process Chatbot",
            "status": "COMPLETED",
            "error": "Execution 22624 for Claim Process Chatbot is already completed, so restart is not allowed.",
            "hint": "Use Fresh Run to run this workflow again.",
        },
        False,
    )

    with patch(
        "agents.orchestrator.get_ae_client",
        return_value=type(
            "StubClient",
            (),
            {
                "get_cached_workflow_parameters": lambda self, workflow_name: [
                    {"name": "request_id", "required": True}
                ]
            },
        )(),
    ):
        with patch.object(
            orchestrator,
            "_extract_params_from_user_message",
            return_value={"request_id": "22624"},
        ):
            tool_name, tool_args = orchestrator._rewrite_completed_execution_followup(
                user_message="resubmit from start",
                state=state,
                tool_name="ae.request.resubmit_from_start",
                tool_args={"request_id": "22624"},
            )

    assert tool_name == "trigger_workflow"
    assert tool_args == {
        "workflow_name": "Claim Process Chatbot",
        "parameters": {"request_id": "22624"},
    }


def test_completed_execution_restart_request_is_rewritten_to_trigger_workflow():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "restart_execution",
        {"execution_id": "22624"},
        {
            "success": False,
            "execution_id": "22624",
            "workflow_name": "Claim Process Chatbot",
            "status": "COMPLETED",
            "error": "Execution 22624 for Claim Process Chatbot is already completed, so restart is not allowed.",
            "hint": "Use Fresh Run to run this workflow again.",
        },
        False,
    )

    with patch(
        "agents.orchestrator.get_ae_client",
        return_value=type(
            "StubClient",
            (),
            {
                "get_cached_workflow_parameters": lambda self, workflow_name: [
                    {"name": "execution_id", "required": True}
                ]
            },
        )(),
    ):
        with patch.object(
            orchestrator,
            "_extract_params_from_user_message",
            return_value={"execution_id": "22624"},
        ):
            tool_name, tool_args = orchestrator._rewrite_completed_execution_followup(
                user_message="restart again",
                state=state,
                tool_name="restart_execution",
                tool_args={"execution_id": "22624"},
            )

    assert tool_name == "trigger_workflow"
    assert tool_args == {
        "workflow_name": "Claim Process Chatbot",
        "parameters": {"execution_id": "22624"},
    }


def test_explicit_different_execution_id_does_not_rewrite_to_trigger_workflow():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "resubmit_execution",
        {"execution_id": "2582126"},
        {
            "success": False,
            "execution_id": "2582126",
            "workflow_name": "LogExtractionAndRecognition",
            "status": "COMPLETED",
            "error": "Execution 2582126 for LogExtractionAndRecognition is already completed, so resubmit is not allowed.",
            "hint": "Use Fresh Run to run this workflow again.",
        },
        False,
    )

    tool_name, tool_args = orchestrator._rewrite_completed_execution_followup(
        user_message="2582540 resubmit again",
        state=state,
        tool_name="resubmit_execution",
        tool_args={"execution_id": "2582540"},
    )

    assert tool_name == "resubmit_execution"
    assert tool_args == {"execution_id": "2582540"}


def test_failed_execution_generic_retrigger_rewrites_resubmit_to_restart():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "check_workflow_status",
        {"workflow_name": "timesheet_report_generation_v5"},
        {
            "success": True,
            "execution_id": "2611283",
            "workflow_name": "timesheet_report_generation_v5",
            "status": "Failure",
        },
        True,
    )

    tool_name, tool_args = orchestrator._rewrite_failed_execution_followup(
        user_message="retrigger this bot",
        state=state,
        tool_name="resubmit_execution",
        tool_args={"execution_id": "2611283"},
    )

    assert tool_name == "restart_execution"
    assert tool_args == {
        "execution_id": "2611283",
        "workflow_name": "timesheet_report_generation_v5",
    }


def test_failed_execution_rewrite_prefers_latest_status_execution_id_over_older_history():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "restart_execution",
        {"execution_id": "2611427"},
        {
            "success": False,
            "execution_id": "2611427",
            "workflow_name": "timesheet_report_generation_v5",
            "status": "FAILED",
        },
        False,
    )
    state.log_tool_call(
        "check_workflow_status",
        {"workflow_name": "timesheet_report_generation_v5"},
        {
            "success": True,
            "workflow_name": "timesheet_report_generation_v5",
            "latest_status": "Failure",
            "latest_execution_id": "2612306",
            "latest_execution": {
                "id": "2612306",
                "bot_name": "timesheet_report_generation_v5",
                "status": "Failure",
            },
        },
        True,
    )

    tool_name, tool_args = orchestrator._rewrite_failed_execution_followup(
        user_message="restart workflow",
        state=state,
        tool_name="restart_execution",
        tool_args={},
    )

    assert tool_name == "restart_execution"
    assert tool_args == {
        "execution_id": "2612306",
        "workflow_name": "timesheet_report_generation_v5",
    }


def test_retry_arg_alignment_overrides_stale_retry_id_when_user_did_not_specify_one():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "check_workflow_status",
        {"workflow_name": "timesheet_report_generation_v5"},
        {
            "success": True,
            "workflow_name": "timesheet_report_generation_v5",
            "latest_status": "Failure",
            "latest_execution_id": "2612306",
            "latest_execution": {
                "id": "2612306",
                "bot_name": "timesheet_report_generation_v5",
                "status": "Failure",
            },
        },
        True,
    )

    tool_name, tool_args = orchestrator._align_retry_tool_args_with_recent_failed_context(
        user_message="restart workflow",
        state=state,
        tool_name="restart_execution",
        tool_args={"execution_id": "2611427", "workflow_name": "timesheet_report_generation_v5"},
    )

    assert tool_name == "restart_execution"
    assert tool_args == {
        "execution_id": "2612306",
        "workflow_name": "timesheet_report_generation_v5",
    }


def test_retry_arg_alignment_keeps_explicit_requested_execution_id():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "check_workflow_status",
        {"workflow_name": "timesheet_report_generation_v5"},
        {
            "success": True,
            "workflow_name": "timesheet_report_generation_v5",
            "latest_status": "Failure",
            "latest_execution_id": "2612306",
            "latest_execution": {
                "id": "2612306",
                "bot_name": "timesheet_report_generation_v5",
                "status": "Failure",
            },
        },
        True,
    )

    tool_name, tool_args = orchestrator._align_retry_tool_args_with_recent_failed_context(
        user_message="restart execution 2611427",
        state=state,
        tool_name="restart_execution",
        tool_args={"execution_id": "2611427", "workflow_name": "timesheet_report_generation_v5"},
    )

    assert tool_name == "restart_execution"
    assert tool_args == {
        "execution_id": "2611427",
        "workflow_name": "timesheet_report_generation_v5",
    }


def test_failed_execution_explicit_resubmit_from_start_keeps_resubmit():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "check_workflow_status",
        {"workflow_name": "timesheet_report_generation_v5"},
        {
            "success": True,
            "execution_id": "2611283",
            "workflow_name": "timesheet_report_generation_v5",
            "status": "Failure",
        },
        True,
    )

    tool_name, tool_args = orchestrator._rewrite_failed_execution_followup(
        user_message="resubmit from start",
        state=state,
        tool_name="resubmit_execution",
        tool_args={"execution_id": "2611283", "from_failure_point": False},
    )

    assert tool_name == "resubmit_execution"
    assert tool_args == {"execution_id": "2611283", "from_failure_point": False}


def test_failed_execution_trigger_workflow_followup_rewrites_to_resubmit():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "check_workflow_status",
        {"workflow_name": "timesheet_report_generation_v5"},
        {
            "success": True,
            "execution_id": "2611752",
            "workflow_name": "timesheet_report_generation_v5",
            "status": "Failure",
        },
        True,
    )

    tool_name, tool_args = orchestrator._rewrite_trigger_followup_from_failed_context(
        user_message="resubmit request again i have added this file",
        state=state,
        tool_name="trigger_workflow",
        tool_args={"workflow_name": "resubmit request again i have added this file", "parameters": {}},
    )

    assert tool_name == "resubmit_execution"
    assert tool_args == {"execution_id": "2611752", "from_failure_point": True}


def test_failed_execution_trigger_workflow_followup_rewrites_to_restart():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.log_tool_call(
        "check_workflow_status",
        {"workflow_name": "timesheet_report_generation_v5"},
        {
            "success": True,
            "execution_id": "2611752",
            "workflow_name": "timesheet_report_generation_v5",
            "status": "Failure",
        },
        True,
    )

    tool_name, tool_args = orchestrator._rewrite_trigger_followup_from_failed_context(
        user_message="retry this bot again",
        state=state,
        tool_name="trigger_workflow",
        tool_args={"workflow_name": "timesheet_report_generation_v5", "parameters": {}},
    )

    assert tool_name == "restart_execution"
    assert tool_args == {
        "execution_id": "2611752",
        "workflow_name": "timesheet_report_generation_v5",
    }
