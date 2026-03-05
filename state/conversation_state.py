"""
Conversation state management.
Tracks the current state of a support conversation including
phase, messages, findings, tool calls, and queued messages.
Persists core state to PostgreSQL via save()/load().
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional

from psycopg2.extras import Json

from config.db import get_conn
from config.settings import CONFIG

logger = logging.getLogger("ops_agent.state")


class ConversationPhase(Enum):
    IDLE = "idle"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


@dataclass
class Finding:
    """A single investigative finding produced by a tool call."""
    category: str
    summary: str
    severity: str = "info"
    details: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class ConversationState:
    """Tracks all state for a single support conversation."""

    def __init__(self):
        self.conversation_id: str = ""
        self.user_id: str = ""
        self.user_role: str = "technical"

        self.phase: ConversationPhase = ConversationPhase.IDLE
        self.messages: list[dict] = []
        self.findings: list[Finding] = []
        self.tool_call_log: list[dict] = []
        self.affected_workflows: list[str] = []

        self.pending_action: Optional[dict] = None
        self.pending_action_summary: str = ""
        self.param_collection: dict = {}
        self.rca_data: Optional[dict] = None

        # Concurrency
        self.is_agent_working: bool = False
        self.interrupt_requested: bool = False
        self._message_queue: list[dict] = []
        self._queue_lock = threading.Lock()

    # ── Messages ──

    def add_message(self, role: str, content: str):
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })

    # ── Findings ──

    def add_finding(self, category: str, summary: str,
                    severity: str = "info", details: dict = None):
        finding = Finding(
            category=category,
            summary=summary,
            severity=severity,
            details=details or {},
        )
        self.findings.append(finding)

    # ── Tool calls ──

    def log_tool_call(self, tool_name: str, params: dict,
                      result: dict, success: bool):
        self.tool_call_log.append({
            "tool": tool_name,
            "params": params,
            "result": result,
            "success": success,
            "timestamp": datetime.now().isoformat(),
        })

    # ── Concurrent message queue ──

    def queue_user_message(self, message: str, hint: str = ""):
        with self._queue_lock:
            self._message_queue.append({
                "content": message,
                "hint": hint,
                "timestamp": datetime.now().isoformat(),
            })

    def get_queued_messages(self) -> list[dict]:
        with self._queue_lock:
            messages = list(self._message_queue)
            self._message_queue.clear()
            return messages

    @property
    def message_queue(self) -> list[dict]:
        with self._queue_lock:
            return list(self._message_queue)

    def has_queued_messages(self) -> bool:
        with self._queue_lock:
            return len(self._message_queue) > 0

    # ── Persistence ──

    def save(self):
        """Persist conversation state to PostgreSQL."""
        if not self.conversation_id:
            return
        try:
            state_data = {
                "messages": self.messages[-50:],
                "findings": [asdict(f) for f in self.findings[-20:]],
                "tool_call_log": self.tool_call_log[-30:],
                "affected_workflows": self.affected_workflows,
                "pending_action": self.pending_action,
                "pending_action_summary": self.pending_action_summary,
                "param_collection": self.param_collection,
            }
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO conversation_state
                            (conversation_id, user_id, user_role,
                             phase, state_data, updated_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (conversation_id)
                        DO UPDATE SET
                            user_id = EXCLUDED.user_id,
                            user_role = EXCLUDED.user_role,
                            phase = EXCLUDED.phase,
                            state_data = EXCLUDED.state_data,
                            updated_at = NOW()
                    """, (
                        self.conversation_id,
                        self.user_id,
                        self.user_role,
                        self.phase.value,
                        Json(state_data),
                    ))
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not persist conversation state: {e}")

    @classmethod
    def load(cls, conversation_id: str) -> "ConversationState":
        """Load conversation state from PostgreSQL. Returns new state if not found."""
        state = cls()
        state.conversation_id = conversation_id
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id, user_role, phase, state_data "
                        "FROM conversation_state WHERE conversation_id = %s",
                        (conversation_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        state.user_id = row[0] or ""
                        state.user_role = row[1] or "technical"
                        state.phase = ConversationPhase(row[2] or "idle")
                        data = row[3] or {}
                        state.messages = data.get("messages", [])
                        state.tool_call_log = data.get("tool_call_log", [])
                        state.affected_workflows = data.get(
                            "affected_workflows", []
                        )
                        state.pending_action = data.get("pending_action")
                        state.pending_action_summary = data.get(
                            "pending_action_summary", ""
                        )
                        state.param_collection = data.get("param_collection", {}) or {}
                        for f_data in data.get("findings", []):
                            state.findings.append(Finding(**f_data))
        except Exception as e:
            logger.warning(f"Could not load conversation state: {e}")
        return state

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "user_role": self.user_role,
            "phase": self.phase.value,
            "message_count": len(self.messages),
            "finding_count": len(self.findings),
            "affected_workflows": self.affected_workflows,
            "is_agent_working": self.is_agent_working,
        }
