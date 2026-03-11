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
        self.exists_in_store: bool = False
        self.user_id: str = ""
        self.user_role: str = "technical"
        self.user_name: str = ""
        self.user_email: str = ""
        self.user_team: str = ""
        self.user_metadata: dict = {}

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
                    # sync user registry first
                    if self.user_id:
                        cur.execute("""
                            INSERT INTO user_registry (user_id, user_role, user_name, user_email, user_team, metadata, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, NOW())
                            ON CONFLICT (user_id) DO UPDATE SET
                                user_role = EXCLUDED.user_role,
                                user_name = COALESCE(NULLIF(EXCLUDED.user_name, ''), user_registry.user_name),
                                user_email = COALESCE(NULLIF(EXCLUDED.user_email, ''), user_registry.user_email),
                                user_team = COALESCE(NULLIF(EXCLUDED.user_team, ''), user_registry.user_team),
                                metadata = user_registry.metadata || EXCLUDED.metadata,
                                updated_at = NOW()
                        """, (self.user_id, self.user_role, self.user_name, self.user_email, self.user_team, Json(self.user_metadata)))

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
                        "user": {
                            "user_name": self.user_name,
                            "user_email": self.user_email,
                            "user_team": self.user_team,
                            "user_metadata": self.user_metadata,
                        }
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
                        state.exists_in_store = True
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
                        
                        # Load user details from registry
                        if state.user_id:
                            cur.execute(
                                "SELECT user_name, user_email, user_team, metadata, user_role "
                                "FROM user_registry WHERE user_id = %s",
                                (state.user_id,),
                            )
                            u_row = cur.fetchone()
                            if u_row:
                                state.user_name = u_row[0] or ""
                                state.user_email = u_row[1] or ""
                                state.user_team = u_row[2] or ""
                                state.user_metadata = u_row[3] or {}
                                state.user_role = u_row[4] or state.user_role
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

    def to_detail_dict(self) -> dict:
        return {
            **self.to_dict(),
            "messages": list(self.messages),
            "findings": [asdict(finding) for finding in self.findings],
            "tool_call_log": list(self.tool_call_log),
            "pending_action": self.pending_action,
            "pending_action_summary": self.pending_action_summary,
            "param_collection": dict(self.param_collection or {}),
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

    @staticmethod
    def search_conversations(query: str, limit: int = 20) -> list[dict]:
        """Search conversations with one result row per conversation."""
        clean_query = str(query or "").strip()
        text_like = f"%{clean_query}%"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            s.conversation_id,
                            s.user_id,
                            s.user_role,
                            s.phase,
                            COALESCE(s.summary, ''),
                            s.is_human_handoff,
                            s.updated_at,
                            COALESCE(msg_counts.message_count, 0),
                            COALESCE(latest.content, ''),
                            latest.created_at,
                            COALESCE(match_hit.content, '')
                        FROM conversation_state s
                        LEFT JOIN LATERAL (
                            SELECT COUNT(*) AS message_count
                            FROM chat_messages
                            WHERE conversation_id = s.conversation_id
                        ) AS msg_counts ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT content, created_at
                            FROM chat_messages
                            WHERE conversation_id = s.conversation_id
                            ORDER BY created_at DESC
                            LIMIT 1
                        ) AS latest ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT content, created_at
                            FROM chat_messages
                            WHERE conversation_id = s.conversation_id
                              AND (%s = '' OR content ILIKE %s)
                            ORDER BY created_at DESC
                            LIMIT 1
                        ) AS match_hit ON TRUE
                        WHERE (
                            %s = ''
                            OR s.conversation_id ILIKE %s
                            OR COALESCE(s.user_id, '') ILIKE %s
                            OR COALESCE(s.summary, '') ILIKE %s
                            OR match_hit.content <> ''
                        )
                        ORDER BY COALESCE(match_hit.created_at, latest.created_at, s.updated_at) DESC
                        LIMIT %s
                        """,
                        (
                            clean_query,
                            text_like,
                            clean_query,
                            text_like,
                            text_like,
                            text_like,
                            limit,
                        ),
                    )
                    results = []
                    for row in cur.fetchall():
                        results.append(
                            {
                                "conversation_id": row[0],
                                "user_id": row[1] or "",
                                "user_role": row[2] or "technical",
                                "phase": row[3] or "idle",
                                "summary": row[4] or "",
                                "is_human_handoff": bool(row[5]),
                                "updated_at": row[6].isoformat() if row[6] else "",
                                "message_count": int(row[7] or 0),
                                "last_message": row[8] or "",
                                "last_message_at": row[9].isoformat() if row[9] else "",
                                "match_excerpt": row[10] or "",
                            }
                        )
                    return results
        except Exception as e:
            logger.warning(f"Conversation search failed: {e}")
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

    # ── Conversational Context (Cross-turn Entity Memory) ────────────────────

    def get_recent_context_summary(self, n_turns: int = 5) -> str:
        """Build a compact context block from the last N conversation turns.

        Extracts named entities and resolved actions so the LLM can carry
        forward values (schedule IDs, workflow names, etc.) across issue
        boundaries without asking the user to repeat themselves.

        Returns a formatted string ready for system-prompt injection.
        """
        import re

        # ── Collect last N user/assistant message pairs ─────────────────────
        recent_messages = [
            m for m in self.messages
            if m.get("role") in ("user", "assistant")
        ][-n_turns * 2:]  # n_turns pairs = 2*n messages

        if not recent_messages:
            return ""

        # ── Regex patterns for common entity types ──────────────────────────
        _SCHEDULE_ID = re.compile(r"\bschedule[_\s]?id[:\s#]*(\d{3,})\b", re.IGNORECASE)
        _EXEC_ID     = re.compile(r"\bexecution[_\s]?id[:\s#]*(\d{4,})\b", re.IGNORECASE)
        _REQUEST_ID  = re.compile(r"\b(?:request|req)[_\s]?id[:\s#]*(\d{4,})\b", re.IGNORECASE)
        _STANDALONE_ID = re.compile(r"(?<!\d)(\d{4,6})(?!\d)")
        _WF_NAME     = re.compile(r"\b([A-Z][a-zA-Z0-9_]{2,}(?:_bot|_wf|_workflow|_job))\b")
        _ACTION_DONE = re.compile(
            r"ae\.(schedule|workflow|request|agent)\."
            r"(disable|enable|run_now|restart|cancel|trigger)",
            re.IGNORECASE,
        )

        entities: dict[str, str] = {}
        resolved_actions: list[str] = []

        # ── Scan message text ────────────────────────────────────────────────
        for msg in recent_messages:
            text = str(msg.get("content", "") or "")

            for m in _SCHEDULE_ID.finditer(text):
                entities.setdefault("schedule_id", m.group(1))
            for m in _EXEC_ID.finditer(text):
                entities.setdefault("execution_id", m.group(1))
            for m in _REQUEST_ID.finditer(text):
                entities.setdefault("request_id", m.group(1))
            for m in _WF_NAME.finditer(text):
                entities.setdefault("workflow_name", m.group(1))

            # Standalone numbers in user messages often are IDs in context
            if msg.get("role") == "user":
                for m in _STANDALONE_ID.finditer(text):
                    val = m.group(1)
                    # Don't overwrite already-labelled entities
                    if "schedule_id" not in entities and "id" in text.lower():
                        entities.setdefault("mentioned_id", val)

        # ── Scan tool call log for resolved actions and values ───────────────
        for call in self.tool_call_log[-n_turns:]:
            tool_name = call.get("tool", "")
            params = call.get("params", {}) or {}
            success = call.get("success", False)

            # Extract values from params
            for key, val in params.items():
                if not val:
                    continue
                if "schedule" in key.lower():
                    entities.setdefault("schedule_id", str(val))
                elif "workflow" in key.lower() or "name" in key.lower():
                    entities.setdefault("workflow_name", str(val))
                elif "execution" in key.lower():
                    entities.setdefault("execution_id", str(val))
                elif "request" in key.lower():
                    entities.setdefault("request_id", str(val))

            # Record successful action tools
            if success and _ACTION_DONE.search(tool_name):
                status = "[ok]" if success else "[fail]"
                resolved_actions.append(f"{tool_name} {status}")

        # ── Also scan result data for workflow/schedule names ────────────────
        for call in self.tool_call_log[-3:]:
            result_data = call.get("result") or {}
            if not isinstance(result_data, dict):
                continue
            items = (
                result_data.get("schedules")
                or result_data.get("instances")
                or result_data.get("failures")
                or [result_data]
            )
            for item in (items if isinstance(items, list) else [items]):
                if not isinstance(item, dict):
                    continue
                for k, v in item.items():
                    if not v:
                        continue
                    kl = k.lower()
                    if "schedule_id" in kl or kl == "id":
                        entities.setdefault("schedule_id", str(v))
                    if "name" in kl and len(str(v)) > 2:
                        entities.setdefault("schedule_name", str(v))
                    if "workflow" in kl and "name" in kl:
                        entities.setdefault("workflow_name", str(v))

        if not entities and not resolved_actions:
            return ""

        # ── Format the context block ─────────────────────────────────────────
        lines = ["## Recent Conversation Context"]
        lines.append("Use the following values from earlier in this conversation.")
        lines.append("Do NOT ask the user to provide them again:\n")
        for key, val in entities.items():
            label = key.replace("_", " ").title()
            lines.append(f"  - {label}: **{val}**")
        if resolved_actions:
            lines.append(f"  - Last Actions: {', '.join(resolved_actions[-3:])}")

        # Recent message digest (last 5 turns, compact)
        lines.append("\n### Last Turns Summary:")
        for msg in recent_messages[-6:]:
            role = "User" if msg["role"] == "user" else "Agent"
            content = str(msg.get("content", ""))[:150].replace("\n", " ")
            lines.append(f"  [{role}] {content}")

        lines.append(
            "\nCRITICAL: If the user's current message refers to a schedule, "
            "workflow, or ID without specifying it explicitly, use the values "
            "above from recent context. Never ask for an ID already mentioned."
        )
        return "\n".join(lines)

