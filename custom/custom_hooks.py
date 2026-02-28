"""
Router hook — api_messages_hook.
The 'brainstem' of the Extension: lock + dedupe + smalltalk gate +
issue classification + routing to support agent.

Implements the AI Studio Cognibot hook contract:
  - Class extends ChatbotHooks
  - All hooks are async static methods
  - api_messages_hook receives (request, activity)
"""
from __future__ import annotations

import logging
import uuid

from asgiref.sync import sync_to_async
from django.utils import timezone

try:
    from aistudiobot.hooks import ChatbotHooks
except ImportError:
    class ChatbotHooks:
        """Stub for standalone development outside AI Studio runtime."""
        pass

logger = logging.getLogger("support_agent.hooks")

from custom.helpers.locks import pg_advisory_lock
from custom.helpers.db import is_duplicate_message, mark_message_processed
from custom.helpers.teams import make_text_reply
from custom.models import ConversationState, Case, Approval
from custom.functions.python.support_agent import handle_support_turn
from custom.helpers.issue_classifier import (
    classify_message,
    IssueClassification,
    link_cases,
    should_escalate_recurrence,
)


# ── Activity normalisation ──

def _activity_to_dict(activity) -> dict:
    """Normalize a Bot Framework Activity (object or dict) to a plain dict."""
    if isinstance(activity, dict):
        return activity
    result = {}
    for attr in ("text", "id"):
        result[attr] = getattr(activity, attr, None) or ""
    conv = getattr(activity, "conversation", None)
    result["conversation"] = {"id": getattr(conv, "id", "") or ""} if conv else {}
    frm = getattr(activity, "from_property", None) or getattr(activity, "from", None)
    result["from"] = {"id": getattr(frm, "id", "") or ""} if frm else {}
    if hasattr(activity, "user_type"):
        result["user_type"] = activity.user_type
    return result


def _extract_thread_id(activity: dict) -> str:
    convo = activity.get("conversation", {}) or {}
    return convo.get("id") or "unknown-thread"


def _extract_message_id(activity: dict) -> str:
    return activity.get("id") or str(uuid.uuid4())


def _extract_text(activity: dict) -> str:
    return (activity.get("text") or "").strip()


def _extract_user_id(activity: dict) -> str:
    return (activity.get("from", {}) or {}).get("id", "")


def _is_smalltalk(text: str) -> bool:
    t = text.lower().strip()
    return (
        t in {"hi", "hello", "hey", "thanks", "thank you", "ok"}
        or t.startswith(("hi ", "hello ", "hey "))
    )


# ── Synchronous processing core (runs inside sync_to_async) ──

def _process_message_sync(activity_dict: dict):
    """
    All Django ORM + business logic runs here synchronously.
    Wrapped by sync_to_async in the async hook.
    """
    thread_id = _extract_thread_id(activity_dict)
    msg_id = _extract_message_id(activity_dict)
    text = _extract_text(activity_dict)
    user_id = _extract_user_id(activity_dict)

    if not text:
        return make_text_reply(
            "It looks like your message was empty. How can I help?"
        )

    with pg_advisory_lock(thread_id):
        if is_duplicate_message(thread_id, msg_id):
            return None
        mark_message_processed(thread_id, msg_id)

        if _is_smalltalk(text):
            cs, _ = ConversationState.objects.get_or_create(
                thread_id=thread_id
            )
            cs.last_user_message_id = msg_id
            cs.updated_at = timezone.now()
            cs.save()
            return make_text_reply(
                "Hello! How can I help you with support today?"
            )

        # ── Load conversation state and active case ──
        cs, _ = ConversationState.objects.get_or_create(thread_id=thread_id)
        active_case = None
        if cs.active_case_id:
            active_case = Case.objects.filter(
                case_id=cs.active_case_id
            ).first()

        # ── Approval with authorization check ──
        if (text.strip().upper() in {"APPROVE", "REJECT"}
                and active_case
                and active_case.state == "WAITING_APPROVAL"):
            appr = Approval.objects.filter(
                case_id=active_case.case_id, status="PENDING",
            ).order_by("-created_at").first()

            if appr:
                if (user_id and appr.requested_to
                        and user_id not in appr.requested_to):
                    return make_text_reply(
                        "You are not authorized to approve/reject "
                        "this action. Authorized reviewers: "
                        f"{', '.join(appr.requested_to)}"
                    )
                is_approve = text.strip().upper() == "APPROVE"
                appr.status = "APPROVED" if is_approve else "REJECTED"
                appr.decided_by = user_id
                appr.decided_at = timezone.now()
                appr.save()
                if not is_approve:
                    active_case.state = "PLANNING"
                    active_case.updated_at = timezone.now()
                    active_case.save()
                    return make_text_reply(
                        "Action rejected. How would you like to proceed?"
                    )
                return handle_support_turn(
                    thread_id=thread_id,
                    teams_message_id=msg_id,
                    user_text=text,
                    raw_activity=activity_dict,
                )

        # ── Issue classification ──
        classification, ref_case_id = classify_message(
            thread_id, text, active_case
        )

        if classification == IssueClassification.RECURRENCE:
            old_case = (
                Case.objects.filter(case_id=ref_case_id).first()
                if ref_case_id else None
            )
            if not old_case:
                logger.warning(
                    "RECURRENCE ref_case %s not found — treating as new issue",
                    ref_case_id,
                )
                cs.active_case_id = None
                cs.updated_at = timezone.now()
                cs.save()
                return handle_support_turn(
                    thread_id=thread_id,
                    teams_message_id=msg_id,
                    user_text=text,
                    raw_activity=activity_dict,
                )

            old_case.recurrence_count += 1
            old_case.updated_at = timezone.now()
            old_case.save()

            if should_escalate_recurrence(old_case):
                old_case.state = "WAITING_ON_TEAM"
                old_case.owner_type = "HUMAN_TEAM"
                old_case.owner_team = "L2_SUPPORT"
                old_case.save()
                return make_text_reply(
                    f"This issue has now recurred "
                    f"{old_case.recurrence_count} times. "
                    f"The previous fix is not holding. "
                    f"Escalating to L2 support for a "
                    f"permanent resolution."
                )

            old_case.state = "PLANNING"
            old_case.resolved_at = None
            old_case.save()
            cs.active_case_id = old_case.case_id
            cs.updated_at = timezone.now()
            cs.save()
            prefix = (
                f"This looks like a recurrence "
                f"(#{old_case.recurrence_count}) of a previous "
                f"issue. "
            )
            if old_case.resolution_summary:
                prefix += (
                    f"Last resolution: "
                    f"{old_case.resolution_summary[:200]}. "
                )
            prefix += (
                "Let me check if the same cause applies.\n\n"
            )
            result = handle_support_turn(
                thread_id=thread_id,
                teams_message_id=msg_id,
                user_text=text,
                raw_activity=activity_dict,
            )
            result["text"] = prefix + result.get("text", "")
            return result

        elif classification == IssueClassification.NEW_ISSUE:
            cs.active_case_id = None
            cs.updated_at = timezone.now()
            cs.save()

        elif classification == IssueClassification.RELATED_NEW:
            parent_id = ref_case_id or (
                active_case.case_id if active_case else None
            )
            cs.active_case_id = None
            cs.updated_at = timezone.now()
            cs.save()
            result = handle_support_turn(
                thread_id=thread_id,
                teams_message_id=msg_id,
                user_text=text,
                raw_activity=activity_dict,
            )
            new_cs = ConversationState.objects.get(thread_id=thread_id)
            if parent_id and new_cs.active_case_id:
                link_cases(parent_id, new_cs.active_case_id, "CASCADE")
            prefix = (
                "This looks related to a previous issue but "
                "appears to be a separate problem. "
                "Tracking as a linked case.\n\n"
            )
            result["text"] = prefix + result.get("text", "")
            return result

        elif classification == IssueClassification.FOLLOWUP:
            target_case = (
                Case.objects.filter(case_id=ref_case_id).first()
                if ref_case_id else active_case
            )
            if target_case and target_case.resolution_summary:
                return make_text_reply(
                    f"Regarding [{target_case.case_id}]: "
                    f"{target_case.resolution_summary}\n\n"
                    f"Would you like me to verify the current status?"
                )
            if target_case:
                return make_text_reply(
                    f"Case [{target_case.case_id}] is currently "
                    f"in state **{target_case.state}**. "
                    f"No resolution recorded yet — would you like "
                    f"me to check the latest status?"
                )

        elif classification == IssueClassification.STATUS_CHECK:
            cases = Case.objects.filter(
                thread_id=thread_id,
            ).exclude(
                state__in=["CLOSED", "CANCELLED"],
            ).order_by("-updated_at")[:10]
            summary = "\n".join(
                f"- [{c.case_id}] {c.state} | "
                f"Workflows: {c.workflows_involved}"
                for c in cases
            ) or "No active cases."
            return make_text_reply(
                f"Current session status:\n{summary}"
            )

        return handle_support_turn(
            thread_id=thread_id,
            teams_message_id=msg_id,
            user_text=text,
            raw_activity=activity_dict,
        )


# ── AI Studio Hook Class ──

class CustomChatbotHooks(ChatbotHooks):
    export_dialogs = []

    async def api_messages_hook(request, activity):
        """
        Invoked for every api/messages REST API call.
        Normalises the Activity, then delegates to synchronous processing.
        Returns a dict ``{"type": "message", "text": "..."}`` or None.
        """
        activity_dict = _activity_to_dict(activity)
        return await sync_to_async(
            _process_message_sync, thread_sensitive=False
        )(activity_dict)
