from unittest.mock import patch

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState


def test_business_persona_filter_uses_higher_token_limit_and_preserves_completeness():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_role = "business"

    captured = {}

    def fake_chat(prompt, system="", temperature=None, max_tokens=None):
        captured["prompt"] = prompt
        captured["system"] = system
        captured["max_tokens"] = max_tokens
        return "Business-friendly response"

    with patch("agents.orchestrator.llm_client.chat", side_effect=fake_chat):
        result = orchestrator._filter_for_persona(
            "Technical response with status, timing, and next steps.",
            state,
        )

    assert result == "Business-friendly response"
    assert captured["max_tokens"] == 32000
    assert "Do not shorten the response unnecessarily" in captured["prompt"]
    assert "Keep the response complete and well-structured" in captured["prompt"]
