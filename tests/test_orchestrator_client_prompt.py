from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState
from tools.registry import tool_registry


def test_system_prompt_includes_client_policy_addendum_for_org_code():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_metadata = {"org_code": "AEGEMS"}

    prompt = orchestrator._build_system_prompt(state, tracker=None)

    assert "AEGEMS Workflow Policy" in prompt
    assert "confirm the related application health only when the user asks to retry" in prompt


def test_retry_tools_inherit_org_scope_from_state():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_metadata = {"org_code": "AEGEMS"}

    restart_def = tool_registry.get_tool("restart_execution")
    resubmit_def = tool_registry.get_tool("resubmit_execution")

    restart_args = orchestrator._inject_user_scope(
        "restart_execution",
        {"execution_id": "22024"},
        restart_def,
        state,
    )
    resubmit_args = orchestrator._inject_user_scope(
        "resubmit_execution",
        {"execution_id": "22024"},
        resubmit_def,
        state,
    )

    assert restart_args["org_code"] == "AEGEMS"
    assert resubmit_args["org_code"] == "AEGEMS"
