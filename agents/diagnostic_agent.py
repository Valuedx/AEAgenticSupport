"""
Diagnostic agent - specializes in investigating logs, status, and failures.
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
from agents.agent_context import SharedContext
from agents.orchestrator import Orchestrator
from config.llm_client import llm_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.agents.diagnostic")

class DiagnosticAgent(BaseAgent):
    """
    Specialist agent for technical diagnostics.
    
    It focuses on:
    - Checking workflow status
    - Retrieving and analyzing execution logs
    - Checking service dependencies
    - Verifying file availability
    """

    def __init__(self):
        self._orchestrator = Orchestrator()

    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="diagnostic_agent",
            name="Diagnostic Specialist",
            description=(
                "Specializes in deep technical investigation of RPA workflow "
                "failures, log analysis, and infrastructure health checks."
            ),
            capabilities=[AgentCapability.DIAGNOSTICS.value],
            domains=["logs", "errors", "failures", "status", "files", "database"],
            status=AgentStatus.ACTIVE,
            priority=50,
            version="1.0.0",
        )

    def can_handle(self, user_message: str, context: dict | None = None) -> float:
        """Score high for investigation-related keywords."""
        msg = user_message.lower()
        cues = ["logs", "error", "fail", "check", "status", "why", "debug", "investigate", "where"]
        if any(cue in msg for cue in cues):
            return 0.8
        return 0.3

    def handle(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Investigation loop using diagnostic tools.
        """
        state = kwargs.get("state")
        on_progress = kwargs.get("on_progress")
        
        if not state:
            return AgentResult(response="No state provided", success=False)

        # Execute using restricted categories
        response = self._orchestrator.handle_message(
            user_message=user_message,
            state=state,
            on_progress=on_progress,
            allowed_categories=["status", "logs", "dependency", "file"]
        )

        return AgentResult(
            response=response,
            success=True,
            tool_calls=[tc["tool"] for tc in state.tool_call_log[-5:]],
            findings=[{"category": f.category, "summary": f.summary} for f in state.findings[-3:]]
        )
