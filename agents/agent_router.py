"""
Agent router — dispatches user messages to the best-matching agent.

The router:
1. Scores all registered agents against the incoming message.
2. Picks the best match (or falls back to the default orchestrator).
3. Handles delegation chains (agent A delegates to agent B).
4. Maintains a ``SharedContext`` for the full request lifecycle.

Delegation chain depth is capped at ``MAX_DELEGATION_DEPTH`` to prevent
infinite loops.
"""
from __future__ import annotations

import logging
from typing import Optional

from agents.base_agent import (
    AgentInvocation,
    AgentResult,
    BaseAgent,
    DelegationRequest,
)
from agents.agent_context import SharedContext
from agents.agent_registry import AgentRegistry, get_agent_registry
from config.llm_client import llm_client

logger = logging.getLogger("ops_agent.agents.router")
audit = logging.getLogger("ops_agent.audit")

MAX_DELEGATION_DEPTH = 5


def _score_agent(agent: BaseAgent, user_message: str, context: dict | None) -> float:
    """
    Compute a routing score for an agent given a user message.

    Uses the agent's own ``can_handle()`` method plus keyword matching
    against the agent's declared domains and capabilities.
    """
    base_score = agent.can_handle(user_message, context)
    msg_lower = user_message.lower()

    # Bonus for domain keyword matches
    domain_bonus = 0.0
    for domain in agent.info.domains:
        if domain.lower() in msg_lower:
            domain_bonus = max(domain_bonus, 0.2)

    # Bonus for capability keyword matches
    cap_bonus = 0.0
    for cap in agent.info.capabilities:
        if cap.lower() in msg_lower:
            cap_bonus = max(cap_bonus, 0.1)

    return min(1.0, base_score + domain_bonus + cap_bonus)


class AgentRouter:
    """
    Routes incoming messages to the best-matching agent.

    Supports delegation chains: if agent A returns a ``DelegationRequest``,
    the router dispatches to agent B, passing along the shared context.
    """

    def __init__(self, registry: AgentRegistry | None = None):
        self.registry = registry or get_agent_registry()

    # ── Main routing entry point ─────────────────────────────────────

    def route(
        self,
        user_message: str,
        conversation_id: str = "",
        user_id: str = "",
        context_overrides: dict | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Route a user message to the best agent and handle delegation.

        Parameters
        ----------
        user_message : str
            The user's message.
        conversation_id : str
            ID of the ongoing conversation.
        user_id : str
            ID of the user.
        context_overrides : dict, optional
            Extra data to inject into the shared context.
        **kwargs
            Passed through to the agent's ``handle()`` method
            (e.g., ``state``, ``on_progress``).

        Returns
        -------
        AgentResult
            The final response (possibly after a delegation chain).
        """
        shared = SharedContext(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if context_overrides:
            for k, v in context_overrides.items():
                shared.set(k, v)

        # Score all agents
        agents = self.registry.list_agents(active_only=True)
        if not agents:
            return AgentResult(
                response="No agents are currently available. Please try again later.",
                success=False,
            )

        scored = [
            (agent, _score_agent(agent, user_message, context_overrides))
            for agent in agents
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0].info.priority))

        best_agent, best_score = scored[0]

        shared.set_routing({
            "scored_agents": [
                {
                    "agent_id": a.info.agent_id,
                    "score": round(s, 3),
                }
                for a, s in scored
            ],
            "selected_agent_id": best_agent.agent_id,
            "selected_score": round(best_score, 3),
        })

        logger.info(
            "Router selected agent=%s score=%.3f for message='%s'",
            best_agent.agent_id, best_score, user_message[:80],
        )

        return self._execute_with_delegation(
            agent=best_agent,
            user_message=user_message,
            shared=shared,
            depth=0,
            **kwargs,
        )

    # ── Direct dispatch (bypass routing) ─────────────────────────────

    def dispatch_to(
        self,
        agent_id: str,
        user_message: str,
        conversation_id: str = "",
        user_id: str = "",
        **kwargs,
    ) -> AgentResult:
        """Dispatch directly to a specific agent by ID."""
        agent = self.registry.get(agent_id)
        if not agent:
            return AgentResult(
                response=f"Agent '{agent_id}' not found.",
                success=False,
            )
        shared = SharedContext(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        return self._execute_with_delegation(
            agent=agent,
            user_message=user_message,
            shared=shared,
            depth=0,
            **kwargs,
        )

    # ── Delegation chain executor ────────────────────────────────────

    def _execute_with_delegation(
        self,
        agent: BaseAgent,
        user_message: str,
        shared: SharedContext,
        depth: int,
        **kwargs,
    ) -> AgentResult:
        if depth >= MAX_DELEGATION_DEPTH:
            logger.warning(
                "Delegation depth limit (%d) reached — stopping chain.",
                MAX_DELEGATION_DEPTH,
            )
            return AgentResult(
                response=(
                    "I reached the maximum number of agent handoffs. "
                    "Here is what I have so far. Please let me know how "
                    "to proceed."
                ),
                success=False,
            )

        # Create invocation record
        invocation = AgentInvocation(
            agent_id=agent.agent_id,
            conversation_id=shared.conversation_id,
            user_message=user_message[:200],
        )

        audit.info(
            "AGENT_INVOKE agent=%s depth=%d msg='%s'",
            agent.agent_id, depth, user_message[:100],
        )

        try:
            result = agent.handle(
                user_message=user_message,
                context=shared.to_dict(),
                shared_context=shared,
                **kwargs,
            )
        except Exception as exc:
            logger.error(
                "Agent %s failed: %s", agent.agent_id, exc, exc_info=True,
            )
            result = AgentResult(
                response=(
                    "I encountered an error during processing. "
                    "The operations team has been notified."
                ),
                success=False,
            )

        invocation.complete(result)
        self.registry.log_invocation(invocation)

        # Store result on the blackboard
        shared.store_agent_result(agent.agent_id, {
            "response": result.response[:500],
            "success": result.success,
            "confidence": result.confidence,
            "tool_calls_count": len(result.tool_calls),
            "findings_count": len(result.findings),
        })

        # Handle delegation
        if result.wants_delegation:
            delegation = result.delegation
            target = self._resolve_delegation_target(delegation)
            if target:
                shared.record_handoff(
                    from_agent=agent.agent_id,
                    to_agent=target.agent_id,
                    reason=delegation.reason,
                    context_snapshot=delegation.context,
                )
                target.on_delegate_receive(delegation, parent_result=result)

                audit.info(
                    "AGENT_DELEGATE from=%s to=%s reason='%s'",
                    agent.agent_id, target.agent_id,
                    delegation.reason[:100],
                )

                delegate_result = self._execute_with_delegation(
                    agent=target,
                    user_message=user_message,
                    shared=shared,
                    depth=depth + 1,
                    **kwargs,
                )

                # Compose: prepend original agent's partial response
                if result.response.strip():
                    combined_response = (
                        f"{result.response}\n\n"
                        f"---\n\n"
                        f"{delegate_result.response}"
                    )
                else:
                    combined_response = delegate_result.response

                return AgentResult(
                    response=combined_response,
                    success=delegate_result.success,
                    confidence=delegate_result.confidence,
                    tool_calls=result.tool_calls + delegate_result.tool_calls,
                    findings=result.findings + delegate_result.findings,
                    metadata={
                        "delegation_chain": [
                            agent.agent_id,
                            target.agent_id,
                        ],
                    },
                )
            else:
                logger.warning(
                    "Delegation target not found: agent_id=%s capability=%s",
                    delegation.target_agent_id,
                    delegation.target_capability,
                )

        return result

    def _resolve_delegation_target(
        self, delegation: DelegationRequest,
    ) -> Optional[BaseAgent]:
        """Find the agent to delegate to."""
        # Try by explicit agent ID first
        if delegation.target_agent_id:
            agent = self.registry.get(delegation.target_agent_id)
            if agent and agent.is_active:
                return agent

        # Try by capability
        if delegation.target_capability:
            candidates = self.registry.get_by_capability(
                delegation.target_capability
            )
            if candidates:
                candidates.sort(key=lambda a: a.info.priority)
                return candidates[0]

        return None


# ── Module-level singleton ───────────────────────────────────────────

_agent_router: Optional[AgentRouter] = None


def get_agent_router() -> AgentRouter:
    global _agent_router
    if _agent_router is None:
        _agent_router = AgentRouter()
    return _agent_router
