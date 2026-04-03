"""
Approval gate for risky remediation actions.
Manages the approval workflow: request -> wait -> execute or reject.
"""
from __future__ import annotations

import logging
import json
import ntpath
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from config.settings import CONFIG
from config.llm_client import llm_client
from state.app_config import get_approval_tier_sets, get_runtime_value

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

_INTERNAL_PROMPT_KEYS: frozenset[str] = frozenset({
    "user_id", "userid", "org_code", "orgcode", "authorized_users",
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
        request_id: str = "",
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
        if not status:
            # Backward compatibility for older callers that passed
            # (conversation_id, status, approver_id).
            status = request_id
            request_id = ""

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if request_id:
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
                            (status, approver_id, conversation_id, request_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE approval_audit_log
                            SET    status      = %s,
                                   approver_id = %s,
                                   decided_at  = NOW()
                            WHERE  id = (
                                SELECT id
                                FROM approval_audit_log
                                WHERE conversation_id = %s
                                  AND status = 'PENDING'
                                  AND created_at > NOW() - INTERVAL '24 hours'
                                ORDER BY created_at DESC
                                LIMIT 1
                            )
                            """,
                            (status, approver_id, conversation_id),
                        )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to log approval decision: %s", e)

    @staticmethod
    def _generate_request_id() -> str:
        import uuid
        return f"apprv-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # User-facing prompt formatting
    # ------------------------------------------------------------------

    def format_approval_prompt(
        self,
        request: ApprovalRequest,
        audience: str = "technical",
    ) -> str:
        role = str(audience or "technical").strip().lower()
        safe = self._redact_sensitive_params(request.tool_params)
        details = self._visible_param_items(safe, audience=role)
        action_text = self._describe_action(request.tool_name, safe, audience=role)
        risk_text = self._humanize_risk(request.tier)
        why_text = self._approval_reason_text(request.tool_name, request.tier, audience=role)

        if role == "business":
            lines = [
                "I'm ready to continue with this request.",
                "",
                "**What Will Happen**",
                f"- {action_text}",
                f"- Risk level: {risk_text}",
                f"- Why I need your confirmation: {why_text}",
            ]
            if details:
                lines.extend(
                    [
                        "",
                        "**Details**",
                        *[f"- {label}: {value}" for label, value in details],
                    ]
                )
            lines.extend(
                [
                    "",
                    "Reply **approve** to continue or **reject** to cancel.",
                ]
            )
            return "\n".join(lines)

        lines = [
            "I'm ready to run this action.",
            "",
            "**Planned Action**",
            f"- {action_text}",
            f"- Risk level: {risk_text}",
            f"- Why approval is required: {why_text}",
        ]
        if details:
            lines.extend(
                [
                    "",
                    "**Execution Details**",
                    *[f"- {label}: {value}" for label, value in details],
                ]
            )
        lines.extend(
            [
                "",
                "Reply **approve** to continue or **reject** to cancel.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _humanize_risk(tier: str) -> str:
        labels = {
            "low_risk": "Low",
            "medium_risk": "Medium",
            "high_risk": "High",
        }
        return labels.get(str(tier or "").strip().lower(), str(tier or "Medium"))

    @staticmethod
    def _humanize_tool_name(tool_name: str) -> str:
        custom = {
            "trigger_workflow": "run workflow",
            "t4_execute_and_poll": "run workflow and wait for the result",
            "restart_execution": "restart execution",
            "resubmit_execution": "resubmit execution",
            "create_hdfc_ticket": "create support ticket",
            "create_incident_ticket": "create incident ticket",
        }
        clean = str(tool_name or "").strip()
        if clean in custom:
            return custom[clean]
        return clean.replace("_", " ").replace("-", " ").strip().lower() or "run this action"

    @classmethod
    def _describe_action(cls, tool_name: str, params: dict, audience: str) -> str:
        workflow_name = str(params.get("workflow_name") or "").strip()
        process_name = str(params.get("process_name") or "").strip()
        execution_id = str(params.get("execution_id") or params.get("request_id") or "").strip()
        target = workflow_name or process_name or execution_id
        target_human = target.replace("_", " ").replace("-", " ").strip()
        target_technical = f"`{target}`" if target else ""

        if tool_name == "t4_execute_and_poll":
            if audience == "business":
                return f"Start {target_human or 'the requested automation'} and check the result."
            return f"Run {target_technical or 'the requested workflow'} and wait for the result."
        if tool_name == "trigger_workflow":
            if audience == "business":
                return f"Start {target_human or 'the requested automation'}."
            return f"Run {target_technical or 'the requested workflow'}."
        if tool_name == "restart_execution":
            if audience == "business":
                return f"Restart {target_human or 'the selected automation run'}."
            return f"Restart execution {target_technical or ''}.".replace("  ", " ").strip()
        if tool_name == "resubmit_execution":
            if audience == "business":
                return f"Submit {target_human or 'the selected automation run'} again."
            return f"Resubmit execution {target_technical or ''}.".replace("  ", " ").strip()
        if tool_name in {"create_hdfc_ticket", "create_incident_ticket"}:
            if audience == "business":
                return f"Create a support request for {target_human or 'this issue'}."
            return f"Create a support ticket for {target_technical or 'this issue'}."

        action = cls._humanize_tool_name(tool_name).capitalize()
        if audience == "business" and target_human:
            return f"{action} for {target_human}."
        if target_technical:
            return f"{action} for {target_technical}."
        return f"{action}."

    @classmethod
    def _approval_reason_text(cls, tool_name: str, tier: str, audience: str) -> str:
        if tool_name in {"trigger_workflow", "t4_execute_and_poll", "restart_execution", "resubmit_execution"}:
            if audience == "business":
                return "this will start or change a live automation run"
            return "this action can start or change live automation activity"
        if tool_name in {"create_hdfc_ticket", "create_incident_ticket"}:
            if audience == "business":
                return "this will create a support record that teams may act on"
            return "this action creates a support record in an external system"
        if str(tier or "").strip().lower() == "high_risk":
            return "this action has higher operational impact"
        return "this action needs confirmation before it is executed"

    @classmethod
    def _visible_param_items(
        cls,
        params: dict,
        *,
        audience: str,
    ) -> list[tuple[str, str]]:
        flattened = cls._flatten_params(params)
        items: list[tuple[str, str]] = []
        hidden_for_business = {"workflow_id"}
        for key, value in flattened:
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            if clean_key.lower() in _INTERNAL_PROMPT_KEYS:
                continue
            if audience == "business" and clean_key.lower() in hidden_for_business:
                continue
            label = cls._humanize_param_label(clean_key, audience=audience)
            rendered = cls._format_param_value(clean_key, value, audience=audience)
            if not rendered:
                continue
            items.append((label, rendered))
        return items

    @classmethod
    def _flatten_params(cls, params: dict, prefix: str = "") -> list[tuple[str, object]]:
        items: list[tuple[str, object]] = []
        for raw_key, value in (params or {}).items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            if isinstance(value, dict):
                next_prefix = prefix
                if key.lower() not in {"params", "parameters"}:
                    next_prefix = f"{prefix}.{key}" if prefix else key
                items.extend(cls._flatten_params(value, next_prefix))
                continue
            items.append((f"{prefix}.{key}" if prefix else key, value))
        return items

    @staticmethod
    def _humanize_param_label(key: str, *, audience: str) -> str:
        leaf = str(key or "").split(".")[-1].strip().lower()
        business_map = {
            "workflow_name": "Process",
            "process_name": "Process",
            "output_path": "Document",
            "input_path": "Document",
            "file_path": "Document",
            "execution_id": "Run ID",
            "request_id": "Run ID",
        }
        technical_map = {
            "workflow_name": "Workflow",
            "process_name": "Process",
            "workflow_id": "Workflow ID",
            "output_path": "Output path",
            "input_path": "Input path",
            "file_path": "File path",
            "execution_id": "Execution ID",
            "request_id": "Request ID",
        }
        mapping = business_map if audience == "business" else technical_map
        if leaf in mapping:
            return mapping[leaf]
        human = leaf.replace("_", " ").replace("-", " ").strip()
        if human.lower() == "emp id":
            return "Employee ID"
        return human[:1].upper() + human[1:] if human else key

    @staticmethod
    def _format_param_value(key: str, value: object, *, audience: str) -> str:
        if value in (None, "", [], {}):
            return ""
        leaf = str(key or "").split(".")[-1].strip().lower()
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(item) for item in value if item not in (None, ""))[:500]

        text = str(value).strip()
        if not text:
            return ""

        if audience == "business" and "path" in leaf:
            name = ntpath.basename(text) or text
            return name

        if leaf in {"workflow_name", "process_name"} and audience == "business":
            return text.replace("_", " ").replace("-", " ").strip()

        return f"`{text}`" if audience != "business" else text

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
        return llm_based if llm_based else rule_based

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
        audience: str = "technical",
    ) -> str:
        role = str(audience or "technical").strip().lower()
        if not pending_action:
            return (
                "There isn't a pending approval right now. "
                "Please tell me what you'd like me to do next."
            )

        safe = self._redact_sensitive_params(pending_action.get("args") or {})
        details = self._visible_param_items(safe, audience=role)
        action_text = self._describe_action(
            str(pending_action.get("tool") or ""),
            safe,
            audience=role,
        )

        if role == "business":
            lines = [
                "I'm waiting for your decision on this request.",
                "",
                "**What Will Happen**",
                f"- {action_text}",
            ]
            if details:
                lines.extend(
                    [
                        "",
                        "**Details**",
                        *[f"- {label}: {value}" for label, value in details],
                    ]
                )
            elif pending_summary:
                lines.extend(["", f"Summary: {pending_summary}"])
            lines.extend(
                [
                    "",
                    "Reply **approve** to continue, **reject** to cancel, or tell me what you'd like to change.",
                ]
            )
            return "\n".join(lines)

        lines = [
            "I'm waiting for your decision on this action.",
            "",
            "**Planned Action**",
            f"- {action_text}",
        ]
        if details:
            lines.extend(
                [
                    "",
                    "**Execution Details**",
                    *[f"- {label}: {value}" for label, value in details],
                ]
            )
        elif pending_summary:
            lines.extend(["", f"Summary: {pending_summary}"])
        lines.extend(
            [
                "",
                "Reply **approve** to continue, **reject** to cancel, or tell me what you'd like to change.",
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
        context_block = "\n".join(
            # Truncate each message and append an ellipsis so the LLM knows the
            # content was cut — a silent mid-sentence cut can cause misclassification.
            f"{m.get('role', 'unknown')}: "
            + (
                m.get("content", "")[:220] + "..."
                if len(m.get("content", "")) > 220
                else m.get("content", "")
            )
            for m in context_tail
        )
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
