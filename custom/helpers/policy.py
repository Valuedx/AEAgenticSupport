"""
Safe auto-run vs approval policy for plan steps.
Steps tagged READ_ONLY always auto-run.
Steps tagged DESTRUCTIVE always require approval.
SAFE_WRITE auto-runs only if the capability is allowlisted.
"""

from typing import Any, Dict, Tuple

SAFE_AUTORUN_CAPABILITIES = {
    "CAP_TICKET_UPDATE",
    "CAP_GET_REQUEST_STATUS",
    "CAP_GET_REQUEST_DETAILS",
    "CAP_GET_EXECUTION_LOGS",
    "CAP_DOWNLOAD_EXECUTION_ARTIFACTS",
}


def classify_step(step: Dict[str, Any]) -> Tuple[str, bool]:
    """Returns (risk_level, needs_approval)."""
    risk = step.get("policy_tags", {}).get("risk", "READ_ONLY")
    cap = step.get("capability_id")

    if risk == "DESTRUCTIVE":
        return (risk, True)

    if risk == "SAFE_WRITE":
        return (risk, cap not in SAFE_AUTORUN_CAPABILITIES)

    return (risk, False)
