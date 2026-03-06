"""
RCA Generation Tools.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.rca_agent import RCAAgent
from state.conversation_state import ConversationState
from tools.base import ToolDefinition
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.rca")


def generate_rca_report(
    incident_summary: str = "",
    conversation_id: str = "",
    state: ConversationState | None = None,
    tracker: Any = None,
    issue_id: str = "",
) -> dict:
    """
    Generate a Root Cause Analysis (RCA) report for the current incident.
    Synthesizes findings from the investigation into a structured report.
    """
    try:
        resolved_state = state
        conversation_id = (conversation_id or "").strip()
        if resolved_state is None:
            if not conversation_id:
                return {
                    "success": False,
                    "error": (
                        "RCA generation needs conversation context. Provide "
                        "conversation_id or state."
                    ),
                }
            resolved_state = ConversationState.load(conversation_id)

        if resolved_state is None:
            return {"success": False, "error": "Conversation state could not be loaded."}

        agent = RCAAgent()
        previous_generated_at = (resolved_state.rca_data or {}).get("generated_at")
        report = agent.generate_rca(
            resolved_state,
            incident_summary=incident_summary,
            tracker=tracker,
            issue_id=issue_id,
        )
        generated_at = (resolved_state.rca_data or {}).get("generated_at")
        generated = bool(generated_at and generated_at != previous_generated_at)

        if not generated:
            return {
                "success": False,
                "error": report or "RCA could not be generated yet.",
                "report": report,
                "incident_summary": incident_summary,
                "generated_at": generated_at,
                "conversation_id": resolved_state.conversation_id or conversation_id,
            }

        return {
            "success": True,
            "report": report,
            "incident_summary": incident_summary,
            "generated_at": generated_at,
            "conversation_id": resolved_state.conversation_id or conversation_id,
        }
    except Exception as e:
        logger.error(f"Failed to generate RCA: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

tool_registry.register(
    ToolDefinition(
        name="generate_rca_report",
        description=(
            "Generate a formal Root Cause Analysis (RCA) report. "
            "Use this when the user explicitly asks for an RCA, a 'what happened' "
            "report, or when an investigation is complete and a summary is needed. "
            "Requires prior investigation findings to be effective."
        ),
        category="general",
        tier="read_only",
        parameters={
            "incident_summary": {
                "type": "string",
                "description": "Short summary of what happened (optional)",
            },
            "conversation_id": {
                "type": "string",
                "description": (
                    "Conversation ID whose findings should be summarized. "
                    "Optional when the runtime passes state context."
                ),
            },
        },
        required_params=[],
    ),
    generate_rca_report,
)
