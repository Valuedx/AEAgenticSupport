"""
Message Gateway handles:
1. Receiving messages from chat interfaces (web, Teams)
2. Classifying new messages that arrive while agents are working
3. Managing the message queue per conversation
4. Routing to the orchestrator
"""
from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Callable, Optional

from config.llm_client import llm_client
from gateway.progress import ProgressCallback, create_noop_progress
from state.conversation_state import ConversationState, ConversationPhase
from agents.orchestrator import Orchestrator

logger = logging.getLogger("ops_agent.gateway")


class MessageIntent(Enum):
    ADDITIVE = "additive"
    INTERRUPT = "interrupt"
    CANCEL = "cancel"
    APPROVAL = "approval"
    NEW_REQUEST = "new_request"


class MessageGateway:
    """Thread-safe message gateway for handling concurrent messages."""

    def __init__(self):
        self.orchestrator = Orchestrator()
        self._sessions: dict[str, ConversationState] = {}
        self._locks: dict[str, threading.Lock] = {}

    def get_or_create_session(
        self, conversation_id: str,
        user_id: str = "",
        user_role: str = "technical",
    ) -> ConversationState:
        if conversation_id not in self._sessions:
            state = ConversationState.load(conversation_id)
            if not state.user_id:
                state.user_id = user_id
            if user_role:
                state.user_role = user_role
            self._sessions[conversation_id] = state
            self._locks[conversation_id] = threading.Lock()
        return self._sessions[conversation_id]

    def process_message(
        self, conversation_id: str, user_message: str,
        user_id: str = "", user_role: str = "technical",
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Main entry point called by the chat interface.
        Thread-safe — handles concurrent messages gracefully.

        Args:
            on_progress: optional callback ``fn(status_text)`` invoked
                with user-friendly progress messages during long operations.
                For webchat this pushes SSE events; for Teams it sends
                proactive messages.
        """
        if not user_message or not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        state = self.get_or_create_session(
            conversation_id, user_id, user_role
        )
        lock = self._locks[conversation_id]

        progress = ProgressCallback(
            send_fn=on_progress,
            user_role=state.user_role,
        )

        # ── Fast path: agents NOT currently working ──
        if state.phase == ConversationPhase.AWAITING_APPROVAL:
            with lock:
                return self.orchestrator.handle_message(
                    user_message, state, on_progress=progress
                )

        if not state.is_agent_working:
            with lock:
                return self.orchestrator.handle_message(
                    user_message, state, on_progress=progress
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
                return self.orchestrator.handle_message(
                    user_message, state, on_progress=progress
                )

        else:
            state.queue_user_message(user_message, hint="new_request")
            return "I'm working on something else right now. I'll get to this next."

    def _classify_message_intent(
        self, message: str, state: ConversationState,
    ) -> MessageIntent:
        msg_lower = message.strip().lower()

        cancel_words = {"stop", "cancel", "never mind", "abort", "quit"}
        if msg_lower in cancel_words:
            return MessageIntent.CANCEL

        urgent_words = {
            "urgent", "critical", "emergency", "p1", "asap",
            "immediately", "production down",
        }
        if any(w in msg_lower for w in urgent_words):
            return MessageIntent.INTERRUPT

        current_context = ""
        if state.affected_workflows:
            current_context = (
                f"Currently investigating: "
                f"{', '.join(state.affected_workflows)}"
            )

        classification = llm_client.chat(
            f"Classify this message. Current work: {current_context}\n"
            f"New message: {message}\n\n"
            f"Reply with exactly one word: ADDITIVE (related to current "
            f"work) or INTERRUPT (urgent/different topic) or CANCEL (stop)",
            system="You classify user messages. Reply with one word only.",
        ).strip().upper()

        if "CANCEL" in classification:
            return MessageIntent.CANCEL
        elif "INTERRUPT" in classification:
            return MessageIntent.INTERRUPT
        return MessageIntent.ADDITIVE
