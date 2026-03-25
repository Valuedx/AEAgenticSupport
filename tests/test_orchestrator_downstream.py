from __future__ import annotations

import pytest

from agents.approval_gate import ApprovalRequest
from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState
from state.issue_tracker import MessageClassification


class _FakeTracker:
    active_issue_id = None
    issues = {}

    def classify_message(self, *_args, **_kwargs):
        return MessageClassification.STATUS_CHECK, None

    def get_all_issues_summary(self):
        return "- [issue-1] INVESTIGATING | Workflow: nightly_load"


def test_status_check_is_read_only(monkeypatch):
    orch = Orchestrator()
    tracker = _FakeTracker()
    orch.issue_trackers["conv-1"] = tracker

    state = ConversationState()
    state.conversation_id = "conv-1"
    monkeypatch.setattr(state, "save", lambda: None)
    monkeypatch.setattr(
        orch,
        "_classify_conversational_route",
        lambda *_args, **_kwargs: "OPS",
    )
    monkeypatch.setattr(
        orch,
        "_process_message",
        lambda *_args, **_kwargs: pytest.fail("status check should not trigger investigation"),
    )

    response = orch.handle_message("what is the status?", state)

    assert response == (
        "Here's the current session status:\n\n"
        "- [issue-1] INVESTIGATING | Workflow: nightly_load"
    )


def test_queue_pending_approval_returns_teams_card(monkeypatch):
    orch = Orchestrator()
    state = ConversationState()
    state.conversation_id = "conv-2"
    state.user_metadata = {"channel": "msteams"}

    monkeypatch.setattr(
        orch.approval_gate,
        "create_approval_request",
        lambda *_args, **_kwargs: ApprovalRequest(
            tool_name="restart_workflow",
            tool_params={"workflow_name": "nightly_load"},
            tier="high_risk",
            reason="Needs approval",
            summary="restart_workflow on nightly_load",
            request_id="req-42",
        ),
    )

    response = orch._queue_pending_approval(
        state=state,
        tool_name="restart_workflow",
        tool_args={"workflow_name": "nightly_load"},
        tier="high_risk",
        summary="restart_workflow on nightly_load",
    )

    assert state.pending_action["request_id"] == "req-42"
    assert isinstance(response, dict)
    assert response["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert response["attachments"][0]["content"]["actions"][0]["data"]["request_id"] == "req-42"
