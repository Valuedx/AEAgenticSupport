> - **Multi-Agent 2.0 (Patch 2026-03-06)**:
>   - **Strict Tool Isolation**: Implemented role-based tool filtering. Diagnostic specialists are restricted to `logs`/`status` tools; Remediation specialists to `remediation`/`config`.
>   - **Verification Loop**: Added mandatory specialist handoff. Remediation actions now trigger an automatic cross-agent verification turn to confirm resolution.
>   - **Agent Memory**: Added `SharedContext` memory buckets. Specialists now maintain short-term state (e.g., specific log patterns) across multi-turn delegation chains.
>   - **Context-Aware RAG**: RAG queries now automatically ingest active issue metadata (error signatures, workflow names) to prioritize relevant SOPs and KB articles.
>   - **Rich Notifications**: Added `Adaptive Cards` support for MS Teams, enabling interactive high-fidelity approval and escalation alerts.
> - Validation status: `test_enhancements.py` and `test_multi_agent.py` passed.
>
> **Documentation Update (2026-03-04)**  
>
> **Documentation Update (2026-03-02)**  
> Patch release notes included in this version:
> - Fixed circular import initialization in `agents` and `gateway` packages.
> - Fixed approval and protected-workflow enforcement logic.
> - Fixed tool result success/error propagation across execution paths.
> - Improved busy-turn intent routing and queued message handling.
> - Added cross-channel persona propagation (`business` and `technical`) and semantic approval handling.
> - Validation status: `pytest -q tests` passed (`31 passed`).
>

## 9. Phase 6 — RCA Engine (contd)

### 9.1 RCA Agent

Create file: `agents/rca_agent.py`

```python
# agents/rca_agent.py
"""
Root Cause Analysis Agent.
Generates structured RCA reports for business and technical audiences.
"""

import json
import logging
from datetime import datetime
from config.llm_client import llm_client
from state.conversation_state import ConversationState
from rag.engine import get_rag_engine

logger = logging.getLogger("ops_agent.rca")


class RCAAgent:

    def generate_rca(self, state: ConversationState,
                     incident_summary: str = "",
                     tracker=None, issue_id: str = "") -> str:
        # Use per-issue findings if tracker is available, else fall back to state
        if tracker and issue_id:
            findings = tracker.get_issue_findings(issue_id)
            issue = tracker.issues.get(issue_id)
            affected_wfs = issue.workflows_involved if issue else state.affected_workflows
        else:
            findings = state.findings
            affected_wfs = state.affected_workflows

        if not findings:
            return "I need to investigate first before generating an RCA."

        search_query = incident_summary or " ".join(affected_wfs)
        past_incidents = get_rag_engine().search_past_incidents(search_query, top_k=3)

        findings_text = json.dumps(
            [{"category": f.category, "summary": f.summary,
              "severity": f.severity, "details": f.details}
             for f in findings], indent=2, default=str
        )

        past_text = ""
        for inc in past_incidents:
            meta = inc.get("metadata", {})
            past_text += (
                f"\n- Past: {meta.get('summary', '')}"
                f"\n  Root Cause: {meta.get('root_cause', '')}"
                f"\n  Resolution: {meta.get('resolution', '')}\n"
            )

        if state.user_role == "business":
            rca = self._generate_business_rca(findings_text, past_text,
                                              state.affected_workflows, incident_summary)
        else:
            rca = self._generate_technical_rca(findings_text, past_text,
                                               state.affected_workflows, incident_summary,
                                               state.tool_call_log)

        state.rca_data = {
            "generated_at": datetime.now().isoformat(),
            "report": rca,
            "user_role": state.user_role,
        }
        self._index_as_past_incident(state, rca)
        return rca

    def _generate_business_rca(self, findings_text, past_text, affected_wfs, summary):
        prompt = f"""Generate a Root Cause Analysis for a BUSINESS AUDIENCE.
Write in plain English. No jargon. Focus on:
1. What happened (business terms)
2. Business impact (delays, affected processes)
3. Why it happened (simplified)
4. What was done to fix it
5. How we prevent recurrence

Incident: {summary}
Affected: {', '.join(affected_wfs)}
Findings:
{findings_text}
Similar Past Incidents:
{past_text}

Format as a clean report with sections. Keep it under 500 words."""

        return llm_client.chat(
            prompt,
            system="You write clear, non-technical RCA reports for business stakeholders in insurance."
        )

    def _generate_technical_rca(self, findings_text, past_text, affected_wfs,
                                summary, tool_logs):
        tool_log_text = json.dumps(tool_logs[-15:], indent=2, default=str)

        prompt = f"""Generate a detailed technical Root Cause Analysis.
Include:
1. Incident Summary
2. Timeline of events (from tool calls and findings)
3. Root Cause Chain (A caused B caused C)
4. Impact Analysis (affected workflows, dependencies, data pipelines)
5. Resolution Steps Taken
6. Corrective Actions / Prevention
7. Recommendations

Incident: {summary}
Affected Workflows: {', '.join(affected_wfs)}
Investigation Findings:
{findings_text}
Tool Call Log:
{tool_log_text}
Similar Past Incidents:
{past_text}

Be specific with workflow names, execution IDs, timestamps, and error details."""

        return llm_client.chat(
            prompt,
            system="You write detailed technical RCA reports for RPA operations teams."
        )

    def _index_as_past_incident(self, state, rca_report):
        """Store this resolution for future RAG matching."""
        try:
            incident_id = f"INC-AUTO-{state.conversation_id}"
            summary = " ".join(state.affected_workflows) + " - auto-generated"
            root_cause_prompt = (
                f"Extract the root cause in one sentence from this RCA:\n{rca_report[:1000]}"
            )
            root_cause = llm_client.chat(root_cause_prompt)
            get_rag_engine().index_past_incident(
                incident_id=incident_id,
                summary=summary,
                root_cause=root_cause,
                resolution=rca_report[:500],
                workflows_involved=state.affected_workflows,
                category="auto_resolved",
            )
        except Exception as e:
            logger.warning(f"Failed to index past incident: {e}")
```

---

## 10. Phase 7 — Message Queue & Concurrent Request Handling

### 10.1 Message Gateway

Create file: `gateway/message_gateway.py`

> **Progress callback:** The gateway and orchestrator support an optional `on_progress` callback. When provided (e.g., by the agent server for SSE streaming or by the Cognibot proxy for Teams), the orchestrator invokes it at key milestones: investigation start, before each tool call, after errors, and before the final response. See `gateway/progress.py` for the `ProgressCallback` class that maps tool names to user-friendly status text (business vs technical personas).

```python
# gateway/message_gateway.py
"""
Message Gateway handles:
1. Receiving messages from chat interfaces (web, Teams)
2. Classifying new messages that arrive while agents are working
3. Managing the message queue per conversation
4. Routing to the orchestrator
"""

import logging
import threading
import time
from enum import Enum
from config.llm_client import llm_client
from state.conversation_state import ConversationState, ConversationPhase
from agents.orchestrator import Orchestrator

logger = logging.getLogger("ops_agent.gateway")


class MessageIntent(Enum):
    ADDITIVE = "additive"       # Related to current investigation
    INTERRUPT = "interrupt"     # Urgent / unrelated — switch immediately
    CANCEL = "cancel"           # Stop current work
    APPROVAL = "approval"       # Response to an approval request
    NEW_REQUEST = "new_request" # Completely new request (agents idle)


class MessageGateway:
    """Thread-safe message gateway for handling concurrent messages."""

    def __init__(self):
        self.orchestrator = Orchestrator()
        self._sessions: dict[str, ConversationState] = {}
        self._locks: dict[str, threading.Lock] = {}

    def get_or_create_session(self, conversation_id: str,
                              user_id: str = "",
                              user_role: str = "technical") -> ConversationState:
        if conversation_id not in self._sessions:
            state = ConversationState()
            state.conversation_id = conversation_id
            state.user_id = user_id
            state.user_role = user_role
            self._sessions[conversation_id] = state
            self._locks[conversation_id] = threading.Lock()
        return self._sessions[conversation_id]

    def process_message(self, conversation_id: str, user_message: str,
                        user_id: str = "", user_role: str = "technical") -> str:
        """
        Main entry point called by the chat interface.
        Thread-safe — handles concurrent messages gracefully.
        """
        # Guard: empty / whitespace-only messages
        if not user_message or not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        state = self.get_or_create_session(conversation_id, user_id, user_role)
        lock = self._locks[conversation_id]

        # ── Fast path: agents NOT currently working ──
        if not state.is_agent_working:
            with lock:
                return self.orchestrator.handle_message(user_message, state)

        # ── Agents ARE currently working — classify this new message ──
        intent = self._classify_message_intent(user_message, state)

        if intent == MessageIntent.CANCEL:
            state.interrupt_requested = True
            return "Stopping current work. What would you like me to do instead?"

        elif intent == MessageIntent.INTERRUPT:
            state.interrupt_requested = True
            # Store classification hint so orchestrator doesn't re-classify differently
            state.queue_user_message(user_message, hint="interrupt")
            return "Got your urgent message. Pausing current work to handle this."

        elif intent == MessageIntent.ADDITIVE:
            state.queue_user_message(user_message, hint="additive")
            return "Noted — I'll include this in my current investigation."

        elif intent == MessageIntent.APPROVAL:
            with lock:
                return self.orchestrator.handle_message(user_message, state)

        else:
            state.queue_user_message(user_message, hint="new_request")
            return "I'm working on something else right now. I'll get to this next."

    def _classify_message_intent(self, message: str,
                                 state: ConversationState) -> MessageIntent:
        """Classify what the user wants when agents are busy."""
        msg_lower = message.strip().lower()

        if state.phase == ConversationPhase.AWAITING_APPROVAL:
            approval_words = {"approve", "yes", "go ahead", "proceed",
                              "reject", "no"}
            if msg_lower in approval_words:
                return MessageIntent.APPROVAL

        cancel_words = {"stop", "cancel", "never mind", "abort", "quit"}
        if msg_lower in cancel_words:
            return MessageIntent.CANCEL

        urgent_words = {"urgent", "critical", "emergency", "p1", "asap",
                        "immediately", "production down"}
        if any(w in msg_lower for w in urgent_words):
            return MessageIntent.INTERRUPT

        current_context = ""
        if state.affected_workflows:
            current_context = f"Currently investigating: {', '.join(state.affected_workflows)}"

        classification = llm_client.chat(
            f"Classify this message. Current work: {current_context}\n"
            f"New message: {message}\n\n"
            f"Reply with exactly one word: ADDITIVE (related to current work) "
            f"or INTERRUPT (urgent/different topic) or CANCEL (stop)",
            system="You classify user messages. Reply with one word only."
        ).strip().upper()

        if "CANCEL" in classification:
            return MessageIntent.CANCEL
        elif "INTERRUPT" in classification:
            return MessageIntent.INTERRUPT
        return MessageIntent.ADDITIVE
```

---

## 10.2 Issue Context Tracking & Multi-Issue Management

### The Problem

When a user sends multiple messages within one session, the system must determine whether each message is about **the same incident already under investigation**, a **new issue entirely**, or a **related but distinct issue** (e.g., a downstream cascade).

```
Session starts:
  User: "Claims batch processing failed this morning"        ← Issue A
  Agent: [investigates, finds file missing, proposes restart]
  User: "approve"
  Agent: [restarts]
  User: "The reconciliation workflow also failed"             ← Is this Issue A (cascade)?
                                                                Or Issue B (new)?
  User: "Actually the premium collection is stuck too"        ← Issue C? Or still A?
  
  ... 2 hours later, same session ...
  
  User: "Claims batch failed again"                           ← Is this Issue A recurring?
                                                                Or Issue D (new occurrence)?
```

Without explicit issue tracking, the agent conflates separate problems into one investigation, loses resolution history, and cannot detect recurrence patterns.

### Solution: Issue Registry + LLM-Powered Classifier

The system maintains an **Issue Registry** per user/session that tracks distinct issues. An LLM-powered classifier (with fast heuristic fallbacks) determines whether each new message belongs to an existing issue or starts a new one.

#### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MESSAGE GATEWAY                           │
│                         │                                    │
│                         ▼                                    │
│              ┌─────────────────────┐                         │
│              │  Issue Classifier   │◄── Uses LLM + heuristics│
│              │  (new vs existing)  │                         │
│              └────────┬────────────┘                         │
│                       │                                      │
│         ┌─────────────┼──────────────┐                       │
│         ▼             ▼              ▼                        │
│    Existing       New Issue     Related but                  │
│    Issue          (create)      Distinct                     │
│    (continue)                   (link + create)              │
│         │             │              │                        │
│         ▼             ▼              ▼                        │
│              ┌─────────────────────┐                         │
│              │   Issue Registry    │                          │
│              │  (per user/session) │                          │
│              └─────────────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

Three layers of detection, fastest first:

1. **Keyword heuristics (instant, no LLM)** — "again", "same error", "approve" → continue; "different issue", "by the way" → new
2. **Error signature + workflow matching (instant)** — if the user mentions a workflow that was previously resolved with a failure keyword, it's a recurrence
3. **LLM classification (fallback for ambiguous)** — feeds active issues, resolved issues, and recent conversation to the LLM and asks for a single-word classification

### Implementation

Create file: `state/issue_tracker.py`

```python
# state/issue_tracker.py
"""
Tracks distinct issues within a conversation.
Determines whether a new message is about an existing issue or a new one.
Persists issue state to PostgreSQL so it survives process restarts.
"""

import json
import uuid
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from enum import Enum
from config.llm_client import llm_client
from config.settings import CONFIG
import psycopg2
from psycopg2.extras import Json

logger = logging.getLogger("ops_agent.issue_tracker")

RECURRENCE_ESCALATION_THRESHOLD = 3  # auto-escalate after this many recurrences


class IssueStatus(Enum):
    ACTIVE = "active"               # Currently being investigated
    AWAITING_APPROVAL = "awaiting"  # Remediation proposed, waiting for user
    RESOLVED = "resolved"           # Fix applied and confirmed
    ESCALATED = "escalated"         # Handed off to human
    STALE = "stale"                 # No activity for >30 mins


class MessageClassification(Enum):
    CONTINUE_EXISTING = "continue_existing"   # Same issue, more context
    NEW_ISSUE = "new_issue"                   # Completely different issue
    RELATED_NEW = "related_new"               # Related but distinct (e.g., cascade)
    RECURRENCE = "recurrence"                 # Same issue happening again
    FOLLOWUP = "followup"                     # Asking about outcome of resolved issue
    STATUS_CHECK = "status_check"             # General "how's everything" query


@dataclass
class Issue:
    """Represents a single distinct issue being tracked."""
    issue_id: str = field(default_factory=lambda: f"ISS-{uuid.uuid4().hex[:8]}")
    title: str = ""
    description: str = ""
    status: IssueStatus = IssueStatus.ACTIVE
    workflows_involved: list[str] = field(default_factory=list)
    error_signatures: list[str] = field(default_factory=list)
    root_cause: str = ""
    resolution: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: str = ""
    message_ids: list[str] = field(default_factory=list)
    finding_ids: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)  # Per-issue findings
    affected_workflows: list[str] = field(default_factory=list)  # Per-issue affected WFs
    related_issue_ids: list[str] = field(default_factory=list)
    recurrence_count: int = 0

    def is_stale(self, stale_minutes: int = 30) -> bool:
        last = datetime.fromisoformat(self.updated_at)
        return (datetime.now() - last) > timedelta(minutes=stale_minutes)

    def touch(self):
        self.updated_at = datetime.now().isoformat()

    def to_summary(self) -> str:
        return (
            f"[{self.issue_id}] {self.title} | Status: {self.status.value} | "
            f"Workflows: {', '.join(self.workflows_involved)} | "
            f"Error: {', '.join(self.error_signatures[:2])} | "
            f"Recurrences: {self.recurrence_count} | Created: {self.created_at}"
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        d["status"] = IssueStatus(d["status"])
        return cls(**d)


class IssueTracker:
    """
    Maintains a registry of issues per conversation.
    Uses heuristics → workflow matching → LLM (fastest first) to classify.
    Persists to PostgreSQL for durability across restarts.
    """

    # ── Heuristic signals ──
    # NOTE: "also", "additionally" etc. are intentionally NOT here — they are
    # ambiguous (could be continuation OR a related-new issue) and must go to LLM.
    APPROVAL_SIGNALS = [
        "approve", "reject", "yes", "no", "go ahead", "proceed",
    ]

    CANCEL_SIGNALS = ["cancel", "stop", "abort", "never mind"]

    CONTINUE_SIGNALS = [
        "same workflow", "same one", "related to that",
        "on the same topic", "regarding that", "about that",
        "for that same", "going back to",
    ]

    NEW_ISSUE_SIGNALS = [
        "different issue", "new problem", "something else",
        "unrelated", "separate issue", "by the way",
        "changing topic", "on a different note",
    ]

    RECURRENCE_SIGNALS = [
        "happened again", "same error again", "still failing",
        "back again", "recurring", "keeps failing", "not fixed",
        "failed again", "same issue", "it's back",
    ]

    FOLLOWUP_SIGNALS = [
        "did it work", "is it fixed", "did the restart work",
        "how did it go", "any update", "what happened after",
        "is it running now", "did it complete",
    ]

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.issues: dict[str, Issue] = {}
        self.active_issue_id: str | None = None
        self._load_from_db()

    # =========================================================================
    # Persistence (PostgreSQL)
    # =========================================================================

    def _get_conn(self):
        return psycopg2.connect(CONFIG["POSTGRES_DSN"])

    def _load_from_db(self):
        """Restore issue tracker state from PostgreSQL on init."""
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT issue_data FROM issue_registry "
                        "WHERE conversation_id = %s",
                        (self.conversation_id,)
                    )
                    rows = cur.fetchall()
                    for (issue_data,) in rows:
                        issue = Issue.from_dict(issue_data)
                        self.issues[issue.issue_id] = issue

                    cur.execute(
                        "SELECT active_issue_id FROM issue_tracker_state "
                        "WHERE conversation_id = %s",
                        (self.conversation_id,)
                    )
                    row = cur.fetchone()
                    if row:
                        self.active_issue_id = row[0]
        except Exception as e:
            logger.warning(f"Could not load issue tracker state: {e}")

    def _persist_issue(self, issue: Issue):
        """Upsert a single issue to PostgreSQL."""
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO issue_registry (conversation_id, issue_id, issue_data, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (conversation_id, issue_id)
                        DO UPDATE SET issue_data = EXCLUDED.issue_data, updated_at = NOW()
                    """, (self.conversation_id, issue.issue_id, Json(issue.to_dict())))
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not persist issue {issue.issue_id}: {e}")

    def _persist_active_id(self):
        """Save which issue is currently focused."""
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO issue_tracker_state (conversation_id, active_issue_id, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (conversation_id)
                        DO UPDATE SET active_issue_id = EXCLUDED.active_issue_id, updated_at = NOW()
                    """, (self.conversation_id, self.active_issue_id))
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not persist active issue id: {e}")

    # =========================================================================
    # Classification pipeline
    # =========================================================================

    def classify_message(self, user_message: str,
                         conversation_messages: list[dict]) -> tuple[MessageClassification, str | None]:
        """
        Classify a new user message. Three layers, fastest first:
          1. Keyword heuristics (instant)
          2. Workflow + error signature matching (instant)
          3. LLM classification (fallback for ambiguous)

        Returns: (classification, issue_id or None)
        """
        msg_lower = user_message.strip().lower()

        # Step 1: Mark stale issues (but keep them queryable — see _get_all_known_issues)
        self._mark_stale_issues()

        # Step 2: No known non-stale issues = definitely new
        active_issues = self._get_active_issues()
        if not active_issues and not self._get_resolved_issues():
            return MessageClassification.NEW_ISSUE, None

        # Step 3: Fast heuristic check (only unambiguous signals)
        heuristic_result = self._heuristic_classify(msg_lower)
        if heuristic_result:
            return heuristic_result

        # Step 4: Workflow + error signature matching (recurrence & cascade)
        recurrence_match = self._check_recurrence(user_message)
        if recurrence_match:
            return MessageClassification.RECURRENCE, recurrence_match

        cascade_match = self._check_cascade(user_message)
        if cascade_match:
            return MessageClassification.RELATED_NEW, cascade_match

        # Step 5: LLM classification (for everything else)
        all_issues = self._get_active_issues() + self._get_resolved_issues()
        if all_issues:
            return self._llm_classify(user_message, all_issues, conversation_messages)

        return MessageClassification.NEW_ISSUE, None

    def _heuristic_classify(self, msg_lower: str) -> tuple[MessageClassification, str | None] | None:
        """
        Fast keyword-based classification. Returns None if uncertain.
        Only matches unambiguous signals to avoid false positives.
        """
        # Approval/rejection responses — always continue current issue
        if msg_lower in ("approve", "reject", "yes", "no", "go ahead", "proceed"):
            return MessageClassification.CONTINUE_EXISTING, self.active_issue_id

        # Cancel/stop — pass through (gateway handles these, not issue tracker)
        if msg_lower in ("cancel", "stop", "abort", "never mind"):
            return MessageClassification.CONTINUE_EXISTING, self.active_issue_id

        # Explicit new issue signals (high confidence)
        for signal in self.NEW_ISSUE_SIGNALS:
            if signal in msg_lower:
                return MessageClassification.NEW_ISSUE, None

        # Explicit recurrence signals — but validate with workflow matching
        for signal in self.RECURRENCE_SIGNALS:
            if signal in msg_lower:
                match = self._check_recurrence_by_workflow(msg_lower)
                if match:
                    return MessageClassification.RECURRENCE, match
                # "again" without workflow match → could be new or recurrence, ask LLM
                return None

        # Explicit follow-up signals — find the best matching issue
        for signal in self.FOLLOWUP_SIGNALS:
            if signal in msg_lower:
                best_issue = self._find_followup_target(msg_lower)
                if best_issue:
                    return MessageClassification.FOLLOWUP, best_issue
                return MessageClassification.FOLLOWUP, self.active_issue_id

        # Explicit continuation signals (narrow, high-confidence only)
        for signal in self.CONTINUE_SIGNALS:
            if msg_lower.startswith(signal) or f" {signal} " in f" {msg_lower} ":
                return MessageClassification.CONTINUE_EXISTING, self.active_issue_id

        return None  # Ambiguous — needs LLM (e.g., "also", "additionally")

    def _check_recurrence_by_workflow(self, msg_lower: str) -> str | None:
        """Match recurrence signals against resolved issues by workflow name."""
        resolved = self._get_resolved_issues()
        for issue in resolved:
            for wf in issue.workflows_involved:
                wf_parts = wf.lower().replace("_", " ").split()
                if any(part in msg_lower for part in wf_parts if len(part) > 3):
                    return issue.issue_id
        return None

    def _check_recurrence(self, message: str) -> str | None:
        """Check if message describes a problem we've already resolved."""
        resolved = self._get_resolved_issues()
        if not resolved:
            return None

        msg_lower = message.lower()
        failure_words = ["fail", "error", "broken", "down", "stuck",
                         "issue", "problem", "not working"]

        for issue in resolved:
            workflow_match = False
            for wf in issue.workflows_involved:
                wf_parts = wf.lower().replace("_", " ").split()
                if any(part in msg_lower for part in wf_parts if len(part) > 3):
                    workflow_match = True
                    break

            if not workflow_match:
                for sig in issue.error_signatures:
                    if sig.lower() in msg_lower:
                        workflow_match = True
                        break

            if workflow_match and any(fw in msg_lower for fw in failure_words):
                return issue.issue_id
        return None

    def _check_cascade(self, message: str) -> str | None:
        """
        Check if message mentions a DIFFERENT workflow than any active issue,
        combined with a failure word — suggests a cascade / related-new issue.
        """
        msg_lower = message.lower()
        failure_words = ["fail", "error", "broken", "down", "stuck", "issue"]
        if not any(fw in msg_lower for fw in failure_words):
            return None

        active = self._get_active_issues()
        for issue in active:
            active_wfs = {wf.lower() for wf in issue.workflows_involved}
            # If the message does NOT mention any active workflow but DOES mention
            # a failure, it could be a cascade from the active issue
            mentions_active = any(
                any(part in msg_lower for part in wf.replace("_", " ").split() if len(part) > 3)
                for wf in active_wfs
            )
            if not mentions_active:
                return issue.issue_id
        return None

    def _find_followup_target(self, msg_lower: str) -> str | None:
        """Find the resolved issue the user is most likely asking about."""
        resolved = self._get_resolved_issues()
        for issue in resolved:
            for wf in issue.workflows_involved:
                wf_parts = wf.lower().replace("_", " ").split()
                if any(part in msg_lower for part in wf_parts if len(part) > 3):
                    return issue.issue_id
        # No workflow match — check stale issues too (user may be resuming)
        stale = [i for i in self.issues.values() if i.status == IssueStatus.STALE]
        for issue in stale:
            for wf in issue.workflows_involved:
                wf_parts = wf.lower().replace("_", " ").split()
                if any(part in msg_lower for part in wf_parts if len(part) > 3):
                    return issue.issue_id
        return None

    def _llm_classify(self, user_message: str, all_issues: list[Issue],
                      conversation_messages: list[dict]) -> tuple[MessageClassification, str | None]:
        """Use Vertex AI for ambiguous classification."""

        active_summary = "\n".join(
            f"  - {i.to_summary()}" for i in all_issues
            if i.status in (IssueStatus.ACTIVE, IssueStatus.AWAITING_APPROVAL)
        )
        resolved_summary = "\n".join(
            f"  - {i.to_summary()}" for i in all_issues
            if i.status == IssueStatus.RESOLVED
        )
        stale_summary = "\n".join(
            f"  - {i.to_summary()}" for i in all_issues
            if i.status == IssueStatus.STALE
        )

        recent_messages = "\n".join(
            f"  {m['role']}: {m['content'][:150]}"
            for m in conversation_messages[-8:]
        )

        prompt = f"""Classify this user message in a support conversation.

Active issues:
{active_summary or '  (none)'}

Recently resolved issues:
{resolved_summary or '  (none)'}

Stale issues (no activity >30 min):
{stale_summary or '  (none)'}

Recent conversation:
{recent_messages}

New message from user: "{user_message}"

Classify as exactly ONE of:
- CONTINUE_EXISTING: More info or question about an active issue
- NEW_ISSUE: Completely different problem
- RELATED_NEW: Related but distinct issue (e.g., downstream cascade)
- RECURRENCE: A previously resolved issue happening again
- FOLLOWUP: Asking about outcome of a resolved/stale issue
- STATUS_CHECK: General health or status query

Reply in this exact format: CLASSIFICATION|issue_id
If no issue_id applies, use: CLASSIFICATION|none
Examples: CONTINUE_EXISTING|ISS-abc12345  or  NEW_ISSUE|none"""

        response = llm_client.chat(
            prompt,
            system="You classify support messages. Reply in exactly the format requested."
        ).strip()

        parts = response.split("|")
        classification_str = parts[0].strip().upper()
        issue_id = parts[1].strip() if len(parts) > 1 else None
        if issue_id == "none":
            issue_id = None

        classification_map = {
            "CONTINUE_EXISTING": MessageClassification.CONTINUE_EXISTING,
            "NEW_ISSUE": MessageClassification.NEW_ISSUE,
            "RELATED_NEW": MessageClassification.RELATED_NEW,
            "RECURRENCE": MessageClassification.RECURRENCE,
            "FOLLOWUP": MessageClassification.FOLLOWUP,
            "STATUS_CHECK": MessageClassification.STATUS_CHECK,
        }

        classification = classification_map.get(
            classification_str, MessageClassification.NEW_ISSUE
        )

        if issue_id and issue_id not in self.issues:
            issue_id = self.active_issue_id

        logger.info(f"LLM classified message as {classification.value}, issue={issue_id}")
        return classification, issue_id

    # =========================================================================
    # Issue lifecycle management
    # =========================================================================

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
            logger.info(f"Reopened issue {issue_id} (recurrence #{issue.recurrence_count})")
            return issue
        return self.create_issue("Reopened issue", "Recurrence of previous issue")

    def should_escalate_recurrence(self, issue_id: str) -> bool:
        """Returns True if the issue has recurred too many times."""
        if issue_id in self.issues:
            return self.issues[issue_id].recurrence_count >= RECURRENCE_ESCALATION_THRESHOLD
        return False

    def link_issues(self, issue_id_1: str, issue_id_2: str):
        if issue_id_1 in self.issues and issue_id_2 in self.issues:
            if issue_id_2 not in self.issues[issue_id_1].related_issue_ids:
                self.issues[issue_id_1].related_issue_ids.append(issue_id_2)
            if issue_id_1 not in self.issues[issue_id_2].related_issue_ids:
                self.issues[issue_id_2].related_issue_ids.append(issue_id_1)
            self._persist_issue(self.issues[issue_id_1])
            self._persist_issue(self.issues[issue_id_2])

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
        """Store a finding scoped to this specific issue."""
        if issue_id in self.issues:
            self.issues[issue_id].findings.append(finding)
            self._persist_issue(self.issues[issue_id])

    def get_issue_findings(self, issue_id: str) -> list[dict]:
        """Get findings for a specific issue (not the whole conversation)."""
        if issue_id in self.issues:
            return self.issues[issue_id].findings
        return []

    def resume_stale_issue(self, issue_id: str) -> Issue | None:
        """Bring a stale issue back to active status."""
        if issue_id in self.issues and self.issues[issue_id].status == IssueStatus.STALE:
            issue = self.issues[issue_id]
            issue.status = IssueStatus.ACTIVE
            issue.touch()
            self.active_issue_id = issue_id
            self._persist_issue(issue)
            self._persist_active_id()
            logger.info(f"Resumed stale issue: {issue_id}")
            return issue
        return None

    # =========================================================================
    # Query helpers
    # =========================================================================

    def _get_active_issues(self) -> list[Issue]:
        return [i for i in self.issues.values()
                if i.status in (IssueStatus.ACTIVE, IssueStatus.AWAITING_APPROVAL)]

    def _get_resolved_issues(self) -> list[Issue]:
        return [i for i in self.issues.values()
                if i.status == IssueStatus.RESOLVED]

    def _mark_stale_issues(self, stale_minutes: int = 30):
        for issue in self._get_active_issues():
            if issue.is_stale(stale_minutes):
                issue.status = IssueStatus.STALE
                self._persist_issue(issue)
                logger.info(f"Issue {issue.issue_id} marked stale")

    def get_all_issues_summary(self) -> str:
        if not self.issues:
            return "No issues tracked in this session."
        return "\n".join(issue.to_summary() for issue in self.issues.values())
```

**PostgreSQL schema for issue tracker persistence** — run once during setup:

```sql
CREATE TABLE IF NOT EXISTS issue_registry (
    conversation_id VARCHAR(256) NOT NULL,
    issue_id        VARCHAR(64)  NOT NULL,
    issue_data      JSONB        NOT NULL,
    updated_at      TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (conversation_id, issue_id)
);

CREATE TABLE IF NOT EXISTS issue_tracker_state (
    conversation_id  VARCHAR(256) PRIMARY KEY,
    active_issue_id  VARCHAR(64),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_issue_registry_conv ON issue_registry(conversation_id);
```

### 10.3 Orchestrator Integration — Updated `handle_message`

The orchestrator uses the `IssueTracker` to route messages, maintain per-issue findings, and handle resolution lifecycle. Key changes from the baseline orchestrator:

- `_get_issue_tracker` creates a **persistent** tracker (PostgreSQL-backed) per conversation
- `_process_message` now takes a `tracker` parameter and stores findings per-issue
- Approval shortcut still touches the issue tracker to prevent stale timeouts
- Recurrence above threshold auto-escalates
- FOLLOWUP searches resolved issues by workflow match, not just the active issue

```python
# In agents/orchestrator.py — updated orchestrator

from state.issue_tracker import (
    IssueTracker, MessageClassification, IssueStatus,
    RECURRENCE_ESCALATION_THRESHOLD,
)

class Orchestrator:

    def __init__(self):
        self.approval_gate = ApprovalGate()
        self.issue_trackers: dict[str, IssueTracker] = {}

    def _get_issue_tracker(self, conversation_id: str) -> IssueTracker:
        if conversation_id not in self.issue_trackers:
            self.issue_trackers[conversation_id] = IssueTracker(conversation_id)
        return self.issue_trackers[conversation_id]

    def handle_message(self, user_message: str, state: ConversationState) -> str:
        if not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        state.add_message("user", user_message)
        tracker = self._get_issue_tracker(state.conversation_id)

        # ── Step 1: Approval flow — still touch the tracker to prevent stale ──
        if state.phase == ConversationPhase.AWAITING_APPROVAL:
            active = tracker.get_active_issue()
            if active:
                active.touch()
                tracker._persist_issue(active)
            return self._handle_approval_response(user_message, state, tracker)

        # ── Step 2: Classify — new issue or existing? ──
        classification, issue_id = tracker.classify_message(
            user_message, state.messages
        )

        # ── Step 3: Route based on classification ──

        if classification == MessageClassification.NEW_ISSUE:
            issue = tracker.create_issue(
                title=user_message[:80],
                description=user_message,
            )
            state.phase = ConversationPhase.IDLE
            response = self._process_message(user_message, state, tracker)

        elif classification == MessageClassification.CONTINUE_EXISTING:
            tracker.switch_to_issue(issue_id or tracker.active_issue_id)
            response = self._process_message(user_message, state, tracker)

        elif classification == MessageClassification.RELATED_NEW:
            parent_id = issue_id or tracker.active_issue_id
            issue = tracker.create_issue(
                title=user_message[:80],
                description=user_message,
            )
            if parent_id:
                tracker.link_issues(parent_id, issue.issue_id)
            response = (
                "This looks related to the issue I'm already investigating but "
                "appears to be a separate problem. I'll track it as a linked issue.\n\n"
                + self._process_message(user_message, state, tracker)
            )

        elif classification == MessageClassification.RECURRENCE:
            old_issue = tracker.reopen_issue(issue_id)

            # Auto-escalate if recurrence exceeds threshold
            if tracker.should_escalate_recurrence(old_issue.issue_id):
                old_issue.status = IssueStatus.ESCALATED
                tracker._persist_issue(old_issue)
                return (
                    f"This issue has now recurred {old_issue.recurrence_count} times. "
                    f"Previous resolution ({old_issue.resolution[:150]}) is not holding. "
                    f"I'm escalating to the operations team for a permanent fix."
                )

            state.phase = ConversationPhase.IDLE
            recurrence_note = (
                f"This appears to be a recurrence of a previous issue "
                f"(occurrence #{old_issue.recurrence_count}). "
            )
            if old_issue.resolution:
                recurrence_note += (
                    f"Last time the resolution was: {old_issue.resolution[:200]}. "
                    f"Let me check if the same root cause applies.\n\n"
                )
            response = recurrence_note + self._process_message(
                user_message, state, tracker
            )

        elif classification == MessageClassification.FOLLOWUP:
            # Search resolved/stale issues by workflow match, not just active
            target_id = issue_id
            target_issue = tracker.issues.get(target_id) if target_id else None

            if not target_issue:
                target_issue = tracker.get_active_issue()

            if target_issue and target_issue.status == IssueStatus.RESOLVED:
                response = (
                    f"Regarding [{target_issue.issue_id}] {target_issue.title}: "
                    f"it was resolved. {target_issue.resolution}\n\n"
                    f"Would you like me to verify the current status?"
                )
            elif target_issue and target_issue.status == IssueStatus.STALE:
                tracker.resume_stale_issue(target_issue.issue_id)
                response = (
                    f"Resuming investigation of [{target_issue.issue_id}] "
                    f"{target_issue.title}.\n\n"
                    + self._process_message(user_message, state, tracker)
                )
            else:
                response = self._process_message(user_message, state, tracker)

        elif classification == MessageClassification.STATUS_CHECK:
            summary = tracker.get_all_issues_summary()
            response = f"Here's the current session status:\n\n{summary}\n\n"
            response += self._process_message(user_message, state, tracker)

        else:
            response = self._process_message(user_message, state, tracker)

        state.save()
        return response

    def _process_message(self, user_message: str, state: ConversationState,
                         tracker: IssueTracker) -> str:
        """
        Core investigation/remediation loop. Uses the existing agent loop
        (RAG-filtered tool selection, LLM reasoning, tool execution) but scopes
        findings to the current issue via the tracker. When the catalog has >30
        tools, only always_available + RAG-matched tools (up to MAX_RAG_TOOLS) +
        the discover_tools meta-tool are sent to the LLM; the LLM can call
        discover_tools with a query or category to find more tools mid-conversation.
        """
        system_prompt = self._build_system_prompt(state, tracker)
        # ... existing agent loop (tool calls, LLM reasoning) ...
        # After each tool call that returns findings:
        active_issue = tracker.get_active_issue()
        if active_issue:
            active_issue.touch()
            # Example: after check_workflow_status returns a workflow name
            # tracker.add_workflow_to_issue(active_issue.issue_id, workflow_name)
            # After get_execution_logs returns an error:
            # tracker.add_error_signature(active_issue.issue_id, error_signature)
            # After investigation produces a finding:
            # tracker.add_finding_to_issue(active_issue.issue_id, finding_dict)
        # ... rest of existing logic ...
        # IMPORTANT: After successful remediation, resolve the issue:
        # tracker.resolve_issue(active_issue.issue_id, "Restarted from checkpoint")
        return response  # placeholder — actual implementation in Phase 4 code

    def _handle_approval_response(self, user_message: str,
                                  state: ConversationState,
                                  tracker: IssueTracker) -> str:
        """Handle approve/reject. On successful remediation, resolve the issue."""
        # ... existing approval logic ...
        # After successful execution of approved remediation:
        active_issue = tracker.get_active_issue()
        if active_issue:
            tracker.resolve_issue(
                active_issue.issue_id,
                f"Approved and executed: {state.pending_action_summary}"
            )
        return response  # placeholder
```

### 10.4 System Prompt Enhancement — Issue Context Injection

Add the following block inside `_build_system_prompt()`. This gives the LLM full awareness of tracked issues so it can reason about whether findings relate to the current issue or suggest creating a new one.

```python
def _build_system_prompt(self, state: ConversationState,
                         tracker: IssueTracker) -> str:
    # ... existing prompt sections (role, tools, persona, safety) ...

    issue_context = ""
    if tracker and tracker.issues:
        active = tracker.get_active_issue()
        issue_context = f"""
## Active Issues in This Session
{tracker.get_all_issues_summary()}

Currently focused issue: {active.issue_id if active else 'None'}
Focused issue findings so far: {json.dumps(active.findings[-5:], default=str) if active else '[]'}

IMPORTANT: Scope your investigation to the currently focused issue.
- When you discover a workflow name, call tracker.add_workflow_to_issue()
- When you find an error signature, call tracker.add_error_signature()
- When you produce a finding, call tracker.add_finding_to_issue()
- After successful remediation, call tracker.resolve_issue() with a summary
"""

    # Inject into the full prompt
    return f"{base_prompt}\n{tool_context}\n{persona_context}\n{issue_context}"
```

### 10.5 Complete Flow Example

```
Timeline:

09:00 User: "Claims batch processing failed this morning"
  → IssueTracker: No active/resolved issues → NEW_ISSUE
  → Creates ISS-abc001, title="Claims batch processing failed"
  → Agent investigates: finds missing input file
  → tracker.add_workflow_to_issue(ISS-abc001, "claims_batch_processor")
  → tracker.add_error_signature(ISS-abc001, "FileNotFoundException")
  → tracker.add_finding_to_issue(ISS-abc001, {summary: "Input file missing"})

09:05 User: "approve" (restart)
  → IssueTracker: Heuristic → approval signal → CONTINUE_EXISTING (ISS-abc001)
  → Approval shortcut also calls issue.touch() to prevent stale timeout
  → Restarts workflow → success
  → tracker.resolve_issue(ISS-abc001, "Restarted from checkpoint after file arrived")

09:10 User: "The reconciliation workflow also failed"
  → IssueTracker: "also" is NOT in heuristic signals (intentionally ambiguous)
  → Workflow matching: "reconciliation" ≠ "claims_batch_processor" → no match
  → LLM fallback: sees resolved claims issue, different workflow mentioned
  → Classification: RELATED_NEW (linked to ISS-abc001)
  → Creates ISS-abc002, links to ISS-abc001
  → Agent investigates: finds reconciliation failed because
    claims output (its input) was late — cascade!

09:15 User: "Is the claims workflow fixed?"
  → IssueTracker: "is it fixed" → FOLLOWUP_SIGNALS match
  → _find_followup_target: "claims" matches ISS-abc001's workflows
  → Classification: FOLLOWUP (ISS-abc001)  ← NOT active issue (abc002 is active)
  → Returns: "Regarding [ISS-abc001] Claims batch processing failed:
    it was resolved. Restarted from checkpoint after file arrived."

11:30 User: "Claims batch failed again"
  → IssueTracker: "failed again" → RECURRENCE_SIGNALS match
  → _check_recurrence_by_workflow: "claims" matches ISS-abc001
  → Classification: RECURRENCE (ISS-abc001)
  → Reopens ISS-abc001, recurrence_count=1 (< threshold of 3)
  → Agent: "This is a recurrence (occurrence #1). Last time the fix was
    restarting after the file arrived. Let me check if the same cause applies..."
  → Persisted to PostgreSQL — survives process restart

12:00 [Process restarts — AI Studio redeploy]
  → IssueTracker.__init__: loads from PostgreSQL → ISS-abc001 (active),
    ISS-abc002 (resolved), all with workflows/signatures intact

14:00 User: "The policy issuance workflow is throwing timeout errors"
  → IssueTracker: "policy issuance" not in any tracked issue workflows
  → No heuristic match, no workflow match
  → LLM fallback: sees active claims issue, unrelated workflow
  → Classification: NEW_ISSUE
  → Creates ISS-abc003, completely fresh investigation
  → (Large-catalog mode: if catalog >30 tools, LLM may call discover_tools
    with query "timeout" or "policy" to find relevant tools mid-conversation)

15:00 User: "Claims batch failed again" (3rd recurrence)
  → Classification: RECURRENCE → recurrence_count = 3
  → should_escalate_recurrence() returns True (≥ threshold)
  → Auto-escalates: "This issue has now recurred 3 times. The previous fix
    is not holding. I'm escalating to the operations team for a permanent fix."
```

---

## 11. Phase 8 — Persona-Based Response Filtering

### 11.1 How Persona Filtering Works

The system detects user role at login (from AE user profile or session) and adjusts ALL responses:

| Aspect | Business User | Technical Staff |
|---|---|---|
| **Error messages** | "The claims file processing encountered an issue" | "claims_batch_v2 execution EXC-4521 failed with FileNotFoundException at step 3" |
| **Status updates** | "Processing is delayed by about 2 hours" | "Queue depth: 145, processing rate: 12/min, ETA: 2.1 hours" |
| **RCA reports** | Impact + what happened + prevention | Full timeline + root cause chain + technical details |
| **Remediation** | "I've fixed the issue and processing should resume shortly" | "Restarted execution EXC-4521 from checkpoint step-2, new exec ID: EXC-4522" |
| **Approval requests** | "I'd like to restart the claims processing. Approve?" | "Tool: restart_execution, Params: {wf: claims_batch_v2, exec: EXC-4521, checkpoint: true}" |

Persona detection is set at session creation. The `_filter_for_persona` method in the orchestrator handles response transformation for business users (see Phase 4 code).

---

## 12. Phase 9 — Chat Interface Integration

### 12.1 AE AI Studio Web Chat Entry Point

Create file: `main.py` — this is what AE AI Studio will execute.

```python
# main.py
"""
Main entry point for AutomationEdge AI Studio.
This script is deployed as a Python project in AI Studio
and exposed via its web chat interface.
"""

import os
import sys
import json
import logging

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import CONFIG
from config.logging_setup import setup_logging
from gateway.message_gateway import MessageGateway
from rag.engine import get_rag_engine
from tools.registry import tool_registry

# Initialize
app_logger, audit_logger = setup_logging()
gateway = MessageGateway()

# Index tools into RAG on startup
tool_docs = tool_registry.get_all_rag_documents()
get_rag_engine().index_tools(tool_docs)
app_logger.info("Ops Agent initialized successfully")


def handle_chat_message(message: str, session_id: str = "default",
                        user_id: str = "", user_role: str = "technical") -> str:
    """
    Called by AE AI Studio for each incoming chat message.

    Parameters:
        message: The user's chat message
        session_id: Unique conversation/session identifier
        user_id: Authenticated user ID from AE
        user_role: "business" or "technical" (from AE user profile)

    Returns:
        Response string to display in chat
    """
    try:
        app_logger.info(f"Message from {user_id} [{user_role}]: {message[:100]}...")
        response = gateway.process_message(
            conversation_id=session_id,
            user_message=message,
            user_id=user_id,
            user_role=user_role,
        )
        app_logger.info(f"Response: {response[:100]}...")
        return response

    except Exception as e:
        app_logger.error(f"Unhandled error: {e}", exc_info=True)
        return (
            "I encountered an unexpected error. The operations team has been notified. "
            "Please try again or contact support directly."
        )


# ── AE AI Studio Integration ──
# The exact integration depends on your AE AI Studio version.
# Common patterns:

# Pattern A: AI Studio calls a function directly
# Configure AI Studio to call handle_chat_message()

# Pattern B: AI Studio expects a Flask/FastAPI endpoint
# Uncomment below if AI Studio routes via HTTP:

# from flask import Flask, request, jsonify
# app = Flask(__name__)
#
# @app.route("/chat", methods=["POST"])
# def chat_endpoint():
#     data = request.json
#     response = handle_chat_message(
#         message=data.get("message", ""),
#         session_id=data.get("session_id", "default"),
#         user_id=data.get("user_id", ""),
#         user_role=data.get("user_role", "technical"),
#     )
#     return jsonify({"response": response})
#
# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=5050)

# Pattern C: Standalone CLI for testing
if __name__ == "__main__":
    print("=" * 60)
    print("  AutomationEdge Ops Agent — Interactive CLI")
    print("=" * 60)
    print("Type 'quit' to exit. Type 'role:business' to switch persona.\n")

    session_id = "cli-test-001"
    user_role = "technical"

    while True:
        try:
            user_input = input(f"[{user_role}] You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.lower() == "quit":
            break
        if user_input.startswith("role:"):
            user_role = user_input.split(":")[1].strip()
            print(f"  Switched to role: {user_role}")
            continue
        if not user_input:
            continue

        response = handle_chat_message(
            message=user_input,
            session_id=session_id,
            user_id="cli_tester",
            user_role=user_role,
        )
        print(f"\n  Agent: {response}\n")
```

### 12.2 Deploying in AE AI Studio — Click by Click

1. **Open AE AI Studio** → Navigate to `https://<ae-server>:<port>/aistudio`
2. **Create New Project** → Click "New" → Select "Python Script" project type
3. **Name it** → `ops_agent_v1`
4. **Upload files** → Upload the entire `ops_agent/` folder structure (or use git clone if AI Studio supports it)
5. **Set entry point** → Point to `main.py`
6. **Configure environment variables** (in AI Studio settings):
   - `AE_BASE_URL` = `https://localhost:8443` (or your AE REST API URL)
   - `AE_API_KEY` = your service account API key
   - `GOOGLE_CLOUD_PROJECT` = your GCP project ID
   - `GOOGLE_CLOUD_LOCATION` = `us-central1` (or your Vertex AI region)
   - `VERTEX_AI_MODEL` = `gemini-2.0-flash` (or `gemini-1.5-pro` for higher quality)
   - `POSTGRES_DSN` = `postgresql://user:pass@<db-host>:5432/ops_agent`
   - `GOOGLE_APPLICATION_CREDENTIALS` = path to service account JSON key
7. **Set Python dependencies** → paste content of `requirements.txt` (or install manually via terminal)
8. **Enable Web Chat** → In AI Studio project settings, enable "Chat Interface"
9. **Test** → Open the chat window and type: `What workflows failed today?`
10. **Verify** → Check logs at `/opt/automationedge/aistudio/scripts/ops_agent/logs/`

### 12.2a SSE Streaming Endpoint (`/chat/stream`)

The agent server exposes `POST /chat/stream` for real-time progress updates. It sends Server-Sent Events (SSE): `event: progress` with status text during investigation (e.g., "Looking into this...", "Checking workflow status..."), then `event: done` with the final response. The webchat uses this endpoint so users see progress messages as italic text that updates in-place. The original `POST /chat` endpoint remains for backwards compatibility.

### 12.3 MS Teams Integration (Deferred Phase)

When ready to integrate with MS Teams:

1. Register a Bot in Azure Bot Service (or use AE's Teams connector if available)
2. Create a webhook endpoint in `main.py` that translates Teams Bot Framework messages into `handle_chat_message()` calls
3. Map Teams user IDs to AE user profiles for role detection
4. Handle Teams Adaptive Cards for approval requests (approve/reject buttons)

---

## 13. Phase 10 — Testing, Hardening & Go-Live

### 13.1 Test Scenarios

Create file: `tests/test_scenarios.py`

```python
# tests/test_scenarios.py
"""
Test scenarios covering common support workflows.
Run: python -m tests.test_scenarios
"""

TEST_SCENARIOS = [
    # ── Scenario 1: Simple status check ──
    {
        "name": "Simple Status Check",
        "user_role": "business",
        "messages": [
            "What is the status of the claims processing workflow?",
        ],
        "expected_tools": ["check_workflow_status"],
        "expected_behavior": "Returns plain English status, no technical jargon",
    },

    # ── Scenario 2: Failed workflow investigation ──
    {
        "name": "Failed Workflow Investigation",
        "user_role": "technical",
        "messages": [
            "The claims batch processing failed this morning. Can you investigate?",
        ],
        "expected_tools": [
            "check_workflow_status",
            "get_execution_logs",
            "check_input_file",
        ],
        "expected_behavior": "Checks status, pulls logs, checks input file, provides diagnosis",
    },

    # ── Scenario 3: Cascade failure detection ──
    {
        "name": "Cascade Failure Detection",
        "user_role": "technical",
        "messages": [
            "Multiple workflows seem to be failing. What's going on?",
        ],
        "expected_tools": [
            "list_recent_failures",
            "get_workflow_dependencies",
            "check_workflow_status",
        ],
        "expected_behavior": "Lists failures, traces to root upstream workflow, identifies cascade",
    },

    # ── Scenario 4: Remediation with approval ──
    {
        "name": "Remediation with Approval",
        "user_role": "technical",
        "messages": [
            "The policy sync workflow failed because the input file was late. The file is here now. Can you restart it?",
            "approve",
        ],
        "expected_tools": [
            "check_workflow_status",
            "check_input_file",
            "restart_execution",  # After approval
        ],
        "expected_behavior": "Verifies file arrived, requests approval, restarts after approval",
    },

    # ── Scenario 5: Business user RCA request ──
    {
        "name": "Business User RCA Request",
        "user_role": "business",
        "messages": [
            "Why were the policy documents delayed yesterday? Can you give me an RCA?",
        ],
        "expected_tools": [
            "check_workflow_status",
            "get_execution_history",
            "get_execution_logs",
        ],
        "expected_behavior": "Investigates, then generates business-friendly RCA with no jargon",
    },

    # ── Scenario 6: Technical RCA request ──
    {
        "name": "Technical RCA Request",
        "user_role": "technical",
        "messages": [
            "Generate an RCA for the reconciliation failure from last night",
        ],
        "expected_tools": [
            "check_workflow_status",
            "get_execution_logs",
            "get_workflow_dependencies",
            "get_execution_history",
        ],
        "expected_behavior": "Full technical RCA with timeline, root cause chain, tool logs",
    },

    # ── Scenario 7: Concurrent message — additive ──
    {
        "name": "Concurrent Message - Additive",
        "user_role": "technical",
        "messages": [
            "Investigate why claims processing failed",
            # (sent while agents are working):
            "Also check the premium collection workflow",
        ],
        "expected_behavior": "Queues second message, investigates both, merged response",
    },

    # ── Scenario 8: Concurrent message — cancel ──
    {
        "name": "Concurrent Message - Cancel",
        "user_role": "technical",
        "messages": [
            "Check the status of all batch workflows",
            "stop",
        ],
        "expected_behavior": "Stops investigation immediately",
    },

    # ── Scenario 9: Protected workflow ──
    {
        "name": "Protected Workflow Escalation",
        "user_role": "technical",
        "messages": [
            "Restart the regulatory IRDAI reporting workflow",
            "approve",
        ],
        "expected_behavior": "Refuses to restart protected workflow, suggests escalation",
    },

    # ── Scenario 10: Unknown workflow ──
    {
        "name": "Unknown Workflow Handling",
        "user_role": "business",
        "messages": [
            "What happened to the XYZ123 workflow?",
        ],
        "expected_behavior": "Attempts lookup, if not found says so clearly, suggests alternatives",
    },

    # ── Scenario 11: Multi-issue — new issue detection ──
    {
        "name": "Multi-Issue — New Issue Detection",
        "user_role": "technical",
        "messages": [
            "Claims batch processing failed this morning",
            "approve",  # approves restart
            "The policy issuance workflow is throwing timeout errors",
        ],
        "expected_behavior": (
            "First message creates Issue A (claims). Approval continues Issue A. "
            "Third message is classified as NEW_ISSUE — creates Issue B with fresh investigation context"
        ),
    },

    # ── Scenario 12: Multi-issue — related cascade ──
    {
        "name": "Multi-Issue — Related Cascade Detection",
        "user_role": "technical",
        "messages": [
            "Claims batch processing failed this morning",
            "approve",  # approves restart
            "The reconciliation workflow also failed",
        ],
        "expected_behavior": (
            "Third message classified as RELATED_NEW — creates linked Issue B. "
            "Agent investigates reconciliation separately but notes the link to claims failure"
        ),
    },

    # ── Scenario 13: Multi-issue — recurrence ──
    {
        "name": "Multi-Issue — Recurrence Detection",
        "user_role": "technical",
        "messages": [
            "Claims batch processing failed this morning",
            "approve",  # restart resolves it
            # ... 2 hours later ...
            "Claims batch failed again",
        ],
        "expected_behavior": (
            "Third message classified as RECURRENCE — reopens Issue A. "
            "Agent references previous resolution and checks if same root cause applies. "
            "recurrence_count = 1"
        ),
    },

    # ── Scenario 13b: Multi-issue — recurrence escalation ──
    {
        "name": "Multi-Issue — Recurrence Auto-Escalation",
        "user_role": "technical",
        "messages": [
            "Claims batch processing failed this morning",
            "approve",  # restart resolves it (recurrence 1)
            "Claims batch failed again",
            "approve",  # restart resolves it (recurrence 2)
            "Claims batch failed again",
            "approve",  # restart resolves it (recurrence 3)
            "Claims batch failed again",  # recurrence 4 → auto-escalate
        ],
        "expected_behavior": (
            "After 3+ recurrences, should_escalate_recurrence returns True. "
            "Agent auto-escalates to human operations team instead of re-investigating"
        ),
    },

    # ── Scenario 14: Multi-issue — follow-up on resolved ──
    {
        "name": "Multi-Issue — Follow-up Query",
        "user_role": "technical",
        "messages": [
            "Claims batch processing failed this morning",
            "approve",
            "Is the claims workflow fixed now?",
        ],
        "expected_behavior": (
            "Third message classified as FOLLOWUP — returns resolution summary "
            "and offers to verify current status"
        ),
    },

    # ── Scenario 15: Multi-issue — interleaved issues ──
    {
        "name": "Multi-Issue — Interleaved Context Switching",
        "user_role": "technical",
        "messages": [
            "Claims batch processing failed this morning",
            "Also the premium collection is stuck",
            "Going back to claims — did the restart work?",
        ],
        "expected_behavior": (
            "First message: NEW_ISSUE (claims). "
            "Second: 'Also' is ambiguous — LLM fallback classifies as RELATED_NEW or NEW_ISSUE. "
            "Third: FOLLOWUP switching context back to claims issue (workflow match)"
        ),
    },

    # ── Scenario 16: Empty and whitespace messages ──
    {
        "name": "Edge Case — Empty Messages",
        "user_role": "technical",
        "messages": ["", "   ", "\n"],
        "expected_behavior": (
            "All messages return a polite 'message was empty' prompt. "
            "No issues created, no state changes"
        ),
    },

    # ── Scenario 17: Stale issue resumption ──
    {
        "name": "Multi-Issue — Stale Issue Resume",
        "user_role": "technical",
        "messages": [
            "Claims batch processing failed this morning",
            # ... 45 minutes of silence (issue goes stale) ...
            "What about the claims workflow?",
        ],
        "expected_behavior": (
            "Second message triggers FOLLOWUP. _find_followup_target finds stale ISS-abc001. "
            "resume_stale_issue() brings it back to ACTIVE. Investigation resumes."
        ),
    },

    # ── Scenario 18: Persistence across restart ──
    {
        "name": "Edge Case — Process Restart Persistence",
        "user_role": "technical",
        "messages": [
            "Claims batch processing failed this morning",
            # [simulated restart]
            "Any update on the claims issue?",
        ],
        "expected_behavior": (
            "After restart, IssueTracker loads from PostgreSQL. "
            "Second message finds ISS-abc001 and returns its state/findings"
        ),
    },
]


if __name__ == "__main__":
    print(f"Test Scenarios Loaded: {len(TEST_SCENARIOS)}")
    for i, s in enumerate(TEST_SCENARIOS, 1):
        print(f"\n{i}. {s['name']} [{s['user_role']}]")
        print(f"   Messages: {s['messages']}")
        print(f"   Expected: {s['expected_behavior']}")
```

### 13.2 Mock AE API for Testing

Create file: `tests/mock_ae_api.py`

```python
# tests/mock_ae_api.py
"""
Mock AutomationEdge API server for testing without hitting production.
Run: python -m tests.mock_ae_api
Listens on http://localhost:9999
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from datetime import datetime, timedelta
import random


MOCK_WORKFLOWS = {
    "claims_batch_processor": {"status": "FAILED", "last_run": "2024-03-15T06:00:00Z"},
    "policy_issuance_workflow": {"status": "COMPLETED", "last_run": "2024-03-15T07:30:00Z"},
    "premium_collection": {"status": "RUNNING", "last_run": "2024-03-15T08:00:00Z"},
    "daily_reconciliation": {"status": "QUEUED", "last_run": "2024-03-14T23:00:00Z"},
    "regulatory_report_irdai": {"status": "COMPLETED", "last_run": "2024-03-15T05:00:00Z"},
}


class MockAEHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "/workflows/" in self.path and "/status" in self.path:
            wf_name = self.path.split("/workflows/")[1].split("/")[0]
            wf = MOCK_WORKFLOWS.get(wf_name, {"status": "UNKNOWN"})
            self._respond(200, {
                "workflowName": wf_name,
                "status": wf["status"],
                "lastRunTime": wf.get("last_run", ""),
                "lastDuration": random.randint(30, 600),
                "errorMessage": "FileNotFoundException: /data/input/claims_20240315.csv"
                    if wf["status"] == "FAILED" else None,
            })
        elif "/executions/failures" in self.path:
            self._respond(200, {
                "failures": [
                    {"workflowName": "claims_batch_processor",
                     "executionId": "EXC-4521",
                     "failedAt": "2024-03-15T06:05:00Z",
                     "error": "FileNotFoundException"},
                ]
            })
        elif "/system/health" in self.path:
            self._respond(200, {
                "agentsOnline": 5, "agentsOffline": 0,
                "totalQueues": 12, "stuckItems": 2,
                "scheduledWorkflows": 800, "disabledWorkflows": 3,
            })
        else:
            self._respond(404, {"error": "Not found"})

    def do_POST(self):
        self._respond(200, {"success": True, "executionId": "EXC-NEW-001"})

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9999), MockAEHandler)
    print("Mock AE API running on http://localhost:9999")
    server.serve_forever()
```

### 13.3 Go-Live Checklist

| # | Step | Owner | Status |
|---|---|---|---|
| 1 | AE service account created with minimal required permissions | AE Admin | ☐ |
| 2 | Vertex AI API enabled, service account has `Vertex AI User` role | DevOps | ☐ |
| 3 | PostgreSQL + pgvector deployed, tables created (`rag_documents`, `issue_registry`, `issue_tracker_state`) | DevOps | ☐ |
| 4 | All atomic action workflows created in AE | RPA Dev | ☐ |
| 5 | KB articles and SOPs indexed in pgvector via `python -m rag.index_all` | Ops Team | ☐ |
| 6 | Tool registry covers top 20 most common failure scenarios | Dev Team | ☐ |
| 7 | Protected workflow list reviewed and approved by business | Business | ☐ |
| 8 | Safety limits configured (max restarts, max bulk ops) | Ops Team | ☐ |
| 9 | Mock API tests passing for all 18 scenarios | QA | ☐ |
| 10 | UAT with 3 business users and 3 technical users | UAT Lead | ☐ |
| 11 | Audit logging verified — all tool calls recorded | Compliance | ☐ |
| 12 | Escalation notifications reaching Teams/email | DevOps | ☐ |
| 13 | Rollback plan documented | Ops Lead | ☐ |

---

## 14. Appendix A — Complete Folder Structure

```
ops_agent/
├── main.py                          # Entry point for AI Studio
├── requirements.txt                 # Python dependencies
├── config/
│   ├── __init__.py
│   ├── settings.py                  # All configuration
│   ├── llm_client.py                # LLM wrapper (Vertex AI / Gemini)
│   └── logging_setup.py            # Logging configuration
├── agents/
│   ├── __init__.py
│   ├── orchestrator.py              # Main orchestrator agent
│   ├── approval_gate.py             # Approval logic
│   ├── escalation.py                # Escalation agent
│   └── rca_agent.py                 # RCA generation agent
├── tools/
│   ├── __init__.py
│   ├── base.py                      # Base classes, AE API client
│   ├── registry.py                  # Tool registry
│   ├── mcp_tools.py                 # MCP P0+P1 bridge (local shared-spec mode or remote MCP client mode when AE_MCP_SERVER_URL is set)
│   ├── general_tools.py             # General escape-hatch tools (call_ae_api, query_database, search_knowledge_base)
│   ├── status_tools.py              # Status & health tools
│   ├── log_tools.py                 # Log & history tools
│   ├── file_tools.py                # File validation tools
│   ├── remediation_tools.py         # Restart, trigger, requeue
│   ├── dependency_tools.py          # Dependency analysis
│   └── notification_tools.py        # Email, Teams notifications
├── rag/
│   ├── __init__.py
│   ├── engine.py                    # RAG engine (PostgreSQL + pgvector)
│   ├── index_all.py                 # Index builder script
│   └── data/
│       ├── kb_articles/             # JSON/MD knowledge base files
│       ├── sops/                    # Standard operating procedures
│       ├── tool_docs/               # Extended tool documentation
│       └── past_incidents/          # Historical incident data
├── gateway/
│   ├── __init__.py
│   ├── message_gateway.py           # Message routing & queuing
│   └── progress.py                  # ProgressCallback — real-time status messages
├── state/
│   ├── __init__.py
│   ├── conversation_state.py        # Conversation state management
│   └── issue_tracker.py             # Multi-issue tracking & classification
├── templates/
│   ├── __init__.py
│   └── rca_templates.py            # RCA report templates
├── logs/                            # Runtime logs (auto-created)
├── tests/
│   ├── test_scenarios.py            # Test scenario definitions
│   └── mock_ae_api.py              # Mock AE API server
└── docs/
    └── ADDING_NEW_TOOLS.md         # Guide for adding new tools
```

---

## 15. Appendix B — Tool Catalogue (24+ tools)

When `AE_MCP_TOOLS_ENABLED=true`, the main app also catalogs **106 AutomationEdge MCP tools** (71 P0 + 35 support-priority P1) via `tools/mcp_tools.py` — e.g. `ae.request.get_summary`, `ae.support.diagnose_failed_request`, `ae.request.list_recent`, `ae.dependency.run_full_preflight_for_workflow`. In co-located mode, those tools come from the local `mcp_server` package; when `AE_MCP_SERVER_URL` is set, the app discovers them remotely with MCP `list_tools()` and executes them with `call_tool()`. See `SETUP_GUIDE.md` §13 and `mcp_server/README.md` for the full MCP tool list.

| # | Tool Name | Category | Tier | Description |
|---|---|---|---|---|
| 1 | `check_workflow_status` | status | read_only | Check workflow or execution status |
| 2 | `list_recent_failures` | status | read_only | List failed executions in time window |
| 3 | `get_system_health` | status | read_only | Overall platform health |
| 4 | `get_execution_logs` | logs | read_only | Get logs for a specific execution |
| 5 | `get_execution_history` | logs | read_only | Last N executions for a workflow |
| 6 | `check_input_file` | file | read_only | Validate input file presence and format |
| 7 | `check_output_file` | file | read_only | Validate output file existence |
| 8 | `get_workflow_dependencies` | dependency | read_only | Upstream/downstream mapping |
| 9 | `restart_execution` | remediation | low_risk | Restart a failed execution |
| 10 | `trigger_workflow` | remediation | medium_risk | Trigger new execution |
| 11 | `requeue_item` | remediation | low_risk | Requeue a failed queue item |
| 12 | `send_notification` | notification | medium_risk | Send alert to team |
| 13 | `get_queue_status` | status | read_only | Queue depth and health |
| 14 | `get_agent_status` | status | read_only | AE bot/agent online status |
| 15 | `get_workflow_config` | config | read_only | Workflow configuration details |
| 16 | `get_schedule_info` | config | read_only | Workflow schedule details |
| 17 | `check_agent_resources` | status | read_only | CPU/memory of AE agents |
| 18 | `bulk_retry_failures` | remediation | high_risk | Retry all failed in time window |
| 19 | `disable_workflow` | remediation | high_risk | Disable a workflow |
| 20 | `create_incident_ticket` | notification | medium_risk | Create ITSM ticket |
| 21 | `discover_tools` | meta | read_only | Search the tool catalog for tools matching a query or category; enables mid-conversation discovery when RAG filtering is active |
| 22 | `call_ae_api` | general | medium_risk | Call any AE REST endpoint directly (GET/POST/PUT/DELETE). GET bypasses approval; write methods require approval. Fallback when no typed tool exists. Params: method, endpoint, params, body |
| 23 | `query_database` | general | read_only | Run read-only SQL (SELECT only) against ops_agent database. Mutations blocked. Results capped at 50 rows. Params: sql, params |
| 24 | `search_knowledge_base` | general | read_only | Semantic search across RAG collections (kb_articles, sops, tools, past_incidents). Params: query, collection (optional), top_k |

**Adding New Tools**: Create the function in the appropriate `tools/*.py` file, create a `ToolDefinition`, register in `tools/registry.py`, run `python -m rag.index_all` to update RAG index.

---

## 16. Appendix C — Prompt Templates

### System Prompt — Core (used in orchestrator)

See Section 7.2 `_build_system_prompt()` for the full dynamic prompt. Key principles:

1. **Always verify before acting** — never guess based on symptoms
2. **File-first investigation** — since 800+ workflows are file-based, always check input files early
3. **Cascade awareness** — when multiple failures exist, trace to the root
4. **Persona-aware** — adjust detail level based on user role
5. **Audit everything** — every tool call is logged

### RCA Prompt — Business (key template)

```
Generate a Root Cause Analysis for a business audience.
Structure:
- **What Happened**: One paragraph, plain English
- **Business Impact**: Which processes were affected, any delays or data issues
- **Root Cause**: Simple explanation of why, no technical jargon
- **Resolution**: What was done to fix it
- **Prevention**: What changes will prevent recurrence
Keep it under 500 words. No workflow names, execution IDs, or error codes.
```

### RCA Prompt — Technical (key template)

```
Generate a detailed technical RCA.
Structure:
- **Incident Summary**: One-liner
- **Timeline**: Chronological events with timestamps
- **Root Cause Chain**: A → caused → B → caused → C
- **Impact Analysis**: All affected workflows, downstream dependencies, data pipelines
- **Resolution Steps**: Exact tools used, execution IDs, outcomes
- **Corrective Actions**: Config changes, monitoring additions, code fixes needed
- **Recommendations**: Long-term improvements
Include workflow names, execution IDs, error codes, and timestamps.
```

---

## 17. Appendix D — Troubleshooting

| Problem | Likely Cause | Solution |
|---|---|---|
| Agent gives generic answers | RAG not indexed or Vertex AI temperature too high | Run `python -m rag.index_all`, set temperature=0.1 in `VertexAIClient.gen_config` |
| Tool calls fail with 401 | AE API key invalid or expired | Regenerate API key in AE Admin |
| Tool calls fail with timeout | AE server overloaded or network issue | Increase `timeout_seconds` in config, check AE health |
| Agent loops without resolving | Insufficient tools or LLM not understanding context | Add more tools, improve system prompt, increase `max_agent_iterations` |
| RAG search returns no results | Embeddings not indexed; if pgvector is unavailable the RAG engine falls back to a numpy-based in-memory store for local dev | Run `python -m rag.index_all`; for production ensure pgvector is installed |
| Vertex AI 403 / permission denied | Service account lacks `aiplatform.endpoints.predict` permission | Grant `Vertex AI User` role to the service account in GCP IAM |
| Vertex AI quota exceeded | Too many concurrent requests or token limits hit | Request quota increase in GCP console, or implement request queuing |
| LLM responds slowly | Network latency to Vertex AI region | Use a closer region, switch to `gemini-2.0-flash` for faster responses |
| Business user sees technical details | Persona filtering not triggered | Check that `user_role` is correctly set in session |
| Approvals not working | Phase state not transitioning | Check `ConversationPhase` transitions in orchestrator |
| Concurrent messages lost | Threading issue | Ensure `_locks` dictionary is properly initialized per session |
| RCA report too short | LLM max_tokens too low | Increase `max_output_tokens` in `VertexAIClient.gen_config` |
| PostgreSQL connection errors | DSN misconfigured or DB unreachable | Verify `POSTGRES_DSN` in config, check pg_hba.conf allows connections |
| New messages always create new issues | Heuristic signals not matching; LLM classification defaulting to NEW_ISSUE | Check `error_signatures` and `workflows_involved` are populated during investigation; ambiguous words like "also" correctly fall through to LLM |
| Recurrence not detected | Resolved issue missing `workflows_involved` or `error_signatures` | Ensure `_process_message` calls `tracker.add_workflow_to_issue()` and `tracker.add_error_signature()` after each tool call |
| Issue marked stale too quickly | Default 30-minute stale timeout too short for long investigations | Increase `stale_minutes` parameter in `_mark_stale_issues()` or ensure `issue.touch()` is called on approval responses |
| LLM classification slow or inaccurate | Classification prompt too large or model struggles with format | Reduce `conversation_messages` window (default last 8), use `gemini-2.0-flash` for classification calls |
| Related issues not linked | LLM returns RELATED_NEW but parent_id is None | Check that `active_issue_id` is set before the classification call; ensure resolved issues retain their IDs |
| Issue tracker state lost on restart | PostgreSQL persistence not configured | Verify `issue_registry` and `issue_tracker_state` tables exist; check `POSTGRES_DSN` |
| Recurrence keeps happening (>3 times) | Root cause not addressed | System auto-escalates after `RECURRENCE_ESCALATION_THRESHOLD` (default 3); adjust threshold if needed |
| Specific typed tool not available for needed operation | No typed tool covers the required AE API call or data query | Use `call_ae_api` as fallback with the raw endpoint path (e.g., `/api/v1/workflows/...`) |

---

## Requirements File

Create `requirements.txt`:

```
google-cloud-aiplatform>=1.60.0
psycopg2-binary>=2.9.9
pgvector>=0.3.0
numpy>=1.26.0
httpx>=0.27.0
pydantic>=2.6.0
tenacity>=8.2.3
jinja2>=3.1.3
python-dateutil>=2.9.0
requests>=2.31.0
flask>=3.0.0
python-dotenv>=1.0.0
```

### Vertex AI LLM Client

Update `config/llm_client.py` to use Vertex AI (replaces Ollama/OpenAI):

```python
# config/llm_client.py
"""
LLM client using Google Vertex AI (Gemini models).
Supports both on-prem service account auth and Workload Identity.
"""

import logging
from config.settings import CONFIG
from tenacity import retry, stop_after_attempt, wait_exponential
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

logger = logging.getLogger("ops_agent.llm")

vertexai.init(
    project=CONFIG["GOOGLE_CLOUD_PROJECT"],
    location=CONFIG.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
)


class VertexAIClient:
    def __init__(self):
        self.model_name = CONFIG.get("VERTEX_AI_MODEL", "gemini-2.0-flash")
        self.model = GenerativeModel(self.model_name)
        self.gen_config = GenerationConfig(
            temperature=0.1,
            max_output_tokens=4096,
            top_p=0.95,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def chat(self, prompt: str, system: str = "",
             temperature: float = None, max_tokens: int = None) -> str:
        config = GenerationConfig(
            temperature=temperature or self.gen_config.temperature,
            max_output_tokens=max_tokens or self.gen_config.max_output_tokens,
            top_p=0.95,
        )
        model = GenerativeModel(
            self.model_name,
            system_instruction=system if system else None,
        )
        response = model.generate_content(prompt, generation_config=config)
        return response.text

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def chat_with_tools(self, messages: list[dict], tools: list[dict],
                        system: str = "") -> dict:
        """For the agentic tool-calling loop."""
        model = GenerativeModel(
            self.model_name,
            system_instruction=system if system else None,
        )
        response = model.generate_content(
            messages,
            tools=tools,
            generation_config=self.gen_config,
        )
        return response


llm_client = VertexAIClient()
```

### PostgreSQL + pgvector RAG Engine

Update `rag/engine.py` — uses Vertex AI embeddings with pgvector (production) and numpy fallback (local dev):

```python
# rag/engine.py
"""
RAG engine backed by PostgreSQL + pgvector.
Uses Google Vertex AI text-embedding models. Two storage backends:
pgvector (production) and numpy fallback (local dev).
"""

import logging
import json
import numpy as np
from config.settings import CONFIG
from vertexai.language_models import TextEmbeddingModel

logger = logging.getLogger("ops_agent.rag")

EMBED_DIM = 768


class VertexEmbedder:
    """Wraps Vertex AI text-embedding-004 model."""

    def __init__(self):
        self.model = TextEmbeddingModel.from_pretrained("text-embedding-004")

    def embed(self, text: str) -> list[float]:
        embeddings = self.model.get_embeddings([text])
        return embeddings[0].values


class NumpyFallbackStore:
    """In-memory numpy store for local dev when pgvector is not available."""

    def __init__(self, embedder: VertexEmbedder):
        self.embedder = embedder
        self._collections: dict[str, list[dict]] = {}

    def index_documents(self, documents: list[dict], collection: str):
        if collection not in self._collections:
            self._collections[collection] = []
        for doc in documents:
            emb = np.array(self.embedder.embed(doc["content"]))
            self._collections[collection].append({
                "id": doc["id"],
                "content": doc["content"],
                "metadata": doc.get("metadata", {}),
                "embedding": emb,
            })
        logger.info(f"Indexed {len(documents)} docs into '{collection}' (numpy)")

    def search(self, query: str, collection: str, top_k: int = 5) -> list[dict]:
        if collection not in self._collections:
            return []
        q_emb = np.array(self.embedder.embed(query))
        scored = []
        for doc in self._collections[collection]:
            sim = float(np.dot(q_emb, doc["embedding"]) /
                        (np.linalg.norm(q_emb) * np.linalg.norm(doc["embedding"]) + 1e-9))
            scored.append((sim, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"id": d["id"], "content": d["content"],
             "metadata": d["metadata"], "similarity": s}
            for s, d in scored[:top_k]
        ]


class PgVectorRAGEngine:
    def __init__(self):
        self.dsn = CONFIG.get("POSTGRES_DSN", "")
        self.embedder = VertexEmbedder()
        self._pg_available = False
        self._fallback = NumpyFallbackStore(self.embedder)
        if self.dsn:
            try:
                import psycopg2
                self._psycopg2 = psycopg2
                self._ensure_tables()
                self._pg_available = True
            except Exception as e:
                logger.warning(f"pgvector unavailable, using numpy fallback: {e}")

    def _get_conn(self):
        return self._psycopg2.connect(self.dsn)

    def _ensure_tables(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS rag_documents (
                        id          TEXT PRIMARY KEY,
                        content     TEXT NOT NULL,
                        metadata    JSONB DEFAULT '{{}}',
                        collection  TEXT NOT NULL,
                        embedding   vector({EMBED_DIM}),
                        created_at  TIMESTAMPTZ DEFAULT NOW()
                    );
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_rag_embedding
                    ON rag_documents USING hnsw (embedding vector_cosine_ops);
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rag_collection
                    ON rag_documents (collection);
                """)
            conn.commit()

    def _embed(self, text: str) -> list[float]:
        return self.embedder.embed(text)

    def index_documents(self, documents: list[dict], collection: str):
        """
        Index documents. Uses pgvector when available, numpy otherwise.
        Each doc: {"id": str, "content": str, "metadata": dict}
        """
        if not self._pg_available:
            self._fallback.index_documents(documents, collection)
            return

        from psycopg2.extras import Json, execute_values
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                values = []
                for doc in documents:
                    emb = self._embed(doc["content"])
                    values.append((
                        doc["id"], doc["content"],
                        Json(doc.get("metadata", {})),
                        collection, emb,
                    ))
                execute_values(
                    cur,
                    """INSERT INTO rag_documents (id, content, metadata, collection, embedding)
                       VALUES %s
                       ON CONFLICT (id) DO UPDATE SET
                         content = EXCLUDED.content,
                         metadata = EXCLUDED.metadata,
                         embedding = EXCLUDED.embedding""",
                    values,
                    template="(%s, %s, %s, %s, %s::vector)",
                )
            conn.commit()
        logger.info(f"Indexed {len(documents)} docs into collection '{collection}'")

    def search(self, query: str, collection: str, top_k: int = 5) -> list[dict]:
        if not self._pg_available:
            return self._fallback.search(query, collection, top_k)

        query_emb = self._embed(query)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, content, metadata,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM rag_documents
                    WHERE collection = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (query_emb, collection, query_emb, top_k))
                rows = cur.fetchall()
        return [
            {"id": r[0], "content": r[1], "metadata": r[2], "similarity": r[3]}
            for r in rows
        ]

    def index_tools(self, tool_docs: list[dict]):
        self.index_documents(tool_docs, collection="tools")

    def search_tools(self, query: str, top_k: int = 5) -> list[dict]:
        return self.search(query, collection="tools", top_k=top_k)

    def search_kb(self, query: str, top_k: int = 5) -> list[dict]:
        return self.search(query, collection="kb_articles", top_k=top_k)

    def search_past_incidents(self, query: str, top_k: int = 3) -> list[dict]:
        return self.search(query, collection="past_incidents", top_k=top_k)

    def index_past_incident(self, incident_id: str, summary: str,
                            root_cause: str, resolution: str,
                            workflows_involved: list[str],
                            category: str = ""):
        doc = {
            "id": incident_id,
            "content": f"{summary}\nRoot Cause: {root_cause}\nResolution: {resolution}",
            "metadata": {
                "summary": summary,
                "root_cause": root_cause,
                "resolution": resolution,
                "workflows": workflows_involved,
                "category": category,
            },
        }
        self.index_documents([doc], collection="past_incidents")


_rag_engine: PgVectorRAGEngine | None = None


def get_rag_engine() -> PgVectorRAGEngine:
    """Lazy singleton — avoids initialization at import time."""
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = PgVectorRAGEngine()
    return _rag_engine
```

---

*End of Implementation Guide — Version 2.0 — Updated 2026-03-04*
> **Documentation Update (2026-03-08)**
> - The standalone stack now includes a React-based admin control center at `/admin` and `/tools`.
> - Public documentation is now served at `/docs` from a persisted document catalog instead of hardcoded page constants.
> - Tool overrides, custom scheduler tasks, and conversation-history review are now part of the admin surface and should be treated as the primary operations workflow.
>
