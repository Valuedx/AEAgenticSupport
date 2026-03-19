"""
RCA Generation Tools — improved edition.

Key improvements
----------------
1.  Returns a richer payload: severity + affected_workflows exposed to callers.
2.  Accepts an RCAResult from the agent instead of a bare string, so no
    information is lost crossing the agent/tool boundary.
3.  `conversation_id` resolution is extracted into a private helper to avoid
    duplication between the main function and any future variants.
4.  Clear distinction between "no findings yet" (success=False, actionable
    message) and "LLM/system error" (success=False, error detail).
5.  All log lines are structured (key=value) for log aggregators.
6.  ToolDefinition parameters are more descriptive — improves LLM tool selection.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.rca_agent import RCAAgent, RCAResult
from state.conversation_state import ConversationState
from tools.base import ToolDefinition
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.rca")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_state(
    state: ConversationState | None,
    conversation_id: str,
) -> ConversationState | None:
    """Return *state* as-is if provided; otherwise load from store by ID."""
    if state is not None:
        return state
    cid = conversation_id.strip()
    if not cid:
        return None
    return ConversationState.load(cid)


def _build_error_payload(reason: str, conversation_id: str = "") -> dict:
    return {
        "success": False,
        "error": reason,
        "report": None,
        "severity": "unknown",
        "affected_workflows": [],
        "generated_at": None,
        "conversation_id": conversation_id,
    }


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

def generate_rca_report(
    incident_summary: str = "",
    conversation_id: str = "",
    state: ConversationState | None = None,
    tracker: Any = None,
    issue_id: str = "",
) -> dict:
    """
    Generate a Root Cause Analysis (RCA) report for the current incident.

    Synthesises investigation findings, tool-call logs, SOP knowledge, and
    historical incidents into a structured report.  The report format is
    automatically tailored to the user's role (business vs technical).

    Returns a dict with the following keys:
        success          (bool)   — whether the RCA was successfully generated
        report           (str)    — the formatted markdown report
        severity         (str)    — "high" | "medium" | "low" | "unknown"
        affected_workflows (list) — workflow names referenced in the report
        incident_summary (str)   — the summary provided by the caller
        generated_at     (str)   — ISO-8601 timestamp of generation
        conversation_id  (str)   — resolved conversation ID
        error            (str)   — human-readable error message (on failure)
    """
    cid = (conversation_id or "").strip()

    try:
        resolved_state = _resolve_state(state, cid)

        if resolved_state is None:
            reason = (
                "RCA generation requires conversation context. "
                "Provide a valid conversation_id or pass state directly."
            )
            logger.warning("rca_state_missing conversation_id=%s", cid)
            return _build_error_payload(reason, cid)

        resolved_cid = resolved_state.conversation_id or cid

        # Track what was already generated so we can detect a fresh report
        previous_generated_at: str | None = (resolved_state.rca_data or {}).get("generated_at")

        agent = RCAAgent()
        result: RCAResult = agent.generate_rca(
            resolved_state,
            incident_summary=incident_summary,
            tracker=tracker,
            issue_id=issue_id,
        )

        # Distinguish "not enough data yet" from a genuine generation
        if not result.success:
            logger.info(
                "rca_not_generated conversation_id=%s reason=%s",
                resolved_cid, result.error,
            )
            return {
                "success": False,
                "error": result.error or "RCA could not be generated yet.",
                "report": result.report,          # may contain the "investigate first" message
                "severity": result.severity,
                "affected_workflows": result.affected_workflows,
                "incident_summary": incident_summary,
                "generated_at": result.generated_at,
                "conversation_id": resolved_cid,
            }

        # Verify the agent actually wrote a new report
        new_generated_at: str | None = (resolved_state.rca_data or {}).get("generated_at")
        newly_generated = bool(new_generated_at and new_generated_at != previous_generated_at)

        if not newly_generated:
            reason = "RCA generation did not produce a new report. Findings may be insufficient."
            logger.warning("rca_unchanged conversation_id=%s", resolved_cid)
            return {
                "success": False,
                "error": reason,
                "report": result.report,
                "severity": result.severity,
                "affected_workflows": result.affected_workflows,
                "incident_summary": incident_summary,
                "generated_at": new_generated_at,
                "conversation_id": resolved_cid,
            }

        logger.info(
            "rca_success conversation_id=%s severity=%s workflows=%s",
            resolved_cid, result.severity, result.affected_workflows,
        )
        return {
            "success": True,
            "report": result.report,
            "severity": result.severity,
            "affected_workflows": result.affected_workflows,
            "incident_summary": incident_summary,
            "generated_at": result.generated_at,
            "conversation_id": resolved_cid,
        }

    except Exception as exc:
        logger.error("rca_exception conversation_id=%s error=%s", cid, exc, exc_info=True)
        return _build_error_payload(str(exc), cid)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

tool_registry.register(
    ToolDefinition(
        name="generate_rca_report",
        description=(
            "Generate a formal Root Cause Analysis (RCA) report for an ongoing or "
            "recently resolved incident.  The report is automatically tailored to "
            "the user's role: plain-language executive summary for business users, "
            "detailed technical deep-dive (5-Whys, timeline, CAPA table) for "
            "engineers.  Requires prior investigation findings to be effective — "
            "use after the investigation tools have run.  Trigger on phrases like "
            "'generate RCA', 'what happened', 'incident report', 'post-mortem', "
            "or 'root cause analysis'."
        ),
        category="general",
        tier="read_only",
        parameters={
            "incident_summary": {
                "type": "string",
                "description": (
                    "Short human-readable description of the incident "
                    "(e.g. 'Invoice workflow failed at step 3 on 2024-06-01'). "
                    "Optional but improves report quality."
                ),
            },
            "conversation_id": {
                "type": "string",
                "description": (
                    "The conversation ID whose investigation findings should be "
                    "included in the report.  Omit when the runtime injects state "
                    "directly via the `state` parameter."
                ),
            },
            "issue_id": {
                "type": "string",
                "description": (
                    "Optional tracker issue ID.  When provided together with a "
                    "`tracker` object, findings and affected workflows are loaded "
                    "from the tracker rather than from the conversation state."
                ),
            },
        },
        required_params=[],
    ),
    generate_rca_report,
)