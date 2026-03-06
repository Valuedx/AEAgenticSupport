"""
Remediation agent - specializes in fixing issues, restarting workflows, and RCA.
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
)
from agents.agent_context import SharedContext
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
        return AgentResult(
            response=(
                "I've identified the necessary corrective actions. "
                "I'll now attempt to restart the failed workflow and notify the team."
            ),
            findings=[{"agent": "remediation", "status": "started_resolution"}]
        )
