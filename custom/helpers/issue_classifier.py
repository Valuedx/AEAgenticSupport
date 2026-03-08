"""
Issue classifier for multi-issue per thread.
Three layers: heuristics -> workflow matching -> LLM fallback.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from django.utils import timezone

from custom.models import Case, IssueLink
from state.app_config import get_classification_signal_groups, get_runtime_value

logger = logging.getLogger("support_agent.issue_classifier")

class IssueClassification:
    CONTINUE_EXISTING = "continue_existing"
    NEW_ISSUE = "new_issue"
    RELATED_NEW = "related_new"
    RECURRENCE = "recurrence"
    FOLLOWUP = "followup"
    STATUS_CHECK = "status_check"


def classify_message(thread_id: str, user_text: str,
                     active_case: Optional[Case]
                     ) -> Tuple[str, Optional[str]]:
    msg_lower = user_text.strip().lower()
    signals = get_classification_signal_groups()

    if not active_case:
        return IssueClassification.NEW_ISSUE, None

    for signal in signals["cancel"]:
        if signal in msg_lower:
            if active_case and active_case.state not in ("CLOSED", "CANCELLED"):
                active_case.state = "CANCELLED"
                active_case.updated_at = timezone.now()
                active_case.save()
            return IssueClassification.NEW_ISSUE, None

    for signal in signals["status_check"]:
        if signal in msg_lower:
            return IssueClassification.STATUS_CHECK, None

    if msg_lower in signals["approval"]:
        return IssueClassification.CONTINUE_EXISTING, active_case.case_id

    for signal in signals["new_issue"]:
        if signal in msg_lower:
            return IssueClassification.NEW_ISSUE, None

    for signal in signals["recurrence"]:
        if signal in msg_lower:
            match = _find_recurrence_match(thread_id, msg_lower)
            if match:
                return IssueClassification.RECURRENCE, match
            break

    for signal in signals["followup"]:
        if signal in msg_lower:
            target = _find_followup_target(thread_id, msg_lower, active_case)
            return IssueClassification.FOLLOWUP, target

    for signal in signals["continue"]:
        if msg_lower.startswith(signal) or f" {signal} " in f" {msg_lower} ":
            return IssueClassification.CONTINUE_EXISTING, active_case.case_id

    cascade_match = _check_cascade(thread_id, msg_lower, active_case)
    if cascade_match:
        return IssueClassification.RELATED_NEW, cascade_match

    recurrence_match = _check_resolved_workflow_match(thread_id, msg_lower)
    if recurrence_match:
        return IssueClassification.RECURRENCE, recurrence_match

    llm_result = _llm_classify(thread_id, msg_lower, active_case)
    if llm_result:
        return llm_result

    if active_case.state not in ("CLOSED", "CANCELLED",
                                  "RESOLVED_PENDING_CONFIRMATION"):
        return IssueClassification.CONTINUE_EXISTING, active_case.case_id

    return IssueClassification.NEW_ISSUE, None


def _find_recurrence_match(thread_id: str, msg_lower: str) -> Optional[str]:
    resolved = Case.objects.filter(
        thread_id=thread_id,
        state__in=["CLOSED", "RESOLVED_PENDING_CONFIRMATION"],
    ).order_by("-updated_at")[:5]

    for case in resolved:
        for wf in (case.workflows_involved or []):
            wf_parts = wf.lower().replace("_", " ").split()
            if any(part in msg_lower for part in wf_parts if len(part) > 3):
                return case.case_id
    return None


def _find_followup_target(thread_id: str, msg_lower: str,
                          active_case: Case) -> str:
    resolved = Case.objects.filter(
        thread_id=thread_id,
        state__in=["CLOSED", "RESOLVED_PENDING_CONFIRMATION"],
    ).order_by("-updated_at")[:5]

    for case in resolved:
        for wf in (case.workflows_involved or []):
            wf_parts = wf.lower().replace("_", " ").split()
            if any(part in msg_lower for part in wf_parts if len(part) > 3):
                return case.case_id
    return active_case.case_id


def _check_cascade(thread_id: str, msg_lower: str,
                   active_case: Case) -> Optional[str]:
    failure_words = ["fail", "error", "broken", "down", "stuck", "issue"]
    if not any(fw in msg_lower for fw in failure_words):
        return None

    active_wfs = {wf.lower() for wf in (active_case.workflows_involved or [])}
    mentions_active = any(
        any(part in msg_lower
            for part in wf.replace("_", " ").split() if len(part) > 3)
        for wf in active_wfs
    )
    if not mentions_active and active_wfs:
        return active_case.case_id
    return None


def _check_resolved_workflow_match(thread_id: str,
                                   msg_lower: str) -> Optional[str]:
    failure_words = [
        "fail", "error", "broken", "down", "stuck", "issue", "problem",
    ]
    if not any(fw in msg_lower for fw in failure_words):
        return None

    resolved = Case.objects.filter(
        thread_id=thread_id,
        state__in=["CLOSED", "RESOLVED_PENDING_CONFIRMATION"],
    ).order_by("-updated_at")[:5]

    for case in resolved:
        for wf in (case.workflows_involved or []):
            wf_parts = wf.lower().replace("_", " ").split()
            if any(part in msg_lower for part in wf_parts if len(part) > 3):
                return case.case_id
    return None


def _llm_classify(thread_id: str, msg_lower: str,
                  active_case: Case
                  ) -> Optional[Tuple[str, Optional[str]]]:
    try:
        from config.llm_client import llm_client

        active_desc = (
            f"Active case: {active_case.case_id}, state={active_case.state}, "
            f"workflows={active_case.workflows_involved}"
        )

        resolved = Case.objects.filter(
            thread_id=thread_id,
            state__in=["CLOSED", "RESOLVED_PENDING_CONFIRMATION"],
        ).order_by("-updated_at")[:3]
        resolved_desc = "; ".join(
            f"{c.case_id}: workflows={c.workflows_involved}" for c in resolved
        ) or "(none)"

        prompt = (
            f"Classify this support message.\n"
            f"Active: {active_desc}\n"
            f"Resolved: {resolved_desc}\n"
            f"Message: \"{msg_lower}\"\n\n"
            f"Reply with ONE word: CONTINUE_EXISTING, NEW_ISSUE, "
            f"RELATED_NEW, RECURRENCE, FOLLOWUP, or STATUS_CHECK"
        )

        resp = llm_client.chat(
            prompt,
            system="You classify support messages. Reply with one word only.",
        ).strip().upper()

        classification_map = {
            "CONTINUE_EXISTING": IssueClassification.CONTINUE_EXISTING,
            "NEW_ISSUE": IssueClassification.NEW_ISSUE,
            "RELATED_NEW": IssueClassification.RELATED_NEW,
            "RECURRENCE": IssueClassification.RECURRENCE,
            "FOLLOWUP": IssueClassification.FOLLOWUP,
            "STATUS_CHECK": IssueClassification.STATUS_CHECK,
        }

        cls = classification_map.get(resp)
        if cls:
            issue_id = (
                active_case.case_id
                if cls != IssueClassification.NEW_ISSUE else None
            )
            return cls, issue_id
    except Exception as e:
        logger.warning(f"LLM classification fallback failed: {e}")

    return None


def link_cases(case_id_1: str, case_id_2: str,
               link_type: str = "RELATED"):
    IssueLink.objects.get_or_create(
        case_id_1=case_id_1,
        case_id_2=case_id_2,
        defaults={"link_type": link_type},
    )


def should_escalate_recurrence(case: Case) -> bool:
    return case.recurrence_count >= int(
        get_runtime_value(
            "RECURRENCE_ESCALATION_THRESHOLD",
            os.environ.get("RECURRENCE_ESCALATION_THRESHOLD", "3"),
        )
    )
