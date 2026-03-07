"""
Tests for AE Agentic Support enhancements.
Covers: Agent Memory, Tool Isolation, Verification Loop.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, mock_open

from agents.agent_context import SharedContext
from tools.registry import ToolRegistry
from tools.base import ToolDefinition
from tools.catalog import ToolCatalogEntry
from agents.orchestrator import Orchestrator
from agents.diagnostic_agent import DiagnosticAgent
from agents.remediation_agent import RemediationAgent
from state.conversation_state import ConversationState, ConversationPhase
from state.issue_tracker import IssueTracker, MessageClassification

@patch("config.db.get_conn")
class TestAgentEnhancements:
    
    # ── 1. SharedContext Agent Memory ──
    def test_shared_context_agent_memory(self, mock_conn):
        ctx = SharedContext()
        ctx.conversation_id = "conv-mem"
        
        # Set memory for diagnostic agent
        ctx.set_memory("diagnostic_agent", "last_log_line", 450)
        assert ctx.get_memory("diagnostic_agent", "last_log_line") == 450
        
        # Verify isolation: remediation agent shouldn't see it by default
        assert ctx.get_memory("remediation_agent", "last_log_line") is None
        
        # Check get_all_memories
        mems = ctx.get_all_memories("diagnostic_agent")
        assert mems == {"last_log_line": 450}

    # ── 2. ToolRegistry Category Filtering ──
    def test_tool_registry_category_filtering(self, mock_conn):
        reg = ToolRegistry()
        
        # Mock diagnostic tool
        diag_tool = ToolDefinition(
            name="check_logs",
            description="check logs",
            category="logs",
            tier="read_only"
        )
        reg.register(diag_tool, lambda: "logs")
        
        # Mock remediation tool
        rem_tool = ToolDefinition(
            name="restart_wf",
            description="restart",
            category="remediation",
            tier="high_risk"
        )
        reg.register(rem_tool, lambda: "restarted")
        
        # Filter for diagnostic categories
        tools = reg.get_vertex_tools_filtered(
            rag_tool_names=["check_logs", "restart_wf"],
            allowed_categories=["logs"]
        )
        
        # Extract names from vertex schema
        names = [f["name"] for f in tools[0]["function_declarations"]]
        assert "check_logs" in names
        assert "restart_wf" not in names  # Should be filtered out

    @patch("rag.engine.get_rag_engine")
    def test_discover_tools_returns_usage_guidance(self, mock_get_rag, mock_conn):
        reg = ToolRegistry()
        tool = ToolDefinition(
            name="search_knowledge_base",
            description="Search KB and SOP content.",
            category="general",
            tier="read_only",
            parameters={"query": {"type": "string", "description": "Search query"}},
            required_params=["query"],
            use_when="You need historical or SOP context before acting.",
            avoid_when="You already have a request ID and need live status.",
            input_examples=[{"query": "dns resolution failure overnight batch"}],
            metadata={"tags": ["kb", "sop"]},
        )
        reg.register(tool, lambda **_: {"success": True}, hydrate=False)

        mock_rag = MagicMock()
        mock_rag.search_tools.return_value = [
            {
                "id": "tool-search_knowledge_base",
                "metadata": {"tool_name": "search_knowledge_base"},
                "rrf_score": 0.91,
            }
        ]
        mock_get_rag.return_value = mock_rag

        reg._ensure_meta_tools()
        result = reg.execute("discover_tools", query="find SOPs", top_k=3)

        assert result.success
        tool_card = result.data["tools"][0]
        assert tool_card["required_params"] == ["query"]
        assert tool_card["use_when"] == "You need historical or SOP context before acting."
        assert tool_card["avoid_when"] == "You already have a request ID and need live status."
        assert tool_card["input_examples"][0]["query"] == "dns resolution failure overnight batch"

    @patch("rag.engine.get_rag_engine")
    def test_discover_tools_guides_generic_runner_workflow(self, mock_get_rag, mock_conn):
        reg = ToolRegistry()
        reg.register_catalog_entry(
            ToolCatalogEntry.from_definition(
                ToolDefinition(
                    name="write_file_tool",
                    description="Write a file via AE workflow.",
                    category="automationedge",
                    tier="medium_risk",
                    parameters={"targetPath": {"type": "string", "description": "Path"}},
                    required_params=["targetPath"],
                    metadata={"source": "automationedge", "workflow_name": "FileWriterWorkflow"},
                ),
                source_ref="FileWriterWorkflow",
                hydration_mode="execute_via_generic_runner",
                metadata={"use_tool": "trigger_workflow"},
            ),
            handler_factory=lambda: (lambda **_: {"success": True}),
            hydrate=False,
        )

        mock_rag = MagicMock()
        mock_rag.search_tools.return_value = [
            {
                "id": "tool-write_file_tool",
                "metadata": {"tool_name": "write_file_tool"},
                "rrf_score": 0.88,
            }
        ]
        mock_get_rag.return_value = mock_rag

        reg._ensure_meta_tools()
        result = reg.execute("discover_tools", query="write a file", top_k=3)

        assert result.success
        tool_card = result.data["tools"][0]
        assert tool_card["registered"] is False
        assert tool_card["llm_callable"] is False
        assert tool_card["use_tool"] == "trigger_workflow"
        assert tool_card["latency_class"] == "medium"
        assert tool_card["mutating"] is True

    @patch("rag.engine.get_rag_engine")
    def test_discover_tools_reranks_toward_safer_direct_tool(self, mock_get_rag, mock_conn):
        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="diagnose_failed_request",
                description="Diagnose a failed request.",
                category="status",
                tier="read_only",
                metadata={"source": "mcp", "tags": ["diagnose", "request", "failure"]},
            ),
            lambda **_: {"success": True},
            hydrate=False,
        )
        reg.register(
            ToolDefinition(
                name="restart_failed_request",
                description="Restart a failed request.",
                category="remediation",
                tier="high_risk",
                metadata={"source": "automationedge", "tags": ["restart", "request"]},
            ),
            lambda **_: {"success": True},
            hydrate=False,
        )

        mock_rag = MagicMock()
        mock_rag.search_tools.return_value = [
            {
                "id": "tool-restart_failed_request",
                "metadata": {"tool_name": "restart_failed_request"},
                "rrf_score": 0.94,
            },
            {
                "id": "tool-diagnose_failed_request",
                "metadata": {"tool_name": "diagnose_failed_request"},
                "rrf_score": 0.72,
            },
        ]
        mock_get_rag.return_value = mock_rag

        reg._ensure_meta_tools()
        result = reg.execute("discover_tools", query="diagnose failed request", top_k=2)

        assert result.success
        assert result.data["tools"][0]["name"] == "diagnose_failed_request"
        assert result.data["tools"][0]["score"] > result.data["tools"][1]["score"]

    def test_turn_toolset_filtered_uses_ranked_candidates(self, mock_conn):
        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="diagnose_failed_request",
                description="Diagnose a failed request.",
                category="status",
                tier="read_only",
                metadata={"source": "mcp", "tags": ["diagnose", "request", "failure"]},
            ),
            lambda **_: {"success": True},
            hydrate=False,
        )
        reg.register(
            ToolDefinition(
                name="restart_failed_request",
                description="Restart a failed request.",
                category="remediation",
                tier="high_risk",
                metadata={"source": "automationedge", "tags": ["restart", "request"]},
            ),
            lambda **_: {"success": True},
            hydrate=False,
        )

        toolset = reg.build_turn_toolset_filtered(
            ["restart_failed_request", "diagnose_failed_request"],
            query="diagnose failed request",
            rag_hits=[
                {
                    "id": "tool-restart_failed_request",
                    "metadata": {"tool_name": "restart_failed_request"},
                    "rrf_score": 0.94,
                },
                {
                    "id": "tool-diagnose_failed_request",
                    "metadata": {"tool_name": "diagnose_failed_request"},
                    "rrf_score": 0.72,
                },
            ],
            max_rag_tools=1,
        )

        names = toolset.list_tool_names()
        assert "diagnose_failed_request" in names
        assert "restart_failed_request" not in names

    # ── 3. Orchestrator Context-Aware RAG (Integration check) ──
    @patch("state.issue_tracker.IssueTracker._load_from_db", return_value=None)
    @patch("agents.orchestrator.get_rag_engine")
    def test_orchestrator_query_enrichment(self, mock_get_rag, mock_load_db, mock_conn):
        mock_rag = MagicMock()
        mock_get_rag.return_value = mock_rag
        
        orch = Orchestrator()
        state = ConversationState()
        state.conversation_id = "test-rag"
        tracker = IssueTracker("test-rag")
        
        # Inject tracker directly into orch to avoid DB load
        orch.issue_trackers["test-rag"] = tracker
        
        # Create an active issue with error context
        issue = tracker.create_issue(title="Failure", description="Workflow failed")
        issue.workflows_involved = ["WfA"]
        issue.error_signatures = ["TimeoutError"]
        
        # Process message
        with patch.object(orch, "_build_system_prompt", return_value="prompt"):
            with patch.object(orch, "_classify_conversational_route", return_value="OPS"):
                # Mock classification to CONTINUE_EXISTING so it uses the issue we prepared
                with patch.object(tracker, "classify_message", return_value=(MessageClassification.CONTINUE_EXISTING, issue.issue_id)):
                    orch.handle_message("help", state)
                
                # Verify RAG was called with context
                # The Orchestrator should have enriched the query
                mock_rag.embed_query.assert_any_call(
                    "help (Context: Workflows: WfA Errors: TimeoutError)"
                )

    # ── 4. RemediationAgent Verification Loop ──
    @patch("state.issue_tracker.IssueTracker._load_from_db", return_value=None)
    @patch("agents.remediation_agent.Orchestrator")
    def test_remediation_agent_verification_loop(self, mock_orch_class, mock_load_db, mock_conn):
        # Mock orchestrator behavior to simulate a tool call success
        mock_orch = mock_orch_class.return_value
        mock_orch.handle_message.return_value = "Fix attempted."
        
        rem_agent = RemediationAgent()
        state = ConversationState()
        state.conversation_id = "test-loop"
        state.affected_workflows = ["Wf1"]
        
        # Simulate a successful tool call in the state log
        # We need to make sure this is in the last 3 calls as per logic
        state.log_tool_call("restart_workflow", {}, {}, True)
        
        # Mock handle_message to not clear the log or return something else
        result = rem_agent.handle("fix it", state=state)
        
        # Should request delegation to diagnostic_agent for verification
        assert result.delegation is not None, "Delegation not found in result"
        assert result.delegation.target_agent_id == "diagnostic_agent"
        assert "Verification" in result.delegation.reason

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
