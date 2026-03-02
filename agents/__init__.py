"""
Lazy exports for the agents package.

Avoid importing heavy modules at package import time to prevent
import-order cycles (for example during tests importing submodules like
``agents.approval_gate`` directly).
"""
from __future__ import annotations

from importlib import import_module

__all__ = [
    "Orchestrator",
    "RCAAgent",
    "ApprovalGate",
    "EscalationAgent",
]

_EXPORTS = {
    "Orchestrator": ("agents.orchestrator", "Orchestrator"),
    "RCAAgent": ("agents.rca_agent", "RCAAgent"),
    "ApprovalGate": ("agents.approval_gate", "ApprovalGate"),
    "EscalationAgent": ("agents.escalation", "EscalationAgent"),
}


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if not target:
        raise AttributeError(f"module 'agents' has no attribute '{name}'")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
