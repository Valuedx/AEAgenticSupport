from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState
from tools.ae_dynamic_tools import DynamicToolMapping, extract_dynamic_tool_mapping
from tools.base import ToolResult
from tools.registry import ToolRegistry


def test_execution_intent_uses_explicit_trigger_cue_when_llm_misclassifies(monkeypatch):
    orchestrator = Orchestrator()
    monkeypatch.setattr("agents.orchestrator.llm_client.chat", lambda *args, **kwargs: "NOT_EXECUTE")

    assert orchestrator._is_execution_request("trigger test demo bot") is True


def test_execution_intent_handles_common_trigger_typos(monkeypatch):
    orchestrator = Orchestrator()
    monkeypatch.setattr("agents.orchestrator.llm_client.chat", lambda *args, **kwargs: "NOT_EXECUTE")

    assert orchestrator._is_execution_request("please triger the test demo bot") is True


def test_workflow_name_resolution_uses_catalog_fuzzy_match_without_hardcoding(monkeypatch):
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_id = "webchat:kirtibala.gujar"
    state.user_metadata = {"org_code": "AEGEMS"}

    class StubClient:
        default_org_code = "AEGEMS"

        @staticmethod
        def resolve_cached_workflow_name(workflow_name, **kwargs):
            return ""

        @staticmethod
        def resolve_workflow_name_from_text(workflow_name, **kwargs):
            assert "daily claims processing bot" in workflow_name.lower()
            return "Claims_Processing_Daily"

    monkeypatch.setattr("agents.orchestrator.get_ae_client", lambda: StubClient())

    resolved = orchestrator._resolve_workflow_name_from_message(
        "please trigger the daily claims processing bot",
        state,
    )

    assert resolved == "Claims_Processing_Daily"


def test_preflight_prefers_cached_schema_over_noisy_search_metadata(monkeypatch):
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_id = "webchat:kirtibala.gujar"
    state.user_metadata = {"org_code": "AEGEMS"}

    noisy_hit = {
        "name": "Test_Demo",
        "workflow_name": "Test_Demo",
        "category": "automationedge",
        "score": 0.99,
        "metadata": {
            "source": "automationedge",
            "workflow_id": "9109",
            "parameters": [
                {"name": "empid", "description": "Employee ID", "optional": False},
                {"name": "appname", "description": "Application name", "optional": False},
                {"name": "reason", "description": "Reason", "optional": False},
                {"name": "additionalInfo", "description": "Additional info", "optional": True},
                {"name": "chatbotURL", "description": "Chatbot URL", "optional": True},
            ],
        },
    }

    class StubClient:
        default_org_code = "AEGEMS"

        @staticmethod
        def resolve_cached_workflow_name(workflow_name, **kwargs):
            normalized = str(workflow_name or "").strip().lower().replace("_", " ")
            if normalized == "test demo":
                return "Test_Demo"
            return ""

        @staticmethod
        def get_cached_workflow_info(workflow_name, **kwargs):
            assert workflow_name == "Test_Demo"
            return (
                "9109",
                [
                    {
                        "name": "output_path",
                        "type": "String",
                        "optional": False,
                        "displayName": "output_path",
                        "description": "Full destination file path",
                    }
                ],
            )

        @staticmethod
        def get_cached_workflow_parameters(workflow_name):
            return StubClient.get_cached_workflow_info(workflow_name)[1]

    monkeypatch.setattr(orchestrator, "_is_execution_request", lambda message: True)
    monkeypatch.setattr(
        orchestrator,
        "_build_param_request_message",
        lambda workflow_name, items, intro, sop_guidance=None: " | ".join(label for label, _ in items),
    )
    monkeypatch.setattr(
        "agents.orchestrator.tool_registry.execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("discover_tools should not be used when the workflow name resolves directly")),
    )
    monkeypatch.setattr("agents.orchestrator.get_ae_client", lambda: StubClient())
    monkeypatch.setattr("agents.orchestrator.can_execute_workflow", lambda user_id, workflow_id, org_code: True)

    response = orchestrator._preflight_workflow_param_collection(
        "trigger the test demo bot",
        state,
    )

    assert response == "output path"


def test_preflight_requires_exact_accessible_workflow_before_asking_for_params(monkeypatch):
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_id = "webchat:kirtibala.gujar"
    state.user_metadata = {"org_code": "AEGEMS"}

    suggestions_hit = {
        "name": "Test_Demo",
        "workflow_name": "Test_Demo",
        "category": "automationedge",
        "score": 0.91,
        "metadata": {
            "source": "automationedge",
            "workflow_id": "9109",
            "workflow_name": "Test_Demo",
        },
    }

    class StubClient:
        default_org_code = "AEGEMS"

        @staticmethod
        def resolve_cached_workflow_name(workflow_name, **kwargs):
            return ""

    monkeypatch.setattr(orchestrator, "_is_execution_request", lambda message: True)
    monkeypatch.setattr(
        "agents.orchestrator.tool_registry.execute",
        lambda *args, **kwargs: ToolResult(
            success=True,
            data={"tools": [suggestions_hit]},
            tool_name="discover_tools",
        ),
    )
    monkeypatch.setattr("agents.orchestrator.get_ae_client", lambda: StubClient())

    response = orchestrator._preflight_workflow_param_collection(
        "can you trigger dron bot",
        state,
    )

    assert "exact workflow" in response.lower()
    assert "Test_Demo" in response
    assert "empid" not in response


def test_preflight_checks_execute_access_before_param_collection(monkeypatch):
    orchestrator = Orchestrator()
    state = ConversationState()
    state.user_id = "webchat:kirtibala.gujar"
    state.user_metadata = {"org_code": "AEGEMS"}

    class StubClient:
        default_org_code = "AEGEMS"

        @staticmethod
        def resolve_cached_workflow_name(workflow_name, **kwargs):
            return "Test_Demo"

        @staticmethod
        def get_cached_workflow_info(workflow_name, **kwargs):
            return (
                "9109",
                [
                    {
                        "name": "output_path",
                        "type": "String",
                        "optional": False,
                        "displayName": "output_path",
                    }
                ],
            )

        @staticmethod
        def get_cached_workflow_parameters(workflow_name):
            return StubClient.get_cached_workflow_info(workflow_name)[1]

    monkeypatch.setattr(orchestrator, "_is_execution_request", lambda message: True)
    monkeypatch.setattr("agents.orchestrator.get_ae_client", lambda: StubClient())
    monkeypatch.setattr("agents.orchestrator.can_execute_workflow", lambda user_id, workflow_id, org_code: False)

    response = orchestrator._preflight_workflow_param_collection(
        "trigger the test demo bot",
        state,
    )

    assert "exact workflow" in response.lower() or "workflow list" in response.lower()
    assert "output path" not in response.lower()


def test_dynamic_mapping_prefers_direct_workflow_parameters_over_nested_noise():
    workflow = {
        "workflowName": "Test_Demo",
        "workflowId": "9109",
        "parameters": [
            {"name": "output_path", "type": "String", "optional": False},
        ],
        "agenticToolConfiguration": {
            "toolName": "Test_Demo",
            "description": "Run Test Demo",
        },
        "nested": {
            "parameters": [
                {"name": "empid", "type": "String", "optional": False},
                {"name": "appname", "type": "String", "optional": False},
                {"name": "reason", "type": "String", "optional": False},
            ]
        },
    }

    mapping = extract_dynamic_tool_mapping(workflow)

    assert mapping is not None
    assert mapping.required_params == ["output_path"]
    assert [item["name"] for item in mapping.parameter_meta] == ["output_path"]


def test_dynamic_tool_handler_uses_cached_required_inputs_and_flattens_parameters():
    registry = ToolRegistry()
    mapping = DynamicToolMapping(
        tool_name="Test_Demo",
        workflow_name="Test_Demo",
        workflow_id="9109",
        org_code="AEGEMS",
        description="Run Test Demo",
        category="automationedge",
        tier="medium_risk",
        active=True,
        tags=[],
        use_when="",
        avoid_when="",
        input_examples=[],
        parameters={},
        required_params=["empid", "appname", "reason"],
        parameter_meta=[],
    )

    class StubClient:
        def __init__(self):
            self.called_with = None

        @staticmethod
        def get_cached_workflow_parameters(workflow_name):
            assert workflow_name == "Test_Demo"
            return [{"name": "output_path", "type": "String", "optional": False}]

        @staticmethod
        def get_required_parameters(workflow_name):
            assert workflow_name == "Test_Demo"
            return ["output_path"]

        def execute_workflow(self, **kwargs):
            self.called_with = kwargs
            return {"status": "NEW", "requestId": "12345"}

    client = StubClient()
    handler = registry._make_dynamic_tool_handler(mapping, client)

    result = handler(parameters={"output_path": r"C:\temp\result.xlsx"})

    assert result["success"] is True
    assert client.called_with is not None
    assert client.called_with["params"] == {"output_path": r"C:\temp\result.xlsx"}
