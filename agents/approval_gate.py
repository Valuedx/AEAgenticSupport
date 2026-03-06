"""
Approval gate for risky remediation actions.
Manages the approval workflow: request -> wait -> execute or reject.
"""
from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from config.settings import CONFIG
from config.llm_client import llm_client

from config.db import get_conn
from psycopg2.extras import Json

logger = logging.getLogger("ops_agent.approval")

SAFE_TIERS = {"read_only"}
AUTO_APPROVE_TIERS = {"low_risk"}
APPROVAL_REQUIRED_TIERS = {"medium_risk", "high_risk"}


@dataclass
class ApprovalRequest:
    tool_name: str
    tool_params: dict
    tier: str
    reason: str
    summary: str


class ApprovalIntent(Enum):
    APPROVE = "approve"
    REJECT = "reject"
    CLARIFY = "clarify"
    CANCEL = "cancel"
    NEW_REQUEST = "new_request"
    UNKNOWN = "unknown"


@dataclass
class ApprovalIntentResult:
    intent: ApprovalIntent
    confidence: float
    reason: str = ""
    normalized_message: str = ""
    question: str = ""
    alternate_request: str = ""


class ApprovalGate:
    """Determines whether a tool call needs approval and manages the flow."""

    _APPROVE_PATTERNS = [
        r"\bapprove\b",
        r"\bapproved\b",
        r"\byes\b",
        r"\byep\b",
        r"\byeah\b",
        r"\bsure\b",
        r"\bok(?:ay)?\b",
        r"\bgo ahead\b",
        r"\bproceed\b",
        r"\bdo it\b",
        r"\brun it\b",
        r"\bexecute\b",
        r"\blet'?s do it\b",
    ]
    _REJECT_PATTERNS = [
        r"\breject\b",
        r"\bden(?:y|ied)\b",
        r"\bno\b",
        r"\bnope\b",
        r"\bdo not\b",
        r"\bdon't\b",
        r"\bnot now\b",
        r"\bthat's risky\b",
        r"\btoo risky\b",
    ]
    _CANCEL_PATTERNS = [
        r"\bcancel\b",
        r"\bnever mind\b",
        r"\bforget it\b",
        r"\babort\b",
        r"\bhold on\b",
        r"\bstop\b",
    ]
    _QUESTION_CUES = (
        "what", "why", "how", "when", "where", "which", "who",
        "can you", "could you", "will this", "is this", "does this",
    )
    _NEW_REQUEST_CUES = (
        "instead", "also", "rather", "check", "investigate", "look into",
        "try", "run", "restart", "disable", "enable", "fix",
    )

    def needs_approval(self, tool_name: str, tier: str,
                       params: dict) -> bool:
        if tool_name == "call_ae_api":
            method = str(params.get("method", "GET")).upper()
            if method == "GET":
                return False
            return True

        # Protected workflows should always require explicit approval,
        # even for otherwise low-risk tools.
        workflow = params.get("workflow_name", "")
        if workflow in CONFIG.get("PROTECTED_WORKFLOWS", []):
            return True

        if tier in SAFE_TIERS:
            return False
        if tier in AUTO_APPROVE_TIERS:
            return False

        return tier in APPROVAL_REQUIRED_TIERS

    def create_approval_request(self, conversation_id: str, tool_name: str, tier: str,
                                params: dict,
                                summary: str) -> ApprovalRequest:
        req = ApprovalRequest(
            tool_name=tool_name,
            tool_params=params,
            tier=tier,
            reason=(
                f"Tool '{tool_name}' is tier '{tier}' and requires "
                f"user approval."
            ),
            summary=summary,
        )
        # Log to DB
        self.log_request(conversation_id, req)
        return req

    def log_request(self, conversation_id: str, request: ApprovalRequest):
        """Record the initial approval request in the audit log."""
        try:
            req_id = self._generate_request_id()
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO approval_audit_log 
                        (conversation_id, request_id, tool_name, tool_params, status, tier, summary)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (conversation_id, req_id, request.tool_name, Json(request.tool_params), 'PENDING', request.tier, request.summary))
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to log approval request: {e}")

    def log_decision(self, conversation_id: str, status: str, approver_id: str = ""):
        """Record the decision (APPROVED/REJECTED/CANCELLED) in the audit log."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # We update the latest pending request for this conversation
                    cur.execute("""
                        UPDATE approval_audit_log 
                        SET status = %s, approver_id = %s, decided_at = NOW()
                        WHERE conversation_id = %s AND status = 'PENDING'
                    """, (status, approver_id, conversation_id))
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to log approval decision: {e}")

    def _generate_request_id(self) -> str:
        import uuid
        return f"apprv-{uuid.uuid4().hex[:8]}"

    def format_approval_prompt(self, request: ApprovalRequest) -> str:
        lines = [
            "I'd like to perform the following action:",
            f"  Action: {request.tool_name}",
            f"  Risk level: {request.tier}",
            f"  Details: {request.summary}",
            "",
            "Parameters:",
        ]
        for k, v in request.tool_params.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append("Reply **approve** to proceed or **reject** to cancel.")
        return "\n".join(lines)

    def parse_approval_response(self, user_message: str) -> Optional[bool]:
        """Returns True for approve, False for reject, None if unrecognised."""
        result = self.classify_approval_turn(user_message)
        if result.intent == ApprovalIntent.APPROVE:
            return True
        if result.intent == ApprovalIntent.REJECT:
            return False
        return None

    def classify_approval_turn(
        self,
        user_message: str,
        pending_action: Optional[dict] = None,
        pending_summary: str = "",
        conversation_messages: Optional[list[dict]] = None,
    ) -> ApprovalIntentResult:
        """
        Classify a user turn while awaiting approval.
        Returns a structured intent including approval, rejection,
        clarification, cancellation, new request, or unknown.
        """
        rule_based = self._classify_rule_based(user_message)
        if rule_based.intent != ApprovalIntent.UNKNOWN and rule_based.confidence >= 0.9:
            return rule_based

        if not pending_action:
            return rule_based

        llm_based = self._classify_with_llm(
            user_message=user_message,
            pending_action=pending_action,
            pending_summary=pending_summary,
            conversation_messages=conversation_messages,
        )
        if llm_based:
            return llm_based
        return rule_based

    def format_clarification_prompt(self, pending_action: Optional[dict],
                                    pending_summary: str) -> str:
        if not pending_action:
            return (
                "There is no pending approval action right now. "
                "Please tell me what you want to do next."
            )

        lines = [
            "You asked for clarification before approving.",
            f"Pending action: {pending_action.get('tool', 'unknown tool')}",
            f"Summary: {pending_summary or 'No summary available'}",
            "Parameters:",
        ]
        for k, v in (pending_action.get("args") or {}).items():
            lines.append(f"  {k}: {v}")
        lines.extend([
            "",
            "Reply in natural language:",
            "- approve (for example: 'yes, proceed')",
            "- reject (for example: 'no, don't do this')",
            "- or ask another question.",
        ])
        return "\n".join(lines)

    def _classify_rule_based(self, user_message: str) -> ApprovalIntentResult:
        msg = (user_message or "").strip()
        msg_lower = msg.lower()
        if not msg:
            return ApprovalIntentResult(
                intent=ApprovalIntent.UNKNOWN,
                confidence=0.0,
                reason="empty_message",
                normalized_message=msg_lower,
            )

        has_cancel = self._has_any_pattern(msg_lower, self._CANCEL_PATTERNS)
        has_reject = self._has_any_pattern(msg_lower, self._REJECT_PATTERNS)
        has_approve = self._has_any_pattern(msg_lower, self._APPROVE_PATTERNS)
        looks_like_question = self._looks_like_question(msg_lower)
        has_new_request = self._looks_like_new_request(msg_lower)

        if has_cancel:
            return ApprovalIntentResult(
                intent=ApprovalIntent.CANCEL,
                confidence=0.98,
                reason="cancel_phrase",
                normalized_message=msg_lower,
            )

        if has_reject and self._has_explicit_alternate_request(msg_lower):
            return ApprovalIntentResult(
                intent=ApprovalIntent.NEW_REQUEST,
                confidence=0.92,
                reason="reject_with_new_request",
                normalized_message=msg_lower,
                alternate_request=msg,
            )

        if has_reject:
            return ApprovalIntentResult(
                intent=ApprovalIntent.REJECT,
                confidence=0.95,
                reason="reject_phrase",
                normalized_message=msg_lower,
            )

        if has_approve and not self._looks_like_negated_approval(msg_lower):
            return ApprovalIntentResult(
                intent=ApprovalIntent.APPROVE,
                confidence=0.95,
                reason="approve_phrase",
                normalized_message=msg_lower,
            )

        if has_new_request and "instead" in msg_lower:
            return ApprovalIntentResult(
                intent=ApprovalIntent.NEW_REQUEST,
                confidence=0.9,
                reason="instead_new_request",
                normalized_message=msg_lower,
                alternate_request=msg,
            )

        if looks_like_question:
            return ApprovalIntentResult(
                intent=ApprovalIntent.CLARIFY,
                confidence=0.92,
                reason="clarification_question",
                normalized_message=msg_lower,
                question=msg,
            )

        if has_new_request:
            return ApprovalIntentResult(
                intent=ApprovalIntent.NEW_REQUEST,
                confidence=0.88,
                reason="new_request_cue",
                normalized_message=msg_lower,
                alternate_request=msg,
            )

        return ApprovalIntentResult(
            intent=ApprovalIntent.UNKNOWN,
            confidence=0.3,
            reason="no_signal",
            normalized_message=msg_lower,
        )

    def _classify_with_llm(
        self,
        user_message: str,
        pending_action: Optional[dict],
        pending_summary: str,
        conversation_messages: Optional[list[dict]],
    ) -> Optional[ApprovalIntentResult]:
        context_tail = (conversation_messages or [])[-4:]
        context_block = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')[:220]}"
            for m in context_tail
        )
        action_tool = (pending_action or {}).get("tool", "")
        action_tier = (pending_action or {}).get("tier", "")
        action_args = (pending_action or {}).get("args", {})

        prompt = (
            "Classify this user message in an approval checkpoint.\n"
            f"Pending tool: {action_tool}\n"
            f"Risk tier: {action_tier}\n"
            f"Pending summary: {pending_summary}\n"
            f"Pending args: {json.dumps(action_args, default=str)}\n"
            f"Recent conversation:\n{context_block}\n\n"
            f"User message: {user_message}\n\n"
            "Return JSON with fields: "
            "intent, confidence, reason, question, alternate_request.\n"
            "intent must be one of: approve, reject, clarify, cancel, "
            "new_request, unknown."
        )
        system = (
            "You classify intent for approval-turn chat. "
            "Reject means user declined pending action. "
            "Clarify means user asked a question before deciding."
        )

        try:
            raw = llm_client.chat(prompt, system=system, temperature=0.0, max_tokens=220)
            data = self._extract_json(raw)
            if not isinstance(data, dict):
                return None
            intent_raw = str(data.get("intent", "")).strip().lower()
            try:
                intent = ApprovalIntent(intent_raw)
            except ValueError:
                intent = ApprovalIntent.UNKNOWN
            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            return ApprovalIntentResult(
                intent=intent,
                confidence=confidence,
                reason=str(data.get("reason", "")),
                normalized_message=(user_message or "").strip().lower(),
                question=str(data.get("question", "")),
                alternate_request=str(data.get("alternate_request", "")),
            )
        except Exception as exc:
            logger.warning("LLM approval classification failed: %s", exc)
            return None

    @staticmethod
    def _extract_json(raw: str):
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _has_any_pattern(message: str, patterns: list[str]) -> bool:
        return any(re.search(pat, message) for pat in patterns)

    @staticmethod
    def _looks_like_negated_approval(message: str) -> bool:
        return bool(
            re.search(
                r"\b(?:don'?t|do not|not|never)\s+(?:approve|go ahead|proceed|do it|run it)\b",
                message,
            )
        )

    def _looks_like_question(self, message: str) -> bool:
        if "?" in message:
            return True
        return any(message.startswith(cue) for cue in self._QUESTION_CUES)

    def _looks_like_new_request(self, message: str) -> bool:
        if " instead" in message:
            return True
        return any(cue in message for cue in self._NEW_REQUEST_CUES)

    @staticmethod
    def _has_explicit_alternate_request(message: str) -> bool:
        if any(marker in message for marker in ("instead", "rather", "also")):
            return True
        if re.search(
            r"\b(can you|could you|please)\b.*\b(check|investigate|look into|try|restart|disable|enable|fix)\b",
            message,
        ):
            return True
        if "," in message and re.search(
            r"\b(check|investigate|look into|try|restart|disable|enable|fix)\b",
            message,
        ):
            return True
        return False
