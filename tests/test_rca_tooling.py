from agents.rca_agent import RCAAgent
from state.conversation_state import ConversationState
from tools.rca_tools import generate_rca_report


def test_rca_handle_without_findings_returns_guidance(monkeypatch):
    def _fake_generate(self, state, incident_summary="", tracker=None, issue_id=""):
        return "I need to investigate first."

    monkeypatch.setattr(RCAAgent, "generate_rca", _fake_generate)

    state = ConversationState()
    state.conversation_id = "rca-handle-no-findings"

    result = RCAAgent().handle("What happened?", state=state)

    assert result.success is False
    assert "investigate" in result.response.lower()
    assert result.metadata.get("rca_generated_at") is None


def test_generate_rca_report_supports_state_context(monkeypatch):
    def _fake_generate(self, state, incident_summary="", tracker=None, issue_id=""):
        state.rca_data = {
            "generated_at": "2026-03-06T00:00:00",
            "report": "RCA content",
            "user_role": "technical",
        }
        return "RCA content"

    monkeypatch.setattr(RCAAgent, "generate_rca", _fake_generate)

    state = ConversationState()
    state.conversation_id = "rca-tool-state-context"

    response = generate_rca_report(
        incident_summary="Workflow failure",
        state=state,
    )

    assert response["success"] is True
    assert response["report"] == "RCA content"
    assert response["generated_at"] == "2026-03-06T00:00:00"
    assert response["conversation_id"] == "rca-tool-state-context"


def test_generate_rca_report_requires_context():
    response = generate_rca_report(incident_summary="Workflow failure")

    assert response["success"] is False
    assert "conversation context" in response["error"].lower()
