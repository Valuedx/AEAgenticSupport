from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState


def test_parameter_followup_query_uses_recent_agent_log_context():
    orch = Orchestrator()
    state = ConversationState()
    state.messages = [
        {
            "role": "assistant",
            "content": "I found agent 2987. Do you want logs for the last 24 hours or a custom range?",
        }
    ]

    enriched = orch._augment_contextual_tool_query(
        "yesterday to today",
        state,
        "yesterday to today",
    )

    assert "agent logs" in enriched
    assert "agent_id 2987" in enriched
