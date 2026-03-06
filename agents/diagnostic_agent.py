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
        
        system_prompt = (
            "You are the Diagnostic Specialist. Your ONLY goal is to find the root cause "
            "of the reported issue. You have access to technical diagnostic tools.\n\n"
            "Rules:\n"
            "1. Focus on logs, status, and configuration.\n"
            "2. Once you have a clear finding (or have ruled out local causes), "
            "summarize your findings and hand back to the supervisor."
        )

        # In a real implementation, we'd use a small ReAct loop here too, 
        # but for this demo, we'll use tool_registry directly if needed or 
        # just act as a specialized prompt wrapper for now.
        # Ideally, we'd move the Orchestrator's internal loop logic into a shareable mixin.
        
        # For now, let's keep it simple: inform the user what we are doing
        return AgentResult(
            response=(
                "I'm now taking over the technical investigation. "
                "I'll start by reviewing the execution logs for the affected workflows."
            ),
            findings=[{"agent": "diagnostic", "status": "started_investigation"}]
        )
