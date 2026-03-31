from unittest.mock import patch

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState


def test_start_or_update_param_collection_prefills_from_conversation_history():
    orchestrator = Orchestrator()
    state = ConversationState()
    state.add_message("assistant", "Request ID: 2582536")
    state.add_message("user", "Fresh Run to run this workflow again.")

    stub_client = type(
        "StubClient",
        (),
        {
            "get_cached_workflow_parameters": lambda self, workflow_name: [
                {"name": "request_id", "required": True}
            ]
        },
    )()

    with patch("agents.orchestrator.get_ae_client", return_value=stub_client):
        with patch.object(
            orchestrator,
            "_extract_params_from_user_message",
            return_value={"request_id": "2582536"},
        ):
            orchestrator._start_or_update_param_collection(
                state=state,
                workflow_name="LogExtractionAndRecognition",
                missing_params=["request_id"],
                tool_name="trigger_workflow",
                tool_args={"workflow_name": "LogExtractionAndRecognition", "parameters": {}},
                auto_execute=False,
            )

    assert state.param_collection["collected_params"]["request_id"] == "2582536"
