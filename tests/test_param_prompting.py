from unittest.mock import patch

from agents.orchestrator import Orchestrator
from tools.remediation_tools import trigger_workflow


def test_orchestrator_param_prompt_fallback_asks_direct_question_for_single_missing_item():
    orch = Orchestrator()

    with patch("agents.orchestrator.llm_client.chat", side_effect=RuntimeError("llm unavailable")):
        message = orch._build_param_request_message(
            workflow_name="Claims_Processing_Daily",
            items=[("Batch Date", "Batch date in YYYYMMDD format")],
        )

    assert "I just need Batch Date." in message
    assert "Please share batch date in YYYYMMDD format." in message
    assert "parameter" not in message.lower()
    assert "Once you share it, I'll take it from there." in message


def test_orchestrator_param_prompt_fallback_lists_multiple_missing_items_without_parameter_word():
    orch = Orchestrator()

    with patch("agents.orchestrator.llm_client.chat", side_effect=RuntimeError("llm unavailable")):
        message = orch._build_param_request_message(
            workflow_name="Claims_Processing_Daily",
            items=[
                ("Batch Date", "Batch date in YYYYMMDD format"),
                ("Region", "Region code"),
            ],
        )

    assert "To continue with Claims Processing Daily, please share:" in message
    assert "- Batch Date: Batch date in YYYYMMDD format" in message
    assert "- Region: Region code" in message
    assert "parameter" not in message.lower()


def test_trigger_workflow_missing_input_question_stays_user_friendly():
    class StubClient:
        def resolve_cached_workflow_name(self, workflow_name):
            return ""

        def resolve_workflow_name_from_text(self, workflow_name, **kwargs):
            assert "claims processing daily bot" in workflow_name.lower()
            return "Claims_Processing_Daily"

        def get_cached_workflow_parameters(self, workflow_name):
            return [
                {
                    "name": "batch_date",
                    "type": "String",
                    "required": True,
                    "description": "Batch date in YYYYMMDD format",
                }
            ]

        def get_workflow_agents(self):
            return []

        def get_running_instances(self, workflow_name):
            return []

        def get_required_parameters(self, workflow_name):
            return ["batch_date"]

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()):
        result = trigger_workflow("Claims Processing Daily bot", {})

    assert result["needs_user_input"] is True
    assert "Please provide the following detail" not in result["question"]
    assert "parameter" not in result["question"].lower()


def test_trigger_workflow_with_no_required_inputs_does_not_ask_for_clarification():
    class StubClient:
        def resolve_cached_workflow_name(self, workflow_name):
            return ""

        def resolve_workflow_name_from_text(self, workflow_name, **kwargs):
            assert "heartbeat bot" in workflow_name.lower()
            return "Heartbeat_Check"

        def get_cached_workflow_info(self, workflow_name, **kwargs):
            return ("9101", [])

        def get_cached_workflow_parameters(self, workflow_name):
            return []

        def get_workflow_agents(self):
            return []

        def get_running_instances(self, workflow_name):
            return []

        def get_required_parameters(self, workflow_name):
            return []

        def execute_workflow(self, **kwargs):
            return {"id": "12345", "status": "QUEUED"}

        def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
            return {"status": "QUEUED", "raw": {}}

    with patch("tools.remediation_tools.get_ae_client", return_value=StubClient()):
        result = trigger_workflow("Heartbeat bot", {})

    assert result["success"] is True
    assert "needs_user_input" not in result
