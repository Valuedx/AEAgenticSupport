"""
Orchestrator agent — wraps the existing ``Orchestrator`` class as a
``BaseAgent`` so it can participate in the multi-agent routing system.

This is the default agent: it handles all ops-support messages unless
a more specialised agent scores higher.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import (
    AgentCapability,
    AgentInfo,
    AgentResult,
    AgentStatus,
    BaseAgent,
    DelegationRequest,
)
from agents.orchestrator import Orchestrator
from agents.agent_context import SharedContext
from gateway.progress import ProgressCallback, create_noop_progress
from state.conversation_state import ConversationState, ConversationPhase

logger = logging.getLogger("ops_agent.agents.orchestrator_agent")


class OrchestratorAgent(BaseAgent):
    """
    Wraps the legacy ``Orchestrator`` class as a ``BaseAgent``.

    This is the default "catch-all" agent.  It handles the full
    investigation → approval → remediation → RCA lifecycle using the
    existing monolithic orchestrator loop.

    Other specialist agents can delegate back to this for general work,
    or it can delegate *to* specialists for domain-specific tasks.
    """

    def __init__(self):
        self._orchestrator = Orchestrator()

    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="ops_orchestrator",
            name="Ops Orchestrator",
            description=(
                "Default orchestrator for AutomationEdge ops support. "
                "Handles investigation, remediation, approval workflows, "
                "and RCA generation for RPA workflow issues."
            ),
            capabilities=[
                AgentCapability.ORCHESTRATION.value,
                AgentCapability.DIAGNOSTICS.value,
                AgentCapability.REMEDIATION.value,
                AgentCapability.KNOWLEDGE.value,
            ],
            domains=[
                "workflow", "execution", "rpa", "automation",
                "scheduling", "queue", "agent", "batch",
            ],
            status=AgentStatus.ACTIVE,
            priority=100,  # High number = low priority (fallback)
            version="2.0.0",
        )

    def can_handle(self, user_message: str, context: dict | None = None) -> float:
        """
        The orchestrator can handle anything — it's the default.
        Returns a moderate baseline score so specialists can outbid it.
        """
        return 0.4

    def handle(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Delegate to the existing ``Orchestrator.handle_message()``.

        Expected kwargs:
        * ``state``       — ``ConversationState`` instance.
        * ``on_progress`` — ``ProgressCallback`` instance.
        * ``shared_context`` — ``SharedContext`` instance (optional).
        """
        state: ConversationState | None = kwargs.get("state")
        on_progress: ProgressCallback | None = kwargs.get("on_progress")
        shared: SharedContext | None = kwargs.get("shared_context")

        if not state:
            return AgentResult(
                response="Internal error: no conversation state provided.",
                success=False,
            )

        try:
            # ── A2A Delegation check (Feature 2.1) ──
            # If the user is asking for a restart/fix and we aren't already in execution phase,
            # delegate to the remediation specialist.
            if any(w in user_message.lower() for w in ("restart", "fix", "resolve", "run tool")):
                if state.phase != ConversationPhase.EXECUTING:
                    logger.info("Orchestrator delegating to remediation_agent")
                    return AgentResult(
                        response="I'm handing this over to our Remediation Specialist to execute the fix.",
                        delegation=DelegationRequest(
                            target_agent_id="remediation_agent",
                            reason="User requested corrective action",
                            context={"phase": state.phase.value}
                        )
                    )

            # If the user is asking "Why" or for "Logs", delegate to diagnostics.
            if any(w in user_message.lower() for w in ("why", "logs", "debug", "investigate")):
                if state.phase == ConversationPhase.IDLE:
                    logger.info("Orchestrator delegating to diagnostic_agent")
                    return AgentResult(
                        response="I'll have our Diagnostic Specialist look into the logs and system status for you.",
                        delegation=DelegationRequest(
                            target_agent_id="diagnostic_agent",
                            reason="User requested technical investigation",
                            context={"phase": state.phase.value}
                        )
                    )

            response_text = self._orchestrator.handle_message(
                user_message=user_message,
                state=state,
                on_progress=on_progress,
                allowed_categories=["general", "meta"]
            )

            # Propagate findings to the shared context
            if shared and state.findings:
                for finding in state.findings[-5:]:
                    shared.add_finding(self.info.agent_id, {
                        "category": finding.category,
                        "summary": finding.summary,
                        "severity": finding.severity,
                    })

            return AgentResult(
                response=response_text,
                success=True,
                confidence=0.8,
                tool_calls=[
                    {"tool": tc["tool"], "success": tc["success"]}
                    for tc in state.tool_call_log[-10:]
                ],
                findings=[
                    {"category": f.category, "summary": f.summary}
                    for f in state.findings[-5:]
                ],
            )

        except Exception as exc:
            logger.error(
                "OrchestratorAgent failed: %s", exc, exc_info=True,
            )
            return AgentResult(
                response=(
                    "I encountered an error during investigation. "
                    "The operations team has been notified."
                ),
                success=False,
            )

    @property
    def orchestrator(self) -> Orchestrator:
        """Expose the underlying orchestrator for backward compatibility."""
        return self._orchestrator
