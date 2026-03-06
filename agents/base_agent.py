"""
Base agent — abstract interface for all agentic components.

Every specialist agent (diagnostic, remediation, orchestrator, etc.)
inherits from ``BaseAgent`` and implements ``handle()``.

Key design choices
------------------
* **Capability declaration** — each agent publishes the domains/topics it
  can handle so the router can match incoming messages.
* **Structured result** — ``AgentResult`` carries the response text plus
  metadata (tool calls made, delegation requests, confidence, etc.) so
  the orchestrator can compose multi-agent flows.
* **Delegation** — an agent can request handoff to another agent by
  returning a delegation in its result, enabling chained workflows.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


logger = logging.getLogger("ops_agent.agents.base")


# ── Agent capability and status ──────────────────────────────────────

class AgentStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class AgentCapability(Enum):
    """Well-known capability tags used for routing."""
    DIAGNOSTICS = "diagnostics"
    REMEDIATION = "remediation"
    MONITORING = "monitoring"
    KNOWLEDGE = "knowledge"
    NOTIFICATION = "notification"
    GENERAL = "general"
    ORCHESTRATION = "orchestration"


@dataclass
class AgentInfo:
    """Static metadata that describes an agent to the router."""
    agent_id: str
    name: str
    description: str
    capabilities: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    status: AgentStatus = AgentStatus.ACTIVE
    priority: int = 50  # lower = higher priority for tie-breaking
    version: str = "1.0.0"

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities,
            "domains": self.domains,
            "status": self.status.value,
            "priority": self.priority,
            "version": self.version,
        }


# ── Agent result ─────────────────────────────────────────────────────

@dataclass
class DelegationRequest:
    """Represents a request to hand off work to another agent."""
    target_agent_id: str = ""
    target_capability: str = ""
    reason: str = ""
    context: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    """Structured output from an agent handling a message."""
    response: str
    success: bool = True
    confidence: float = 1.0
    tool_calls: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    delegation: Optional[DelegationRequest] = None
    metadata: dict = field(default_factory=dict)

    @property
    def wants_delegation(self) -> bool:
        return self.delegation is not None


# ── Agent invocation record ──────────────────────────────────────────

@dataclass
class AgentInvocation:
    """Audit log entry for an agent invocation."""
    invocation_id: str = field(
        default_factory=lambda: f"inv-{uuid.uuid4().hex[:8]}"
    )
    agent_id: str = ""
    conversation_id: str = ""
    user_message: str = ""
    started_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    completed_at: str = ""
    result_summary: str = ""
    delegated_to: str = ""
    success: bool = True

    def complete(self, result: AgentResult):
        self.completed_at = datetime.now().isoformat()
        self.result_summary = result.response[:200]
        self.success = result.success
        if result.delegation:
            self.delegated_to = (
                result.delegation.target_agent_id
                or result.delegation.target_capability
            )

    def to_dict(self) -> dict:
        return {
            "invocation_id": self.invocation_id,
            "agent_id": self.agent_id,
            "conversation_id": self.conversation_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "success": self.success,
            "delegated_to": self.delegated_to,
        }


# ── Abstract base class ─────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Base class for all agents in the multi-agent system.

    Subclasses must implement:
    * ``info`` — property returning ``AgentInfo`` metadata.
    * ``handle()`` — process a user message and return ``AgentResult``.

    Optional overrides:
    * ``can_handle()`` — quick check if this agent is relevant (default True).
    * ``on_delegate_receive()`` — receive context from a delegating agent.
    """

    @property
    @abstractmethod
    def info(self) -> AgentInfo:
        """Return static metadata describing this agent."""
        ...

    @abstractmethod
    def handle(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Process a user message and return a structured result.

        Parameters
        ----------
        user_message : str
            The raw user message text.
        context : dict, optional
            Shared context from the agent router or a delegating agent.
        **kwargs
            Additional keyword arguments (e.g., ``state``, ``on_progress``).

        Returns
        -------
        AgentResult
            The structured response including optional delegation requests.
        """
        ...

    def can_handle(self, user_message: str, context: dict | None = None) -> float:
        """
        Return a confidence score (0.0–1.0) indicating how relevant this
        agent is for the given message.  Used by the router for scoring.

        The default implementation always returns 0.5 (neutral).
        Override in subclasses for smarter routing.
        """
        return 0.5

    def on_delegate_receive(
        self,
        delegation: DelegationRequest,
        parent_result: AgentResult | None = None,
    ) -> None:
        """
        Hook called when this agent receives a delegation from another.
        Override to pre-load context, adjust behaviour, etc.
        """
        pass

    @property
    def agent_id(self) -> str:
        return self.info.agent_id

    @property
    def is_active(self) -> bool:
        return self.info.status == AgentStatus.ACTIVE

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.agent_id}>"
