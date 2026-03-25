from __future__ import annotations

from types import SimpleNamespace

from agents.approval_gate import ApprovalGate, ApprovalIntent
from agents.diagnostic_agent import DiagnosticAgent
from state.conversation_state import ConversationState, message_content_to_text
from state.issue_tracker import Issue, IssueStatus, IssueTracker, MessageClassification


def _approval_card_payload() -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "Action Approval Required",
                            "weight": "Bolder",
                            "size": "Medium",
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Action:", "value": "restart_workflow"},
                                {"title": "Request:", "value": "req-42"},
                            ],
                        },
                    ],
                    "actions": [
                        {"type": "Action.Submit", "title": "Approve"},
                        {"type": "Action.Submit", "title": "Reject"},
                    ],
                },
            }
        ],
    }


def test_message_content_to_text_flattens_adaptive_card():
    text = message_content_to_text(_approval_card_payload())

    assert "Action Approval Required" in text
    assert "Action: restart_workflow" in text
    assert "Request: req-42" in text
    assert "Actions: Approve, Reject" in text


def test_conversation_state_add_message_preserves_structured_content_but_persists_text():
    state = ConversationState()
    state.conversation_id = "conv-1"
    payload = _approval_card_payload()

    state.add_message("assistant", payload)

    assert state.messages[0]["content"] == payload
    persisted_text = state._pending_message_inserts[0][1]
    assert "Action Approval Required" in persisted_text
    assert "Actions: Approve, Reject" in persisted_text


def test_approval_gate_llm_context_handles_structured_history(monkeypatch):
    gate = ApprovalGate()
    captured = {}

    def _fake_chat(prompt, **_kwargs):
        captured["prompt"] = prompt
        return '{"intent":"clarify","confidence":0.92,"reason":"question"}'

    monkeypatch.setattr("agents.approval_gate.llm_client.chat", _fake_chat)

    result = gate._classify_with_llm(
        user_message="what will this do?",
        pending_action={"tool": "restart_workflow", "tier": "high_risk", "args": {}},
        pending_summary="Restart workflow",
        conversation_messages=[
            {"role": "assistant", "content": _approval_card_payload()},
        ],
    )

    assert result is not None
    assert result.intent == ApprovalIntent.CLARIFY
    assert "Action Approval Required" in captured["prompt"]
    assert "Actions: Approve, Reject" in captured["prompt"]


def test_issue_tracker_llm_context_handles_structured_history(monkeypatch):
    captured = {}

    def _fake_chat(prompt, **_kwargs):
        captured["prompt"] = prompt
        return "STATUS_CHECK|none"

    monkeypatch.setattr(IssueTracker, "_load_from_db", lambda self: None)
    monkeypatch.setattr("config.llm_client.llm_client.chat", _fake_chat)

    tracker = IssueTracker("conv-1")
    issue = Issue(issue_id="ISS-1", title="Workflow issue", status=IssueStatus.ACTIVE)

    classification, issue_id = tracker._llm_classify(
        "what is the status?",
        [issue],
        [{"role": "assistant", "content": _approval_card_payload()}],
    )

    assert classification == MessageClassification.STATUS_CHECK
    assert issue_id is None
    assert "Action Approval Required" in captured["prompt"]


def test_diagnostic_agent_can_use_structured_assistant_history():
    agent = DiagnosticAgent()
    state = SimpleNamespace(
        messages=[
            {
                "role": "assistant",
                "content": {
                    "type": "message",
                    "text": "Please share the agent id or logs to continue.",
                },
            }
        ]
    )

    score = agent.can_handle("2887", state=state)

    assert score >= 0.95
