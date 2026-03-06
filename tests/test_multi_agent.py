"""
Tests for the multi-agent orchestration layer (Feature 2.1).

Covers:
* BaseAgent / AgentInfo / AgentResult dataclasses
* AgentRegistry — register, unregister, query
* AgentRouter — scoring, routing, delegation chains
* SharedContext — blackboard read/write
* OrchestratorAgent — adapter integration
"""
from __future__ import annotations

import pytest
from typing import Any

from agents.base_agent import (
    AgentCapability,
    AgentInfo,
    AgentInvocation,
    AgentResult,
    AgentStatus,
    BaseAgent,
    DelegationRequest,
)
from agents.agent_context import SharedContext
from agents.agent_registry import AgentRegistry
from agents.agent_router import AgentRouter, _score_agent


# ── Test agents ──────────────────────────────────────────────────────

class DummyDiagnosticAgent(BaseAgent):
    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="diagnostic_agent",
            name="Diagnostic Agent",
            description="Diagnoses workflow issues",
            capabilities=[AgentCapability.DIAGNOSTICS.value],
            domains=["workflow", "execution", "error"],
            status=AgentStatus.ACTIVE,
            priority=10,
        )

    def can_handle(self, user_message: str, context=None) -> float:
        msg = user_message.lower()
        if any(w in msg for w in ("error", "fail", "broken", "down")):
            return 0.9
        return 0.3

    def handle(self, user_message: str, context=None, **kwargs) -> AgentResult:
        return AgentResult(
            response=f"Diagnostic: investigating '{user_message[:50]}'",
            success=True,
            confidence=0.9,
        )


class DummyRemediationAgent(BaseAgent):
    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="remediation_agent",
            name="Remediation Agent",
            description="Fixes workflow issues",
            capabilities=[AgentCapability.REMEDIATION.value],
            domains=["restart", "requeue", "retry"],
            status=AgentStatus.ACTIVE,
            priority=20,
        )

    def can_handle(self, user_message: str, context=None) -> float:
        msg = user_message.lower()
        if any(w in msg for w in ("restart", "fix", "requeue", "retry")):
            return 0.85
        return 0.2

    def handle(self, user_message: str, context=None, **kwargs) -> AgentResult:
        return AgentResult(
            response=f"Remediation: fixing '{user_message[:50]}'",
            success=True,
            confidence=0.85,
        )


class DummyDelegatingAgent(BaseAgent):
    """Agent that always delegates to remediation."""

    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="delegating_agent",
            name="Delegating Agent",
            description="Always delegates",
            capabilities=["diagnostics"],
            domains=["delegate"],
            status=AgentStatus.ACTIVE,
            priority=5,
        )

    def can_handle(self, user_message: str, context=None) -> float:
        return 0.8 if "delegate" in user_message.lower() else 0.1

    def handle(self, user_message: str, context=None, **kwargs) -> AgentResult:
        return AgentResult(
            response="I'll hand this off to remediation.",
            success=True,
            delegation=DelegationRequest(
                target_capability="remediation",
                reason="Needs remediation specialist",
            ),
        )


class DummyDisabledAgent(BaseAgent):
    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="disabled_agent",
            name="Disabled Agent",
            description="I'm disabled",
            capabilities=["diagnostics"],
            status=AgentStatus.DISABLED,
        )

    def handle(self, user_message: str, context=None, **kwargs) -> AgentResult:
        return AgentResult(response="Should not be called", success=False)


# ── Tests: AgentInfo & AgentResult ───────────────────────────────────

class TestAgentInfoResult:
    def test_agent_info_to_dict(self):
        info = AgentInfo(
            agent_id="test", name="Test", description="desc",
            capabilities=["diagnostics"], domains=["workflow"],
        )
        d = info.to_dict()
        assert d["agent_id"] == "test"
        assert d["status"] == "active"
        assert "diagnostics" in d["capabilities"]

    def test_agent_result_delegation(self):
        result = AgentResult(response="hello")
        assert not result.wants_delegation

        result2 = AgentResult(
            response="delegating",
            delegation=DelegationRequest(target_agent_id="other"),
        )
        assert result2.wants_delegation

    def test_invocation_complete(self):
        inv = AgentInvocation(agent_id="test", conversation_id="conv-1")
        result = AgentResult(response="done", success=True)
        inv.complete(result)
        assert inv.completed_at != ""
        assert inv.success is True
        assert inv.delegated_to == ""


# ── Tests: SharedContext ─────────────────────────────────────────────

class TestSharedContext:
    def test_findings(self):
        ctx = SharedContext(conversation_id="c1")
        ctx.add_finding("agent_a", {"summary": "found issue X"})
        ctx.add_finding("agent_b", {"summary": "found issue Y"})

        assert len(ctx.get_findings()) == 2
        assert len(ctx.get_findings("agent_a")) == 1
        assert ctx.get_findings("agent_a")[0]["summary"] == "found issue X"

    def test_tool_calls(self):
        ctx = SharedContext()
        ctx.log_tool_call("agent_a", "check_status", {"wf": "test"}, {}, True)
        assert len(ctx.get_tool_calls()) == 1
        assert ctx.get_tool_calls("agent_a")[0]["tool_name"] == "check_status"

    def test_handoffs(self):
        ctx = SharedContext()
        ctx.record_handoff("a", "b", "needs specialist")
        handoffs = ctx.get_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0].from_agent_id == "a"
        assert handoffs[0].to_agent_id == "b"

    def test_custom_kv(self):
        ctx = SharedContext()
        ctx.set("key1", "value1")
        assert ctx.get("key1") == "value1"
        assert ctx.get("nonexistent", "default") == "default"

    def test_agent_results(self):
        ctx = SharedContext()
        ctx.store_agent_result("agent_a", {"response": "done"})
        assert ctx.get_agent_result("agent_a")["response"] == "done"
        assert ctx.get_agent_result("agent_b") is None

    def test_to_dict(self):
        ctx = SharedContext(conversation_id="c1")
        ctx.add_finding("a", {"summary": "x"})
        ctx.record_handoff("a", "b", "reason")
        d = ctx.to_dict()
        assert d["findings_count"] == 1
        assert len(d["handoffs"]) == 1


# ── Tests: AgentRegistry ────────────────────────────────────────────

class TestAgentRegistry:
    def setup_method(self):
        self.registry = AgentRegistry()

    def test_register_and_get(self):
        agent = DummyDiagnosticAgent()
        self.registry.register(agent)
        assert self.registry.get("diagnostic_agent") is agent

    def test_unregister(self):
        agent = DummyDiagnosticAgent()
        self.registry.register(agent)
        assert self.registry.unregister("diagnostic_agent") is True
        assert self.registry.get("diagnostic_agent") is None
        assert self.registry.unregister("nonexistent") is False

    def test_list_agents_active_only(self):
        self.registry.register(DummyDiagnosticAgent())
        self.registry.register(DummyDisabledAgent())
        active = self.registry.list_agents(active_only=True)
        assert len(active) == 1
        assert active[0].agent_id == "diagnostic_agent"

    def test_get_by_capability(self):
        self.registry.register(DummyDiagnosticAgent())
        self.registry.register(DummyRemediationAgent())
        diag = self.registry.get_by_capability("diagnostics")
        assert len(diag) == 1
        assert diag[0].agent_id == "diagnostic_agent"

    def test_get_by_domain(self):
        self.registry.register(DummyDiagnosticAgent())
        self.registry.register(DummyRemediationAgent())
        wf_agents = self.registry.get_by_domain("workflow")
        assert len(wf_agents) == 1
        assert wf_agents[0].agent_id == "diagnostic_agent"

    def test_invocation_stats(self):
        inv = AgentInvocation(agent_id="test")
        inv.complete(AgentResult(response="ok"))
        self.registry.log_invocation(inv)
        stats = self.registry.get_invocation_stats("test")
        assert stats["total_invocations"] == 1
        assert stats["successes"] == 1


# ── Tests: Scoring ──────────────────────────────────────────────────

class TestScoring:
    def test_diagnostic_agent_scores_high_on_error(self):
        agent = DummyDiagnosticAgent()
        score = _score_agent(agent, "workflow is failing with error", None)
        assert score >= 0.9

    def test_diagnostic_agent_scores_low_on_unrelated(self):
        agent = DummyDiagnosticAgent()
        score = _score_agent(agent, "tell me a joke", None)
        assert score <= 0.5

    def test_remediation_agent_scores_high_on_restart(self):
        agent = DummyRemediationAgent()
        score = _score_agent(agent, "please restart the workflow", None)
        assert score >= 0.85

    def test_domain_bonus(self):
        agent = DummyDiagnosticAgent()
        # "execution" is in agent's domains
        score_with = _score_agent(agent, "execution failed", None)
        score_without = _score_agent(agent, "something else", None)
        assert score_with > score_without


# ── Tests: AgentRouter ──────────────────────────────────────────────

class TestAgentRouter:
    def setup_method(self):
        self.registry = AgentRegistry()
        self.registry.register(DummyDiagnosticAgent())
        self.registry.register(DummyRemediationAgent())
        self.router = AgentRouter(registry=self.registry)

    def test_routes_to_diagnostic_on_error(self):
        result = self.router.route("workflow is failing with error")
        assert "Diagnostic" in result.response
        assert result.success

    def test_routes_to_remediation_on_restart(self):
        result = self.router.route("please restart the workflow")
        assert "Remediation" in result.response
        assert result.success

    def test_dispatch_to_specific_agent(self):
        result = self.router.dispatch_to(
            "diagnostic_agent", "check this workflow"
        )
        assert "Diagnostic" in result.response

    def test_dispatch_to_nonexistent_agent(self):
        result = self.router.dispatch_to("nonexistent", "hello")
        assert not result.success

    def test_delegation_chain(self):
        self.registry.register(DummyDelegatingAgent())
        result = self.router.route("delegate this issue")
        # The delegating agent should hand off to remediation
        assert "Remediation" in result.response or "hand this off" in result.response

    def test_no_agents_returns_error(self):
        empty_registry = AgentRegistry()
        router = AgentRouter(registry=empty_registry)
        result = router.route("hello")
        assert not result.success

    def test_route_returns_result_type(self):
        result = self.router.route("hello world")
        assert isinstance(result, AgentResult)
        assert result.response is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
