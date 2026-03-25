"""
Message Gateway handles:
1. Receiving messages from chat interfaces (web, Teams)
2. Classifying new messages that arrive while agents are working
3. Managing the message queue per conversation
4. Routing to the agent router (multi-agent) or fallback orchestrator
"""
from __future__ import annotations

import json
import logging
import re
import threading
from enum import Enum
from typing import Any, Callable, Optional

from config.llm_client import llm_client, set_current_trace
from config.observability import trace_context
from gateway.progress import ProgressCallback
from state.conversation_state import ConversationState, ConversationPhase

logger = logging.getLogger("ops_agent.gateway")


def _is_short_intent_json(text: str) -> bool:
    """True if *text* looks like an LLM Router style {"intent": ...} blob (not user-facing)."""
    t = text.strip()
    if len(t) > 400 or not t.startswith("{"):
        return False
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]+\}", t)
        if not m:
            return False
        try:
            obj = json.loads(m.group())
        except json.JSONDecodeError:
            return False
    if not isinstance(obj, dict):
        return False
    keys = set(obj.keys())
    return keys <= {"intent", "confidence", "reason"} and "intent" in keys


def _extract_user_facing_orchestrator_reply(context_json: dict[str, Any]) -> str | None:
    """Pick the best assistant-visible string from completed workflow context (e.g. LLM / ReAct nodes).

    Skips ``trigger``, internal ``_*`` keys, and short router classification JSON.
    Optional explicit override: top-level string key ``orchestrator_user_reply``.
    """
    if not context_json:
        return None
    explicit = context_json.get("orchestrator_user_reply")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    best: tuple[int, str] | None = None
    for key, val in context_json.items():
        if key == "trigger" or (isinstance(key, str) and key.startswith("_")):
            continue
        if not isinstance(val, dict):
            continue
        text = val.get("response")
        if not isinstance(text, str) or not text.strip():
            raw_out = val.get("output")
            if isinstance(raw_out, str) and raw_out.strip():
                text = raw_out
            else:
                continue
        text = text.strip()
        if not text or _is_short_intent_json(text):
            continue
        score = len(text)
        if best is None or score > best[0]:
            best = (score, text)
    return best[1] if best else None


def _parse_metadata_bool(value, *, default: bool) -> bool:
    """Coerce Studio metadata bool-like values; fall back to *default* if unknown."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off", ""):
        return False
    return default


class MessageIntent(Enum):
    ADDITIVE = "additive"
    INTERRUPT = "interrupt"
    CANCEL = "cancel"
    APPROVAL = "approval"
    NEW_REQUEST = "new_request"


class MessageGateway:
    """Thread-safe message gateway with multi-agent routing support."""

    def __init__(self):
        # Lazy import to avoid circular dependency at import time
        self._orchestrator = None
        self._agent_router = None
        self._sessions: dict[str, ConversationState] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._session_guard = threading.Lock()

    @property
    def orchestrator(self):
        """Lazy-load the orchestrator for backward compatibility."""
        if self._orchestrator is None:
            from agents.orchestrator import Orchestrator
            self._orchestrator = Orchestrator()
        return self._orchestrator

    @property
    def agent_router(self):
        """Lazy-load the multi-agent router."""
        if self._agent_router is None:
            try:
                from agents.agent_router import get_agent_router
                from agents.agent_registry import get_agent_registry
                from agents.orchestrator_agent import OrchestratorAgent

                registry = get_agent_registry()

                # Register the default orchestrator agent if not present
                if not registry.get("ops_orchestrator"):
                    orch_agent = OrchestratorAgent()
                    registry.register(orch_agent)

                # Feature 2.1: Specialty agents
                if not registry.get("diagnostic_agent"):
                    from agents.diagnostic_agent import DiagnosticAgent
                    registry.register(DiagnosticAgent())
                if not registry.get("remediation_agent"):
                    from agents.remediation_agent import RemediationAgent
                    registry.register(RemediationAgent())
                if not registry.get("rca_agent"):
                    from agents.rca_agent import RCAAgent
                    registry.register(RCAAgent())

                self._agent_router = get_agent_router()
                logger.info(
                    "Multi-agent router initialized with %d agent(s)",
                    len(registry.list_agents()),
                )
            except Exception as exc:
                logger.warning(
                    "Multi-agent router init failed, using legacy orchestrator: %s",
                    exc,
                )
                self._agent_router = None
        return self._agent_router

    def get_or_create_session(
        self, conversation_id: str,
        user_id: str = "",
        user_role: str = "technical",
        user_name: str = "",
        user_email: str = "",
        user_team: str = "",
        user_metadata: dict | None = None,
    ) -> ConversationState:
        with self._session_guard:
            if conversation_id not in self._sessions:
                state = ConversationState.load(conversation_id)
                if not state.user_id:
                    state.user_id = user_id
                if user_role:
                    state.user_role = user_role
                
                # Update with current details if provided
                if user_name: state.user_name = user_name
                if user_email: state.user_email = user_email
                if user_team: state.user_team = user_team
                if user_metadata: state.user_metadata.update(user_metadata)
                
                self._sessions[conversation_id] = state
                self._locks[conversation_id] = threading.Lock()
            return self._sessions[conversation_id]

    @staticmethod
    def _orchestrator_bridge_wait_for_result(user_metadata: dict | None) -> bool:
        """Whether the Studio bridge should block until the DAG finishes (sync path)."""
        from config.settings import CONFIG

        default = bool(CONFIG.get("ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT", False))
        if not user_metadata or "orchestrator_wait_for_result" not in user_metadata:
            return default
        return _parse_metadata_bool(
            user_metadata.get("orchestrator_wait_for_result"),
            default=default,
        )

    @staticmethod
    def _orchestrator_bridge_chat_reply_mode(user_metadata: dict | None) -> str:
        """``auto`` = extract assistant text for Teams/chat when possible; ``full_context`` = JSON only."""
        from config.settings import CONFIG

        if user_metadata:
            raw = user_metadata.get("orchestrator_chat_reply_mode")
            if raw is not None:
                s = str(raw).strip().lower()
                if s in ("full_context", "full", "raw", "json"):
                    return "full_context"
                if s in ("auto", "friendly", "chat"):
                    return "auto"
        cfg = str(CONFIG.get("ORCHESTRATOR_BRIDGE_CHAT_REPLY_MODE", "auto") or "auto").strip().lower()
        if cfg in ("full_context", "full", "raw", "json"):
            return "full_context"
        return "auto"

    def process_message(
        self, conversation_id: str, user_message: str,
        user_id: str = "", user_role: str = "technical",
        user_name: str = "", user_email: str = "",
        user_team: str = "", user_metadata: dict | None = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> str | dict[str, Any]:
        """
        Main entry point called by the chat interface.
        Thread-safe — handles concurrent messages gracefully.

        Uses the multi-agent router when available, falls back to
        the legacy single orchestrator otherwise.

        Args:
            on_progress: optional callback ``fn(status_text)`` invoked
                with user-friendly progress messages during long operations.
        """
        state = self.get_or_create_session(
            conversation_id, user_id, user_role, user_name, user_email, user_team, user_metadata
        )
        lock = self._locks[conversation_id]

        progress = ProgressCallback(
            send_fn=on_progress,
            user_role=state.user_role,
        )

        # ── Direct orchestrator bridge ──────────────────────────────────────
        # When a caller passes orchestrator_workflow_id in user_metadata we
        # skip LLM routing entirely and invoke the visual orchestrator directly.
        #
        # Expected metadata keys:
        #   orchestrator_workflow_id  (str, required) — UUID of the workflow
        #   orchestrator_payload      (dict, optional) — trigger input (merged with chat fields)
        #   orchestrator_timeout      (int, optional)  — max wait seconds (sync mode only)
        #   orchestrator_wait_for_result (bool, optional) — if True, block until completed/failed/
        #       suspended (legacy). If omitted, use ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT from config
        #       (default: async — POST /execute only). If present (including False), that value wins.
        #   orchestrator_chat_reply_mode (str, optional) — ``auto`` (default): sync completion sends
        #       the main LLM/ReAct ``response`` text for Teams/chat; ``full_context`` keeps JSON-only.
        #   orchestrator_include_context_json (bool, optional) — if True with ``auto``, append the
        #       truncated context JSON after the friendly reply (debugging).
        #
        # In the visual orchestrator, a **Bridge User Reply** action node sets
        # ``orchestrator_user_reply`` in context (preferred over heuristic extraction).
        #
        # Default merge: ``message``, ``session_id``, ``user_id``, ``user_role``, ``user_name``,
        # ``user_email`` are copied from the chat request into the trigger payload when absent,
        # so DAGs that expect ``trigger.message`` work without duplicating Studio hook logic.
        wf_id = (user_metadata or {}).get("orchestrator_workflow_id")
        if wf_id:
            wf_id = str(wf_id).strip()
            if not wf_id:
                return "Invalid orchestrator_workflow_id (empty)."
            try:
                timeout_raw = (user_metadata or {}).get("orchestrator_timeout", 120)
                timeout = int(timeout_raw)
            except (TypeError, ValueError):
                timeout = 120
            wait_for_result = self._orchestrator_bridge_wait_for_result(user_metadata)
            return self._invoke_workflow_bridge(
                workflow_id=wf_id,
                payload=(user_metadata or {}).get("orchestrator_payload") or {},
                timeout=timeout,
                wait_for_result=wait_for_result,
                progress=progress,
                user_message=user_message or "",
                conversation_id=conversation_id,
                user_id=user_id,
                user_role=user_role,
                user_name=user_name,
                user_email=user_email,
                user_metadata=user_metadata,
            )

        if not user_message or not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        # ── Fast path: agents NOT currently working ──
        if state.phase == ConversationPhase.AWAITING_APPROVAL:
            with lock:
                return self._dispatch(
                    user_message, state, progress, conversation_id
                )

        if not state.is_agent_working:
            with lock:
                return self._dispatch(
                    user_message, state, progress, conversation_id
                )

        # ── Agents ARE currently working — classify this new message ──
        intent = self._classify_message_intent(user_message, state)

        if intent == MessageIntent.CANCEL:
            state.interrupt_requested = True
            return "Stopping current work. What would you like me to do instead?"

        elif intent == MessageIntent.INTERRUPT:
            state.interrupt_requested = True
            state.queue_user_message(user_message, hint="interrupt")
            return "Got your urgent message. Pausing current work to handle this."

        elif intent == MessageIntent.ADDITIVE:
            state.queue_user_message(user_message, hint="additive")
            return "Noted — I'll include this in my current investigation."

        elif intent == MessageIntent.APPROVAL:
            with lock:
                return self._dispatch(
                    user_message, state, progress, conversation_id
                )

        elif intent == MessageIntent.NEW_REQUEST:
            state.queue_user_message(user_message, hint="new_request")
            return "I'm working on something else right now. I'll get to this next."

        else:
            state.queue_user_message(user_message, hint="additive")
            return "Noted - I'll include this in my current investigation."

    @staticmethod
    def _merge_orchestrator_trigger_payload(
        base: dict,
        *,
        user_message: str,
        conversation_id: str,
        user_id: str = "",
        user_role: str = "",
        user_name: str = "",
        user_email: str = "",
    ) -> dict:
        """Shallow-merge chat context into trigger_payload keys the DAG expects."""
        out = dict(base)
        msg = (user_message or "").strip()
        if "message" not in out and msg:
            out["message"] = msg
        if "session_id" not in out and conversation_id:
            out["session_id"] = conversation_id
        if user_id and "user_id" not in out:
            out["user_id"] = user_id
        if user_role and "user_role" not in out:
            out["user_role"] = user_role
        if user_name and "user_name" not in out:
            out["user_name"] = user_name
        if user_email and "user_email" not in out:
            out["user_email"] = user_email
        return out

    def _invoke_workflow_bridge(
        self,
        workflow_id: str,
        payload: dict,
        timeout: int,
        wait_for_result: bool,
        progress: ProgressCallback,
        user_message: str,
        conversation_id: str,
        user_id: str = "",
        user_role: str = "",
        user_name: str = "",
        user_email: str = "",
        user_metadata: dict | None = None,
    ) -> str:
        """Direct bridge: execute an orchestrator workflow and return its output.

        Bypasses LLM routing entirely — the caller supplies the exact workflow
        UUID and trigger payload via user_metadata.  Intended for:
          - external systems calling Studio as an orchestrator proxy
          - programmatic Studio → orchestrator pipelines

        When *wait_for_result* is False (default from config), only ``POST /execute``
        is called and the reply includes *instance_id* and polling URLs — the chat
        thread is not blocked on Celery. When True, behaves as before (poll until
        terminal state, up to *timeout*).
        """
        from tools.orchestrator_client import get_orchestrator_client

        merged = self._merge_orchestrator_trigger_payload(
            payload,
            user_message=user_message,
            conversation_id=conversation_id,
            user_id=user_id,
            user_role=user_role,
            user_name=user_name,
            user_email=user_email,
        )

        progress._emit(f"Starting workflow {workflow_id}…", force=True)
        client = get_orchestrator_client()

        if not wait_for_result:
            try:
                instance = client.execute(workflow_id, merged)
            except Exception as exc:
                logger.exception("Orchestrator bridge execute failed for %s", workflow_id)
                return f"Failed to start workflow: {exc}"
            inst_raw = instance.get("id")
            inst_id = str(inst_raw) if inst_raw is not None else "?"
            base = getattr(client, "base_url", "").rstrip("/") or "(orchestrator base URL)"
            ctx_path = f"{base}/api/v1/workflows/{workflow_id}/instances/{inst_id}/context"
            progress._emit("Workflow queued (async).", force=True)
            return (
                f"Workflow **{workflow_id}** has been **queued** (non-blocking).\n\n"
                f"- **Instance id:** `{inst_id}`\n"
                f"- **Poll status / context:**\n  `{ctx_path}`\n"
                f"- **Resume after human approval (when suspended):**\n"
                f"  `POST {base}/api/v1/workflows/{workflow_id}/callback`\n\n"
                "Execution continues in the orchestrator worker. Use the AE AI Hub UI, "
                "your own poller, or a webhook integration to pick up the final result."
            )

        try:
            ctx = client.run_and_wait(
                workflow_id,
                merged,
                timeout=timeout,
                return_on_suspended=True,
            )
        except (RuntimeError, TimeoutError) as exc:
            logger.error("Orchestrator bridge error for %s: %s", workflow_id, exc)
            return f"Workflow execution failed: {exc}"
        except Exception as exc:
            logger.exception("Unexpected orchestrator bridge error for %s", workflow_id)
            return f"Unexpected error running workflow: {exc}"

        status = ctx.get("status")
        if status == "suspended":
            inst = ctx.get("instance_id", "?")
            node = ctx.get("current_node_id") or "?"
            approval = (ctx.get("approval_message") or "").strip()
            snippet = json.dumps(ctx.get("context_json", {}), default=str, indent=2)
            if len(snippet) > 2_500:
                snippet = snippet[:2_500] + "\n… [truncated]"
            progress._emit("Workflow suspended for review.", force=True)
            lead = (
                f"**Human approval required**\n\n{approval}\n\n"
                if approval
                else "**Human approval required**\n\n"
                "The workflow is waiting in **AE AI Hub → Review & Resume**.\n\n"
            )
            return (
                f"{lead}"
                f"- **Workflow:** `{workflow_id}`\n"
                f"- **Instance id:** `{inst}`\n"
                f"- **Node:** `{node}`\n\n"
                "Resume from the orchestrator **Review & Resume** UI, or call "
                f"`POST /api/v1/workflows/{workflow_id}/callback` with "
                "`approval_payload` (and optional `context_patch`).\n\n"
                f"*Context (truncated):*\n```json\n{snippet}\n```"
            )

        reply_mode = self._orchestrator_bridge_chat_reply_mode(user_metadata)
        raw_ctx = ctx.get("context_json") or {}
        output = json.dumps(raw_ctx, default=str, indent=2)
        if len(output) > 4_000:
            output = output[:4_000] + "\n… [truncated]"
        progress._emit("Workflow complete.", force=True)
        if reply_mode == "full_context":
            return f"Workflow `{workflow_id}` completed.\n```json\n{output}\n```"

        friendly = _extract_user_facing_orchestrator_reply(raw_ctx)
        if friendly:
            if _parse_metadata_bool(
                (user_metadata or {}).get("orchestrator_include_context_json"),
                default=False,
            ):
                return (
                    f"{friendly}\n\n---\n"
                    f"**Workflow `{workflow_id}` completed.**\n```json\n{output}\n```"
                )
            return f"{friendly}\n\n---\n*Workflow `{workflow_id}` completed.*"

        return f"Workflow `{workflow_id}` completed.\n```json\n{output}\n```"

    def _dispatch(
        self,
        user_message: str,
        state: ConversationState,
        progress: ProgressCallback,
        conversation_id: str,
    ) -> str | dict[str, Any]:
        """
        Dispatch to the multi-agent router or fallback to legacy orchestrator.
        """
        router = self.agent_router

        # ── Fix: Skip router during approval phase ──
        # Specialist agents currently lack the approval handling logic.
        # If we are AWAITING_APPROVAL, we MUST go to the Orchestrator which
        # owns the approval gate logic.
        if state.phase == ConversationPhase.AWAITING_APPROVAL:
            router = None

        if router:
            try:
                result = router.route(
                    user_message=user_message,
                    conversation_id=conversation_id,
                    user_id=state.user_id,
                    state=state,
                    on_progress=progress,
                )
                return result.response
            except Exception as exc:
                logger.error(
                    "Multi-agent routing failed, falling back: %s",
                    exc, exc_info=True,
                )

        # Fallback: direct orchestrator call (original behaviour)
        return self.orchestrator.handle_message(
            user_message, state, on_progress=progress
        )

    def _classify_message_intent(
        self, message: str, state: ConversationState,
    ) -> MessageIntent:
        msg_lower = message.strip().lower()

        cancel_words = {"stop", "cancel", "never mind", "abort", "quit"}
        if msg_lower in cancel_words:
            return MessageIntent.CANCEL

        if state.pending_action and any(
            re.search(rf"\b{re.escape(cue)}\b", msg_lower)
            for cue in (
                "approve", "approved", "go ahead", "proceed", "yes",
                "reject", "denied", "deny", "no", "don't do it", "do not",
            )
        ):
            return MessageIntent.APPROVAL

        urgent_words = {
            "urgent", "critical", "emergency", "p1", "asap",
            "immediately", "production down",
        }
        if any(w in msg_lower for w in urgent_words):
            return MessageIntent.INTERRUPT

        new_request_signals = (
            "different issue", "new problem", "something else",
            "separate issue", "another issue", "new request",
            "on a different note", "changing topic",
        )
        if any(sig in msg_lower for sig in new_request_signals):
            return MessageIntent.NEW_REQUEST

        current_context = ""
        if state.affected_workflows:
            current_context = (
                f"Currently investigating: "
                f"{', '.join(state.affected_workflows)}"
            )

        with trace_context(
            "gateway_classify_intent",
            session_id=getattr(state, "conversation_id", ""),
            user_id=getattr(state, "user_id", ""),
            input={"message": message[:300]},
            tags=["gateway", "classification"],
        ) as trace:
            set_current_trace(trace)
            try:
                classification = llm_client.chat(
                    f"Classify this message. Current work: {current_context}\n"
                    f"New message: {message}\n\n"
                    f"Reply with exactly one word: ADDITIVE (related to current "
                    f"work) or INTERRUPT (urgent/different topic) or NEW_REQUEST "
                    f"(different non-urgent request) or CANCEL (stop)",
                    system="You classify user messages. Reply with one word only.",
                ).strip().upper()
                trace.update(output={"classification": classification})
            finally:
                set_current_trace(None)

        if "CANCEL" in classification:
            return MessageIntent.CANCEL
        if "INTERRUPT" in classification:
            return MessageIntent.INTERRUPT
        if "NEW_REQUEST" in classification:
            return MessageIntent.NEW_REQUEST
        if "APPROVAL" in classification and state.pending_action:
            return MessageIntent.APPROVAL
        return MessageIntent.ADDITIVE
