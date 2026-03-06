"""
Agent registry — central catalog of all registered agents.

Agents self-register via ``agent_registry.register(agent)`` and the
router uses the registry to discover, score, and dispatch to agents.

Features
--------
* Register / unregister agents at runtime (hot-reload).
* Query agents by capability, domain, or ID.
* Track agent health / invocation stats for routing decisions.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from agents.base_agent import (
    BaseAgent,
    AgentInfo,
    AgentInvocation,
    AgentResult,
    AgentStatus,
)

logger = logging.getLogger("ops_agent.agents.registry")


class AgentRegistry:
    """
    Central catalog of all agents available in the system.

    Thread-safe — multiple threads (e.g., the Flask server) can register
    or query agents concurrently.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._agents: dict[str, BaseAgent] = {}
        self._invocation_log: list[dict] = []
        self._max_log_size = 500

    # ── Registration ─────────────────────────────────────────────────

    def register(self, agent: BaseAgent) -> None:
        """Register an agent.  Replaces if agent_id already exists."""
        with self._lock:
            info = agent.info
            self._agents[info.agent_id] = agent
            logger.info(
                "Registered agent: %s (%s) — capabilities=%s domains=%s",
                info.agent_id, info.name,
                info.capabilities, info.domains,
            )

    def unregister(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id in self._agents:
                del self._agents[agent_id]
                logger.info("Unregistered agent: %s", agent_id)
                return True
            return False

    # ── Queries ──────────────────────────────────────────────────────

    def get(self, agent_id: str) -> Optional[BaseAgent]:
        with self._lock:
            return self._agents.get(agent_id)

    def list_agents(self, active_only: bool = True) -> list[BaseAgent]:
        with self._lock:
            agents = list(self._agents.values())
        if active_only:
            agents = [a for a in agents if a.is_active]
        return agents

    def list_agent_info(self, active_only: bool = True) -> list[dict]:
        return [a.info.to_dict() for a in self.list_agents(active_only)]

    def get_by_capability(self, capability: str) -> list[BaseAgent]:
        """Return all active agents that declare a given capability."""
        return [
            a for a in self.list_agents(active_only=True)
            if capability in a.info.capabilities
        ]

    def get_by_domain(self, domain: str) -> list[BaseAgent]:
        """Return all active agents that claim expertise in a domain."""
        return [
            a for a in self.list_agents(active_only=True)
            if domain in a.info.domains
        ]

    def get_default_orchestrator(self) -> Optional[BaseAgent]:
        """Return the primary orchestrator agent (if registered)."""
        orchestrators = self.get_by_capability("orchestration")
        if orchestrators:
            # Pick the highest priority (lowest number)
            orchestrators.sort(key=lambda a: a.info.priority)
            return orchestrators[0]
        # Fallback: return any agent
        agents = self.list_agents()
        return agents[0] if agents else None

    # ── Invocation tracking ──────────────────────────────────────────

    def log_invocation(self, invocation: AgentInvocation) -> None:
        with self._lock:
            self._invocation_log.append(invocation.to_dict())
            if len(self._invocation_log) > self._max_log_size:
                self._invocation_log = self._invocation_log[
                    -self._max_log_size:
                ]

    def get_invocation_stats(self, agent_id: str = "") -> dict:
        """Return basic invocation statistics."""
        with self._lock:
            log = self._invocation_log
        if agent_id:
            log = [r for r in log if r.get("agent_id") == agent_id]
        total = len(log)
        successes = sum(1 for r in log if r.get("success", True))
        delegated = sum(1 for r in log if r.get("delegated_to"))
        return {
            "total_invocations": total,
            "successes": successes,
            "failures": total - successes,
            "delegations": delegated,
        }

    # ── Serialisation ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        agents = self.list_agent_info(active_only=False)
        return {
            "agent_count": len(agents),
            "agents": agents,
        }


# ── Module-level singleton ───────────────────────────────────────────

_agent_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    global _agent_registry
    if _agent_registry is None:
        _agent_registry = AgentRegistry()
    return _agent_registry
