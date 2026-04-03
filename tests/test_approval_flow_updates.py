from unittest.mock import MagicMock, patch

from agents.approval_gate import ApprovalIntent, ApprovalIntentResult, ApprovalRequest
from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationPhase, ConversationState


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
