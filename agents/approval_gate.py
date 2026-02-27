"""
Approval gate for risky remediation actions.
Manages the approval workflow: request -> wait -> execute or reject.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config.settings import CONFIG

logger = logging.getLogger("ops_agent.approval")

SAFE_TIERS = {"read_only"}
AUTO_APPROVE_TIERS = {"low_risk"}
APPROVAL_REQUIRED_TIERS = {"medium_risk", "high_risk"}


@dataclass
class ApprovalRequest:
    tool_name: str
    tool_params: dict
    tier: str
    reason: str
    summary: str


class ApprovalGate:
    """Determines whether a tool call needs approval and manages the flow."""

    def needs_approval(self, tool_name: str, tier: str,
                       params: dict) -> bool:
        if tier in SAFE_TIERS:
            return False
        if tier in AUTO_APPROVE_TIERS:
            return False

        workflow = params.get("workflow_name", "")
        if workflow in CONFIG.get("PROTECTED_WORKFLOWS", []):
            return True

        return tier in APPROVAL_REQUIRED_TIERS

    def create_approval_request(self, tool_name: str, tier: str,
                                params: dict,
                                summary: str) -> ApprovalRequest:
        return ApprovalRequest(
            tool_name=tool_name,
            tool_params=params,
            tier=tier,
            reason=(
                f"Tool '{tool_name}' is tier '{tier}' and requires "
                f"user approval."
            ),
            summary=summary,
        )

    def format_approval_prompt(self, request: ApprovalRequest) -> str:
        lines = [
            "I'd like to perform the following action:",
            f"  Action: {request.tool_name}",
            f"  Risk level: {request.tier}",
            f"  Details: {request.summary}",
            "",
            "Parameters:",
        ]
        for k, v in request.tool_params.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append("Reply **approve** to proceed or **reject** to cancel.")
        return "\n".join(lines)

    def parse_approval_response(self, user_message: str) -> Optional[bool]:
        """Returns True for approve, False for reject, None if unrecognised."""
        msg = user_message.strip().lower()
        if msg in ("approve", "yes", "go ahead", "proceed"):
            return True
        if msg in ("reject", "no", "deny"):
            return False
        return None
