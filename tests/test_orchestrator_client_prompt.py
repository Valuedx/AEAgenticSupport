from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState


def test_system_prompt_includes_client_policy_addendum_for_org_code():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_metadata = {"org_code": "AEGEMS"}

    prompt = orchestrator._build_system_prompt(state, tracker=None)

    assert "AEGEMS Workflow Policy" in prompt
    assert "always confirm the related application health before retrying the workflow" in prompt
