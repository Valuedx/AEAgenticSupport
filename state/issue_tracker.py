"""
Tracks distinct issues within a conversation.
Determines whether a new message is about an existing issue or a new one.
Persists issue state to PostgreSQL so it survives process restarts.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from psycopg2.extras import Json

from config.db import get_conn
from config.llm_client import llm_client
from config.settings import CONFIG
from rag.engine import get_rag_engine

logger = logging.getLogger("ops_agent.issue_tracker")

RECURRENCE_ESCALATION_THRESHOLD = CONFIG.get(
    "RECURRENCE_ESCALATION_THRESHOLD", 3
)


class IssueStatus(Enum):
    ACTIVE = "active"
    AWAITING_APPROVAL = "awaiting"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    STALE = "stale"


class MessageClassification(Enum):
    CONTINUE_EXISTING = "continue_existing"
    NEW_ISSUE = "new_issue"
    RELATED_NEW = "related_new"
    RECURRENCE = "recurrence"
    FOLLOWUP = "followup"
    STATUS_CHECK = "status_check"


@dataclass
class Issue:
    issue_id: str = field(
        default_factory=lambda: f"ISS-{uuid.uuid4().hex[:8]}"
    )
    title: str = ""
    description: str = ""
    status: IssueStatus = IssueStatus.ACTIVE
    workflows_involved: list[str] = field(default_factory=list)
    error_signatures: list[str] = field(default_factory=list)
    root_cause: str = ""
    resolution: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    resolved_at: str = ""
    message_ids: list[str] = field(default_factory=list)
    finding_ids: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    affected_workflows: list[str] = field(default_factory=list)
    related_issue_ids: list[str] = field(default_factory=list)
    recurrence_count: int = 0

    def is_stale(self, stale_minutes: int = None) -> bool:
        minutes = stale_minutes or CONFIG.get("STALE_ISSUE_MINUTES", 30)
        last = datetime.fromisoformat(self.updated_at)
        return (datetime.now() - last) > timedelta(minutes=minutes)

    def touch(self):
        self.updated_at = datetime.now().isoformat()

    def to_summary(self) -> str:
        return (
            f"[{self.issue_id}] {self.title} | Status: {self.status.value} | "
            f"Workflows: {', '.join(self.workflows_involved)} | "
            f"Error: {', '.join(self.error_signatures[:2])} | "
            f"Recurrences: {self.recurrence_count} | "
            f"Created: {self.created_at}"
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        d["status"] = IssueStatus(d["status"])
        return cls(**d)


# =========================================================================
# Heuristic signal lists
# =========================================================================

from config.classification_signals import (
    CONTINUE_SIGNALS,
    NEW_ISSUE_SIGNALS,
    RECURRENCE_SIGNALS,
    FOLLOWUP_SIGNALS,
    STATUS_CHECK_SIGNALS,
    CANCEL_SIGNALS,
)


class IssueTracker:
    """
    Registry of issues per conversation.
    Classification pipeline: heuristics -> workflow matching -> LLM.
    Persists to PostgreSQL.
    """

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.issues: dict[str, Issue] = {}
        self.active_issue_id: str | None = None
        self._load_from_db()

    # =====================================================================
    # Persistence
    # =====================================================================

    def _load_from_db(self):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT issue_data FROM issue_registry "
                        "WHERE conversation_id = %s",
                        (self.conversation_id,),
                    )
                    for (issue_data,) in cur.fetchall():
                        issue = Issue.from_dict(issue_data)
                        self.issues[issue.issue_id] = issue

                    cur.execute(
                        "SELECT active_issue_id FROM conversation_state "
                        "WHERE conversation_id = %s",
                        (self.conversation_id,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        self.active_issue_id = row[0]
        except Exception as e:
            logger.warning(f"Could not load issue tracker state: {e}")

    def _persist_issue(self, issue: Issue):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO issue_registry
                            (conversation_id, issue_id, issue_data, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (conversation_id, issue_id)
                        DO UPDATE SET
                            issue_data = EXCLUDED.issue_data,
                            updated_at = NOW()
                    """, (
                        self.conversation_id,
                        issue.issue_id,
                        Json(issue.to_dict()),
                    ))
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not persist issue {issue.issue_id}: {e}")

    def _persist_active_id(self):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO conversation_state
                            (conversation_id, active_issue_id, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (conversation_id)
                        DO UPDATE SET
                            active_issue_id = EXCLUDED.active_issue_id,
                            updated_at = NOW()
                    """, (self.conversation_id, self.active_issue_id))
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not persist active issue id: {e}")

    # =====================================================================
    # Classification pipeline
    # =====================================================================

    def classify_message(
        self, user_message: str, conversation_messages: list[dict],
    ) -> tuple[MessageClassification, str | None]:
        msg_lower = user_message.strip().lower()

        self._mark_stale_issues()

        active_issues = self._get_active_issues()
        if not active_issues and not self._get_resolved_issues():
            return MessageClassification.NEW_ISSUE, None

        heuristic = self._heuristic_classify(msg_lower)
        if heuristic:
            return heuristic

        recurrence = self._check_recurrence(user_message)
        if recurrence:
            return MessageClassification.RECURRENCE, recurrence

        cascade = self._check_cascade(user_message)
        if cascade:
            return MessageClassification.RELATED_NEW, cascade

        all_issues = self._get_active_issues() + self._get_resolved_issues()
        if all_issues:
            return self._llm_classify(
                user_message, all_issues, conversation_messages
            )

        return MessageClassification.NEW_ISSUE, None

    def _heuristic_classify(
        self, msg_lower: str,
    ) -> tuple[MessageClassification, str | None] | None:
        if msg_lower in ("approve", "reject", "yes", "no",
                         "go ahead", "proceed"):
            return MessageClassification.CONTINUE_EXISTING, self.active_issue_id

        for signal in CANCEL_SIGNALS:
            if signal in msg_lower:
                if self.active_issue_id and self.active_issue_id in self.issues:
                    issue = self.issues[self.active_issue_id]
                    if issue.status not in (IssueStatus.RESOLVED,
                                            IssueStatus.ESCALATED):
                        issue.status = IssueStatus.RESOLVED
                        issue.resolution = "Cancelled by user"
                        issue.resolved_at = datetime.now().isoformat()
                        self._persist_issue(issue)
                return MessageClassification.NEW_ISSUE, None

        for signal in STATUS_CHECK_SIGNALS:
            if signal in msg_lower:
                return MessageClassification.STATUS_CHECK, None

        for signal in NEW_ISSUE_SIGNALS:
            if signal in msg_lower:
                return MessageClassification.NEW_ISSUE, None

        for signal in RECURRENCE_SIGNALS:
            if signal in msg_lower:
                match = self._check_recurrence_by_workflow(msg_lower)
                if match:
                    return MessageClassification.RECURRENCE, match
                return None  # ambiguous — LLM fallback

        for signal in FOLLOWUP_SIGNALS:
            if signal in msg_lower:
                best = self._find_followup_target(msg_lower)
                return (
                    MessageClassification.FOLLOWUP,
                    best or self.active_issue_id,
                )

        for signal in CONTINUE_SIGNALS:
            if (msg_lower.startswith(signal)
                    or f" {signal} " in f" {msg_lower} "):
                return (
                    MessageClassification.CONTINUE_EXISTING,
                    self.active_issue_id,
                )

        return None

    # ── Workflow matching helpers ──

    def _check_recurrence_by_workflow(self, msg_lower: str) -> str | None:
        for issue in self._get_resolved_issues():
            for wf in issue.workflows_involved:
                wf_parts = wf.lower().replace("_", " ").split()
                if any(p in msg_lower for p in wf_parts if len(p) > 3):
                    return issue.issue_id
        return None

    def _check_recurrence(self, message: str) -> str | None:
        resolved = self._get_resolved_issues()
        if not resolved:
            return None
        msg_lower = message.lower()
        failure_words = [
            "fail", "error", "broken", "down", "stuck",
            "issue", "problem", "not working",
        ]
        for issue in resolved:
            wf_match = any(
                any(p in msg_lower for p in wf.lower().replace("_", " ").split()
                    if len(p) > 3)
                for wf in issue.workflows_involved
            ) or any(s.lower() in msg_lower for s in issue.error_signatures)
            if wf_match and any(fw in msg_lower for fw in failure_words):
                return issue.issue_id
        return None

    def _check_cascade(self, message: str) -> str | None:
        msg_lower = message.lower()
        failure_words = ["fail", "error", "broken", "down", "stuck", "issue"]
        if not any(fw in msg_lower for fw in failure_words):
            return None
        for issue in self._get_active_issues():
            active_wfs = {wf.lower() for wf in issue.workflows_involved}
            mentions_active = any(
                any(p in msg_lower
                    for p in wf.replace("_", " ").split() if len(p) > 3)
                for wf in active_wfs
            )
            if not mentions_active:
                return issue.issue_id
        return None

    def _find_followup_target(self, msg_lower: str) -> str | None:
        for issue in self._get_resolved_issues():
            for wf in issue.workflows_involved:
                wf_parts = wf.lower().replace("_", " ").split()
                if any(p in msg_lower for p in wf_parts if len(p) > 3):
                    return issue.issue_id
        for issue in self.issues.values():
            if issue.status == IssueStatus.STALE:
                for wf in issue.workflows_involved:
                    wf_parts = wf.lower().replace("_", " ").split()
                    if any(p in msg_lower for p in wf_parts if len(p) > 3):
                        return issue.issue_id
        return None

    # ── LLM fallback ──

    def _llm_classify(
        self, user_message: str, all_issues: list[Issue],
        conversation_messages: list[dict],
    ) -> tuple[MessageClassification, str | None]:
        active_summary = "\n".join(
            f"  - {i.to_summary()}" for i in all_issues
            if i.status in (IssueStatus.ACTIVE, IssueStatus.AWAITING_APPROVAL)
        )
        resolved_summary = "\n".join(
            f"  - {i.to_summary()}" for i in all_issues
            if i.status == IssueStatus.RESOLVED
        )
        recent = "\n".join(
            f"  {m['role']}: {m['content'][:150]}"
            for m in conversation_messages[-8:]
        )

        prompt = f"""Classify this user message in a support conversation.

Active issues:
{active_summary or '  (none)'}

Recently resolved issues:
{resolved_summary or '  (none)'}

Recent conversation:
{recent}

New message from user: "{user_message}"

Classify as exactly ONE of:
- CONTINUE_EXISTING: More info about an active issue
- NEW_ISSUE: Completely different problem
- RELATED_NEW: Related but distinct (e.g., downstream cascade)
- RECURRENCE: A previously resolved issue happening again
- FOLLOWUP: Asking about outcome of a resolved issue
- STATUS_CHECK: General health or status query

Reply in format: CLASSIFICATION|issue_id
If no issue_id applies: CLASSIFICATION|none"""

        response = llm_client.chat(
            prompt,
            system="You classify support messages. Reply in the exact format.",
        ).strip()

        parts = response.split("|")
        cls_str = parts[0].strip().upper()
        issue_id = parts[1].strip() if len(parts) > 1 else None
        if issue_id == "none":
            issue_id = None

        cls_map = {
            "CONTINUE_EXISTING": MessageClassification.CONTINUE_EXISTING,
            "NEW_ISSUE": MessageClassification.NEW_ISSUE,
            "RELATED_NEW": MessageClassification.RELATED_NEW,
            "RECURRENCE": MessageClassification.RECURRENCE,
            "FOLLOWUP": MessageClassification.FOLLOWUP,
            "STATUS_CHECK": MessageClassification.STATUS_CHECK,
        }

        cls = cls_map.get(cls_str, MessageClassification.NEW_ISSUE)
        if issue_id and issue_id not in self.issues:
            issue_id = self.active_issue_id

        logger.info(
            f"LLM classified message as {cls.value}, issue={issue_id}"
        )
        return cls, issue_id

    # =====================================================================
    # Issue lifecycle
    # =====================================================================

    def create_issue(self, title: str, description: str,
                     workflows: list[str] = None) -> Issue:
        issue = Issue(
            title=title,
            description=description,
            workflows_involved=workflows or [],
        )
        self.issues[issue.issue_id] = issue
        self.active_issue_id = issue.issue_id
        self._persist_issue(issue)
        self._persist_active_id()
        logger.info(f"Created new issue: {issue.issue_id} — {title}")
        return issue

    def get_active_issue(self) -> Issue | None:
        if self.active_issue_id and self.active_issue_id in self.issues:
            return self.issues[self.active_issue_id]
        return None

    def switch_to_issue(self, issue_id: str):
        if issue_id in self.issues:
            self.active_issue_id = issue_id
            self.issues[issue_id].touch()
            self._persist_issue(self.issues[issue_id])
            self._persist_active_id()

    def resolve_issue(self, issue_id: str, resolution: str):
        if issue_id in self.issues:
            issue = self.issues[issue_id]
            issue.status = IssueStatus.RESOLVED
            issue.resolution = resolution
            issue.resolved_at = datetime.now().isoformat()
            issue.touch()
            self._persist_issue(issue)
            logger.info(f"Resolved issue: {issue_id}")
            self.sync_to_rag(issue_id)

    def reopen_issue(self, issue_id: str) -> Issue:
        if issue_id in self.issues:
            issue = self.issues[issue_id]
            issue.status = IssueStatus.ACTIVE
            issue.recurrence_count += 1
            issue.resolved_at = ""
            issue.touch()
            self.active_issue_id = issue_id
            self._persist_issue(issue)
            self._persist_active_id()
            logger.info(
                f"Reopened issue {issue_id} "
                f"(recurrence #{issue.recurrence_count})"
            )
            return issue
        return self.create_issue("Reopened issue", "Recurrence of previous")

    def should_escalate_recurrence(self, issue_id: str) -> bool:
        if issue_id in self.issues:
            return (
                self.issues[issue_id].recurrence_count
                >= RECURRENCE_ESCALATION_THRESHOLD
            )
        return False

    def link_issues(self, issue_id_1: str, issue_id_2: str):
        if issue_id_1 in self.issues and issue_id_2 in self.issues:
            i1 = self.issues[issue_id_1]
            i2 = self.issues[issue_id_2]
            if issue_id_2 not in i1.related_issue_ids:
                i1.related_issue_ids.append(issue_id_2)
            if issue_id_1 not in i2.related_issue_ids:
                i2.related_issue_ids.append(issue_id_1)
            self._persist_issue(i1)
            self._persist_issue(i2)

    def add_error_signature(self, issue_id: str, signature: str):
        if issue_id in self.issues:
            if signature not in self.issues[issue_id].error_signatures:
                self.issues[issue_id].error_signatures.append(signature)
                self._persist_issue(self.issues[issue_id])

    def add_workflow_to_issue(self, issue_id: str, workflow_name: str):
        if issue_id in self.issues:
            if workflow_name not in self.issues[issue_id].workflows_involved:
                self.issues[issue_id].workflows_involved.append(workflow_name)
                self._persist_issue(self.issues[issue_id])

    def add_finding_to_issue(self, issue_id: str, finding: dict):
        if issue_id in self.issues:
            self.issues[issue_id].findings.append(finding)
            self._persist_issue(self.issues[issue_id])

    def get_issue_findings(self, issue_id: str) -> list[dict]:
        if issue_id in self.issues:
            return self.issues[issue_id].findings
        return []

    def resume_stale_issue(self, issue_id: str) -> Issue | None:
        if (issue_id in self.issues
                and self.issues[issue_id].status == IssueStatus.STALE):
            issue = self.issues[issue_id]
            issue.status = IssueStatus.ACTIVE
            issue.touch()
            self.active_issue_id = issue_id
            self._persist_issue(issue)
            self._persist_active_id()
            logger.info(f"Resumed stale issue: {issue_id}")
            return issue
        return None

    def sync_to_rag(self, issue_id: str):
        """Index a resolved issue into the RAG 'past_incidents' collection."""
        if issue_id not in self.issues:
            return

        issue = self.issues[issue_id]
        if issue.status != IssueStatus.RESOLVED:
            logger.warning(f"Attempted to RAG-sync unresolved issue {issue_id}")
            return

        # Format content for semantic search
        content = (
            f"Title: {issue.title}\n"
            f"Description: {issue.description}\n"
            f"Workflows: {', '.join(issue.workflows_involved)}\n"
            f"Resolution: {issue.resolution}\n"
            f"Root Cause: {issue.root_cause}\n"
        )
        if issue.findings:
            content += "\nFindings:\n"
            for f in issue.findings:
                content += f"  - {f.get('note', f.get('finding', str(f)))}\n"

        doc = {
            "id": issue.issue_id,
            "content": content,
            "metadata": {
                "issue_id": issue.issue_id,
                "type": "incident",
                "org_code": CONFIG.get("AE_ORG_CODE"),
                "resolved_at": issue.resolved_at
            }
        }

        try:
            get_rag_engine().index_documents([doc], collection="past_incidents")
            logger.info(f"Synced resolved issue {issue_id} to RAG (past_incidents)")
        except Exception as e:
            logger.warning(f"Failed to sync issue {issue_id} to RAG: {e}")

    # ── Query helpers ──

    def _get_active_issues(self) -> list[Issue]:
        return [
            i for i in self.issues.values()
            if i.status in (IssueStatus.ACTIVE, IssueStatus.AWAITING_APPROVAL)
        ]

    def _get_resolved_issues(self) -> list[Issue]:
        return [
            i for i in self.issues.values()
            if i.status == IssueStatus.RESOLVED
        ]

    def _mark_stale_issues(self):
        stale_minutes = CONFIG.get("STALE_ISSUE_MINUTES", 30)
        for issue in self._get_active_issues():
            if issue.is_stale(stale_minutes):
                issue.status = IssueStatus.STALE
                self._persist_issue(issue)
                logger.info(f"Issue {issue.issue_id} marked stale")

    def get_all_issues_summary(self) -> str:
        if not self.issues:
            return "No issues tracked in this session."
        return "\n".join(i.to_summary() for i in self.issues.values())
