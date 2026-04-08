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
            "The workflow failed because the API endpoint returned error code X123. Here are the next steps.",
            state,
        )

    assert result == "Business-friendly response"
    assert captured["max_tokens"] == 32000
    assert "Do not shorten the response unnecessarily" in captured["prompt"]
    assert "Keep the response complete and well-structured" in captured["prompt"]


def test_business_persona_filter_skips_plain_greeting():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_role = "business"

    greeting = "Good morning! How can I help you today?"

    with patch("agents.orchestrator.llm_client.chat") as mock_chat:
        result = orchestrator._filter_for_persona(greeting, state)

    assert result == greeting
    mock_chat.assert_not_called()


def test_business_persona_filter_blocks_email_style_output():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_role = "business"

    responses = [
        "Subject: Update on your report\n\nHi [Business User Name],\n\nStatus & Impact:\nThe report was delayed.",
        "Your report was delayed because required input data was missing. Would you like me to check the source data or help retry once it is available?",
    ]

    with patch("agents.orchestrator.llm_client.chat", side_effect=responses) as mock_chat:
        result = orchestrator._filter_for_persona(
            "The timesheet_report_generation_v5 workflow failed because an input file was missing.",
            state,
        )

    assert result == responses[1]
    assert mock_chat.call_count == 2


def test_business_persona_filter_prompt_forbids_email_and_memo_formats():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_role = "business"

    captured = {}

    def fake_chat(prompt, system="", temperature=None, max_tokens=None):
        captured["prompt"] = prompt
        captured["system"] = system
        return "Business-friendly response"

    with patch("agents.orchestrator.llm_client.chat", side_effect=fake_chat):
        orchestrator._filter_for_persona(
            "The workflow failed because the API endpoint returned an exception.",
            state,
        )

    assert "not an email, memo, or letter" in captured["prompt"]
    assert "Do NOT add a subject line" in captured["prompt"]


def test_business_persona_filter_prompt_prefers_simple_business_language_for_issues():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_role = "business"

    captured = {}

    def fake_chat(prompt, system="", temperature=None, max_tokens=None):
        captured["prompt"] = prompt
        captured["system"] = system
        return "Business-friendly response"

    with patch("agents.orchestrator.llm_client.chat", side_effect=fake_chat):
        orchestrator._filter_for_persona(
            "The workflow failed because the shared path returned FileNotFoundException and the API payload was invalid.",
            state,
        )

    assert "Use simple, clear business language that is easy to understand" in captured["prompt"]
    assert "you may use simple operational terms when useful" in captured["prompt"]
    assert "Do not include code-related details" in captured["prompt"]
    assert "The required file is not available in the shared location. Please check or upload the file." in captured["prompt"]
