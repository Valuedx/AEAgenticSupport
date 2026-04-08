from agents.approval_gate import ApprovalGate, ApprovalRequest
from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState


def _sample_approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        tool_name="t4_execute_and_poll",
        tool_params={
            "workflow_name": "Test_Demo",
            "workflow_id": "9109",
            "params": {
                "output_path": r"C:\Users\omkar.patil\Downloads\Application Check Document.xlsx",
            },
            "user_id": "webchat:kirtibala.gujar",
            "org_code": "AEGEMS",
        },
        tier="medium_risk",
        reason="approval required",
        summary="t4_execute_and_poll on Test_Demo",
    )


def test_approval_prompt_is_clean_for_technical_user():
    gate = ApprovalGate()

    prompt = gate.format_approval_prompt(
        _sample_approval_request(),
        audience="technical",
    )

    assert "I'm ready to run this action." in prompt
    assert "**Planned Action**" in prompt
    assert "Run `Test_Demo` and wait for the result." in prompt
    assert "Output path" in prompt
    assert "user_id" not in prompt
    assert "org_code" not in prompt
    assert "t4_execute_and_poll" not in prompt
    assert "params:" not in prompt


def test_approval_prompt_is_clean_for_business_user():
    gate = ApprovalGate()

    prompt = gate.format_approval_prompt(
        _sample_approval_request(),
        audience="business",
    )

    assert "I'm ready to continue with this request." in prompt
    assert "**What Will Happen**" in prompt
    assert "Process: Test Demo" in prompt
    assert "Document: Application Check Document.xlsx" in prompt
    assert r"C:\Users\omkar.patil" not in prompt
    assert "workflow_id" not in prompt
    assert "t4_execute_and_poll" not in prompt


def test_related_issue_notice_avoids_linked_issue_jargon():
    state = ConversationState()
    state.user_role = "business"

    notice = Orchestrator._build_related_issue_notice(state)

    assert "new request" in notice.lower()
    assert "different problem" in notice.lower()
    assert "linked issue" not in notice.lower()


def test_business_prompt_contains_clear_response_guidance():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_role = "business"

    prompt = orchestrator._build_system_prompt(state, tracker=None)

    assert "MEANINGFUL FIRST-LINE RULE" in prompt
    assert "First sentence must answer the question directly in plain English." in prompt
    assert "you may use simple operational terms when they help" in prompt
    assert "Do not include code-level details" in prompt
    assert "The required file is not available in the shared location. Please check or upload the file." in prompt
    assert "If you need approval or more information, explain it in natural chat language" in prompt
    assert "NO EMAIL / LETTER FORMATTING" in prompt


def test_technical_prompt_contains_clear_response_guidance():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_role = "technical"

    prompt = orchestrator._build_system_prompt(state, tracker=None)

    assert "MEANINGFUL FIRST-LINE RULE" in prompt
    assert "First sentence must answer the question or summarize the outcome clearly." in prompt
    assert "Stay conversational, readable, and precise." in prompt
    assert "NO EMAIL / LETTER FORMATTING" in prompt


def test_business_clarification_prompt_stays_chat_friendly():
    gate = ApprovalGate()

    prompt = gate.format_clarification_prompt(
        pending_action={
            "tool": "t4_execute_and_poll",
            "args": {
                "workflow_name": "Test_Demo",
                "workflow_id": "9109",
                "params": {
                    "output_path": r"C:\Users\omkar.patil\Downloads\Application Check Document.xlsx",
                },
                "user_id": "webchat:kirtibala.gujar",
                "org_code": "AEGEMS",
            },
        },
        pending_summary="t4_execute_and_poll on Test_Demo",
        audience="business",
    )

    assert "I'm waiting for your decision on this request." in prompt
    assert "**What Will Happen**" in prompt
    assert "Document: Application Check Document.xlsx" in prompt
    assert "workflow_id" not in prompt
    assert "user_id" not in prompt
    assert "org_code" not in prompt
