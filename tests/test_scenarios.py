"""
Test scenarios for the AutomationEdge Agentic Support system.
Run: python -m pytest tests/test_scenarios.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import CONFIG
from state.conversation_state import ConversationState, ConversationPhase
from agents.approval_gate import ApprovalGate, ApprovalIntent
from templates.rca_templates import (
    render_business_rca,
    render_technical_rca,
    render_escalation_message,
)
from tools.base import ToolDefinition, ToolResult


class TestConversationState(unittest.TestCase):
    """Verify conversation state machine transitions and message tracking."""

    def test_initial_state(self):
        state = ConversationState()
        self.assertEqual(state.phase, ConversationPhase.IDLE)
        self.assertEqual(len(state.messages), 0)
        self.assertFalse(state.is_agent_working)

    def test_add_messages(self):
        state = ConversationState()
        state.add_message("user", "Hello")
        state.add_message("assistant", "Hi there")
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.messages[0]["role"], "user")
        self.assertEqual(state.messages[1]["content"], "Hi there")

    def test_phase_transitions(self):
        state = ConversationState()
        state.phase = ConversationPhase.INVESTIGATING
        self.assertEqual(state.phase, ConversationPhase.INVESTIGATING)
        state.phase = ConversationPhase.AWAITING_APPROVAL
        self.assertEqual(state.phase, ConversationPhase.AWAITING_APPROVAL)

    def test_tool_call_logging(self):
        state = ConversationState()
        state.log_tool_call(
            "check_workflow_status",
            {"workflow_name": "test_wf"},
            {"status": "ok"},
            True,
        )
        self.assertEqual(len(state.tool_call_log), 1)
        self.assertEqual(state.tool_call_log[0]["tool"], "check_workflow_status")
        self.assertTrue(state.tool_call_log[0]["success"])

    def test_message_queue(self):
        state = ConversationState()
        state.is_agent_working = True
        state.queue_user_message("urgent fix", hint="interrupt")
        self.assertEqual(len(state.message_queue), 1)
        self.assertEqual(state.message_queue[0]["content"], "urgent fix")


class TestApprovalGate(unittest.TestCase):
    """Verify approval logic and tier classification."""

    def setUp(self):
        self.gate = ApprovalGate()

    def test_read_only_tier_no_approval(self):
        self.assertFalse(
            self.gate.needs_approval(
                "check_workflow_status", "read_only", {}
            )
        )

    def test_high_risk_tier_needs_approval(self):
        self.assertTrue(
            self.gate.needs_approval(
                "disable_workflow", "high_risk", {}
            )
        )

    def test_medium_risk_tier_needs_approval(self):
        self.assertTrue(
            self.gate.needs_approval(
                "trigger_workflow", "medium_risk", {}
            )
        )

    def test_protected_workflow_needs_approval(self):
        self.assertTrue(
            self.gate.needs_approval(
                "restart_execution", "low_risk",
                {"workflow_name": CONFIG.get(
                    "PROTECTED_WORKFLOWS", ["Claims_Processing_Daily"]
                )[0]}
            )
        )

    def test_parse_approval_yes(self):
        for word in ["approve", "yes", "go ahead", "proceed"]:
            self.assertTrue(self.gate.parse_approval_response(word))

    def test_parse_approval_no(self):
        for word in ["reject", "no", "deny"]:
            self.assertFalse(self.gate.parse_approval_response(word))

    def test_parse_approval_ambiguous(self):
        self.assertIsNone(self.gate.parse_approval_response("maybe later"))

    def test_parse_approval_semantic_yes(self):
        self.assertTrue(
            self.gate.parse_approval_response("sure, go ahead and restart it")
        )

    def test_parse_approval_semantic_no(self):
        self.assertFalse(
            self.gate.parse_approval_response("no, that's risky")
        )

    def test_classify_approval_clarification(self):
        result = self.gate.classify_approval_turn(
            "What exactly will this change?"
        )
        self.assertEqual(result.intent, ApprovalIntent.CLARIFY)

    def test_classify_approval_new_request(self):
        result = self.gate.classify_approval_turn(
            "Don't do that, check logs instead."
        )
        self.assertEqual(result.intent, ApprovalIntent.NEW_REQUEST)

    def test_classify_approval_reject_without_alternate_request(self):
        result = self.gate.classify_approval_turn("No, don't run it.")
        self.assertEqual(result.intent, ApprovalIntent.REJECT)


class TestToolDefinition(unittest.TestCase):
    """Verify tool definition schema generation."""

    def test_to_llm_schema(self):
        tool = ToolDefinition(
            name="test_tool",
            description="A test tool",
            category="test",
            tier="read_only",
            parameters={"workflow_name": {
                "type": "string", "description": "Workflow",
            }},
        )
        schema = tool.to_llm_schema()
        self.assertEqual(schema["name"], "test_tool")
        self.assertIn("parameters", schema)

    def test_to_llm_schema_includes_usage_guidance(self):
        tool = ToolDefinition(
            name="search_docs",
            description="Search documentation.",
            category="general",
            tier="read_only",
            parameters={"query": {"type": "string", "description": "Search query"}},
            required_params=["query"],
            use_when="You need SOP or KB context before acting.",
            avoid_when="You already have a live request ID and need current status.",
            input_examples=[{"query": "vpn connection failed after patching"}],
            metadata={"workflow_name": "WF_DOC_SEARCH", "tags": ["kb", "sop"]},
        )

        schema = tool.to_llm_schema()

        self.assertIn("Use when: You need SOP or KB context before acting.", schema["description"])
        self.assertIn("Avoid when: You already have a live request ID and need current status.", schema["description"])
        self.assertIn("Example arguments:", schema["description"])

    def test_tool_result_success(self):
        result = ToolResult(success=True, data={"status": "ok"})
        self.assertTrue(result.success)
        self.assertEqual(result.error, "")

    def test_tool_result_failure(self):
        result = ToolResult(success=False, data={}, error="Not found")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Not found")


class TestTemplates(unittest.TestCase):
    """Verify RCA template rendering."""

    def test_business_rca_render(self):
        report = render_business_rca(
            incident_summary="Claims batch failed",
            business_impact="All claims delayed by 4 hours",
            root_cause="Missing input file",
            resolution="File regenerated and workflow restarted",
            prevention="Added file check before workflow start",
        )
        self.assertIn("Claims batch failed", report)
        self.assertIn("Missing input file", report)

    def test_technical_rca_render(self):
        report = render_technical_rca(
            incident_summary="Claims_Processing_Daily failed EX-0042",
            timeline="08:00 - Scheduled start\n08:01 - FileNotFoundError",
            root_cause="Input_File_Generator timed out, no batch file created",
            impact="Claims_Processing_Daily, Report_Aggregator blocked",
            resolution="Manual trigger of Input_File_Generator, then restart",
            corrective_actions="Add dependency health check before batch start",
        )
        self.assertIn("EX-0042", report)
        self.assertIn("FileNotFoundError", report)

    def test_escalation_render(self):
        msg = render_escalation_message(
            issue_summary="Recurring failure of Premium_Calculation",
            severity="high",
            attempts="3 automated retries and 2 manual restarts",
            recommendation="Review database connection pool sizing",
        )
        self.assertIn("Premium_Calculation", msg)
        self.assertIn("high", msg)


class TestToolRegistry(unittest.TestCase):
    """Verify tool registration and execution mechanics."""

    def test_register_and_retrieve(self):
        from tools.registry import tool_registry
        tool = tool_registry.get_tool("check_workflow_status")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.category, "status")

    def test_list_all_tools(self):
        from tools.registry import tool_registry
        all_tools = tool_registry.list_tools()
        self.assertGreater(len(all_tools), 0)
        self.assertIn("check_workflow_status", all_tools)

    def test_get_tools_by_category(self):
        from tools.registry import tool_registry
        status_tools = tool_registry.get_tools_by_category("status")
        self.assertGreater(len(status_tools), 0)

    def test_lazy_catalog_tool_executes_on_demand(self):
        from tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="lazy_tool",
                description="Lazy test tool",
                category="test",
                tier="read_only",
                parameters={"name": {"type": "string", "description": "Name"}},
            ),
            lambda **kwargs: {"success": True, "echo": kwargs.get("name", "")},
            hydrate=False,
        )

        self.assertIn("lazy_tool", registry.list_tools())
        self.assertIsNotNone(registry.get_tool("lazy_tool"))
        self.assertEqual(len(registry.get_all_rag_documents()), 1)
        self.assertNotIn("lazy_tool", registry._handlers)

        result = registry.execute("lazy_tool", name="sample")

        self.assertTrue(result.success)
        self.assertEqual(result.data.get("echo"), "sample")
        self.assertIn("lazy_tool", registry._handlers)

    def test_turn_toolset_executes_lazy_tool_without_global_hydration(self):
        from tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="lazy_turn_tool",
                description="Lazy turn-local tool",
                category="test",
                tier="read_only",
                parameters={"value": {"type": "string", "description": "Value"}},
            ),
            lambda **kwargs: {"success": True, "value": kwargs.get("value")},
            hydrate=False,
        )

        toolset = registry.build_turn_toolset(["lazy_turn_tool"], include_meta=False)

        self.assertNotIn("lazy_turn_tool", registry._handlers)
        self.assertIn("lazy_turn_tool", toolset.list_tool_names())

        result = toolset.execute("lazy_turn_tool", value="x")

        self.assertTrue(result.success)
        self.assertEqual(result.data.get("value"), "x")
        self.assertNotIn("lazy_turn_tool", registry._handlers)

    def test_execute_handles_embedded_failure_payload(self):
        from tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="sample_fail_tool",
                description="Fails with payload flag",
                category="test",
                tier="read_only",
                parameters={},
                required_params=[],
            ),
            lambda: {"success": False, "error": "blocked by policy"},
        )

        result = registry.execute("sample_fail_tool")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "blocked by policy")


if __name__ == "__main__":
    unittest.main()
