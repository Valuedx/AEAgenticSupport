"""
Remediation agent - specializes in fixing issues, restarting workflows, and RCA.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.agent_context import SharedContext
from agents.orchestrator import Orchestrator
from agents.base_agent import (
    AgentCapability,
    AgentInfo,
    AgentResult,
    AgentStatus,
    BaseAgent,
    DelegationRequest,
)
from config.llm_client import llm_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.agents.remediation")

class RemediationAgent(BaseAgent):
    """
    Specialist agent for fixing issues.
    
    It focuses on:
    - Restarting failed workflows
    - Triggering corrective actions
    - Scaling resources (if applicable)
    - Notifying stakeholders
    - Generating Root Cause Analysis (RCA)
    """

    def __init__(self):
        self._orchestrator = Orchestrator()

    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="remediation_agent",
            name="Remediation Specialist",
            description=(
                "Specializes in automated fixes, restarting failed workflows, "
                "corrective actions, and root cause analysis (RCA)."
            ),
            capabilities=[AgentCapability.REMEDIATION.value],
            domains=["restart", "fix", "resolve", "execute", "trigger", "notify"],
            status=AgentStatus.ACTIVE,
            priority=50,
            version="1.0.0",
        )

    def can_handle(self, user_message: str, context: dict | None = None) -> float:
        """Score high for remediation-related keywords."""
        msg = user_message.lower()
        cues = ["restart", "fix", "resolve", "correct", "run", "do it", "execute", "trigger"]
        if any(cue in msg for cue in cues):
            return 0.8
        return 0.2

    def handle(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
        **kwargs,
    ) -> AgentResult:
        """Fixing loop using remediation tools."""
        state = kwargs.get("state")
        on_progress = kwargs.get("on_progress")

        if not state:
            return AgentResult(response="No state provided", success=False)

        # Execute with full tool discovery (no restrictive categories)
        response = self._orchestrator.handle_message(
            user_message=user_message,
            state=state,
            on_progress=on_progress,
        )

        # ── Verification Loop (Feature 6.1) ──
        # If we successfully executed a remediation tool, delegate back for verification.
        last_calls = state.tool_call_log[-3:]
        remediation_success = any(tc["success"] for tc in last_calls)
        
        delegation = None
        if remediation_success:
            logger.info("Remediation successful, delegating to diagnostic for verification")
            delegation = DelegationRequest(
                target_agent_id="diagnostic_agent",
                reason="Verification: Confirm fix success (check logs/status)",
                context={"verification_target": state.affected_workflows}
            )

        return AgentResult(
            response=response,
            success=True,
            tool_calls=[tc["tool"] for tc in state.tool_call_log[-5:]],
            findings=[{"category": f.category, "summary": f.summary} for f in state.findings[-3:]],
            delegation=delegation
        )
