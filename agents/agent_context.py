"""
Agent context — shared blackboard for multi-agent collaboration.

Provides a ``SharedContext`` that acts as a blackboard pattern for
agents to read and write shared state during a multi-agent workflow.
This enables:
* The router to pass routing decisions downstream.
* A diagnostic agent to share findings with a remediation agent.
* The orchestrator to accumulate results from multiple sub-agents.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("ops_agent.agents.context")


@dataclass
class AgentHandoff:
    """Record of a handoff between two agents."""
    from_agent_id: str
    to_agent_id: str
    reason: str
    context_snapshot: dict = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )


class SharedContext:
    """
    Thread-safe shared blackboard for multi-agent collaboration.

    Agents read/write to named slots on the blackboard.  The router
    creates one ``SharedContext`` per request and passes it to all
    agents in the chain.

    Slots
    -----
    * ``findings``   — accumulated investigative findings from all agents.
    * ``tool_calls`` — combined tool call log across agents.
    * ``handoffs``   — ordered list of delegation/handoff records.
    * ``routing``    — metadata from the router (matched agents, scores).
    * ``custom``     — arbitrary key-value pairs for domain-specific use.
    """

    def __init__(self, conversation_id: str = "", user_id: str = ""):
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.created_at = datetime.now().isoformat()

        self._lock = threading.Lock()

        # Shared slots
        self._findings: list[dict] = []
        self._tool_calls: list[dict] = []
        self._handoffs: list[AgentHandoff] = []
        self._routing: dict[str, Any] = {}
        self._custom: dict[str, Any] = {}
        self._agent_results: dict[str, dict] = {}
        self._memories: dict[str, dict[str, Any]] = {}

    # ── Findings ─────────────────────────────────────────────────────

    def add_finding(self, agent_id: str, finding: dict) -> None:
        with self._lock:
            finding["_source_agent"] = agent_id
            finding["_timestamp"] = datetime.now().isoformat()
            self._findings.append(finding)

    def get_findings(self, agent_id: str = "") -> list[dict]:
        with self._lock:
            if agent_id:
                return [
                    f for f in self._findings
                    if f.get("_source_agent") == agent_id
                ]
            return list(self._findings)

    # ── Tool calls ───────────────────────────────────────────────────

    def log_tool_call(
        self, agent_id: str, tool_name: str,
        params: dict, result: Any, success: bool,
    ) -> None:
        with self._lock:
            self._tool_calls.append({
                "agent_id": agent_id,
                "tool_name": tool_name,
                "params": params,
                "result": result,
                "success": success,
                "timestamp": datetime.now().isoformat(),
            })

    def get_tool_calls(self, agent_id: str = "") -> list[dict]:
        with self._lock:
            if agent_id:
                return [
                    t for t in self._tool_calls
                    if t.get("agent_id") == agent_id
                ]
            return list(self._tool_calls)

    # ── Handoffs ─────────────────────────────────────────────────────

    def record_handoff(
        self, from_agent: str, to_agent: str,
        reason: str, context_snapshot: dict | None = None,
    ) -> None:
        with self._lock:
            self._handoffs.append(AgentHandoff(
                from_agent_id=from_agent,
                to_agent_id=to_agent,
                reason=reason,
                context_snapshot=context_snapshot or {},
            ))

    def get_handoffs(self) -> list[AgentHandoff]:
        with self._lock:
            return list(self._handoffs)

    # ── Routing metadata ─────────────────────────────────────────────

    def set_routing(self, routing: dict) -> None:
        with self._lock:
            self._routing = routing

    def get_routing(self) -> dict:
        with self._lock:
            return dict(self._routing)

    # ── Agent results ────────────────────────────────────────────────

    def store_agent_result(self, agent_id: str, result_summary: dict) -> None:
        with self._lock:
            self._agent_results[agent_id] = {
                **result_summary,
                "_stored_at": datetime.now().isoformat(),
            }

    def get_agent_result(self, agent_id: str) -> Optional[dict]:
        with self._lock:
            return self._agent_results.get(agent_id)

    def get_all_agent_results(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._agent_results)

    # ── Custom key-value store ───────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._custom[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._custom.get(key, default)

    # ── Agent Memory ──────────────────────────────────────────────────

    def set_memory(self, agent_id: str, key: str, value: Any) -> None:
        """Store a piece of persistent memory for a specific agent."""
        with self._lock:
            if agent_id not in self._memories:
                self._memories[agent_id] = {}
            self._memories[agent_id][key] = value

    def get_memory(self, agent_id: str, key: str, default: Any = None) -> Any:
        """Retrieve a piece of persistent memory for a specific agent."""
        with self._lock:
            return self._memories.get(agent_id, {}).get(key, default)

    def get_all_memories(self, agent_id: str) -> dict[str, Any]:
        """Return all memories for a given agent."""
        with self._lock:
            return dict(self._memories.get(agent_id, {}))

    # ── Serialisation ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "conversation_id": self.conversation_id,
                "user_id": self.user_id,
                "created_at": self.created_at,
                "findings_count": len(self._findings),
                "tool_calls_count": len(self._tool_calls),
                "handoffs": [
                    {
                        "from": h.from_agent_id,
                        "to": h.to_agent_id,
                        "reason": h.reason,
                        "timestamp": h.timestamp,
                    }
                    for h in self._handoffs
                ],
                "agents_involved": list(self._agent_results.keys()),
                "custom_keys": list(self._custom.keys()),
            }
