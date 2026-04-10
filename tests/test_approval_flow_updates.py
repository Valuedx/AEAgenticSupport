from unittest.mock import MagicMock, patch

from agents.approval_gate import ApprovalIntent, ApprovalIntentResult, ApprovalRequest
from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationPhase, ConversationState
from tools.base import ToolResult
from tools.registry import tool_registry


def _build_pending_approval_state() -> ConversationState:
    state = ConversationState()
    state.conversation_id = "conv-approval"
    state.user_id = "webchat:kirtibala.gujar"
    state.phase = ConversationPhase.AWAITING_APPROVAL
    state.pending_action = {
        "tool": "ae.agent.analyze_logs",
        "args": {
            "agent_id": "2963",
            "from_date": "2026-03-30T00:00:00",
            "to_date": "2026-04-01T14:52:00",
        },
        "tier": "high_risk",
        "authorized_users": [],
        "request_id": "apprv-old",
    }
    state.pending_action_summary = "ae.agent.analyze_logs on 2963"
    return state


def test_handle_approval_response_updates_pending_log_dates_from_natural_language():
    orchestrator = Orchestrator()
    state = _build_pending_approval_state()
    tracker = MagicMock()

    expected_args = {
        "agent_id": "2963",
        "from_date": "2026-03-29T00:00:00",
        "to_date": "2026-04-01T14:52:00",
    }
    refreshed_request = ApprovalRequest(
        tool_name="ae.agent.analyze_logs",
        tool_params=expected_args,
        tier="high_risk",
        reason="Tool requires approval.",
        summary="ae.agent.analyze_logs on 2963",
        request_id="apprv-new",
    )

    with patch.object(
        orchestrator.approval_gate,
        "classify_approval_turn",
        return_value=ApprovalIntentResult(
            intent=ApprovalIntent.CLARIFY,
            confidence=0.95,
            reason="clarification_question",
        ),
    ), patch.object(
        orchestrator.approval_gate,
        "create_approval_request",
        return_value=refreshed_request,
    ) as mock_create, patch.object(
        orchestrator.approval_gate,
        "log_decision",
    ) as mock_log:
        response = orchestrator._handle_approval_response(
            "from date use 29 march",
            state,
            tracker,
        )

    assert "Updated the pending action with your requested changes." in response
    assert "From date: `2026-03-29T00:00:00`" in response
    assert state.phase == ConversationPhase.AWAITING_APPROVAL
    assert state.pending_action is not None
    assert state.pending_action["args"]["from_date"] == "2026-03-29T00:00:00"
    assert state.pending_action["request_id"] == "apprv-new"
    assert state.pending_action_summary == "ae.agent.analyze_logs on 2963"
    mock_log.assert_called_once_with(
        "conv-approval",
        "apprv-old",
        "CANCELLED",
        "webchat:kirtibala.gujar",
    )
    assert mock_create.call_args.args[3]["from_date"] == "2026-03-29T00:00:00"


def test_handle_approval_response_reject_uses_request_id_and_cleans_up_state():
    orchestrator = Orchestrator()
    state = _build_pending_approval_state()
    tracker = MagicMock()

    with patch.object(
        orchestrator.approval_gate,
        "classify_approval_turn",
        return_value=ApprovalIntentResult(
            intent=ApprovalIntent.REJECT,
            confidence=0.95,
            reason="reject_phrase",
        ),
    ), patch.object(
        orchestrator.approval_gate,
        "log_decision",
    ) as mock_log:
        response = orchestrator._handle_approval_response(
            "reject",
            state,
            tracker,
        )

    assert response == "Understood. I won't run that action. What would you like me to do instead?"
    assert state.phase == ConversationPhase.IDLE
    assert state.pending_action is None
    assert state.pending_action_summary == ""
    mock_log.assert_called_once_with(
        "conv-approval",
        "apprv-old",
        "REJECTED",
        "webchat:kirtibala.gujar",
    )


def test_retry_action_is_replaced_with_health_check_approval_first():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.conversation_id = "conv-health-approval"
    state.user_id = "webchat:pooja"
    state.user_role = "technical"
    state.user_metadata = {"org_code": "AEGEMS"}

    restart_def = tool_registry.get_tool("restart_execution")

    with patch(
        "tools.remediation_tools.inspect_related_retry_health_requirement",
        return_value={
            "execution_id": "2615124",
            "workflow_name": "Daily_claim_report_bot",
            "issue_type": "life_asia",
            "issue_label": "Life Asia",
            "health_check_label": "Life Asia health check",
            "health_check_workflow": "TEBT_Health_Check",
            "failure_reason": "Login issue Life Asia Portal.",
        },
    ):
        prompt = orchestrator._maybe_queue_retry_health_check_approval(
            state=state,
            tool_name="restart_execution",
            tool_args={
                "execution_id": "2615124",
                "workflow_name": "Daily_claim_report_bot",
                "org_code": "AEGEMS",
            },
            tool_def=restart_def,
        )

    assert "Before I restart" in prompt
    assert "TEBT_Health_Check" in prompt
    assert state.phase == ConversationPhase.AWAITING_APPROVAL
    assert state.pending_action is not None
    assert state.pending_action["tool"] == "run_related_health_check"
    assert state.pending_action["args"]["issue_label"] == "Life Asia"
    assert state.pending_action["follow_up_action"]["tool"] == "restart_execution"
    assert state.pending_action["follow_up_action"]["args"]["_skip_retry_health_gate"] is True


def test_handle_approval_response_for_health_check_executes_follow_up_restart():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.conversation_id = "conv-health-follow-up"
    state.user_id = "webchat:pooja"
    state.user_role = "technical"
    state.phase = ConversationPhase.AWAITING_APPROVAL
    state.pending_action = {
        "tool": "run_related_health_check",
        "args": {
            "execution_id": "2615124",
            "workflow_name": "Daily_claim_report_bot",
            "health_check_workflow": "TEBT_Health_Check",
            "issue_label": "Life Asia",
            "org_code": "AEGEMS",
        },
        "tier": "medium_risk",
        "authorized_users": [],
        "request_id": "apprv-health",
        "follow_up_action": {
            "tool": "restart_execution",
            "args": {
                "execution_id": "2615124",
                "workflow_name": "Daily_claim_report_bot",
                "org_code": "AEGEMS",
                "_skip_retry_health_gate": True,
            },
            "tier": "medium_risk",
            "authorized_users": [],
        },
    }
    state.pending_action_summary = "run_related_health_check on TEBT_Health_Check"
    tracker = MagicMock()

    with patch.object(
        orchestrator.approval_gate,
        "classify_approval_turn",
        return_value=ApprovalIntentResult(
            intent=ApprovalIntent.APPROVE,
            confidence=0.99,
            reason="approve_phrase",
        ),
    ), patch.object(
        orchestrator.approval_gate,
        "log_decision",
    ), patch(
        "agents.orchestrator.tool_registry.execute",
        side_effect=[
            ToolResult(
                success=True,
                data={
                    "success": True,
                    "health_gate_required": True,
                    "health_gate_passed": True,
                    "message": "Life Asia is currently up and running.",
                },
                tool_name="run_related_health_check",
            ),
            ToolResult(
                success=True,
                data={
                    "success": True,
                    "message": "Execution 2615124 has been restarted successfully.",
                    "execution_id": "2615124",
                    "workflow_name": "Daily_claim_report_bot",
                    "status": "QUEUED",
                },
                tool_name="restart_execution",
            ),
        ],
    ):
        response = orchestrator._handle_approval_response(
            "approve",
            state,
            tracker,
        )

    assert "Life Asia is currently up and running." in response
    assert "Execution 2615124 has been restarted successfully." in response
    assert state.phase == ConversationPhase.RESOLVED
    assert state.pending_action is None


def test_direct_health_check_retry_request_is_rewritten_to_restart_execution():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.tool_call_log = [
        {
            "tool": "check_workflow_status",
            "params": {"workflow_name": "New_customer_update_details_bot"},
            "result": {
                "latest_status": "Failure",
                "workflow_name": "New_customer_update_details_bot",
                "latest_execution_id": "2615591",
            },
            "success": True,
        }
    ]

    tool_name, tool_args = orchestrator._rewrite_direct_health_check_followup_from_failed_context(
        user_message="retrigger workflow again",
        state=state,
        tool_name="run_related_health_check",
        tool_args={
            "execution_id": "2615591",
            "workflow_name": "New_customer_update_details_bot",
            "issue_label": "TEBT",
        },
    )

    assert tool_name == "restart_execution"
    assert tool_args["execution_id"] == "2615591"
    assert tool_args["workflow_name"] == "New_customer_update_details_bot"


def test_handle_approval_response_for_health_check_without_follow_up_infers_restart():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.conversation_id = "conv-health-follow-up-inferred"
    state.user_id = "webchat:pooja"
    state.user_role = "technical"
    state.phase = ConversationPhase.AWAITING_APPROVAL
    state.messages = [
        {"role": "user", "content": "check the status of New_customer_update_details_bot"},
        {"role": "assistant", "content": "The workflow failed because of a TEBT login issue."},
        {"role": "user", "content": "retrigger workflow again"},
        {"role": "assistant", "content": "I need approval to verify TEBT health before retrying."},
        {"role": "user", "content": "approve"},
    ]
    state.tool_call_log = [
        {
            "tool": "check_workflow_status",
            "params": {"workflow_name": "New_customer_update_details_bot"},
            "result": {
                "latest_status": "Failure",
                "workflow_name": "New_customer_update_details_bot",
                "latest_execution_id": "2615591",
            },
            "success": True,
        }
    ]
    state.pending_action = {
        "tool": "run_related_health_check",
        "args": {
            "execution_id": "2615591",
            "workflow_name": "New_customer_update_details_bot",
            "issue_label": "TEBT",
            "org_code": "AEGEMS",
        },
        "tier": "medium_risk",
        "authorized_users": [],
        "request_id": "apprv-health-inferred",
    }
    state.pending_action_summary = "run_related_health_check on New_customer_update_details_bot"
    tracker = MagicMock()

    with patch.object(
        orchestrator.approval_gate,
        "classify_approval_turn",
        return_value=ApprovalIntentResult(
            intent=ApprovalIntent.APPROVE,
            confidence=0.99,
            reason="approve_phrase",
        ),
    ), patch.object(
        orchestrator.approval_gate,
        "log_decision",
    ), patch(
        "agents.orchestrator.tool_registry.execute",
        side_effect=[
            ToolResult(
                success=True,
                data={
                    "success": True,
                    "health_gate_required": True,
                    "health_gate_passed": True,
                    "message": "TEBT is currently up and running.",
                },
                tool_name="run_related_health_check",
            ),
            ToolResult(
                success=True,
                data={
                    "success": True,
                    "message": "Execution 2615591 has been restarted successfully.",
                    "execution_id": "2615591",
                    "workflow_name": "New_customer_update_details_bot",
                    "status": "QUEUED",
                },
                tool_name="restart_execution",
            ),
        ],
    ) as mock_execute:
        response = orchestrator._handle_approval_response(
            "approve",
            state,
            tracker,
        )

    assert "TEBT is currently up and running." in response
    assert "Execution 2615591 has been restarted successfully." in response
    assert mock_execute.call_args_list[1].args[0] == "restart_execution"
    assert state.phase == ConversationPhase.RESOLVED
    assert state.pending_action is None
