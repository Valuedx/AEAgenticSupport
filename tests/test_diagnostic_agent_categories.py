from unittest.mock import patch

from agents.diagnostic_agent import DiagnosticAgent
from state.conversation_state import ConversationState


def test_diagnostic_agent_allows_agent_log_categories_for_follow_up_turns():
    agent = DiagnosticAgent()
    state = ConversationState()
    state.messages = [
        {"role": "assistant", "content": "The agent is running. Do you want logs for the last 24 hours or a custom range?"}
    ]

    with patch.object(agent._orchestrator, "handle_message", return_value="ok") as mock_handle:
        result = agent.handle("last 24 hours.", state=state)

    assert result.success is True
    _, kwargs = mock_handle.call_args
    assert kwargs["allowed_categories"] == [
        "status",
        "logs",
        "dependency",
        "file",
        "diagnostics",
        "agent_read",
        "agent_diag",
    ]
