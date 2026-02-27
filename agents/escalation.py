"""
Escalation agent — handles handoff to human support teams
when the bot cannot resolve an issue automatically.
"""

import logging
from typing import Optional

from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.escalation")

ESCALATION_TIERS = {
    "L1": "First-line support (bot-assisted)",
    "L2": "Technical operations team",
    "L3": "Engineering / DevOps",
    "BUSINESS": "Business stakeholders",
}


class EscalationAgent:
    """Manages escalation of issues that cannot be auto-resolved."""

    def should_escalate(
        self,
        attempts: int,
        max_attempts: int = 3,
        has_protected_workflow: bool = False,
        recurrence_count: int = 0,
        recurrence_threshold: int = 3,
    ) -> bool:
        if has_protected_workflow:
            return True
        if attempts >= max_attempts:
            return True
        if recurrence_count >= recurrence_threshold:
            return True
        return False

    def determine_escalation_tier(
        self,
        error_type: str = "",
        is_protected: bool = False,
        recurrence_count: int = 0,
    ) -> str:
        if is_protected:
            return "L3"
        if recurrence_count >= 3:
            return "L2"
        if "permission" in error_type.lower() or "auth" in error_type.lower():
            return "L3"
        return "L2"

    def escalate(
        self,
        issue_summary: str,
        tier: str = "L2",
        channel: str = "teams",
        recipients: Optional[list[str]] = None,
        ticket_priority: str = "P3",
    ) -> dict:
        results = {}

        try:
            ticket_result = tool_registry.execute(
                "create_incident_ticket",
                title=f"[Escalation-{tier}] {issue_summary[:100]}",
                description=issue_summary,
                priority=ticket_priority,
                assignee_group=tier,
            )
            results["ticket"] = (
                ticket_result.data if ticket_result.success
                else ticket_result.error
            )
        except Exception as e:
            logger.error(f"Failed to create escalation ticket: {e}")
            results["ticket"] = str(e)

        if recipients:
            try:
                notif_result = tool_registry.execute(
                    "send_notification",
                    channel=channel,
                    recipients=recipients,
                    subject=f"[{tier} Escalation] {issue_summary[:80]}",
                    message=issue_summary,
                )
                results["notification"] = (
                    notif_result.data if notif_result.success
                    else notif_result.error
                )
            except Exception as e:
                logger.error(f"Failed to send escalation notification: {e}")
                results["notification"] = str(e)

        return results

    def format_escalation_message(
        self,
        issue_summary: str,
        tier: str,
        reason: str,
        findings: list = None,
    ) -> str:
        lines = [
            f"This issue has been escalated to "
            f"**{ESCALATION_TIERS.get(tier, tier)}**.",
            f"Reason: {reason}",
            "",
            "**Summary:**",
            issue_summary,
        ]
        if findings:
            lines.append("")
            lines.append("**Investigation findings:**")
            for f in findings[:5]:
                lines.append(f"- {f.get('summary', str(f))}")
        lines.append("")
        lines.append(
            "A support ticket has been created. "
            "The team will follow up shortly."
        )
        return "\n".join(lines)
