from unittest.mock import MagicMock

from agents.orchestrator import Orchestrator
from gateway.message_gateway import MessageGateway
from state.conversation_state import ConversationPhase, ConversationState
from state.issue_tracker import Issue


class _StubAEClient:
    default_org_code = "ORG-A"

    def __init__(self, visible_workflows=None):
        self.visible_workflows = set(visible_workflows or [])

    def get_cached_workflow_info(self, workflow_name, **kwargs):
        if workflow_name in self.visible_workflows:
            return (f"id-{workflow_name}", [])
        return ("", [])


def test_smalltalk_greeting_is_not_forced_into_ops_when_issue_exists(monkeypatch):
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_id = "user-1"

    issue = Issue(
        title="Timesheet issue",
        workflows_involved=["timesheet_report_generation"],
    )
    tracker = MagicMock()
    tracker.get_active_issue.return_value = issue

    monkeypatch.setattr(
        "agents.orchestrator.get_ae_client",
        lambda: _StubAEClient({"timesheet_report_generation"}),
    )

    route = orchestrator._classify_conversational_route(
        "Good morning",
        state,
        tracker,
    )

    assert route == "SMALLTALK"


def test_system_prompt_hides_unauthorized_issue_workflow(monkeypatch):
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_id = "user-1"

    issue = Issue(
        title="timesheet_report_generation failed in production",
        workflows_involved=["timesheet_report_generation"],
    )
    tracker = MagicMock()
    tracker.issues = {issue.issue_id: issue}
    tracker.get_active_issue.return_value = issue

    monkeypatch.setattr(
        "agents.orchestrator.get_ae_client",
        lambda: _StubAEClient(),
    )

    prompt = orchestrator._build_system_prompt(state, tracker)

    assert "timesheet_report_generation" not in prompt
    assert "No active issues." in prompt
    assert "Currently focused issue: None" in prompt


def test_message_gateway_resets_session_when_user_changes(monkeypatch):
    loaded_state = ConversationState()
    loaded_state.conversation_id = "conv-1"
    loaded_state.exists_in_store = True
    loaded_state.user_id = "old-user"
    loaded_state.user_name = "Old User"
    loaded_state.user_email = "old.user@company.com"
    loaded_state.messages = [{"role": "assistant", "content": "old context"}]
    loaded_state.affected_workflows = ["timesheet_report_generation"]
    loaded_state.summary = "Old summary"
    loaded_state.pending_action = {"tool": "trigger_workflow"}
    loaded_state.pending_action_summary = "trigger_workflow on timesheet_report_generation"
    loaded_state.param_collection = {"workflow_name": "timesheet_report_generation"}
    loaded_state.suspended_flow = {"type": "approval"}
    loaded_state.last_agent_id = "ops_orchestrator"

    cleared: list[str] = []

    def _fake_load(cls, conversation_id):
        return loaded_state

    monkeypatch.setattr(ConversationState, "load", classmethod(_fake_load))
    monkeypatch.setattr(
        ConversationState,
        "clear_persisted_context",
        staticmethod(lambda conversation_id: cleared.append(conversation_id) or True),
    )
    monkeypatch.setattr(
        ConversationState,
        "ensure_workflow_access_sync",
        lambda self, force=False: None,
    )

    gateway = MessageGateway()
    state = gateway.get_or_create_session(
        "conv-1",
        user_id="new-user",
        user_role="technical",
        user_name="New User",
        user_email="new.user@company.com",
        user_team="TEAM-A",
        user_metadata={"org_code": "ORG-A"},
    )

    assert cleared == ["conv-1"]
    assert state.user_id == "new-user"
    assert state.user_name == "New User"
    assert state.user_email == "new.user@company.com"
    assert state.user_team == "TEAM-A"
    assert state.user_metadata == {"org_code": "ORG-A"}
    assert state.phase == ConversationPhase.IDLE
    assert state.exists_in_store is False
    assert state.messages == []
    assert state.affected_workflows == []
    assert state.summary == ""
    assert state.pending_action is None
    assert state.pending_action_summary == ""
    assert state.param_collection == {}
    assert state.suspended_flow == {}
    assert state.last_agent_id == ""
