from unittest.mock import MagicMock

from agents.base_agent import AgentResult
from gateway.message_gateway import MessageGateway
from state.conversation_state import ConversationPhase, ConversationState


def test_dispatch_filters_routed_response_for_business_user():
    gateway = MessageGateway()
    gateway._agent_router = MagicMock()
    gateway._agent_router.route.return_value = AgentResult(
        response="The timesheet_report_generation_v5 workflow failed with error code X123.",
        success=True,
    )

    orchestrator = MagicMock()
    orchestrator._filter_for_persona.return_value = "Your report could not be completed. I can help check the delay."
    gateway._orchestrator = orchestrator

    state = ConversationState()
    state.conversation_id = "conv-1"
    state.user_id = "user-1"
    state.user_role = "business"
    state.phase = ConversationPhase.IDLE

    progress = MagicMock()

    response = gateway._dispatch(
        "I haven't received my daily timesheet report for today.",
        state,
        progress,
        "conv-1",
    )

    assert response == "Your report could not be completed. I can help check the delay."
    orchestrator._filter_for_persona.assert_called_once()
