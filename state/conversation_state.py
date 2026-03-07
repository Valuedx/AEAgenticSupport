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

        # Feature 2.3: Metadata
        self.summary: str = ""
        self.is_human_handoff: bool = False
        self.tags: list[str] = []
        self.preferred_language: str = "en" # Feature 2.2

        # Concurrency
        self.is_agent_working: bool = False
        self.interrupt_requested: bool = False
        self._message_queue: list[dict] = []
        self._queue_lock = threading.Lock()
        # Deferred message writes: flushed in save() to reduce hot-path DB round-trips
        self._pending_message_inserts: list[tuple[str, str, dict]] = []

    # ── Messages ──

    def add_message(self, role: str, content: str, metadata: dict = None):
        """Add a message to the state; persistence to chat_messages is deferred until save()."""
        timestamp = datetime.now().isoformat()
        msg = {
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "metadata": metadata or {},
        }
        self.messages.append(msg)
        if self.conversation_id:
            self._pending_message_inserts.append((role, content, metadata or {}))


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
        """Persist conversation state and any pending message inserts to PostgreSQL."""
        if not self.conversation_id:
            return
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # Flush deferred message inserts in one batch
                    if self._pending_message_inserts:
                        for role, content, metadata in self._pending_message_inserts:
                            cur.execute("""
                                INSERT INTO chat_messages (conversation_id, role, content, metadata)
                                VALUES (%s, %s, %s, %s)
                            """, (self.conversation_id, role, content, Json(metadata)))
                        self._pending_message_inserts.clear()
                    state_data = {
                        "messages": self.messages[-50:],
                        "findings": [asdict(f) for f in self.findings[-20:]],
                        "tool_call_log": self.tool_call_log[-30:],
                        "affected_workflows": self.affected_workflows,
                        "pending_action": self.pending_action,
                        "pending_action_summary": self.pending_action_summary,
                        "param_collection": self.param_collection,
                        "preferred_language": self.preferred_language,
                    }
                    cur.execute("""
                        INSERT INTO conversation_state
                            (conversation_id, user_id, user_role,
                             phase, state_data, summary, is_human_handoff, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (conversation_id)
                        DO UPDATE SET
                            user_id = EXCLUDED.user_id,
                            user_role = EXCLUDED.user_role,
                            phase = EXCLUDED.phase,
                            state_data = EXCLUDED.state_data,
                            summary = EXCLUDED.summary,
                            is_human_handoff = EXCLUDED.is_human_handoff,
                            updated_at = NOW()
                    """, (
                        self.conversation_id,
                        self.user_id,
                        self.user_role,
                        self.phase.value,
                        Json(state_data),
                        self.summary,
                        self.is_human_handoff,
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
                        "SELECT user_id, user_role, phase, state_data, summary, is_human_handoff "
                        "FROM conversation_state WHERE conversation_id = %s",
                        (conversation_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        state.user_id = row[0] or ""
                        state.user_role = row[1] or "technical"
                        state.phase = ConversationPhase(row[2] or "idle")
                        data = row[3] or {}
                        state.summary = row[4] or ""
                        state.is_human_handoff = bool(row[5])
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
                        state.preferred_language = data.get("preferred_language", "en")
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
            "summary": self.summary,
            "is_human_handoff": self.is_human_handoff,
            "preferred_language": self.preferred_language,
        }

    # ── History & Search ───────────────────────────────────────────

    @staticmethod
    def search_history(query: str, limit: int = 10) -> list[dict]:
        """Search across all conversation messages."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT m.conversation_id, m.role, m.content, m.created_at, s.summary
                        FROM chat_messages m
                        LEFT JOIN conversation_state s ON m.conversation_id = s.conversation_id
                        WHERE m.content ILIKE %s
                        ORDER BY m.created_at DESC
                        LIMIT %s
                    """, (f"%{query}%", limit))
                    results = []
                    for row in cur.fetchall():
                        results.append({
                            "conversation_id": row[0],
                            "role": row[1],
                            "content": row[2],
                            "timestamp": row[3].isoformat(),
                            "summary": row[4] or "",
                        })
                    return results
        except Exception as e:
            logger.warning(f"Search failed: {e}")
            return []

    def export_history(self, format: str = "json") -> str:
        """Export full conversation history as string."""
        if format == "json":
            return json.dumps({
                "conversation_id": self.conversation_id,
                "summary": self.summary,
                "messages": self.messages,
                "findings": [asdict(f) for f in self.findings],
            }, indent=2)
        elif format == "markdown":
            lines = [f"# Conversation Export: {self.conversation_id}\n"]
            if self.summary:
                lines.append(f"**Summary:** {self.summary}\n")
            lines.append("## Chat History\n")
            for m in self.messages:
                lines.append(f"**{m['role'].upper()}** ({m.get('timestamp','')}): {m['content']}\n")
            lines.append("\n## Findings\n")
            for f in self.findings:
                lines.append(f"- **{f.category}** [{f.severity}]: {f.summary}")
            return "\n".join(lines)
        return ""

    # ── Summary & Feedback ───────────────────────────────────────────

    def generate_summary(self, force: bool = False):
        """Generate/Update conversation summary using LLM."""
        if self.summary and not force:
            return self.summary
        
        if len(self.messages) < 2:
            return ""

        try:
            from config.llm_client import llm_client
            history = "\n".join([f"{m['role']}: {m['content']}" for m in self.messages[-20:]])
            prompt = f"Summarize this support conversation in ONE brief sentence (max 20 words):\n\n{history}"
            summary = llm_client.chat(prompt, system="You provide concise summaries of support interactions.")
            self.summary = summary.strip()
            self.save()
            return self.summary
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return self.summary

    def save_feedback(self, rating: int, comments: str = ""):
        """Save user feedback for this conversation."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO user_feedback (conversation_id, user_id, rating, comments)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (conversation_id)
                        DO UPDATE SET rating = EXCLUDED.rating, comments = EXCLUDED.comments
                    """, (self.conversation_id, self.user_id, rating, comments))
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to save feedback: {e}")
