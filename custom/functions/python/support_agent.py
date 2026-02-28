"""
Planner + Executor for the Extension route.
Bridges AI Studio's Django-based Extension to the standalone orchestrator.

Two modes:
  1. AGENTIC MODE (default): Delegates to agents/orchestrator.py for full
     LLM-powered investigation with RAG tool selection.
  2. PLAN-EXECUTE MODE: Builds a deterministic plan via RAG, then executes
     steps sequentially with approval gates.
"""
from __future__ import annotations

import uuid
import logging
import os
import sys
from datetime import datetime

from django.utils import timezone

from custom.models import ConversationState, Case, Approval
from custom.helpers.teams import make_text_reply, make_approval_card
from custom.helpers.tools_rest import RestToolClient, ToolError
from custom.helpers.rag import rag_search_sop, rag_search_tools
from custom.helpers.policy import classify_step
from custom.helpers.roster import pick_onshift_techs

logger = logging.getLogger("support_agent")

TOOL_BASE_URL = os.environ.get("TOOL_BASE_URL", "http://localhost:9999")
TOOL_AUTH_TOKEN = os.environ.get("TOOL_AUTH_TOKEN", "")

# Ensure standalone modules are importable
_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_USE_AGENTIC = os.environ.get("USE_AGENTIC_MODE", "true").lower() == "true"


def _get_orchestrator():
    """Lazy import to avoid circular deps and allow fallback."""
    try:
        from gateway.message_gateway import MessageGateway
        return MessageGateway()
    except Exception as e:
        logger.warning(f"Could not load agentic orchestrator: {e}")
        return None


_gateway = None


def _get_gateway():
    global _gateway
    if _gateway is None:
        _gateway = _get_orchestrator()
    return _gateway


def _sync_state_from_gateway(gw, thread_id: str, case: Case) -> None:
    """
    After the agentic gateway returns, sync key state back to the
    Django Case model so the Extension hook layer stays consistent
    with the standalone orchestrator's state.
    """
    try:
        session = gw.get_or_create_session(thread_id)
        phase = session.phase.value if hasattr(session.phase, "value") else str(session.phase)

        phase_to_state = {
            "idle": "PLANNING",
            "investigating": "EXECUTING",
            "awaiting_approval": "WAITING_APPROVAL",
            "executing": "EXECUTING",
            "resolved": "RESOLVED_PENDING_CONFIRMATION",
            "escalated": "WAITING_ON_TEAM",
        }
        new_state = phase_to_state.get(phase, case.state)
        if new_state != case.state:
            case.state = new_state
            case.updated_at = timezone.now()

        if session.affected_workflows:
            merged = list(set(
                (case.workflows_involved or []) + session.affected_workflows
            ))
            case.workflows_involved = merged

        if session.pending_action_summary:
            case.latest_plan_json = {
                "pending_action": session.pending_action,
                "summary": session.pending_action_summary,
            }

        case.save()
    except Exception as e:
        logger.warning(f"State sync from gateway failed (non-fatal): {e}")


def _get_or_create_case(thread_id: str) -> Case:
    cs, _ = ConversationState.objects.get_or_create(thread_id=thread_id)
    if cs.active_case_id:
        c = Case.objects.filter(case_id=cs.active_case_id).first()
        if c and c.state not in {"CLOSED", "CANCELLED"}:
            return c

    case_id = str(uuid.uuid4())
    c = Case.objects.create(
        case_id=case_id,
        thread_id=thread_id,
        state="PLANNING",
        owner_type="BOT_L1",
        planner_state_json={},
        latest_plan_json={},
        plan_version=0,
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    cs.active_case_id = case_id
    cs.updated_at = timezone.now()
    cs.save()
    return c


def _ensure_ticket(client: RestToolClient, case: Case) -> None:
    if case.ticket_id:
        return
    try:
        resp = client.call(
            "/tools/ticket/create",
            {"case_id": case.case_id, "thread_id": case.thread_id},
            idempotency_key=f"ticket-create:{case.case_id}",
        )
        case.ticket_id = resp.get("ticket_id")
        case.updated_at = timezone.now()
        case.save()
    except ToolError as e:
        logger.warning(f"Ticket creation failed (non-fatal): {e}")


def _build_plan_with_rag(client: RestToolClient, case: Case,
                         user_text: str) -> dict:
    sop_hits = rag_search_sop(client, query=user_text, top_k=6)
    tool_hits = rag_search_tools(client, query=user_text, top_k=8)

    issue_bucket = (
        "OUTPUT_NOT_RECEIVED"
        if ("output" in user_text.lower() and "not" in user_text.lower())
        else "GENERIC"
    )

    step_idx = 1
    steps = [
        {
            "index": step_idx,
            "type": "TICKET_UPDATE",
            "capability_id": "CAP_TICKET_UPDATE",
            "tool_ref": "/tools/ticket/update",
            "inputs": {
                "ticket_id": case.ticket_id,
                "note": f"User reported: {user_text}",
            },
            "policy_tags": {"risk": "SAFE_WRITE"},
        },
    ]
    step_idx += 1

    steps.append({
        "index": step_idx,
        "type": "TOOL_CALL",
        "capability_id": "CAP_GET_REQUEST_STATUS",
        "tool_ref": "/tools/ae/request/status",
        "inputs": {"ticket_id": case.ticket_id},
        "policy_tags": {"risk": "READ_ONLY"},
    })
    step_idx += 1

    seen_refs = {"/tools/ticket/update", "/tools/ae/request/status"}
    for hit in tool_hits:
        tool_ref = hit.get("tool_ref") or hit.get("metadata", {}).get("tool_ref")
        if not tool_ref or tool_ref in seen_refs:
            continue
        seen_refs.add(tool_ref)
        risk = hit.get("risk") or hit.get("metadata", {}).get("risk", "SAFE_WRITE")
        cap = hit.get("capability_id") or hit.get("metadata", {}).get("capability_id", "CAP_RAG_SUGGESTED")
        steps.append({
            "index": step_idx,
            "type": "TOOL_CALL",
            "capability_id": cap,
            "tool_ref": tool_ref,
            "inputs": {"ticket_id": case.ticket_id},
            "policy_tags": {"risk": risk},
        })
        step_idx += 1

    if issue_bucket == "OUTPUT_NOT_RECEIVED":
        if "/tools/ae/output/publish" not in seen_refs:
            steps.append({
                "index": step_idx,
                "type": "TOOL_CALL",
                "capability_id": "CAP_PUBLISH_OUTPUT_TO_SHARED_PATH",
                "tool_ref": "/tools/ae/output/publish",
                "inputs": {"ticket_id": case.ticket_id},
                "policy_tags": {"risk": "SAFE_WRITE"},
            })
            step_idx += 1

    sop_refs = [h.get("id") or h.get("title") for h in sop_hits]
    tool_refs = [
        h.get("tool_ref") or h.get("metadata", {}).get("tool_ref")
        for h in tool_hits
        if h.get("tool_ref") or h.get("metadata", {}).get("tool_ref")
    ]
    workflows = list({
        h.get("workflow_name") or h.get("metadata", {}).get("workflow_name")
        for h in sop_hits + tool_hits
        if h.get("workflow_name") or h.get("metadata", {}).get("workflow_name")
    })
    if workflows:
        case.workflows_involved = list(set(
            (case.workflows_involved or []) + workflows
        ))
        case.save()

    return {
        "case_id": case.case_id,
        "ticket_id": case.ticket_id,
        "issue_bucket": issue_bucket,
        "sop_refs": sop_refs,
        "tool_refs": tool_refs,
        "steps": steps,
        "close_criteria": {
            "requires_user_confirmation": True,
            "confirmation_prompt": "Please confirm the output is received.",
        },
    }


def _execute_plan(client: RestToolClient, case: Case, plan: dict) -> str:
    if case.owner_type == "HUMAN_TEAM" or case.state == "WAITING_ON_TEAM":
        client.call("/tools/ticket/update", {
            "ticket_id": case.ticket_id,
            "note": "User added info (hands-off).",
        })
        return (
            "This ticket is assigned to the support team. "
            "I've added your update to the ticket."
        )

    needs_approval = []
    for step in plan["steps"]:
        _risk, ask = classify_step(step)
        if ask:
            needs_approval.append(step)

    if needs_approval:
        now_local = datetime.now()
        onshift = pick_onshift_techs(now_local=now_local)

        Approval.objects.create(
            case_id=case.case_id,
            plan_version=case.plan_version + 1,
            status="PENDING",
            requested_to=onshift,
            created_at=timezone.now(),
        )

        case.state = "WAITING_APPROVAL"
        case.latest_plan_json = plan
        case.plan_version += 1
        case.updated_at = timezone.now()
        case.save()

        step_labels = [
            f"Step {s['index']}: {s.get('capability_id', s.get('type'))}"
            for s in needs_approval
        ]
        card = make_approval_card(
            case_id=case.case_id,
            action_summary="; ".join(step_labels),
            reviewers=onshift,
        )
        return card.get("text", str(card))

    for step in plan["steps"]:
        try:
            client.call(
                step["tool_ref"],
                step.get("inputs", {}),
                idempotency_key=(
                    f"{case.case_id}:{case.plan_version}:{step['index']}"
                ),
            )
        except ToolError as e:
            err_sig = str(e)[:200]
            if err_sig not in (case.error_signatures or []):
                sigs = list(case.error_signatures or [])
                sigs.append(err_sig)
                case.error_signatures = sigs
            client.call("/tools/ticket/assign", {
                "ticket_id": case.ticket_id,
                "team": "L2_SUPPORT",
                "reason": str(e),
            })
            case.owner_type = "HUMAN_TEAM"
            case.owner_team = "L2_SUPPORT"
            case.state = "WAITING_ON_TEAM"
            case.updated_at = timezone.now()
            case.save()
            return (
                "Couldn't auto-resolve. Assigned to L2 Support; "
                "I'll stay hands-off and add any new info to the ticket."
            )

    case.state = "RESOLVED_PENDING_CONFIRMATION"
    case.latest_plan_json = plan
    case.plan_version += 1
    case.resolved_at = timezone.now()
    case.resolution_summary = (
        f"Auto-resolved: executed {len(plan['steps'])} steps "
        f"for {plan.get('issue_bucket', 'GENERIC')}"
    )
    case.updated_at = timezone.now()
    case.save()
    return "Done. Please confirm if you received the output file."


def handle_support_turn(thread_id: str, teams_message_id: str,
                        user_text: str, raw_activity: dict) -> dict:
    """
    Main entry point called from custom_hooks.py.
    Routes to agentic orchestrator or plan-execute mode.
    """
    _user_id = (raw_activity.get("from", {}) or {}).get("id", "")
    _user_type = raw_activity.get("user_type", "technical")

    # Populate user_type on the active case if not yet set
    cs = ConversationState.objects.filter(thread_id=thread_id).first()
    if cs and cs.active_case_id:
        Case.objects.filter(
            case_id=cs.active_case_id, user_type__isnull=True,
        ).update(user_type=_user_type)

    # ── Agentic mode: delegate to our full orchestrator ──
    if _USE_AGENTIC:
        gw = _get_gateway()
        if gw:
            case = _get_or_create_case(thread_id)

            response = gw.process_message(
                conversation_id=thread_id,
                user_message=user_text,
                user_id=_user_id,
                user_role=_user_type,
            )

            _sync_state_from_gateway(gw, thread_id, case)
            return make_text_reply(response)

    # ── Plan-execute mode: deterministic plan via RAG ──
    client = RestToolClient(base_url=TOOL_BASE_URL, auth_token=TOOL_AUTH_TOKEN)

    case = _get_or_create_case(thread_id)
    _ensure_ticket(client, case)

    if (user_text.strip().upper() in {"APPROVE", "REJECT"}
            and case.state == "WAITING_APPROVAL"):
        decision = user_text.strip().upper()
        appr = Approval.objects.filter(
            case_id=case.case_id, status="PENDING",
        ).order_by("-created_at").first()

        if appr:
            appr.status = "APPROVED" if decision == "APPROVE" else "REJECTED"
            appr.decided_by = (
                raw_activity.get("from", {}) or {}
            ).get("id")
            appr.decided_at = timezone.now()
            appr.save()

            if decision == "REJECT":
                case.state = "PLANNING"
                case.updated_at = timezone.now()
                case.save()
                return make_text_reply(
                    "Approval rejected. Tell me what to do next, "
                    "or I can assign it to the team."
                )

            case.state = "EXECUTING"
            case.updated_at = timezone.now()
            case.save()
            msg = _execute_plan(client, case, case.latest_plan_json)
            return make_text_reply(msg)

    case.state = "PLANNING"
    case.updated_at = timezone.now()
    case.save()

    plan = _build_plan_with_rag(client, case, user_text)

    case.state = "EXECUTING"
    case.updated_at = timezone.now()
    case.save()

    msg = _execute_plan(client, case, plan)
    return make_text_reply(msg)
