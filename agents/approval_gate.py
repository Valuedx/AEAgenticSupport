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
from typing import Any, Optional

from config.settings import CONFIG
from config.llm_client import llm_client, get_current_trace
from config.observability import span_context
from state.app_config import get_approval_tier_sets, get_runtime_value
from state.conversation_state import message_content_to_text

from config.db import get_conn
from psycopg2.extras import Json

logger = logging.getLogger("ops_agent.approval")

# ---------------------------------------------------------------------------
# Sensitive parameter keys that must never be shown to users.
# Extend this list as new tools are added.
# ---------------------------------------------------------------------------
_SENSITIVE_PARAM_KEYS: frozenset[str] = frozenset({
    "api_key", "token", "secret", "password", "auth", "credential",
    "private_key", "access_key", "client_secret",
})


@dataclass
class ApprovalRequest:
    tool_name: str
    tool_params: dict
    tier: str
    reason: str
    summary: str
    request_id: str = ""  # populated by log_request; threaded into log_decision


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

    _DECISION_STATUSES: frozenset[str] = frozenset({
        "APPROVED",
        "REJECTED",
        "CANCELLED",
        "PENDING",
    })

    # Pre-compiled patterns — compiled once at class definition time for
    # performance and to avoid re-compiling on every classification call.
    _APPROVE_RES: list[re.Pattern] = [
        re.compile(p) for p in [
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
    ]
    _REJECT_RES: list[re.Pattern] = [
        re.compile(p) for p in [
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
    ]
    _CANCEL_RES: list[re.Pattern] = [
        re.compile(p) for p in [
            r"\bcancel\b",
            r"\bnever mind\b",
            r"\bforget it\b",
            r"\babort\b",
            r"\bhold on\b",
            r"\bstop\b",
        ]
    ]
    _NEGATED_APPROVAL_RE = re.compile(
        r"\b(?:don'?t|do not|not|never)\s+(?:approve|go ahead|proceed|do it|run it)\b"
    )
    _EXPLICIT_ALTERNATE_RE = re.compile(
        r"\b(can you|could you|please)\b.*\b(check|investigate|look into|try|restart|disable|enable|fix)\b"
    )
    _ALTERNATE_WITH_COMMA_RE = re.compile(
        r"\b(check|investigate|look into|try|restart|disable|enable|fix)\b"
    )

    _QUESTION_CUES = (
        "what", "why", "how", "when", "where", "which", "who",
        "can you", "could you", "will this", "is this", "does this",
    )
    _NEW_REQUEST_CUES_RES: list[re.Pattern] = [
        re.compile(rf"\b{re.escape(cue)}\b") for cue in (
            "instead", "also", "rather", "check", "investigate", "look into",
            "try", "run", "restart", "disable", "enable", "fix",
        )
    ]

    # ------------------------------------------------------------------
    # Needs-approval logic
    # ------------------------------------------------------------------

    def needs_approval(self, tool_name: str, tier: str, params: dict) -> bool:
        if tool_name == "call_ae_api":
            method = str(params.get("method", "GET")).upper()
            if method == "GET":
                return False
            return True

        # Protected workflows always require explicit approval.
        workflow = params.get("workflow_name", "")
        if workflow in get_runtime_value(
            "PROTECTED_WORKFLOWS",
            CONFIG.get("PROTECTED_WORKFLOWS", []),
        ):
            return True

        tier_sets = get_approval_tier_sets()
        if tier in tier_sets["safe"]:
            return False
        if tier in tier_sets["auto"]:
            return False

        return tier in tier_sets["required"]

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    def create_approval_request(
        self,
        conversation_id: str,
        tool_name: str,
        tier: str,
        params: dict,
        summary: str,
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            tool_name=tool_name,
            tool_params=params,
            tier=tier,
            reason=self._build_reason(tool_name, tier),
            summary=summary,
        )
        req.request_id = self.log_request(conversation_id, req)
        return req

    @staticmethod
    def _build_reason(tool_name: str, tier: str) -> str:
        return f"Tool '{tool_name}' is tier '{tier}' and requires user approval."

    def log_request(self, conversation_id: str, request: ApprovalRequest) -> str:
        """
        Record the initial approval request in the audit log.
        Returns the generated request_id so callers can thread it into
        log_decision — this prevents multi-row ambiguity when a conversation
        has more than one pending request.
        """
        req_id = self._generate_request_id()
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO approval_audit_log
                            (conversation_id, request_id, tool_name,
                             tool_params, status, tier, summary)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            conversation_id,
                            req_id,
                            request.tool_name,
                            Json(request.tool_params),
                            "PENDING",
                            request.tier,
                            request.summary,
                        ),
                    )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to log approval request: %s", e)
        return req_id

    def log_decision(
        self,
        conversation_id: str,
        request_id: str,
        status: str = "",
        approver_id: str = "",
    ) -> None:
        """
        Record the decision (APPROVED / REJECTED / CANCELLED) in the audit log.

        Uses both conversation_id and request_id in the WHERE clause to ensure
        exactly one row is updated — guards against stale-row overwrites when a
        conversation has had multiple approval cycles, or when session IDs are
        reused across different users/sessions.

        Also constrains to rows created in the last 24 hours as an extra
        safeguard against accidentally touching archived audit records.
        """
        normalized_request_id, normalized_status, normalized_approver = (
            self._normalize_log_decision_args(
                request_id=request_id,
                status=status,
                approver_id=approver_id,
            )
        )
        if not normalized_status:
            logger.warning(
                "Approval decision missing status for conversation_id=%s request_id=%s",
                conversation_id,
                normalized_request_id,
            )
            return

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if normalized_request_id:
                        cur.execute(
                            """
                            UPDATE approval_audit_log
                            SET    status      = %s,
                                   approver_id = %s,
                                   decided_at  = NOW()
                            WHERE  conversation_id = %s
                              AND  request_id      = %s
                              AND  status          = 'PENDING'
                              AND  created_at      > NOW() - INTERVAL '24 hours'
                            """,
                            (
                                normalized_status,
                                normalized_approver,
                                conversation_id,
                                normalized_request_id,
                            ),
                        )
                    else:
                        logger.warning(
                            "Approval decision missing request_id for conversation_id=%s; "
                            "falling back to latest pending request",
                            conversation_id,
                        )
                        cur.execute(
                            """
                            UPDATE approval_audit_log
                            SET    status      = %s,
                                   approver_id = %s,
                                   decided_at  = NOW()
                            WHERE  conversation_id = %s
                              AND  request_id = (
                                    SELECT request_id
                                    FROM approval_audit_log
                                    WHERE conversation_id = %s
                                      AND status = 'PENDING'
                                      AND created_at > NOW() - INTERVAL '24 hours'
                                    ORDER BY created_at DESC
                                    LIMIT 1
                              )
                            """,
                            (
                                normalized_status,
                                normalized_approver,
                                conversation_id,
                                conversation_id,
                            ),
                        )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to log approval decision: %s", e)

    def _normalize_log_decision_args(
        self,
        *,
        request_id: str,
        status: str,
        approver_id: str,
    ) -> tuple[str, str, str]:
        """Support both the new and legacy call shapes.

        New:
            log_decision(conversation_id, request_id, status, approver_id="")

        Legacy:
            log_decision(conversation_id, status)
            log_decision(conversation_id, status, approver_id)
        """
        req = str(request_id or "").strip()
        stat = str(status or "").strip().upper()
        approver = str(approver_id or "").strip()

        if not stat and req.upper() in self._DECISION_STATUSES:
            return "", req.upper(), approver

        if req.upper() in self._DECISION_STATUSES and stat.upper() not in self._DECISION_STATUSES:
            return "", req.upper(), stat or approver

        return req, stat.upper(), approver

    @staticmethod
    def _generate_request_id() -> str:
        import uuid
        return f"apprv-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # User-facing prompt formatting
    # ------------------------------------------------------------------

    def format_approval_prompt(self, request: ApprovalRequest) -> str:
        lines = [
            "I'd like to perform the following action:",
            f"  Action: {request.tool_name}",
            f"  Risk level: {request.tier}",
            f"  Details: {request.summary}",
            "",
            "Parameters:",
        ]
        safe = self._redact_sensitive_params(request.tool_params)
        for k, v in safe.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append("Reply **approve** to proceed or **reject** to cancel.")
        return "\n".join(lines)

    def format_approval_response(
        self,
        request: ApprovalRequest,
        *,
        channel: str = "",
    ) -> str | dict[str, Any]:
        """Return a user-facing approval prompt, preserving Teams cards when possible."""
        prompt = self.format_approval_prompt(request)
        if str(channel or "").strip().lower() != "msteams":
            return prompt

        safe = self._redact_sensitive_params(request.tool_params)
        reviewer_ids = request.tool_params.get("authorized_users") or []
        reviewer_text = (
            ", ".join(str(user) for user in reviewer_ids)
            if isinstance(reviewer_ids, list) and reviewer_ids
            else "Any authorized reviewer"
        )
        facts = [
            {"title": "Action:", "value": request.tool_name},
            {"title": "Risk:", "value": request.tier},
            {"title": "Summary:", "value": request.summary},
            {"title": "Request:", "value": request.request_id or "pending"},
            {"title": "Reviewers:", "value": reviewer_text},
        ]
        if safe:
            facts.extend(
                {"title": f"{key}:", "value": str(value)}
                for key, value in safe.items()
            )

        return {
            "type": "message",
            "text": prompt,
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": "Action Approval Required",
                                "weight": "Bolder",
                                "size": "Medium",
                                "wrap": True,
                            },
                            {
                                "type": "FactSet",
                                "facts": facts,
                            },
                        ],
                        "actions": [
                            {
                                "type": "Action.Submit",
                                "title": "Approve",
                                "data": {
                                    "action": "approve",
                                    "request_id": request.request_id,
                                },
                            },
                            {
                                "type": "Action.Submit",
                                "title": "Reject",
                                "style": "destructive",
                                "data": {
                                    "action": "reject",
                                    "request_id": request.request_id,
                                },
                            },
                        ],
                    },
                }
            ],
        }

    @staticmethod
    def _redact_sensitive_params(params: dict) -> dict:
        """
        Return a copy of params with values for sensitive keys replaced by
        '[REDACTED]'.  Prevents API tokens, passwords, and similar secrets
        from being shown to users in the approval prompt.
        """
        redacted = {}
        for k, v in params.items():
            if k.lower() in _SENSITIVE_PARAM_KEYS:
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = v
        return redacted

    # ------------------------------------------------------------------
    # Intent classification — public API
    # ------------------------------------------------------------------

    def classify_approval_turn(
        self,
        user_message: str,
        pending_action: Optional[dict] = None,
        pending_summary: str = "",
        conversation_messages: Optional[list[dict]] = None,
    ) -> ApprovalIntentResult:
        """
        Classify a user turn while awaiting approval.

        Returns a structured ApprovalIntentResult.  Callers should prefer this
        method over parse_approval_response when they need to distinguish
        between clarify / cancel / new_request and a plain unknown.
        """
        trace = get_current_trace()
        with span_context(trace, "approval_classification", input={"message": user_message[:300]}) as span:
            rule_based = self._classify_rule_based(user_message)
            if rule_based.intent != ApprovalIntent.UNKNOWN and rule_based.confidence >= 0.9:
                span.update(output={
                    "intent": rule_based.intent.value,
                    "confidence": rule_based.confidence,
                    "method": "rule_based",
                })
                return rule_based

            if not pending_action:
                span.update(output={
                    "intent": rule_based.intent.value,
                    "confidence": rule_based.confidence,
                    "method": "rule_based_fallback",
                })
                return rule_based

            llm_based = self._classify_with_llm(
                user_message=user_message,
                pending_action=pending_action,
                pending_summary=pending_summary,
                conversation_messages=conversation_messages,
            )
            result = llm_based if llm_based else rule_based
            span.update(output={
                "intent": result.intent.value,
                "confidence": result.confidence,
                "method": "llm" if llm_based else "rule_based_fallback",
            })
            return result

    def parse_approval_response(self, user_message: str) -> Optional[ApprovalIntentResult]:
        """
        Classify a user message and return the full ApprovalIntentResult, or
        None if the intent could not be determined.

        Callers can inspect result.intent for APPROVE / REJECT / CLARIFY /
        CANCEL / NEW_REQUEST rather than collapsing everything non-approve /
        non-reject into a silent None.  A simple boolean check is still
        possible:

            result = gate.parse_approval_response(msg)
            if result and result.intent == ApprovalIntent.APPROVE:
                ...
        """
        result = self.classify_approval_turn(user_message)
        if result.intent == ApprovalIntent.UNKNOWN:
            return None
        return result

    # ------------------------------------------------------------------
    # Clarification prompt
    # ------------------------------------------------------------------

    def format_clarification_prompt(
        self,
        pending_action: Optional[dict],
        pending_summary: str,
    ) -> str:
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
        safe = self._redact_sensitive_params(pending_action.get("args") or {})
        for k, v in safe.items():
            lines.append(f"  {k}: {v}")
        lines.extend(
            [
                "",
                "Reply in natural language:",
                "- approve (for example: 'yes, proceed')",
                "- reject (for example: 'no, don't do this')",
                "- or ask another question.",
            ]
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Rule-based classification
    # ------------------------------------------------------------------

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

        has_cancel = self._has_any_compiled(msg_lower, self._CANCEL_RES)
        has_reject = self._has_any_compiled(msg_lower, self._REJECT_RES)
        has_approve = self._has_any_compiled(msg_lower, self._APPROVE_RES)
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

        # Check approve BEFORE new_request so "ok, approve this instead" is
        # classified as APPROVE rather than NEW_REQUEST.
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

    # ------------------------------------------------------------------
    # LLM-based classification
    # ------------------------------------------------------------------

    def _classify_with_llm(
        self,
        user_message: str,
        pending_action: Optional[dict],
        pending_summary: str,
        conversation_messages: Optional[list[dict]],
    ) -> Optional[ApprovalIntentResult]:
        context_tail = (conversation_messages or [])[-4:]
        context_block_parts: list[str] = []
        for m in context_tail:
            # content was cut — a silent mid-sentence cut can cause misclassification.
            rendered = message_content_to_text(m.get("content"))
            shortened = rendered[:220] + "..." if len(rendered) > 220 else rendered
            context_block_parts.append(
                f"{m.get('role', 'unknown')}: {shortened}"
            )
        context_block = "\n".join(context_block_parts)
        action_tool = (pending_action or {}).get("tool", "")
        action_tier = (pending_action or {}).get("tier", "")
        # Never send raw args to the LLM — redact sensitive values first.
        action_args = self._redact_sensitive_params(
            (pending_action or {}).get("args", {})
        )

        # Delimit the user message clearly to reduce prompt-injection risk.
        # A crafted message like `"intent": "approve"` in free text should
        # not influence the JSON the model is asked to produce.
        prompt = (
            "Classify this user message in an approval checkpoint.\n"
            f"Pending tool: {action_tool}\n"
            f"Risk tier: {action_tier}\n"
            f"Pending summary: {pending_summary}\n"
            f"Pending args: {json.dumps(action_args, default=str)}\n"
            f"Recent conversation:\n{context_block}\n\n"
            "<user_message>\n"
            f"{user_message}\n"
            "</user_message>\n\n"
            "Return JSON with fields: "
            "intent, confidence, reason, question, alternate_request.\n"
            "intent must be one of: approve, reject, clarify, cancel, "
            "new_request, unknown."
        )
        system = (
            "You classify intent for approval-turn chat. "
            "Reject means user declined pending action. "
            "Clarify means user asked a question before deciding. "
            "Respond only with the JSON object — no preamble or explanation."
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
    def _has_any_compiled(message: str, patterns: list[re.Pattern]) -> bool:
        """Search a pre-compiled pattern list against message."""
        return any(p.search(message) for p in patterns)

    def _looks_like_negated_approval(self, message: str) -> bool:
        return bool(self._NEGATED_APPROVAL_RE.search(message))

    def _looks_like_question(self, message: str) -> bool:
        if "?" in message:
            return True
        return any(message.startswith(cue) for cue in self._QUESTION_CUES)

    def _looks_like_new_request(self, message: str) -> bool:
        if " instead" in message:
            return True
        return any(p.search(message) for p in self._NEW_REQUEST_CUES_RES)

    def _has_explicit_alternate_request(self, message: str) -> bool:
        if any(marker in message for marker in ("instead", "rather", "also")):
            return True
        if self._EXPLICIT_ALTERNATE_RE.search(message):
            return True
        if "," in message and self._ALTERNATE_WITH_COMMA_RE.search(message):
            return True
        return False
