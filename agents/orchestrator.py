"""
Main orchestrator agent.
Routes messages, manages the investigation/remediation loop,
coordinates tool calls via RAG-selected tools, and handles
issue lifecycle (new, continue, recurrence, escalation).
"""
from __future__ import annotations

import concurrent.futures
from difflib import SequenceMatcher
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import cast, Any, List, Optional
from zoneinfo import ZoneInfo


from google.genai import types

from agents.approval_gate import ApprovalGate, ApprovalIntent
from agents.escalation import EscalationAgent
from config.llm_client import llm_client
from config.metrics import metrics_collector
from config.settings import CONFIG
from gateway.progress import ProgressCallback, create_noop_progress
from rag.engine import get_rag_engine
from security.workflow_access import can_execute_workflow, is_execute_enforced
from state.app_config import get_runtime_value
from state.conversation_state import ConversationState, ConversationPhase
from state.issue_tracker import (
    IssueTracker,
    MessageClassification,
    IssueStatus,
)
from tools.base import get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.orchestrator")
audit = logging.getLogger("ops_agent.audit")


class Orchestrator:

    def __init__(self):
        self.approval_gate = ApprovalGate()
        self.escalation = EscalationAgent()
        self.issue_trackers: dict[str, IssueTracker] = {}

    @staticmethod
    def _get_display_time_context() -> tuple[datetime, str, str, str]:
        tz_name = str(CONFIG.get("DISPLAY_TIMEZONE") or "Asia/Kolkata").strip() or "Asia/Kolkata"
        try:
            display_tz = ZoneInfo(tz_name)
        except Exception:
            display_tz = timezone(timedelta(hours=5, minutes=30))
            tz_name = "Asia/Kolkata"

        now_local = datetime.now(display_tz)
        hour = now_local.hour
        if hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        elif hour < 21:
            greeting = "Good evening"
        else:
            greeting = "Good night"

        label = "IST" if tz_name in {"Asia/Kolkata", "Asia/Calcutta"} else tz_name
        now_str = now_local.strftime("%A, %d %B %Y  %I:%M %p") + f" {label}"
        return now_local, greeting, now_str, label

    @staticmethod
    def _state_org_code(state: ConversationState) -> str:
        metadata = state.user_metadata if isinstance(state.user_metadata, dict) else {}
        for key in ("org_code", "orgCode", "tenant_org_code", "tenantOrgCode"):
            value = metadata.get(key)
            if value:
                return str(value).strip()
        return str(get_ae_client().default_org_code or "").strip()

    @staticmethod
    def _build_pending_action_summary(tool_name: str, tool_args: dict) -> str:
        args = tool_args or {}
        target = (
            args.get("process_name")
            or args.get("title")
            or args.get("workflow_name")
            or args.get("agent_name")
            or args.get("agent_id")
            or args.get("execution_id")
            or args.get("request_id")
            or args.get("workflow_id")
            or "unknown"
        )
        return f"{tool_name} on {target}"

    @staticmethod
    def _collect_exception_messages(exc: BaseException) -> list[str]:
        messages: list[str] = []
        queue: list[BaseException] = [exc]
        seen: set[int] = set()

        while queue:
            current = queue.pop(0)
            ident = id(current)
            if ident in seen:
                continue
            seen.add(ident)

            text = str(current).strip()
            if text:
                messages.append(text)

            for attr in ("__cause__", "__context__"):
                nested = getattr(current, attr, None)
                if isinstance(nested, BaseException):
                    queue.append(nested)

            last_attempt = getattr(current, "last_attempt", None)
            if last_attempt is not None:
                try:
                    last_attempt.result()
                except BaseException as nested:
                    queue.append(nested)

        return messages

    @classmethod
    def _is_rate_limit_error(cls, exc: BaseException) -> bool:
        combined = " ".join(msg.lower() for msg in cls._collect_exception_messages(exc))
        return any(token in combined for token in ("429", "resource_exhausted", "rate limit", "quota"))

    @staticmethod
    def _build_recent_tool_result_fallback_response(state: ConversationState) -> str:
        for call in reversed(state.tool_call_log[-10:]):
            result = call.get("result") or {}
            if not isinstance(result, dict):
                continue

            report = str(result.get("report") or "").strip()
            if len(report) > 40:
                return report

            message = str(result.get("message") or result.get("error") or "").strip()
            if not message:
                continue

            details: list[str] = []
            workflow_name = str(result.get("workflow_name") or "").strip()
            request_id = str(
                result.get("execution_id")
                or result.get("request_id")
                or ""
            ).strip()
            status = str(result.get("status") or result.get("state") or "").strip()

            if workflow_name and workflow_name.lower() not in message.lower():
                details.append(f"Workflow: `{workflow_name}`")
            if request_id:
                details.append(f"Request ID: `{request_id}`")
            if status:
                details.append(f"Status: {status}")

            if details:
                return f"{message}\n\n" + "\n".join(details)
            return message

        return ""

    def _log_pending_action_decision(
        self,
        state: ConversationState,
        status: str,
        approver_id: str = "",
    ) -> None:
        action = state.pending_action or {}
        self.approval_gate.log_decision(
            state.conversation_id,
            str(action.get("request_id") or ""),
            status,
            approver_id,
        )

    @staticmethod
    def _extract_named_date_phrase(message: str, labels: tuple[str, ...]) -> str:
        text = " ".join(str(message or "").strip().split())
        if not text:
            return ""
        pattern = r"\b(?:" + "|".join(re.escape(label) for label in labels) + r")\b"
        match = re.search(
            pattern + r"(?:\s+(?:use|set|as|is|be|to|for))?\s+(.+?)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return ""
        return str(match.group(1) or "").strip(" .,:;")

    @staticmethod
    def _coerce_existing_datetime(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if getattr(parsed, "tzinfo", None) is not None:
                return parsed.replace(tzinfo=None)
            return parsed
        except ValueError:
            return None

    @staticmethod
    def _parse_human_date_phrase(
        raw_text: str,
        *,
        default_year: int,
        end_of_day: bool,
    ) -> Optional[str]:
        text = str(raw_text or "").strip()
        if not text:
            return None

        text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
        text = text.replace(",", " ")
        text = " ".join(text.split())

        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if getattr(parsed, "tzinfo", None) is not None:
                parsed = parsed.replace(tzinfo=None)
            return parsed.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass

        has_time = bool(
            re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b|\b\d{1,2}\s*(?:am|pm)\b", text, re.IGNORECASE)
        )
        candidates = [text]
        if not re.search(r"\b\d{4}\b", text):
            candidates.insert(0, f"{text} {default_year}")

        formats = [
            "%d %B %Y %H:%M:%S",
            "%d %B %Y %H:%M",
            "%d %B %Y %I:%M %p",
            "%d %B %Y %I %p",
            "%d %B %Y",
            "%d %b %Y %H:%M:%S",
            "%d %b %Y %H:%M",
            "%d %b %Y %I:%M %p",
            "%d %b %Y %I %p",
            "%d %b %Y",
            "%B %d %Y %H:%M:%S",
            "%B %d %Y %H:%M",
            "%B %d %Y %I:%M %p",
            "%B %d %Y %I %p",
            "%B %d %Y",
            "%b %d %Y %H:%M:%S",
            "%b %d %Y %H:%M",
            "%b %d %Y %I:%M %p",
            "%b %d %Y %I %p",
            "%b %d %Y",
        ]
        for candidate in candidates:
            for fmt in formats:
                try:
                    parsed = datetime.strptime(candidate, fmt)
                except ValueError:
                    continue
                if not has_time:
                    parsed = parsed.replace(
                        hour=23 if end_of_day else 0,
                        minute=59 if end_of_day else 0,
                        second=59 if end_of_day else 0,
                    )
                return parsed.strftime("%Y-%m-%dT%H:%M:%S")
        return None

    def _extract_pending_action_updates(
        self,
        user_message: str,
        pending_action: dict,
    ) -> dict[str, str]:
        args = dict((pending_action or {}).get("args") or {})
        if not args:
            return {}

        existing_from = self._coerce_existing_datetime(args.get("from_date"))
        existing_to = self._coerce_existing_datetime(args.get("to_date"))
        default_year = (
            (existing_from or existing_to or datetime.now()).year
        )

        updates: dict[str, str] = {}
        if "from_date" in args:
            raw_from = self._extract_named_date_phrase(
                user_message,
                ("from date", "from_date", "start date", "start_date", "from"),
            )
            parsed_from = self._parse_human_date_phrase(
                raw_from,
                default_year=default_year,
                end_of_day=False,
            ) if raw_from else None
            if parsed_from:
                updates["from_date"] = parsed_from

        if "to_date" in args:
            raw_to = self._extract_named_date_phrase(
                user_message,
                ("to date", "to_date", "end date", "end_date", "till", "until", "to"),
            )
            parsed_to = self._parse_human_date_phrase(
                raw_to,
                default_year=default_year,
                end_of_day=True,
            ) if raw_to else None
            if parsed_to:
                updates["to_date"] = parsed_to

        return updates

    def _refresh_pending_approval_prompt(
        self,
        state: ConversationState,
        updated_args: dict,
        *,
        note: str,
    ) -> str:
        action = dict(state.pending_action or {})
        tool_name = str(action.get("tool") or "unknown")
        actual_tier = str(action.get("tier") or "medium_risk")
        authorized_users = list(action.get("authorized_users") or [])

        self._log_pending_action_decision(
            state,
            "CANCELLED",
            state.user_id or "user",
        )

        summary = self._build_pending_action_summary(tool_name, updated_args)
        approval_request = self.approval_gate.create_approval_request(
            state.conversation_id,
            tool_name,
            actual_tier,
            updated_args,
            summary,
        )
        state.pending_action = {
            "tool": tool_name,
            "args": dict(updated_args),
            "tier": actual_tier,
            "authorized_users": authorized_users,
            "request_id": approval_request.request_id,
        }
        state.pending_action_summary = summary
        state.phase = ConversationPhase.AWAITING_APPROVAL
        return (
            f"{note}\n\n"
            + self.approval_gate.format_approval_prompt(
                approval_request,
                audience=state.user_role,
            )
        )

    def _inject_user_scope(self, tool_name: str, tool_args: dict, tool_def, state: ConversationState) -> dict:
        args = dict(tool_args or {})
        metadata = getattr(tool_def, "metadata", {}) or {}
        needs_scope = (
            tool_name in {
                "discover_tools",
                "check_workflow_status",
                "list_recent_failures",
                "trigger_workflow",
                "t4_execute_and_poll",
                "ae.workflow.list",
            }
            or bool(metadata.get("workflow_name"))
            or bool(metadata.get("workflow_id"))
        )
        if not needs_scope:
            return args
        if state.user_id and "user_id" not in args and "userId" not in args:
            args["user_id"] = state.user_id
        org_code = self._state_org_code(state)
        if org_code and "org_code" not in args and "orgCode" not in args:
            args["org_code"] = org_code
        return args

    @staticmethod
    def _issue_tracker_key(conversation_id: str, user_id: str = "") -> str:
        user = str(user_id or "").strip()
        return f"{conversation_id}::{user}" if user else conversation_id

    @staticmethod
    def _looks_like_greeting(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" .,!?\t")
        if not normalized:
            return False

        simple_greetings = {
            "hi",
            "hello",
            "hi there",
            "hello there",
            "hey",
            "hey there",
            "good morning",
            "good afternoon",
            "good evening",
            "good night",
        }
        if normalized in simple_greetings:
            return True

        if normalized.startswith(("good morning", "good afternoon", "good evening", "good night")):
            operational_terms = (
                "workflow",
                "bot",
                "status",
                "issue",
                "error",
                "fail",
                "request",
                "execution",
                "agent",
                "check",
                "show",
                "run",
                "trigger",
                "timesheet",
            )
            return len(normalized.split()) <= 5 and not any(term in normalized for term in operational_terms)

        return False

    def _get_visible_workflows_for_issue(
        self,
        issue,
        state: ConversationState,
    ) -> list[str]:
        workflows = [
            str(wf or "").strip()
            for wf in getattr(issue, "workflows_involved", []) or []
            if str(wf or "").strip()
        ]
        if not workflows:
            return []
        if not state.user_id:
            return workflows

        visible: list[str] = []
        client = get_ae_client()
        org_code = self._state_org_code(state)
        for workflow_name in workflows:
            try:
                workflow_id, _ = client.get_cached_workflow_info(
                    workflow_name,
                    user_id=state.user_id,
                    org_code=org_code,
                )
            except TypeError:
                workflow_id, _ = client.get_cached_workflow_info(workflow_name)
            except Exception:
                workflow_id = ""
            if workflow_id:
                visible.append(workflow_name)
        return visible

    def _is_issue_visible(self, issue, state: ConversationState) -> bool:
        if not issue:
            return False
        workflows = getattr(issue, "workflows_involved", []) or []
        if not workflows:
            return True
        return bool(self._get_visible_workflows_for_issue(issue, state))

    def _get_visible_active_issue(
        self,
        tracker: IssueTracker | None,
        state: ConversationState,
    ):
        active = tracker.get_active_issue() if tracker else None
        if active and self._is_issue_visible(active, state):
            return active
        return None

    def _get_visible_issue_summary(
        self,
        tracker: IssueTracker | None,
        state: ConversationState,
    ) -> str:
        if not tracker or not tracker.issues:
            return "No active issues."

        visible_lines: list[str] = []
        for issue in tracker.issues.values():
            if issue.status in (IssueStatus.RESOLVED, IssueStatus.STALE):
                continue
            if not self._is_issue_visible(issue, state):
                continue
            visible_workflows = self._get_visible_workflows_for_issue(issue, state)
            workflow_info = (
                f" | Workflows: {', '.join(visible_workflows)}"
                if visible_workflows
                else ""
            )
            visible_lines.append(
                f"[{issue.issue_id}] {issue.title} | Status: {issue.status.value}{workflow_info}"
            )

        return "\n".join(visible_lines) if visible_lines else "No active issues."

    def _get_issue_tracker(self, conversation_id: str, user_id: str = "") -> IssueTracker:
        tracker_key = self._issue_tracker_key(conversation_id, user_id)
        if tracker_key not in self.issue_trackers:
            self.issue_trackers[tracker_key] = IssueTracker(conversation_id)
        return self.issue_trackers[tracker_key]

    @staticmethod
    def _build_related_issue_notice(state: ConversationState) -> str:
        if str(state.user_role or "").strip().lower() == "business":
            return (
                "This seems related to what we were discussing earlier, "
                "but it looks like a different problem. I'll handle it as a "
                "new request from here.\n\n"
            )
        return (
            "This looks related to the earlier conversation, but it appears "
            "to be a different issue. I'll handle it as a new request from here.\n\n"
        )

    @staticmethod
    def _is_ticket_like_endpoint(endpoint: str) -> bool:
        lowered = str(endpoint or "").lower()
        return any(token in lowered for token in ("ticket", "incident", "/support/", "support/user", "support/create", "usercreateticket"))

    def _extract_ticket_args_from_call_ae_api(self, tool_args: dict) -> dict | None:
        if str(tool_args.get("method", "GET")).upper() == "GET":
            return None

        endpoint = str(tool_args.get("endpoint", "") or "")
        body = tool_args.get("body") or tool_args.get("payload") or {}
        parsed: dict[str, Any] = {}

        if isinstance(body, dict):
            parsed = dict(body)
        elif isinstance(body, str) and body.strip():
            raw = body.strip()
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    parsed = loaded
            except Exception:
                parsed = {}
                patterns = {
                    "process_name": r"process[_ ]?name\s*[:=]\s*[\"']?([^,\"'\]\}]+)",
                    "description": r"description\s*[:=]\s*[\"']?([^\"'\]\}]+)",
                    "request_type": r"request[_ ]?type\s*[:=]\s*[\"']?([^,\"'\]\}]+)",
                }
                for key, pattern in patterns.items():
                    match = re.search(pattern, raw, re.IGNORECASE)
                    if match:
                        parsed[key] = match.group(1).strip()

        lowered_keys = {str(k).lower(): v for k, v in parsed.items()}
        process_name = lowered_keys.get("process_name") or lowered_keys.get("title") or lowered_keys.get("processname")
        description = lowered_keys.get("description") or lowered_keys.get("message") or lowered_keys.get("details")
        request_type = lowered_keys.get("request_type")

        if not self._is_ticket_like_endpoint(endpoint) and not (process_name or description):
            return None
        if not process_name or not description:
            return None

        if not request_type:
            request_type = "Incident" if re.search(r"\b(fail\w*|error\w*|exception\w*|down|issue\w*)\b", str(description), re.IGNORECASE) else "Request"

        return self._sanitize_ticket_args(
            {
                "process_name": str(process_name),
                "description": str(description),
                "request_type": str(request_type),
            }
        )

    @staticmethod
    def _looks_like_parameter_followup(text: str) -> bool:
        normalized = " ".join(str(text or "").strip().lower().split())
        if not normalized or len(normalized.split()) > 8:
            return False

        date_phrases = (
            "today",
            "yesterday",
            "tomorrow",
            "last 24 hours",
            "last 24 hrs",
            "last day",
            "last week",
            "this week",
            "from ",
            "to ",
            "between ",
            "until ",
            "till ",
        )
        if any(phrase in normalized for phrase in date_phrases):
            return True

        if re.search(r"\b\d{4}-\d{2}-\d{2}\b", normalized):
            return True
        if re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", normalized):
            return True
        if re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", normalized):
            return True
        return False

    def _augment_contextual_tool_query(
        self,
        user_message: str,
        state: ConversationState,
        enriched_query: str,
    ) -> str:
        if not self._looks_like_parameter_followup(user_message):
            return enriched_query

        last_assistant = next(
            (
                str(message.get("content") or "").strip()
                for message in reversed(state.messages)
                if message.get("role") == "assistant"
                and str(message.get("content") or "").strip()
            ),
            "",
        )
        if not last_assistant:
            return enriched_query

        lowered = last_assistant.lower()
        hint_parts: list[str] = []
        if "agent" in lowered and "log" in lowered:
            hint_parts.append("agent logs")
            agent_match = re.search(
                r"\b(?:agent(?:\s+id)?|id)\s*[:#-]?\s*(\d{3,})\b",
                last_assistant,
                re.IGNORECASE,
            )
            if agent_match:
                hint_parts.append(f"agent_id {agent_match.group(1)}")
        elif "execution" in lowered and "log" in lowered:
            hint_parts.append("execution logs")

        if not hint_parts:
            return enriched_query

        augmented = (
            f"{enriched_query} "
            f"(Recent assistant context: {' '.join(hint_parts)})"
        )
        logger.info("RAG augmented query: %s", augmented)
        return augmented

    def _extract_agent_log_args_from_call_ae_api(self, tool_args: dict) -> dict | None:
        if str(tool_args.get("method", "GET")).upper() != "GET":
            return None

        endpoint = "/" + str(tool_args.get("endpoint", "") or "").strip().lstrip("/")
        endpoint = endpoint.split("?", 1)[0]
        match = re.match(
            r"^(?:/aeengine/rest)?/api/v1/agents/([^/]+)/logs/?$",
            endpoint,
            re.IGNORECASE,
        )
        if not match:
            return None

        params_payload = tool_args.get("params") or {}
        parsed_params: dict[str, Any] = {}
        if isinstance(params_payload, dict):
            parsed_params = dict(params_payload)
        elif isinstance(params_payload, str) and params_payload.strip():
            try:
                loaded = json.loads(params_payload)
                if isinstance(loaded, dict):
                    parsed_params = loaded
            except Exception:
                parsed_params = {}

        extracted: dict[str, Any] = {"agent_id": str(match.group(1))}
        from_date = parsed_params.get("from_date") or parsed_params.get("fromDate")
        to_date = parsed_params.get("to_date") or parsed_params.get("toDate")
        if from_date:
            extracted["from_date"] = str(from_date)
        if to_date:
            extracted["to_date"] = str(to_date)
        return extracted

    @staticmethod
    def _canonicalize_tool_name(tool_name: str) -> str:
        clean = str(tool_name or "").strip()
        alias_map = {
            "ae.ticket.create": "create_support_ticket",
        }
        return alias_map.get(clean, clean)

    @classmethod
    def _is_support_ticket_tool(cls, tool_name: str) -> bool:
        return cls._canonicalize_tool_name(tool_name) in {"create_support_ticket", "create_incident_ticket"}

    def _rewrite_call_ae_api_tool(self, tool_name: str, tool_args: dict) -> tuple[str, dict]:
        if tool_name != "call_ae_api":
            return self._canonicalize_tool_name(tool_name), tool_args

        redirected_ticket_args = self._extract_ticket_args_from_call_ae_api(tool_args)
        if redirected_ticket_args:
            return "create_support_ticket", redirected_ticket_args

        redirected_agent_log_args = self._extract_agent_log_args_from_call_ae_api(tool_args)
        if redirected_agent_log_args:
            return "analyze_agent_logs", redirected_agent_log_args

        return self._canonicalize_tool_name(tool_name), tool_args

    def _ensure_turn_tool_active(
        self,
        turn_tools,
        active_tool_names: set[str],
        tool_name: str,
        *,
        allowed_categories: list[str] | None,
        feedback_agent_id: str,
    ):
        if tool_name in active_tool_names:
            return turn_tools, active_tool_names

        tool_def = tool_registry.get_tool(tool_name)
        if not tool_def:
            return turn_tools, active_tool_names
        if allowed_categories and tool_def.category not in allowed_categories:
            return turn_tools, active_tool_names

        turn_tools = tool_registry.build_turn_toolset(
            list(active_tool_names) + [tool_name],
            allowed_categories=allowed_categories,
            include_meta=True,
            feedback_agent_id=feedback_agent_id,
        )
        return turn_tools, set(turn_tools.list_tool_names())

    # =====================================================================
    # Public entry point
    # =====================================================================

    def handle_message(self, user_message: str,
                       state: ConversationState,
                       on_progress: ProgressCallback | None = None,
                       allowed_categories: list[str] | None = None,
                       feedback_agent_id: str = "ops_orchestrator") -> str:
        if not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        progress = on_progress or create_noop_progress()
        
        # Start tracking turn metrics
        import uuid
        turn_id = f"turn-{cast(Any, uuid.uuid4().hex)[:8]}"
        metrics_collector.start_turn(state.conversation_id, turn_id)
        
        try:
            state.add_message("user", user_message)
            tracker = self._get_issue_tracker(state.conversation_id, state.user_id)

            # ── Approval flow ──
            if state.phase == ConversationPhase.AWAITING_APPROVAL:
                active = tracker.get_active_issue()
                if active:
                    active.touch()
                    tracker._persist_issue(active)

                # Ask the LLM whether the user is responding to the approval
                # or pivoting to something completely new.
                intent_result = self.approval_gate.classify_approval_turn(
                    user_message=user_message,
                    pending_action=state.pending_action,
                    pending_summary=state.pending_action_summary,
                    conversation_messages=state.messages,
                )
                if intent_result.intent == ApprovalIntent.NEW_REQUEST:
                    # Suspend the pending approval so the user can resume later
                    state.suspended_flow = {
                        "type": "approval",
                        "pending_action": dict(state.pending_action or {}),
                        "pending_action_summary": state.pending_action_summary,
                    }
                    state.pending_action = None
                    state.pending_action_summary = ""
                    state.phase = ConversationPhase.IDLE
                    self.approval_gate.log_decision(
                        state.conversation_id,
                        str(state.suspended_flow.get("pending_action", {}).get("request_id") or ""),
                        "CANCELLED",
                        state.user_id or "user",
                    )
                    logger.info(
                        "approval_suspended conversation_id=%s intent=%s",
                        state.conversation_id, intent_result.reason,
                    )
                    # Answer the new question and append a resume reminder
                    inner = self._process_message(
                        user_message, state, tracker, progress,
                        feedback_agent_id=feedback_agent_id,
                    )
                    suspended_summary = state.suspended_flow.get(
                        "pending_action_summary", "previous action"
                    )
                    reminder = (
                        f"\n\n---\n"
                        f"\u23f8\ufe0f A pending action is on hold: **{suspended_summary}**\n"
                        f"Reply **continue** to review it again, or **drop it** to cancel."
                    )
                    state.save()
                    return inner + reminder

                response = self._handle_approval_response(
                    user_message, state, tracker
                )
                state.save()
                return response

            # Conversational router (LLM-based): ACK/SMALLTALK/GENERAL/OPS.
            # Skip for specialists (allowed_categories != None) or ongoing investigations.
            conv_route = "OPS"
            if allowed_categories is None and state.phase == ConversationPhase.IDLE:
                conv_route = self._classify_conversational_route(user_message, state, tracker)
            
            if conv_route in {"ACK", "SMALLTALK", "GENERAL"}:
                response = self._build_conversational_response(
                    user_message=user_message,
                    route=conv_route,
                    tracker=tracker,
                )
                state.phase = ConversationPhase.IDLE
                state.is_agent_working = False
                state.add_message("assistant", response)
                state.save()
                return response

            # ── Classify message ──
            classification, issue_id = tracker.classify_message(
                user_message, state.messages
            )
            target_issue = (
                tracker.issues.get(issue_id) if issue_id else tracker.get_active_issue()
            )
            if (
                classification in {
                    MessageClassification.CONTINUE_EXISTING,
                    MessageClassification.RELATED_NEW,
                    MessageClassification.RECURRENCE,
                    MessageClassification.FOLLOWUP,
                }
                and target_issue
                and not self._is_issue_visible(target_issue, state)
            ):
                logger.info(
                    "Hidden issue context suppressed for conversation_id=%s issue_id=%s user_id=%s",
                    state.conversation_id,
                    target_issue.issue_id,
                    state.user_id or "anonymous",
                )
                classification = MessageClassification.NEW_ISSUE
                issue_id = None

            # ── Route based on classification ──
            if classification == MessageClassification.NEW_ISSUE:
                tracker.create_issue(
                    title=cast(Any, user_message)[:80],
                    description=user_message,
                )
                state.phase = ConversationPhase.IDLE
                response = self._process_message(
                    user_message,
                    state,
                    tracker,
                    progress,
                    feedback_agent_id=feedback_agent_id,
                )

            elif classification == MessageClassification.CONTINUE_EXISTING:
                tracker.switch_to_issue(issue_id or tracker.active_issue_id)
                response = self._process_message(
                    user_message,
                    state,
                    tracker,
                    progress,
                    feedback_agent_id=feedback_agent_id,
                )

            elif classification == MessageClassification.RELATED_NEW:
                parent_id = issue_id or tracker.active_issue_id
                issue = tracker.create_issue(
                    title=cast(Any, user_message)[:80],
                    description=user_message,
                )
                if parent_id:
                    tracker.link_issues(parent_id, issue.issue_id)
                response = (
                    self._build_related_issue_notice(state)
                    + self._process_message(
                        user_message,
                        state,
                        tracker,
                        progress,
                        feedback_agent_id=feedback_agent_id,
                    )
                )

            elif classification == MessageClassification.RECURRENCE:
                old_issue = tracker.reopen_issue(issue_id)

                if tracker.should_escalate_recurrence(old_issue.issue_id):
                    old_issue.status = IssueStatus.ESCALATED
                    tracker._persist_issue(old_issue)
                    state.phase = ConversationPhase.ESCALATED
                    response = (
                        f"This issue has now recurred {old_issue.recurrence_count} "
                        f"times. Previous resolution "
                        f"({cast(Any, old_issue.resolution)[:150]}) is not holding. "
                        f"I'm escalating to the operations team for a permanent fix."
                    )
                    state.save()
                    return response

                state.phase = ConversationPhase.IDLE
                recurrence_note = (
                    f"This appears to be a recurrence of a previous issue "
                    f"(occurrence #{old_issue.recurrence_count}). "
                )
                if old_issue.resolution:
                    recurrence_note += (
                        f"Last time the resolution was: "
                        f"{cast(Any, old_issue.resolution)[:200]}. "
                        f"Let me check if the same root cause applies.\n\n"
                    )
                response = recurrence_note + self._process_message(
                    user_message,
                    state,
                    tracker,
                    progress,
                    feedback_agent_id=feedback_agent_id,
                )

            elif classification == MessageClassification.FOLLOWUP:
                target_issue = (
                    tracker.issues.get(issue_id) if issue_id else None
                ) or tracker.get_active_issue()

                if target_issue and target_issue.status == IssueStatus.RESOLVED:
                    response = (
                        f"Regarding [{target_issue.issue_id}] "
                        f"{target_issue.title}: it was resolved. "
                        f"{target_issue.resolution}\n\n"
                        f"Would you like me to verify the current status?"
                    )
                elif target_issue and target_issue.status == IssueStatus.STALE:
                    tracker.resume_stale_issue(target_issue.issue_id)
                    response = (
                        f"Resuming investigation of [{target_issue.issue_id}] "
                        f"{target_issue.title}.\n\n"
                        + self._process_message(
                            user_message,
                            state,
                            tracker,
                            progress,
                            allowed_categories,
                            feedback_agent_id=feedback_agent_id,
                        )
                    )
                else:
                    response = self._process_message(
                        user_message,
                        state,
                        tracker,
                        progress,
                        allowed_categories,
                        feedback_agent_id=feedback_agent_id,
                    )

            elif classification == MessageClassification.STATUS_CHECK:
                summary = self._get_visible_issue_summary(tracker, state)
                response = (
                    f"Here's the current session status:\n\n{summary}\n\n"
                    + self._process_message(
                        user_message,
                        state,
                        tracker,
                        progress,
                        feedback_agent_id=feedback_agent_id,
                    )
                )
            else:
                response = self._process_message(
                    user_message,
                    state,
                    tracker,
                    progress,
                    feedback_agent_id=feedback_agent_id,
                )

            state.save()
            return response
        except Exception as e:
            logger.exception(f"Error in handle_message: {e}")
            metrics_collector.record_turn_error(turn_id, str(e))
            error_response = f"I encountered a technical problem: {cast(Any, str(e))[:100]}. Please try again or contact support."
            # Append error response to state so it's persisted in chat_messages
            state.add_message("assistant", error_response)
            state.save() 
            return error_response
        finally:
            metrics_collector.end_turn(turn_id)

    def _classify_conversational_route(
        self,
        user_message: str,
        state: ConversationState,
        tracker: IssueTracker | None = None,
    ) -> str:
        """LLM router for conversational turns. Returns: ACK, SMALLTALK, GENERAL, or OPS."""
        text = str(user_message or "").strip()
        if not text:
            return "GENERAL"
        if self._looks_like_greeting(text):
            return "SMALLTALK"

        # Fast-path: if the message contains IDs, dates, or time ranges, it is OPS.
        import re
        id_pattern = r"\b(request|req|id|execution|exec|automation|agent|workflow|status|error|fail|issue)\s*(id|#)?\s*:?\s*\d{0,}\b"
        date_pattern = r"\b(\d{1,4}[-/]\d{1,2}[-/]\d{1,4})\b"
        if re.search(id_pattern, text, re.IGNORECASE) or re.search(date_pattern, text):
            return "OPS"
            
        # Also match standalone numeric IDs that look like request IDs or Agent IDs (e.g. 2887)
        if re.search(r"\b\d{4,}\b", text):
            return "OPS"

        active_issue = self._get_visible_active_issue(tracker, state)
        # If there's an active investigation or pending action, bias towards OPS
        if active_issue and text.lower() not in {"thanks", "thank you", "ok", "okay", "yes", "no"}:
            # Check if it looks like a follow-up answer (containing names or specific values)
            if len(text) > 5:
                return "OPS"
                
        active_status = active_issue.status.value if active_issue else "none"

        best_similarity = 0.0
        try:
            rag = get_rag_engine()
            query_vec = rag.embed_query(text)
            from security.workflow_access import is_read_enforced

            if state.user_id:
                tool_hits = rag.search_tools_for_user(
                    text,
                    user_id=state.user_id,
                    org_code=self._state_org_code(state),
                    top_k=5,
                    query_embedding=query_vec,
                )
            elif is_read_enforced():
                tool_hits = []
            else:
                tool_hits = rag.search_tools(text, top_k=5, query_embedding=query_vec)
            for hit in tool_hits or []:
                if not isinstance(hit, dict):
                    continue
                try:
                    best_similarity = max(
                        best_similarity,
                        float(hit.get("rrf_score", hit.get("similarity", 0.0)) or 0.0),
                    )
                except Exception:
                    continue
        except Exception:
            best_similarity = 0.0

        try:
            route = llm_client.chat(
                (
                    "Classify this user message for routing into one of four categories.\n"
                "CRITICAL: YOUR OUTPUT MUST BE EXACTLY ONE WORD: 'ACK', 'SMALLTALK', 'GENERAL', or 'OPS'.\n"
                "DO NOT provide any explanation, preamble, or punctuation.\n\n"
                "ACK = brief acknowledgement, thank you, or closing of the conversation.\n"
                "SMALLTALK = greeting/chit-chat that does not contain a specific task or question.\n"
                "GENERAL = a general non-technical question that does not require workflows, tools, or SOPs.\n"
                "OPS = any operations, support, troubleshooting, or automation intent (e.g. asking about status, errors, workflows, or fixes).\n"
                    f"Active issue status: {active_status}\n"
                    f"Tool relevance score (0-1): {best_similarity:.3f}\n"
                    f'User message: "{text}"'
                ),
                system="Be strict and output one token only.",
                temperature=0.0,
                max_tokens=8,
            ).strip().upper()
            if route in {"ACK", "SMALLTALK", "GENERAL", "OPS"}:
                return route
        except Exception:
            pass

        # Fallback to similarity check if LLM response was ambiguous or failed.
        # A threshold of 0.01 is more appropriate for RRF/vector search scores in this catalog.
        return "OPS" if best_similarity >= 0.01 else "GENERAL"

    def _build_conversational_response(
        self,
        *,
        user_message: str,
        route: str,
        tracker: IssueTracker | None = None,
    ) -> str:
        text = str(user_message or "").strip()
        if route == "ACK":
            active = tracker.get_active_issue() if tracker else None
            if active and active.status == IssueStatus.RESOLVED:
                return "You're welcome. This issue is resolved. If it comes back, share the details and I'll check."
            return "You're welcome. Share any issue when ready and I'll help."

        # Compute the current display time and derive the greeting word
        _, _greeting, _now_str, _tz_label = self._get_display_time_context()

        try:
            return llm_client.chat(
                (
                    f"Current Date/Time ({_tz_label}): {_now_str}\n"
                    f"Appropriate greeting for this time of day: {_greeting}\n\n"
                    "Respond naturally to the user's message.\n"
                    "If this is a greeting or small talk, use the appropriate greeting above naturally — "
                    "do NOT hardcode a different greeting.\n"
                    "If the message seems like an operations request, clarify that you can help but need more detail.\n"
                    "Do not use internal IDs or technical error codes in this chat phase.\n"
                    "End by reminding the user that you are ready to help with AutomationEdge issues.\n"
                    f"Route: {route}\n"
                    f'User message: "{text}"'
                ),
                system="You are a polite, concise assistant. Always use the provided IST date/time and greeting word — never guess the time of day yourself.",
                temperature=0.5,
                max_tokens=120,
            ).strip()
        except Exception:
            return (
                f"{_greeting}! If you need anything, share it and I'll help.\n"
                "I can also help with AutomationEdge issues anytime."
            )

    # =====================================================================
    # Core investigation / remediation loop
    # =====================================================================

    def _process_message(self, user_message: str,
                         state: ConversationState,
                         tracker: IssueTracker,
                         progress: ProgressCallback | None = None,
                         allowed_categories: list[str] | None = None,
                         feedback_agent_id: str = "ops_orchestrator") -> str:
        progress = progress or create_noop_progress()
        state.is_agent_working = True
        state.phase = ConversationPhase.INVESTIGATING
        progress.on_phase("investigating")

        # Feature 2.2: Language Detection
        if len(state.messages) <= 3:
            detected = self._detect_language(user_message)
            if detected != state.preferred_language:
                state.preferred_language = detected
                logger.info(f"Language switch detected: {detected}")
                state.save()

        try:
            active_issue = self._get_visible_active_issue(tracker, state)
            system_prompt = self._build_system_prompt(state, tracker)
            rag = get_rag_engine()

            # ── Context-Aware RAG Enrichment (Feature 1.1) ──
            enriched_query = user_message
            if active_issue:
                context_parts = []
                visible_workflows = self._get_visible_workflows_for_issue(active_issue, state)
                if visible_workflows:
                    context_parts.append(f"Workflows: {', '.join(visible_workflows)}")
                # Only include error signatures if the issue is NOT resolved (Loop Fix)
                if active_issue.status != IssueStatus.RESOLVED and active_issue.error_signatures:
                    context_parts.append(f"Errors: {', '.join(active_issue.error_signatures)}")
                if active_issue.execution_ids:
                    context_parts.append(f"ExecutionIDs: {', '.join(active_issue.execution_ids)}")
                if context_parts:
                    enriched_query = f"{user_message} (Context: {' '.join(context_parts)})"
                    logger.info(f"RAG enriched query: {enriched_query}")
            enriched_query = self._augment_contextual_tool_query(
                user_message,
                state,
                enriched_query,
            )

            query_vec = None
            try:
                query_vec = rag.embed_query(enriched_query)
            except Exception as e:
                logger.warning(f"RAG embedding failed: {e}. Falling back to keyword-only search.")

            # Run four RAG searches in parallel to reduce tail latency
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                f_tools = ex.submit(
                    rag.search_tools, enriched_query, 12, query_vec
                )
                f_kb = ex.submit(
                    rag.search_kb, enriched_query, 3, query_vec
                )
                f_sops = ex.submit(
                    rag.search_sops, enriched_query, 3, query_vec
                )
                f_incidents = ex.submit(
                    rag.search_past_incidents, enriched_query, 3, query_vec
                )
                tool_hits = f_tools.result()
                kb_hits = f_kb.result()
                sop_hits = f_sops.result()
                incident_hits = f_incidents.result()

            context_block = self._format_rag_context(
                [tool_hits[i] for i in range(min(len(tool_hits), 5))], kb_hits, sop_hits, incident_hits
            )

            rag_tool_names = [
                h.get("metadata", {}).get("tool_name", h.get("id", ""))
                for h in tool_hits
            ]
            max_rag = get_runtime_value("MAX_RAG_TOOLS", 12)
            
            # ── Category-based Tool Isolation (Feature 1.2) ──
            turn_tools = tool_registry.build_turn_toolset_filtered(
                rag_tool_names,
                query=enriched_query,
                rag_hits=tool_hits,
                max_rag_tools=max_rag,
                allowed_categories=allowed_categories,
                feedback_agent_id=feedback_agent_id,
            )
            vertex_tools = turn_tools.to_vertex_tools()
            active_tool_names = set(turn_tools.list_tool_names())

            messages = [
                types.Content(
                    role="user",
                    parts=[types.Part(text=f"{context_block}\n\nUser: {user_message}")],
                )
            ]

            active_issue = self._get_visible_active_issue(tracker, state)
            max_iterations = get_runtime_value("MAX_AGENT_ITERATIONS", 15)

            param_followup = self._continue_param_collection(user_message, state, tracker)
            if param_followup:
                state.add_message("assistant", param_followup)
                state.is_agent_working = False
                return param_followup

            # Track whether a flow was just suspended so we can append a reminder below
            _flow_was_suspended = bool(state.suspended_flow)

            preflight = self._preflight_workflow_param_collection(
                user_message=user_message,
                state=state,
                active_issue=active_issue,
                sop_hits=sop_hits,
                feedback_agent_id=feedback_agent_id,
            )
            if preflight:
                state.add_message("assistant", preflight)
                state.is_agent_working = False
                state.phase = ConversationPhase.IDLE
                return preflight

            for iteration in range(max_iterations):
                if state.interrupt_requested:
                    state.interrupt_requested = False
                    state.is_agent_working = False
                    state.phase = ConversationPhase.IDLE
                    return "Investigation paused. What would you like me to do?"

                progress.on_iteration(iteration, max_iterations)

                response = llm_client.chat_with_tools(
                    messages,
                    tools=vertex_tools,
                    system=system_prompt,
                )

                if not hasattr(response, 'candidates') or not response.candidates:
                    break

                candidate = response.candidates[0]
                parts = candidate.content.parts

                fn_calls = [
                    p.function_call for p in parts
                    if p.function_call
                ]
                tool_called = bool(fn_calls)

                if tool_called:
                    needs_expansion = False
                    for fc in fn_calls:
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}
                        tool_name, tool_args = self._rewrite_call_ae_api_tool(tool_name, tool_args)

                        tool_name, tool_args = self._rewrite_completed_execution_followup(
                            user_message=user_message,
                            state=state,
                            tool_name=tool_name,
                            tool_args=tool_args,
                        )
                        turn_tools, active_tool_names = self._ensure_turn_tool_active(
                            turn_tools,
                            cast(set[str], active_tool_names),
                            tool_name,
                            allowed_categories=allowed_categories,
                            feedback_agent_id=feedback_agent_id,
                        )
                        tool_def = turn_tools.get_tool(tool_name)
                        if not tool_def:
                            tool_def = tool_registry.get_tool(tool_name)

                        if tool_def:
                            tool_args = self._inject_user_scope(tool_name, tool_args, tool_def, state)
                            # Check for missing required parameters first. 
                            # If params are missing, we don't ask for approval yet.
                            # We let the tool run (dynamic tools return a friendly prompt)
                            # or the agent will naturally realize it needs them.
                            missing = [
                                p for p in tool_def.required_params
                                if p not in tool_args
                                or tool_args.get(p) in (None, "", {}, [])
                            ]
                            
                            # Pre-sanitize args for the Approval UI to ensure the user sees WAF-safe text
                            # and the backend tool doesn't have to strip characters silently.
                            if self._is_support_ticket_tool(tool_name):
                                tool_args = self._sanitize_ticket_args(tool_args)

                            if not missing and self.approval_gate.needs_approval(
                                tool_name, tool_def.tier, tool_args
                            ):
                                summary = self._build_pending_action_summary(
                                    tool_name,
                                    tool_args,
                                )
                                # Clean summary too for ticketing actions
                                if self._is_support_ticket_tool(tool_name):
                                    summary = summary.replace("_", " ").replace(":", " ")

                                approval_request = self.approval_gate.create_approval_request(
                                    state.conversation_id,
                                    tool_name, tool_def.tier,
                                    tool_args, summary,
                                )
                                state.pending_action = {
                                    "tool": tool_name,
                                    "args": tool_args,
                                    "tier": tool_def.tier,
                                    "authorized_users": tool_args.get(
                                        "authorized_users", []
                                    ),
                                    "request_id": approval_request.request_id,
                                }
                                state.pending_action_summary = summary
                                state.phase = ConversationPhase.AWAITING_APPROVAL
                                state.is_agent_working = False
                                return self.approval_gate.format_approval_prompt(
                                    approval_request,
                                    audience=state.user_role,
                                )

                    messages.append(candidate.content)

                    response_parts = []
                    discovered_names: list[str] = []

                    for fc in fn_calls:
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}
                        tool_name, tool_args = self._rewrite_call_ae_api_tool(tool_name, tool_args)

                        tool_name, tool_args = self._rewrite_completed_execution_followup(
                            user_message=user_message,
                            state=state,
                            tool_name=tool_name,
                            tool_args=tool_args,
                        )

                        turn_tools, active_tool_names = self._ensure_turn_tool_active(
                            turn_tools,
                            cast(set[str], active_tool_names),
                            tool_name,
                            allowed_categories=allowed_categories,
                            feedback_agent_id=feedback_agent_id,
                        )
                        tool_def = turn_tools.get_tool(tool_name)
                        if not tool_def:
                            tool_def = tool_registry.resolve_discovered_tool(
                                tool_name
                            )
                        if not tool_def:
                            response_parts.append(
                                types.Part(
                                    function_response=types.FunctionResponse(
                                        name=tool_name,
                                        response={
                                            "error": f"unknown tool '{tool_name}'"
                                        },
                                    )
                                )
                            )
                            continue

                        tool_args = self._inject_user_scope(tool_name, tool_args, tool_def, state)
                        progress.on_tool_start(tool_name, tool_args)
                        
                        import time
                        tool_start = time.time()
                        
                        result = turn_tools.execute(tool_name, **tool_args)
                        
                        tool_lat = (time.time() - tool_start) * 1000
                        
                        # Find the active turn_id for this conversation
                        with metrics_collector._lock:
                            tid = next((tid for tid, m in metrics_collector.active_turns.items() 
                                       if m.conversation_id == state.conversation_id), None)
                        if tid:
                            metrics_collector.record_tool_call(tid, tool_name, tool_lat, result.success, result.error)

                        state.log_tool_call(
                            tool_name, tool_args, result.data, result.success
                        )
                        # Cleanup param collection on success (Loop Fix)
                        if result.success and tool_name in ("trigger_workflow", "t4_execute_and_poll"):
                            state.clear_param_collection()

                        progress.on_tool_done(
                            tool_name, result.success,
                            result.error if not result.success else "",
                        )

                        if tool_name == "discover_tools":
                            found = (result.data or {}).get("tools", [])
                            found_names: List[str] = [str(t.get("name") or "") for t in cast(List[Any], found) if isinstance(t, dict)]
                            active_set: set[str] = cast(set[str], active_tool_names)
                            new_names = [n for n in found_names if n and not active_set.__contains__(n)]
                            discovered_names.extend(new_names)
                            logger.info(
                                "discover_tools result: found=%s new_to_active=%s",
                                found_names, new_names,
                            )
                            if not found_names:
                                logger.warning(
                                    "discover_tools returned EMPTY results for this query. "
                                    "LLM may fall back to SOP. Check RAG index and category mapping."
                                )

                        if active_issue:
                            active_issue.touch()
                            wf = tool_args.get("workflow_name")
                            if wf:
                                tracker.add_workflow_to_issue(
                                    active_issue.issue_id, wf
                                )
                                tracker.add_error_signature(
                                    active_issue.issue_id,
                                    cast(Any, result.error)[:100],
                                )
                            if isinstance(result.data, dict):
                                eid = result.data.get("execution_id") or result.data.get("request_id")
                                if eid:
                                    tracker.add_execution_id_to_issue(
                                        active_issue.issue_id, str(eid)
                                    )

                        # Handle "Ask Again" pattern from Tool Result
                        result_payload = (
                            {"result": result.data}
                            if result.success or (isinstance(result.data, dict) and result.data.get("needs_user_input"))
                            else {"error": result.error}
                        )

                        if isinstance(result.data, dict) and result.data.get("needs_user_input"):
                            workflow_name = str(result.data.get("workflow_name") or tool_args.get("workflow_name") or "").strip()
                            missing_params = result.data.get("missing_params") or []
                            if workflow_name and isinstance(missing_params, list) and missing_params:
                                self._start_or_update_param_collection(
                                    state=state,
                                    workflow_name=workflow_name,
                                    missing_params=[str(p) for p in missing_params if p],
                                    tool_name=tool_name,
                                    tool_args=tool_args,
                                    auto_execute=False,
                                )
                            pretty_items = [(self._prettify_param_name(str(p)), "") for p in missing_params]
                            question = self._build_param_request_message(
                                workflow_name=workflow_name or "this workflow",
                                items=pretty_items,
                                intro="I need a few more details.",
                            )
                            state.add_message("assistant", question)
                            state.is_agent_working = False
                            # We stop here and ask the user
                            return question

                        response_parts.append(
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=tool_name,
                                    response=result_payload,
                                )
                            )
                        )

                    messages.append(types.Content(role="model", parts=response_parts))
                    logger.info("Iter %s: Tool execution turn complete. Appending results to context.", iteration)

                    if discovered_names or needs_expansion:
                        expanded = list(active_tool_names) + discovered_names
                        turn_tools = tool_registry.build_turn_toolset(
                            expanded,
                            allowed_categories=allowed_categories,
                            include_meta=True,
                            feedback_agent_id=feedback_agent_id,
                        )
                        vertex_tools = turn_tools.to_vertex_tools()
                        active_tool_names = set(turn_tools.list_tool_names())

                if not tool_called:
                    progress.on_phase("almost_done")
                    text_parts = [
                        p.text for p in parts
                        if p.text
                    ]
                    # If no tool was called and tool relevance is weak, prefer SOP-guided resolution.
                    # CRITICAL: Only fall back if NO tools have been successfully called in the session.
                    any_tools_called = any(
                        getattr(p, 'function_call', None) or getattr(p, 'function_response', None) 
                        for m in messages for p in m.parts
                    )
                    logger.warning(
                        "LLM did not call any tool on iteration %s. "
                        "any_tools_called_in_history=%s active_tools=%s",
                        iteration,
                        any_tools_called,
                        sorted(active_tool_names),
                    )
                    use_sop = not any_tools_called and self._should_use_sop_fallback(tool_hits, sop_hits)
                    if use_sop:
                        best_tool_sim = max(
                            (float(h.get("rrf_score", h.get("similarity", 0.0)) or 0.0) for h in (tool_hits or [])),
                            default=0.0,
                        )
                        best_sop_sim = max(
                            (float(h.get("rrf_score", h.get("similarity", 0.0)) or 0.0) for h in (sop_hits or [])),
                            default=0.0,
                        )
                        logger.warning(
                            "SOP fallback triggered: best_tool_score=%.4f best_sop_score=%.4f "
                            "(tool score < 0.52 or < 0.0001 with sop > 0.3). "
                            "Falling back to SOP guidance instead of tool call.",
                            best_tool_sim, best_sop_sim,
                        )
                        final_response = self._build_friendly_sop_fallback_response(
                            user_message=user_message,
                            sop_hits=sop_hits,
                        )
                    else:
                        if text_parts:
                            final_response = "\n".join(text_parts)
                        else:
                            final_response = (
                                "I've completed my investigation. Let me know if "
                                "you need anything else."
                            )
                    final_response = self._filter_for_persona(
                        final_response, state
                    )
                    # If a previous flow was suspended, remind the user they can resume
                    if _flow_was_suspended and state.suspended_flow:
                        wf = (
                            state.suspended_flow.get("workflow_name")
                            or state.suspended_flow.get("pending_action_summary")
                            or "previous request"
                        )
                        final_response += (
                            f"\n\n---\n"
                            f"\u23f8\ufe0f The **{wf}** request is on hold.\n"
                            f"Reply **continue** to pick up where we left off, "
                            f"or **drop it** to cancel."
                        )
                    state.add_message("assistant", final_response)
                    state.is_agent_working = False
                    state.phase = ConversationPhase.IDLE

                    queued = self._drain_queued_messages(state, tracker)
                    if queued:
                        final_response += "\n\n" + queued
                    
                    logger.info("Turn successfully completed with final response (len=%s)", len(final_response))
                    return final_response

            state.is_agent_working = False
            state.phase = ConversationPhase.IDLE
            max_iter_msg = (
                "I've reached the maximum investigation steps. Here's what "
                "I found so far — would you like me to continue, escalate, "
                "or generate an RCA?"
            )
            queued = self._drain_queued_messages(state, tracker)
            if queued:
                max_iter_msg += "\n\n" + queued
            return max_iter_msg
        except Exception as e:
            logger.error(f"Processing error: {e}", exc_info=True)
            state.is_agent_working = False
            state.phase = ConversationPhase.IDLE

            if self._is_rate_limit_error(e):
                fallback = self._build_recent_tool_result_fallback_response(state)
                if fallback:
                    return (
                        f"{fallback}\n\n"
                        "Note: the result above was retrieved successfully, but the final summarization step hit a temporary Vertex AI rate limit."
                    )
                return (
                    "I'm currently experiencing API rate limits while processing "
                    "the response. The data was retrieved successfully. "
                    "Please try your request again in about a minute."
                )

            return (
                "I encountered an error during investigation. "
                "The operations team has been notified."
            )

    # =====================================================================
    # Queued message processing
    # =====================================================================

    def _drain_queued_messages(self, state: ConversationState,
                               tracker: IssueTracker) -> str:
        queued = state.get_queued_messages()
        if not queued:
            return ""

        parts = []
        for msg in queued:
            hint = msg.get("hint", "")
            content = msg.get("content", "")
            if hint == "interrupt":
                parts.append(
                    f"**Queued (urgent):** Processing your earlier message "
                    f"now — \"{cast(Any, content)[:150]}\""
                )
                state.add_message("user", content)
                resp = self._process_message(content, state, tracker)
                parts.append(resp)
            elif hint == "additive":
                state.add_message("user", f"[Additional context] {content}")
                parts.append(
                    f"**Noted additional context:** {cast(Any, content)[:200]}"
                )
            elif hint == "new_request":
                parts.append(
                    f"**Queued request:** \"{cast(Any, content)[:150]}\" — "
                    f"I'll handle this next."
                )
                state.add_message("user", content)
                resp = self._process_message(content, state, tracker)
                parts.append(resp)

        return "\n\n".join(parts)

    # =====================================================================
    # Approval handling
    # =====================================================================

    def _handle_approval_response(self, user_message: str,
                                  state: ConversationState,
                                  tracker: IssueTracker) -> str:
        classification = self.approval_gate.classify_approval_turn(
            user_message=user_message,
            pending_action=state.pending_action,
            pending_summary=state.pending_action_summary,
            conversation_messages=state.messages,
        )
        intent = classification.intent

        if intent == ApprovalIntent.CLARIFY:
            updates = self._extract_pending_action_updates(
                user_message,
                state.pending_action or {},
            )
            if updates:
                refreshed_args = dict((state.pending_action or {}).get("args") or {})
                refreshed_args.update(updates)
                return self._refresh_pending_approval_prompt(
                    state,
                    refreshed_args,
                    note="Updated the pending action with your requested changes.",
                )
            return self.approval_gate.format_clarification_prompt(
                state.pending_action,
                state.pending_action_summary,
                audience=state.user_role,
            )

        if intent == ApprovalIntent.CANCEL:
            self._log_pending_action_decision(
                state,
                "CANCELLED",
                state.user_id or "user",
            )
            state.phase = ConversationPhase.IDLE
            state.pending_action = None
            state.pending_action_summary = ""
            state.param_collection = {}
            return "Understood. I cancelled the pending action. What would you like me to do next?"

        if intent in (ApprovalIntent.REJECT, ApprovalIntent.NEW_REQUEST):
            self._log_pending_action_decision(
                state,
                "REJECTED",
                state.user_id or "user",
            )
            state.phase = ConversationPhase.IDLE
            state.pending_action = None
            state.pending_action_summary = ""
            state.param_collection = {}
            if intent == ApprovalIntent.NEW_REQUEST:
                return (
                    "Understood. I won't run the pending action.\n\n"
                    + self._process_message(user_message, state, tracker)
                )
            return "Understood. I won't run that action. What would you like me to do instead?"

        updates = self._extract_pending_action_updates(
            user_message,
            state.pending_action or {},
        )
        if updates:
            refreshed_args = dict((state.pending_action or {}).get("args") or {})
            refreshed_args.update(updates)
            return self._refresh_pending_approval_prompt(
                state,
                refreshed_args,
                note="Updated the pending action with your requested changes.",
            )

        if intent != ApprovalIntent.APPROVE:
            return (
                "I couldn't tell yet whether you want to approve, reject, or change the request. "
                "Please reply with **approve**, **reject**, or tell me what you'd like to change."
            )

        action = state.pending_action
        if not action:
            state.phase = ConversationPhase.IDLE
            state.param_collection = {}
            return "There isn't a pending action right now. How can I help?"

        allowed = action.get("authorized_users", [])
        if allowed and state.user_id and state.user_id not in allowed:
            return (
                "You can't approve this action from this account. "
                f"Authorized reviewers: {', '.join(allowed)}"
            )

        rbac_ok, rbac_err = self._check_rbac(state, action.get("tier", "high_risk"))
        if not rbac_ok:
            return rbac_err

        self._log_pending_action_decision(
            state,
            "APPROVED",
            state.user_id or "user",
        )
        state.phase = ConversationPhase.EXECUTING
        action_tool_name = self._canonicalize_tool_name(str(action.get("tool") or ""))
        approval_tool_def = tool_registry.get_tool(action_tool_name)
        action_args = self._inject_user_scope(
            action_tool_name,
            action["args"],
            approval_tool_def,
            state,
        )
        result = tool_registry.execute(action_tool_name, **action_args)
        state.log_tool_call(
            action_tool_name, action_args, result.data, result.success
        )
        # Cleanup param collection on success (Loop Fix)
        if result.success and action_tool_name in ("trigger_workflow", "t4_execute_and_poll"):
            state.clear_param_collection()

        action_summary = state.pending_action_summary
        state.pending_action = None
        state.pending_action_summary = ""
        state.phase = ConversationPhase.IDLE

        if isinstance(result.data, dict) and result.data.get("needs_user_input"):
            workflow_name = str(result.data.get("workflow_name") or action.get("args", {}).get("workflow_name") or "").strip()
            missing_params = result.data.get("missing_params") or []
            if workflow_name and isinstance(missing_params, list):
                self._start_or_update_param_collection(
                    state=state,
                    workflow_name=workflow_name,
                    missing_params=[str(p) for p in missing_params if p],
                    tool_name=action.get("tool", "trigger_workflow"),
                    tool_args=action.get("args", {}),
                    auto_execute=True,
                )
            pretty_items = [(self._prettify_param_name(str(p)), "") for p in missing_params]
            question = self._build_param_request_message(
                workflow_name=workflow_name or "this workflow",
                items=pretty_items,
                intro="I need a few more details.",
            )
            state.add_message("assistant", question)
            return question

        active_issue = tracker.get_active_issue()
        if result.success:
            state.param_collection = {}
            if active_issue:
                tracker.resolve_issue(
                    active_issue.issue_id,
                    f"Approved and executed: {action_summary}",
                )
            state.phase = ConversationPhase.RESOLVED
            return self._format_completion_message(action_tool_name, result.data)

        return self._build_action_failure_response(
            action_tool=action_tool_name,
            action_args=action.get("args") or {},
            error_text=result.error,
            error_data=result.data if isinstance(result.data, dict) else None,
        )

    def _check_rbac(self, state: ConversationState, tier: str) -> tuple[bool, str]:
        """Verify the user's role allows actions of the given risk tier."""
        if not get_runtime_value("RBAC_ENABLED", False):
            return True, ""
            
        role = (state.user_role or "readonly").lower()
        role_ranks = dict(get_runtime_value("ROLE_RANK", {}))
        tier_ranks = dict(get_runtime_value("TIER_RANK", {}))
        role_rank = role_ranks.get(role, 0)
        tier_rank = tier_ranks.get(tier.lower(), 100) # Default to max rank for unknown
        
        if role_rank >= tier_rank:
            return True, ""
            
        min_role = self._get_min_role_for_tier(tier)
        return False, (
            f"Your role '{role}' is insufficient for {tier} actions. "
            f"Minimum role required: {min_role}"
        )

    def _get_min_role_for_tier(self, tier: str) -> str:
        tier_ranks = dict(get_runtime_value("TIER_RANK", {}))
        role_ranks = dict(get_runtime_value("ROLE_RANK", {}))
        tier_rank = tier_ranks.get(tier.lower(), 100)
        # Sort roles by rank to find the smallest rank that satisfies the tier
        roles = sorted(role_ranks.items(), key=lambda x: x[1])
        for role, rank in roles:
            if rank >= tier_rank:
                return role
        return "admin"

    @staticmethod
    def _is_ticket_creation_completion(tool_name: str, data: dict | None = None) -> bool:
        clean_tool = Orchestrator._canonicalize_tool_name(str(tool_name or "").strip()).lower()
        if clean_tool in {"create_support_ticket", "create_incident_ticket"}:
            return True
        if "ticket" in clean_tool and "create" in clean_tool:
            return True

        if not isinstance(data, dict):
            return False

        if any(data.get(key) for key in ("ticket_id", "incident_id", "case_id", "support_ticket_id")):
            return True

        message = str(data.get("message") or data.get("report") or "").strip().lower()
        return "ticket" in message and any(
            phrase in message
            for phrase in ("created", "raised", "logged", "submitted")
        )

    @classmethod
    def _should_skip_completion_suggestions(cls, tool_name: str, data: dict | None = None) -> bool:
        return cls._is_ticket_creation_completion(tool_name, data)

    @staticmethod
    def _is_unsupported_ticket_followup_suggestion(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if "ticket" not in normalized:
            return False
        return any(token in normalized for token in ("status", "update", "updates", "track", "progress"))

    @classmethod
    def _support_ticket_failure_hint(cls, tool_name: str, data: dict | None = None) -> str:
        if cls._is_ticket_creation_completion(tool_name, data):
            return ""
        payload = data if isinstance(data, dict) else {}
        combined = " ".join(
            str(payload.get(key) or "").strip().lower()
            for key in ("error", "message", "hint", "action_required", "report")
        )
        if "support ticket" in combined or "create_support_ticket" in combined:
            return ""
        return (
            "\n\n**Next step:** If you'd like, I can raise a support ticket for this issue "
            "using `create_support_ticket`."
        )

    def _format_completion_message(self, tool_name: str, data: dict) -> str:
        """Create a clean, human-readable summary of the tool result with LLM-generated suggestions."""

        # ── Normalise: MCP tools often serialise their dict return value to a
        # JSON string over the transport layer. Parse it back so the guards below
        # always operate on a dict.
        if isinstance(data, str) and data.strip().startswith("{"):
            try:
                import json as _json
                _parsed = _json.loads(data)
                if isinstance(_parsed, dict):
                    data = _parsed
            except Exception:
                pass

        while isinstance(data, dict):
            nested = None
            for key in ("result", "data", "payload", "response"):
                candidate = data.get(key)
                if isinstance(candidate, dict) and (
                    any(
                        marker in candidate
                        for marker in (
                            "success",
                            "error",
                            "report",
                            "message",
                            "logs",
                            "summary",
                            "status",
                            "agent_name",
                            "agent_state",
                        )
                    )
                    or len(data) == 1
                ):
                    nested = dict(candidate)
                    for meta_key in ("_mcp_content", "_mcp_meta", "_mcp_is_error"):
                        if meta_key in data and meta_key not in nested:
                            nested[meta_key] = data[meta_key]
                    break
            if not nested:
                break
            data = nested

        if isinstance(data, dict) and not any(
            key in data
            for key in (
                "success",
                "error",
                "report",
                "message",
                "logs",
                "summary",
                "status",
                "agent_name",
                "agent_state",
            )
        ):
            for block in data.get("_mcp_content", []) or []:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                raw_text = str(block.get("text", "") or "").strip()
                if not raw_text or raw_text[0] not in "[{":
                    continue
                try:
                    parsed = json.loads(raw_text)
                except Exception:
                    continue
                nested = dict(parsed) if isinstance(parsed, dict) else {"result": parsed}
                for meta_key in ("_mcp_content", "_mcp_meta", "_mcp_is_error"):
                    if meta_key in data and meta_key not in nested:
                        nested[meta_key] = data[meta_key]
                data = nested
                break

        # ── Guard: detect error / not-supported responses and show a proper
        # failure message instead of the "✅ Action Completed" banner.
        if not isinstance(data, dict):
            # If the tool crashed or returned a non-dict result, it's a failure.
            # We don't want to show "Action Completed" for a crash.
            return f"### ⚠️ Action Failed\nThe {tool_name} tool encountered an internal error or returned an invalid response."

        if isinstance(data, dict):
            # Extract standard AE tool failure keys
            tool_error = data.get("error") or data.get("message") if not data.get("success") else None
            agent_state = data.get("agent_state", "")
            
            # If the tool explicitly failed or returned an error key while success is False
            if (tool_error or not data.get("success", True)) and data.get("supported") is not False:
                # Error from a live tool (e.g. agent stopped)
                state_tag = f" (Agent state: **{agent_state}**)" if agent_state else ""
                action_required = data.get("action_required", "")
                action_hint = f"\n\n**Action required:** {action_required}" if action_required else ""
                hint = str(data.get("hint") or "").strip()
                hint_block = f"\n\n**Next step:** {hint}" if hint else ""
                sop = data.get("sop", "")
                sop_block = f"\n\n{sop}" if sop else ""
                detail_lines = []
                if data.get("agent_name"):
                    detail_lines.append(f"**Agent:** {data.get('agent_name')}")
                assigned_agents = data.get("assigned_agents") or []
                if isinstance(assigned_agents, list) and assigned_agents:
                    labels = []
                    for agent in assigned_agents[:5]:
                        if not isinstance(agent, dict):
                            continue
                        name = str(
                            agent.get("agentName")
                            or agent.get("name")
                            or agent.get("agentId")
                            or "Unknown"
                        ).strip()
                        state_value = str(agent.get("agentState") or agent.get("state") or "UNKNOWN").strip().upper()
                        labels.append(f"{name} ({state_value})")
                    if labels:
                        detail_lines.append(f"**Assigned agents:** {', '.join(labels)}")
                if data.get("request_id") or data.get("execution_id"):
                    detail_lines.append(f"**Request ID:** `{data.get('request_id') or data.get('execution_id')}`")
                if data.get("last_status"):
                    detail_lines.append(f"**Server Extraction Status:** {data.get('last_status')}")
                if data.get("waited_seconds"):
                    detail_lines.append(f"**Waited:** {data.get('waited_seconds')} seconds")
                if data.get("zip_file_name"):
                    detail_lines.append(f"**ZIP File:** `{data.get('zip_file_name')}`")
                detail_block = f"\n\n" + "\n".join(detail_lines) if detail_lines else ""
                
                # If we have an error but no success flag, or success is False
                if not tool_error:
                    tool_error = "The tool encountered an operational issue."
                support_ticket_hint = self._support_ticket_failure_hint(tool_name, data)

                return (
                    f"### ⚠️ Unable to Complete Action\n"
                    f"{tool_error}{state_tag}{detail_block}{action_hint}{hint_block}{sop_block}{support_ticket_hint}"
                )
            if data.get("supported") is False:
                # Explicitly unsupported operation
                return (
                    f"### ⚠️ Not Supported\n"
                    f"{data.get('message', 'This operation is not supported by the agent.')}"
                )

        report = data.get("report") if isinstance(data, dict) else None
        if not report and isinstance(data, dict):
            text_blocks = []
            for block in data.get("_mcp_content", []) or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text", "") or "").strip()
                    if text:
                        text_blocks.append(text)
            if text_blocks:
                candidate_report = "\n\n".join(text_blocks).strip()
                if len(candidate_report) > 50:
                    report = candidate_report
        msg = (data.get("message") if isinstance(data, dict) else None) or f"I've successfully completed the {tool_name} action."

        # 🎯 PRIORITY: If the tool provides a detailed report (Markdown), show it CLEANLY.
        # No "Action Completed" banner, no secondary suggestions unless requested.
        if report and len(str(report).strip()) > 50:
            # If the report already has a header, just return it.
            return str(report).strip()

        if report:
            msg = report
        
        details = []
        exec_id = (data.get("execution_id") or data.get("request_id")) if isinstance(data, dict) else None
        if exec_id:
            details.append(f"• **Request ID**: `{exec_id}`")
        
        status = (data.get("status") or data.get("state")) if isinstance(data, dict) else None
        if status:
            details.append(f"• **Status**: {status}")

        workflow = data.get("workflow_name") if isinstance(data, dict) else None
        if workflow:
            details.append(f"• **Workflow**: `{workflow}`")

        response = f"### ✅ Action Completed\n{msg}\n"
        if details:
            response += "\n" + "\n".join(details)

        # Skip suggestions if the message is already long (like a partial report)
        # or if the completed action was ticket creation, where follow-up status/update
        # suggestions are not currently supported.
        if len(response) > 400 or self._should_skip_completion_suggestions(tool_name, data):
            return response

        # Ask the LLM to generate 2 context-aware suggestions for what the user might want to do next.
        # If there's an error/failure, we MUST suggest creating a support ticket.
        try:
            status_prev = str(status or "").lower()
            is_failure = any(term in status_prev for term in ("fail", "error", "abort", "reject", "cancel", "invalid"))

            ticket_tool = "create_support_ticket"
            
            error_instruction = ""
            if is_failure:
                error_instruction = (
                    "CRITICAL: The previous action failed or encountered an error. "
                    f"At least ONE of your suggestions MUST be for creating a support ticket using `{ticket_tool}`. "
                    f"If it's a technical error, suggest an 'Incident' type. If it's a service gap, suggest a 'Request' type."
                )
            else:
                # Even on success, maybe they want to raise a service request?
                error_instruction = (
                    f"If the user might need follow-up assistance, you can suggest using `{ticket_tool}` with a 'Request' type."
                )

            context_summary = f"Tool: {tool_name}. Status: {status or 'unknown'}. Workflow: {workflow or 'unknown'}. Result: {str(msg)[:200]}"
            raw = llm_client.chat(
                (
                    "Based on the following action just completed by an AutomationEdge support agent, "
                    "suggest exactly 2 brief, actionable next steps the user might want to take. "
                    f"{error_instruction}\n"
                    "Do not hardcode workflow names, bot names, agent names, tenant names, org names, or special-case business rules. "
                    "Base every suggestion only on the current tool result and the conversation context provided here. "
                    "Never suggest checking ticket status, checking ticket updates, tracking ticket progress, or any other unsupported ticket follow-up feature unless that capability was explicitly completed or confirmed in this conversation. "
                    "After a ticket is created, do not suggest checking status or updates for that ticket.\n"
                    "Return ONLY 2 bullet lines starting with '- '. No preamble, no explanation.\n\n"
                    f"Context: {context_summary}"
                ),
                system="You are a concise assistant. Output exactly 2 lines, each starting with '- '.",
                temperature=0.7,
                max_tokens=500,
            )
            # Handle both "-" and "*" bullet points
            suggestions = []
            for line in (raw or "").splitlines():
                line = line.strip()
                if line.startswith(("- ", "* ")):
                    suggestions.append(line.lstrip("-* ").strip())
            suggestions = [
                suggestion
                for suggestion in suggestions
                if not self._is_unsupported_ticket_followup_suggestion(suggestion)
            ]
            
            # Defensive fallback if LLM ignored the instruction on failure
            if is_failure and suggestions and not any(term in str(suggestions).lower() for term in ("ticket", "escalat", "incident", "support")):
                # Inject a ticket creation suggestion as a second bullet
                suggestions = [suggestions[0], "Raise a support ticket for further investigation."]

            suggestions = [suggestions[i] for i in range(min(len(suggestions), 2))]
        except Exception:
            suggestions = []

        if suggestions:
            _labels = [
                "**Here are a few options:**",
                "**You might also want to:**",
                "**Suggested next actions:**",
                "**Can I help with anything else?**",
                "**What's your next step?**",
            ]
            label = _labels[hash(tool_name) % len(_labels)]
            response += f"\n\n{label}\n" + "\n".join(f"- {s}" for s in suggestions)

        return response

    @staticmethod
    def _extract_active_tool_names(vertex_tools: list) -> set[str]:
        """Get the set of tool names currently in the Vertex Tool object."""
        names: set[str] = set()
        for tool_obj in vertex_tools:
            if hasattr(tool_obj, "function_declarations"):
                for decl in tool_obj.function_declarations:
                    if hasattr(decl, "name"):
                        names.add(decl.name)
                    elif isinstance(decl, dict):
                        names.add(decl.get("name", ""))
            raw = getattr(tool_obj, "_raw_tool", None)
            if raw and isinstance(raw, dict):
                for decl in raw.get("function_declarations", []):
                    names.add(decl.get("name", ""))
        names.discard("")
        return names

    def _detect_language(self, text: str) -> str:
        """Detect the ISO 639-1 language code of the text using LLM."""
        if not text or len(text.strip()) < 5:
            return "en"
        try:
            prompt = (
                "Detect the language of the following text. "
                "Return ONLY the ISO 639-1 language code (e.g. 'en', 'es', 'fr', 'hi', 'zh'). "
                "If unsure, return 'en'.\n\n"
                f"Text: {cast(Any, text)[:200]}"
            )
            # Use raw chat to avoid recursion
            response = llm_client.chat(prompt, system="You are a language detector.")
            lang = str(response).strip().lower()
            return cast(Any, lang)[:2].replace(".", "") if len(lang) >= 2 else "en"
        except Exception:
            return "en"

    # =====================================================================
    # Prompt building
    # =====================================================================

    def _build_system_prompt(self, state: ConversationState,
                             tracker: IssueTracker) -> str:
        base_prompt = """You are an AutomationEdge operations support agent.
You help investigate and resolve issues with RPA workflows.

Rules:
1. Always verify before acting - never guess based on symptoms alone.
2. Check input files early - 800+ workflows are file-based.
3. When multiple failures exist, trace to the upstream root cause.
4. Adjust detail level based on user role.
5. Every tool call is audited.
6. Read tool descriptions carefully. They may include use/avoid guidance,
   required parameters, and example arguments. Follow those hints exactly.
7. Always prioritize `check_workflow_status` for ANY query about a bot's state, performance, or history. Followed by `get_execution_logs` for deep analysis.
8. If no typed tool fits, use the general-purpose escape hatches:
   - call_ae_api: hit any AE REST endpoint directly
   - query_database: run read-only SQL against the ops database
   - search_knowledge_base: semantic search across all KB collections
9. If none of the above help, call discover_tools to search the full
   catalog by description or category.
10. **CRITICAL: TECHNICAL PRIORITIZATION**. If you call a tool and it returns technical data (workflow instances, logs, agent stats) OR a failure message, you MUST use that live result in your reply. Do NOT provide placeholder SOP instructions if tool data or a specific error (e.g., 'Agent Offline') is available. Prefer the tool's live truth over static Knowledge Base or SOP text provided in the context block. For business users, translate technical findings into plain business language and hide raw technical codes, internal identifiers, and low-level error details unless the user explicitly asks for them.
11. **CRITICAL: NUMERIC ID RULE**. If the user provides a specific numeric request ID, or automation request ID (e.g. "request id 2501865"), you MUST call `get_execution_status` with that exact ID immediately. Do NOT ask for more information. Do NOT generate troubleshooting steps. Call the tool first, then report results. **EXCEPTION: This rule does NOT apply to Schedule IDs — see Rule 12.**
12. **CRITICAL: SCHEDULE OPERATION RULE**. If the user says anything containing "schedule" AND an action word (disable, enable, pause, resume, stop, start, halt, activate, deactivate, turn off, turn on), you MUST call the appropriate schedule tool immediately:
    - "disable / pause / stop / halt / deactivate" → call `ae.schedule.disable` with `schedule_id` from the message
    - "enable / resume / start / activate / turn on" → call `ae.schedule.enable` with `schedule_id` from the message
    - "list schedules / show schedules / all schedules" → call `ae.schedule.list_all`
    Do NOT call `get_execution_status` for schedule operations. Do NOT show SOP steps. Call the schedule tool directly.
13. **CRITICAL: PROACTIVE NEXT STEPS**. Every final response MUST end with 2-3 specific, actionable suggestions. 
    - Use a DIFFERENT heading each time — rotate naturally among: "Here are a few options:", "You might also want to:", "Suggested next actions:", "Can I help with anything else?", "What's your next step?" — NEVER repeat the same heading in consecutive turns.
    - Keep suggestions relevant to the context (e.g., after a failure: offer log analysis; after a restart: offer status monitoring).
    - Match the persona: technical users get tool-specific options; business users get plain-language options.
    - Do NOT hardcode workflow names, bot names, ticket patterns, agent names, tenant names, organization names, or customer-specific rules. Use only the live tool data and the current conversation context.
14. **TERMINOLOGY & STATUS-FIRST RULE**: "Bots" and "Workflows" are synonymous. If a user asks about a bot (even by a "friendly" or "natural language" name like 'Email Bot JD'), you MUST call `check_workflow_status` as your FIRST action unless they explicitly say "run", "start", or "trigger". Never assume the user wants to execute a bot just because they mentioned its name.
15. **PROACTIVE PARAMETER DISCOVERY**: When `discover_tools` returns a workflow with `[ORCHESTRATOR_MAPPING]` in its description:
    - **TECHNICAL MAPPING MANDATE**: You MUST silently cross-reference the required parameters against the conversation history before generating a response.
    - **NO REDUNDANCY**: DO NOT list a parameter in your response if its value is already present in history (even if the user used similar terms like "starts tomorrow" or typos like "lleave").
    - **DECISIVE ACTION**: If the history contains ALL required parameters, you MUST skip the conversational summary and immediately propose or prepare the `trigger_workflow` tool call. Only prompt for the values that are strictly missing.
16. **PROACTIVE DIAGNOSTIC DISCOVERY**: If the user asks for logs, status, or diagnostics but context is missing (like `agent_id` or `execution_id`), you MUST NOT ask the user for it first. Instead, call a discovery tool like `t4_check_agent_status`, `list_recent_failures`, or `ae.agent.analyze_logs` (with agent_id="") to find potential targets.
    - **NUMERIC IDS**: Always use numeric IDs for agents when available. Never guess an ID.
    - **AMBIGUITY RESOLUTION**: If discovery returns exactly one candidate, proceed with the investigation. If multiple are found, list them clearly with their names and IDs and ask the user to choose.
17. **LOG DATE SELECTION RULE**: 
    - **Agent Host Logs (`analyze_agent_logs`)**: When requested for an `agent_id`, you MUST inform the user that logs default to the last 24 hours and ask if they want to specify a particular `from_date` or `to_date` BEFORE performing extraction.
    - **Workflow Execution Logs (`get_execution_logs`)**: When requested for a specific Request/Execution ID, you MUST NOT ask for a time range. These logs represent the entire lifecycle of that specific run and do not require date filters. Call the tool immediately.
18. **GOAL PERSISTENCE**: If you have started a multi-step intent (e.g., creating a ticket, triggering a workflow, or asking for specific details), you MUST maintain that goal as your primary objective in the next turn. If the user's response provides the requested details but also mentions a failure symptom, you SHOULD call the relevant tool (e.g., `create_support_ticket` or `trigger_workflow`) FIRST while acknowledging the symptom. Do NOT abandon the original goal to start a fresh diagnostics discovery unless the user explicitly cancels the request.
19. **STRICT CONTEXT INHERITANCE**: If you previously listed agents, workflows, or IDs (e.g., ID 2887) and the user responds with parameters (like a date range, "yes", or "proceed"), you MUST assume they are referring to the MOST RECENT entity mentioned. NEVER ask "which agent" if only one agent was discussed or listed in the immediate history. Use the `Recent Conversation Context` block provided below as your source of truth.
20. **STRICT PAYLOAD SANITIZATION**: When calling support or ticketing tools (e.g. `create_support_ticket`), you MUST provide `description` and `process_name` as PLAIN TEXT only. Do NOT use double quotes ("), colons (:), underscores (_), or parentheses () inside these parameters. Use spaces or hyphens instead to preserve readability. (Example: "execution_id: 2564846" -> "execution id 2564846").
21. **AGENT STATUS DISCOVERY**: Always use `ae.agent.list_all` for any general agent status query to see all Running, Stopped, and Offline agents.
22. **STRICT AGENT ENFORCEMENT**: You MUST call `ae.agent.list_all` (or `list_running`) to discover numeric IDs and verify `RUNNING` status BEFORE suggesting or triggering any diagnostic action (logs, RDP, etc.). NEVER call diagnostics if the agent is `STOPPED`.
23. **PRECISION ID RESOLUTION**: When calling agent-related tools, always use the numeric `agent_id` (e.g. "2928") resolved from the agent list, rather than the search name (e.g. "vaishnavi.malusare..."), to ensure 100% precision.
24. **RELATED SYSTEM HEALTH CHECKS**: If the user asks for a process status, failure reason, or health check and the failing run may be related to Life Asia connectivity or TEBT login/portal problems, call `check_workflow_status` first. That tool may automatically inspect the latest failure evidence and run the configured related-system health check via admin scope. If the tool response includes a related issue check/result, you MUST surface that result clearly to the user.
25. **MEANINGFUL FIRST-LINE RULE**: Start every final reply with the answer, outcome, or current status. Do NOT open with internal narration such as "This looks related", "I would like to perform", "I'll track it", or raw tool names.
26. **CHAT-NOT-SYSTEM RULE**: Write like a helpful teammate in chat. Avoid raw field dumps such as `workflow_name`, `user_id`, `org_code`, JSON-like parameter blocks, or internal tool names unless the user explicitly needs that level of detail.
27. **APPROVAL & INPUT REQUEST STYLE**: When asking for confirmation or missing information, explain what will happen, why it matters, and what you need from the user in clean, user-friendly language. Avoid robotic approval or parameter-collection wording.
28. **NO EMAIL / LETTER FORMATTING**: Unless the user explicitly asks for an email, memo, or letter, never format a response with subject lines, salutations, sign-offs, placeholder names, or drafted-mail structure.
29. **MINIMUM-NECESSARY FOLLOW-UP RULE**: When you need more information, ask only for the minimum missing detail required to continue and briefly explain why you need it.
Available tool categories: status, logs, file, remediation, dependency,
config, notification, general, meta, agent_read, agent_diag.
You have a subset of tools loaded. Use discover_tools to find others.
FORBIDDEN: Never respond with SOP steps like 'Step 1: Check workflow status...' when the user has given you a specific ID to look up. Call the tool instead.
"""

        persona = ""
        if state.user_role == "business":
            persona = """
## Persona: Business User
Audience: business users, managers, and non-technical stakeholders.
Style requirements:
- First sentence must answer the question directly in plain English.
- Focus on current status, business impact, expected timing, and what happens next.
- Never start with internal tracking language such as "linked issue", "tool", "workflow mapping", or "parameter collection".
- Avoid workflow names, request IDs, execution IDs, error codes, endpoint names, and raw file paths unless the user explicitly asks for them.
- Avoid code-heavy or deeply technical jargon, but you may use simple operational terms when they help, such as "file missing", "system unavailable", "agent not running", or "waiting for another process".
- If there is a problem, explain it in a practical way the user can act on. Focus on what went wrong in simple terms and what the user should do next.
- Do not include code-level details, stack traces, internal error text, or deep technical troubleshooting in normal business replies.
- If a required file is missing from a shared location, explain it simply, for example: "The required file is not available in the shared location. Please check or upload the file."
- If structure helps, use short sections such as "What happened", "What this means", and "Next options".
- Keep suggestions simple, concrete, and non-technical.
- Keep the reply in normal chat format, not as an email, memo, or drafted note.
- If you need approval or more information, explain it in natural chat language, not system language."""
        else:
            persona = """
## Persona: Technical Staff
Audience: operations, support, and IT users.
Style requirements:
- First sentence must answer the question or summarize the outcome clearly.
- Then provide the most useful evidence: workflow, request ID, agent, timestamps, status, and error details when relevant.
- Avoid filler or robotic lead-ins such as "I would like to perform the following action" unless the user explicitly asked for a formal summary.
- Use bullets or short sections only when they improve clarity.
- For approvals or next actions, summarize exactly what will happen, what inputs will be used, and what the operator should confirm.
- Keep the reply in chat format, not as an email, memo, or internal audit note.
- Stay conversational, readable, and precise."""

        issue_context = ""
        if tracker and tracker.issues:
            active = self._get_visible_active_issue(tracker, state)
            visible_summary = self._get_visible_issue_summary(tracker, state)
            issue_context = f"""
## Active Issues in This Session
{visible_summary}

Currently focused issue: {active.issue_id if active else 'None'}

IMPORTANT: Scope your investigation to the currently focused issue."""

        # ── Recent Tool Context (Cross-Turn Memory) ──
        # Inject the most recent tool findings into the prompt so that when a
        # follow-up question arrives (e.g. "explain why this failed"), the agent
        # knows which execution_id or workflow was found moments earlier.
        tool_context = ""
        if state.tool_call_log:
            log_len = len(state.tool_call_log)
            recent_calls = [state.tool_call_log[i] for i in range(max(0, log_len - 4), log_len)]
            # Expanded keys to catch Agent IDs and generic IDs
            interesting_keys = {
                "execution_id", "workflow_name", "workflow", "request_id", 
                "agent_id", "agentid", "uuid", "id", "name"
            }
            extracted: dict[str, str] = {}
            for call in recent_calls:
                result_data = call.get("result") or {}
                
                # Robustify: Handle dict, list, or direct item
                items_to_scan = []
                if isinstance(result_data, list):
                    items_to_scan = result_data
                elif isinstance(result_data, dict):
                    # Check common AE list wrappers
                    items_to_scan = (
                        result_data.get("instances") or 
                        result_data.get("failures") or 
                        result_data.get("agents") or
                        [result_data]
                    )
                else:
                    items_to_scan = [result_data]

                for row in items_to_scan:
                    if not isinstance(row, dict):
                        continue
                    for k, v in row.items():
                        if k.lower() in interesting_keys and v and str(v).strip():
                            # Map aliases to canonical keys for the LLM
                            canonical_k = k.lower()
                            if canonical_k in {"agentid", "uuid"}:
                                canonical_k = "agent_id"
                            
                            # Check for 'id' mapping if the tool name suggests it's an agent tool
                            tool_called = str(call.get("tool") or call.get("tool_name") or "").lower()
                            if canonical_k == "id" and "agent" in tool_called:
                                canonical_k = "agent_id"
                            
                            extracted[canonical_k] = str(v).strip()

            if extracted:
                ctx_lines = "\n".join(f"  - {k}: {v}" for k, v in extracted.items())
                tool_context = f"""
## Recent Tool Findings (from this conversation turn)
The following technical values were found in the most recent tool calls.
Use them directly when investigating instead of asking the user to repeat them:
{ctx_lines}
CRITICAL: If an `execution_id` or `request_id` is listed above and the user asks "why" or "explain", call `get_execution_logs` immediately.
CRITICAL: If an `agent_id` is listed above and the user asks for logs, says "yes/ok", or PROVIDES A DATE RANGE, you MUST call `analyze_agent_logs` with that `agent_id` immediately. Do NOT ask for the agent ID again."""

        # Build the current display time context and matching greeting
        _, _greeting, _now_str, _tz_label = self._get_display_time_context()
        _tz_title = "Indian Standard Time" if _tz_label == "IST" else _tz_label
        time_context = (
            f"\n## Current Date & Time ({_tz_title})\n"
            f"- Date/Time : {_now_str}\n"
            f"- Greeting  : {_greeting}\n"
            "Use the greeting above when the context calls for one (e.g. first turn, opening a conversation). "
            "Do NOT use a different greeting; always derive it from the Date/Time provided above.\n"
        )
        
        lang_instr = f"\n## Response Language: {state.preferred_language.upper()}\n"
        lang_instr += f"IMPORTANT: Respond to the user in {state.preferred_language.upper()} only. Keep internal reasoning (if any) or tool outputs as is, but the final text to the user MUST be in {state.preferred_language.upper()}."

        # ── Recent Conversation Context (Cross-Issue Entity Memory) ──────────
        # This block carries forward named entities (IDs, workflow names, last
        # resolved actions) from the last 5 turns so the LLM doesn't re-ask
        # for values the user already provided — even across issue boundaries.
        recent_context = ""
        try:
            recent_context = state.get_recent_context_summary(n_turns=5)
        except Exception as _rc_err:
            logger.debug("Could not build recent context summary: %s", _rc_err)

        # ── Param Collection Persistence (Memory across turns) ──
        param_hint = ""
        active_issue = self._get_visible_active_issue(tracker, state)
        is_resolved = active_issue.status == IssueStatus.RESOLVED if active_issue else False
        
        if not is_resolved and state.param_collection and state.param_collection.get("workflow_name"):
            wf = state.param_collection.get("workflow_name", "the current workflow")
            collected = dict(state.param_collection.get("collected_params") or {})
            required_list = list(state.param_collection.get("required_params") or [])
            remaining = [p for p in required_list if p not in collected]
            param_hint = f"""
## Active Workflow Parameter Collection
Workflow: {wf}
MANDATORY Parameters (ALL ARE REQUIRED): {required_list}
Collected from Conversation: {json.dumps(collected, indent=2)}
STILL MISSING: {remaining}

CRITICAL RULES:
- FORBIDDEN: Do NOT ask for, mention, or 'fill in' any parameter not in the 'MANDATORY Parameters' list.
- NEVER assume a value for a missing parameter (e.g., do NOT guess leave_type, start_date, or employee_id).
- YOU MUST explicitly ask the user for any parameter listed in 'STILL MISSING'.
- NEVER trigger the workflow if 'STILL MISSING' is not empty.
- If you call `trigger_workflow`, use ONLY the exact keys from the collected dict above.
"""

        parts = [base_prompt, persona]
        if recent_context:
            parts.append(recent_context)
        if param_hint:
            parts.append(param_hint)
        parts.extend([issue_context, tool_context, time_context, lang_instr])
        return "\n".join(parts)

    def _preflight_workflow_param_collection(
        self,
        user_message: str,
        state: ConversationState,
        active_issue=None,
        sop_hits: list[dict] | None = None,
        feedback_agent_id: str = "ops_orchestrator",
    ) -> str | None:
        """For workflow-execution intents, ask for all required params up front.

        This avoids free-text one-by-one prompting when the workflow schema is known.
        """
        msg = (user_message or "").strip()
        if not msg:
            return None

        execution_intent = self._is_execution_request(msg)

        # Only measure similarity from WF_ workflow hits — non-workflow tools
        # like ae.agent.get_details can score higher but are irrelevant here.
        # AE-102: Strictly respect execution intent.
        # If the LLM says NOT_EXECUTE, don't proactively start param collection/blocking.
        if not execution_intent:
            return None

        client = get_ae_client()
        resolved_org = self._state_org_code(state)
        direct_workflow_name = self._resolve_workflow_name_from_message(msg, state)
        hits: list[dict] = []
        if not direct_workflow_name:
            suggestions = []
            suggester = getattr(client, "suggest_cached_workflow_names", None)
            if callable(suggester):
                try:
                    suggestions = suggester(
                        msg,
                        user_id=state.user_id,
                        org_code=resolved_org,
                        require_execute=True,
                        limit=5,
                    )
                except TypeError:
                    suggestions = suggester(
                        msg,
                        user_id=state.user_id,
                        org_code=resolved_org,
                    )

            discover = tool_registry.execute(
                "discover_tools",
                query=msg,
                category="automationedge",
                top_k=5,
                _agent_id=feedback_agent_id,
                user_id=state.user_id,
                org_code=resolved_org,
            )
            state.log_tool_call(
                "discover_tools",
                {
                    "query": msg,
                    "category": "automationedge",
                    "top_k": 5,
                    "user_id": state.user_id,
                    "org_code": resolved_org,
                },
                discover.data,
                discover.success,
            )
            if not discover.success:
                return self._build_workflow_resolution_message(
                    workflow_label=msg,
                    suggestions=suggestions,
                )

            hits = (discover.data or {}).get("tools", [])
            if not isinstance(hits, list):
                hits = []
            return self._build_workflow_resolution_message(
                workflow_label=msg,
                suggestions=suggestions or self._workflow_suggestions_from_hits(hits),
            )

        selected_hit = {
            "workflow_name": direct_workflow_name,
            "name": direct_workflow_name,
            "category": "automationedge",
            "metadata": {
                "source": "automationedge",
                "workflow_name": direct_workflow_name,
            },
            "use_tool": "trigger_workflow",
        }
        workflow_name = direct_workflow_name

        workflow_id, schema = client.get_cached_workflow_info(
            workflow_name,
            user_id=state.user_id,
            org_code=resolved_org,
        )
        if state.user_id or is_execute_enforced():
            if not state.user_id or not workflow_id or not can_execute_workflow(state.user_id, workflow_id, resolved_org):
                return self._build_workflow_resolution_message(
                    workflow_label=workflow_name,
                    suggestions=[],
                )

        hit_meta = selected_hit.get("metadata") or {}
        hit_params = hit_meta.get("parameters") or selected_hit.get("parameters") or {}
        
        # Prefer the cached workflow schema when available. Search/RAG metadata is
        # useful for descriptions, but it can contain extra nested config fields
        # that are not true runtime inputs for the workflow.
        combined_schema: dict[str, dict] = {
            str(p.get("name")).strip(): dict(p)
            for p in (schema or [])
            if isinstance(p, dict) and str(p.get("name") or "").strip()
        }
        has_authoritative_schema = bool(combined_schema)

        def merge_hit_param(name: str, param_schema: dict) -> None:
            clean_name = str(name or "").strip()
            if not clean_name or not isinstance(param_schema, dict):
                return
            if not has_authoritative_schema:
                existing = combined_schema.setdefault(clean_name, {"name": clean_name})
                existing.update(param_schema)
                return

            existing = combined_schema.get(clean_name)
            if not isinstance(existing, dict):
                return
            for field in (
                "description",
                "displayName",
                "displayname",
                "helpText",
                "extension",
                "fileextension",
                "type",
                "uiControlType",
            ):
                value = param_schema.get(field)
                if value not in (None, "", [], {}) and not existing.get(field):
                    existing[field] = value

        if isinstance(hit_params, dict):
            for name, p_schema in hit_params.items():
                merge_hit_param(name, p_schema)
        elif isinstance(hit_params, list):
            for p in hit_params:
                if isinstance(p, dict) and p.get("name"):
                    merge_hit_param(str(p.get("name")).strip(), p)

        # AE-77: Intercept file upload parameters before collection starts.
        # File upload is not supported in agentic chat yet.
        file_params = [
            name for name, p in combined_schema.items()
            if str(p.get("type") or p.get("uiControlType") or "").strip().lower() in {"file", "attachment", "upload"}
        ]
        if file_params:
            logger.info(f"Preflight: Workflow '{workflow_name}' matches but requires file upload ({file_params}). Rejecting.")
            params_str = ", ".join(file_params)
            return (
                f"I've identified that the **{workflow_name}** bot requires a **document upload** "
                f"for the following input(s): `{params_str}`. \n\n"
                "Since document uploading is not supported in the chat interface yet, "
                "please log in to the **AutomationEdge (AE) server** to trigger this bot manually. "
                "I apologize for the inconvenience!"
            )

        required_with_desc: list[tuple[str, str]] = []
        for name, p in combined_schema.items():
            # A parameter is REQUIRED unless it is explicitly marked as optional.
            opt = p.get("optional")
            is_explicitly_optional = (
                opt is True or 
                (isinstance(opt, str) and str(opt).strip().lower() in {"true", "1", "yes", "y"}) or
                p.get("is_optional") is True or
                p.get("required") is False or
                p.get("is_required") is False
            )
            
            if not is_explicitly_optional:
                desc = str(p.get("description") or p.get("displayName") or "").strip()
                required_with_desc.append((name, desc))

        required = [name for name, _ in required_with_desc]
        # No longer need manual fallbacks here as the client handles the 'required by default' model.
        if not required:
            return None

        # ── Proactive Extraction (Fix for "Preflight Blindness") ──
        # Extract params from the current message AND history immediately.
        extracted = self._extract_params_from_user_message(
            user_message=msg,
            param_names=required,
            messages=state.messages
        )
        # Apply normalization/mapping logic here too (Fix for "Preflight Blindness")
        collected = {}
        normalized_required = {self._norm_param_key(p): p for p in required}
        for key, value in extracted.items():
            if value in (None, "", "null", "None"):
                continue
            if key in required:
                collected[key] = str(value).strip()
                continue
            # Try fuzzy mapping via normalization
            mapped = normalized_required.get(self._norm_param_key(key))
            if mapped:
                collected[mapped] = str(value).strip()

        # Validate extensions
        validation_errors = self._validate_parameter_extensions(workflow_name, collected)

        if workflow_name not in state.affected_workflows:
            state.affected_workflows.append(workflow_name)
        if active_issue:
            # keep issue tracking in sync with chosen workflow
            self._get_issue_tracker(state.conversation_id, state.user_id).add_workflow_to_issue(
                active_issue.issue_id, workflow_name
            )

        # Update remaining items for the request message
        remaining_required = [p for p in required if p not in collected]
        
        state.param_collection = {
            "workflow_name": workflow_name,
            "required_params": required,
            "collected_params": collected,
            "execution_tool": str(selected_hit.get("use_tool") or "trigger_workflow"),
            "execution_template": {"workflow_name": workflow_name},
            "matched_tool": selected_hit,
            "auto_execute": False,
        }

        # If everything is already collected, return None to let the main loop proceed
        if not remaining_required:
            logger.info(f"Preflight: All params for {workflow_name} found in history/current message.")
            return None

        # Only list truly missing items
        pretty_items = []
        remaining_with_desc = [combined_schema.get(n) for n in remaining_required if combined_schema.get(n)]
        for p_schema in remaining_with_desc:
            name = p_schema.get("name")
            desc = str(p_schema.get("description") or p_schema.get("displayName") or "").strip()
            p_type = p_schema.get("type") or p_schema.get("uiControlType") or ""
            p_ext = p_schema.get("extension") or ""
            
            pretty_name = self._prettify_param_name(name)
            clean_desc = self._clean_param_description(desc, name)
            
            # Enrich label with type/extension info if relevant
            label = pretty_name
            metadata_hint = []
            if p_type.lower() == "file" or p_ext:
                metadata_hint.append("File Upload")
                if p_ext:
                    ext = str(p_ext).strip()
                    if not ext.startswith("."): ext = "." + ext
                    metadata_hint.append(f"format: {ext}")
            
            if metadata_hint:
                label += f" ({', '.join(metadata_hint)})"
                
            pretty_items.append((label, clean_desc))

        sop_guidance = self._extract_sop_param_hints(
            workflow_name=workflow_name,
            required_params=required,
            sop_hits=sop_hits or [],
        )

        intro = "I can help with that."
        if validation_errors:
            intro = "\n".join(validation_errors) + "\n\n" + intro

        return self._build_param_request_message(
            workflow_name=workflow_name,
            items=pretty_items,
            intro=intro,
            sop_guidance=sop_guidance,
        )

    def _validate_parameter_extensions(self, workflow_name: str, collected: dict) -> list[str]:
        """Validate collected parameters against their required extensions.
        Returns a list of human-readable error messages.
        """
        schema = get_ae_client().get_cached_workflow_parameters(workflow_name)
        errors = []
        for p in schema:
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            required_ext = p.get("extension")
            if not name or not required_ext:
                continue
            
            # Extension might be ".xlsx" or "xlsx"
            ext_suffix = str(required_ext).strip()
            if not ext_suffix.startswith("."):
                ext_suffix = "." + ext_suffix

            value = collected.get(name)
            if not value:
                continue
            
            val_str = str(value).strip()
            # Skip validation for GUIDs (already uploaded to AE)
            # Typically looks like GUID-xxxx-xxxx or just a long UUID-like string
            if val_str.upper().startswith("GUID-") or ("-" in val_str and len(val_str) > 30):
                continue
            
            if not val_str.lower().endswith(ext_suffix.lower()):
                errors.append(
                    f"The parameter '{self._prettify_param_name(name)}' requires a **{ext_suffix}** file. "
                    f"You provided '{val_str}'."
                )
                # Remove from collected so it is asked for again
                if name in collected:
                    del collected[name]
                
        return errors

    @staticmethod
    def _has_explicit_execute_cue(user_message: str) -> bool:
        text = str(user_message or "").strip().lower()
        if not text:
            return False
        if re.search(
            r"\b(trigger|run|start|execute|launch|kick\s+off|submit)\b",
            text,
        ):
            return True

        cue_tokens = {"trigger", "run", "start", "execute", "launch", "submit", "rerun", "retry"}
        tokens = [tok for tok in re.split(r"[^a-z]+", text) if tok]
        for token in tokens:
            for cue in cue_tokens:
                if SequenceMatcher(None, token, cue).ratio() >= 0.84:
                    return True
        return False

    def _is_execution_request(self, user_message: str) -> bool:
        """LLM-based intent check to avoid hardcoded workflow-action keyword lists."""
        heuristic_execute = self._has_explicit_execute_cue(user_message)
        try:
            verdict = llm_client.chat(
                (
                    "Classify the user request intent.\n"
                    "Return exactly one token: EXECUTE or NOT_EXECUTE.\n"
                    f'User message: "{user_message}"'
                ),
                system=(
                    "EXECUTE means user wants to run, create, or trigger a new automation workflow. "
                    "NOT_EXECUTE means the user is asking for logs, status, history, errors, or general help with an EXISTING execution or workflow."
                ),
                temperature=0.0,
                max_tokens=8,
            ).strip().upper()
            if "EXECUTE" in verdict:
                return True
            return heuristic_execute
        except Exception:
            return heuristic_execute

    def _detect_context_switch(
        self,
        user_message: str,
        workflow_name: str,
        missing_params: list[str],
        conversation_messages: list[dict],
    ) -> bool:
        """
        Use the LLM to decide whether the user’s message is a new/unrelated
        request rather than an answer to the pending parameter question.

        Returns True  → context switch detected (suspend param collection).
        Returns False → message is (or might be) a param value (keep collecting).

        Deliberately avoids hard-coded keyword lists so detection is language-
        and phrasing-agnostic.
        """
        context_tail = conversation_messages[-4:] if conversation_messages else []
        history_block = "\n".join(
            f"{m.get('role', 'unknown')}: {str(m.get('content', ''))[:200]}"
            for m in context_tail
        )
        prompt = (
            f"The bot was collecting inputs for a workflow named ‘{workflow_name}’.\n"
            f"It is still waiting for: {', '.join(missing_params)}.\n"
            f"Recent conversation:\n{history_block}\n\n"
            f"The user just said: \"{user_message}\"\n\n"
            "Is the user’s message a COMPLETELY NEW, UNRELATED request or question \n"
            "(i.e. NOT providing the value(s) asked for)?\n"
            "Reply with exactly one word: YES or NO."
        )
        try:
            result = llm_client.chat(
                prompt,
                system=(
                    "You classify user intent during multi-turn parameter collection. "
                    "YES means the user has switched topic. "
                    "NO means the user is still answering the parameter question."
                ),
                temperature=0.0,
                max_tokens=5,
            ).strip().upper()
            is_switch = result.startswith("YES")
            logger.debug(
                "context_switch_check workflow=%s message=%r result=%s",
                workflow_name, user_message[:60], result,
            )
            return is_switch
        except Exception as exc:
            logger.warning("_detect_context_switch LLM call failed: %s", exc)
            return False

    def _continue_param_collection(self, user_message: str, state: ConversationState, tracker: IssueTracker | None = None) -> str | None:
        """Continue multi-turn param collection (ported from code_ref remediation flow)."""
        # ── Handle resume / drop for a previously suspended flow ──
        if state.suspended_flow:
            msg_lower = user_message.strip().lower()
            if msg_lower in {
                "continue", "yes", "resume", "yes continue",
                "pick up", "go back", "proceed",
            }:
                # Restore the suspended flow
                state.param_collection = dict(
                    state.suspended_flow.get("param_collection", {})
                )
                state.clear_suspended_flow()
                logger.info("param_collection_resumed conversation_id=%s", state.conversation_id)
                # Fall through so the next missing param is asked
            elif msg_lower in {
                "drop it", "cancel it", "skip it", "cancel",
                "forget it", "no", "don't",
            }:
                state.clear_suspended_flow()
                state.clear_param_collection()
                return "Understood — I’ve cancelled that request. How else can I help?"

        pc = state.param_collection or {}
        workflow_name = str(pc.get("workflow_name") or "").strip()
        required = [p for p in (pc.get("required_params") or []) if p]
        collected = dict(pc.get("collected_params") or {})
        if not workflow_name or not required:
            return None

        missing = [p for p in required if not collected.get(p)]
        if not missing:
            return None

        # ── LLM-based context switch detection ──
        # If the user is asking something completely unrelated, suspend the
        # param flow so _process_message can answer the new question.
        if self._detect_context_switch(
            user_message, workflow_name, missing, state.messages
        ):
            state.suspended_flow = {
                "type": "param_collection",
                "param_collection": dict(state.param_collection),
                "workflow_name": workflow_name,
            }
            state.clear_param_collection()
            logger.info(
                "param_collection_suspended workflow=%s conversation_id=%s",
                workflow_name, state.conversation_id,
            )
            return None  # let _process_message handle the new question

        extracted = self._extract_params_from_user_message(user_message, missing, state.messages)
        normalized_required = {self._norm_param_key(p): p for p in required}
        for key, value in extracted.items():
            if not value:
                continue
            if key in required:
                collected[key] = str(value).strip()
                continue
            mapped = normalized_required.get(self._norm_param_key(key))
            if mapped:
                collected[mapped] = str(value).strip()

        # Validate extensions
        validation_errors = self._validate_parameter_extensions(workflow_name, collected)

        remaining = [p for p in required if not collected.get(p)]
        state.param_collection = {
            **pc,
            "workflow_name": workflow_name,
            "required_params": required,
            "collected_params": collected,
        }

        if remaining:
            # Build request for missing or invalid items
            pretty_items = []
            # Use combined schema info to get descriptions if possible
            # We already have the workflow_name and remaining list.
            # Look up descriptions from the cached schema for better labeling.
            schema_list = get_ae_client().get_cached_workflow_parameters(workflow_name)
            schema_map = {p.get("name"): p for p in schema_list if isinstance(p, dict) and p.get("name")}
            
            # AE-77: Safety check even in continue phase
            file_params = [
                name for name, p in schema_map.items()
                if str(p.get("type") or p.get("uiControlType") or "").strip().lower() in {"file", "attachment", "upload"}
            ]
            if file_params:
                logger.info(f"Continue: Workflow '{workflow_name}' requires file upload. Rejecting.")
                params_str = ", ".join(file_params)
                return (
                    f"I see that the **{workflow_name}** bot requires a **document upload** "
                    f"for: `{params_str}`. \n\n"
                    "Currently, I cannot process file uploads through this chat. "
                    "Please go to the **AutomationEdge (AE) server** to trigger this bot manually. "
                    "Thank you for your understanding!"
                )
            
            for name in remaining:
                # Always include the parameter even if not in local schema
                p_schema = schema_map.get(name, {})
                p_name = self._prettify_param_name(name)
                desc = p_schema.get("description") or p_schema.get("displayName") or ""
                clean_desc = self._clean_param_description(desc, name)
                
                p_type = p_schema.get("type") or p_schema.get("uiControlType") or ""
                p_ext = p_schema.get("extension") or ""
                
                label = p_name
                metadata_hint = []
                if p_type.lower() == "file" or p_ext:
                    metadata_hint.append("File Upload")
                    if p_ext:
                        ext = str(p_ext).strip()
                        if not ext.startswith("."): ext = "." + ext
                        metadata_hint.append(f"format: {ext}")
                
                if metadata_hint:
                    label += f" ({', '.join(metadata_hint)})"

                pretty_items.append((label, clean_desc))

            intro = "Got it, thanks."
            if validation_errors:
                intro = "\n".join(validation_errors) + "\n\n" + intro

            return self._build_param_request_message(
                workflow_name=workflow_name,
                items=pretty_items,
                intro=intro,
            )

        tool_name = self._canonicalize_tool_name(str(pc.get("execution_tool") or "trigger_workflow"))
        action_args = self._build_action_args_for_collection(pc, workflow_name, collected, state)

        # If this param collection was created after an already-approved action,
        # execute automatically once all params are available.
        if pc.get("auto_execute"):
            result = tool_registry.execute(tool_name, **action_args)
            state.log_tool_call(tool_name, action_args, result.data, result.success)

            if isinstance(result.data, dict) and result.data.get("needs_user_input"):
                missing_again = result.data.get("missing_params") or []
                self._start_or_update_param_collection(
                    state=state,
                    workflow_name=workflow_name,
                    missing_params=[str(p) for p in missing_again if p],
                    tool_name=tool_name,
                    tool_args=action_args,
                    auto_execute=True,
                )
                return result.data.get("question", "I still need a few details.")

            if result.success:
                state.param_collection = {}
                active_issue = tracker.get_active_issue() if tracker else None
                if active_issue and tracker:
                    tracker.resolve_issue(
                        active_issue.issue_id,
                        f"Executed {tool_name} for {workflow_name}",
                    )
                state.phase = ConversationPhase.RESOLVED
                return self._format_completion_message(tool_name, result.data or {})

            return self._build_action_failure_response(
                action_tool=tool_name,
                action_args=action_args,
                error_text=result.error,
                error_data=result.data if isinstance(result.data, dict) else None,
            )

        # Otherwise move to approval flow.
        tool_def = tool_registry.get_tool(tool_name)
        actual_tier = tool_def.tier if tool_def else "medium_risk"
        summary = self._build_pending_action_summary(tool_name, action_args)
        approval_request = self.approval_gate.create_approval_request(
            state.conversation_id,
            tool_name,
            actual_tier,
            action_args,
            summary,
        )
        state.pending_action = {
            "tool": tool_name,
            "args": action_args,
            "tier": actual_tier,
            "authorized_users": [],
            "request_id": approval_request.request_id,
        }
        state.pending_action_summary = summary
        state.phase = ConversationPhase.AWAITING_APPROVAL
        return self.approval_gate.format_approval_prompt(
            approval_request,
            audience=state.user_role,
        )

    def _extract_params_from_user_message(self, user_message: str, param_names: list[str], messages: list[dict] | None = None) -> dict[str, str | None]:
        """LLM-based param extractor inspired by code_ref remediation_agent_extract_params."""
        if not param_names:
            return {}
        
        context_str = ""
        if messages:
            # Get last 5 messages for context without overwhelming the prompt
            m_len = len(messages)
            context_history = [messages[i] for i in range(max(0, m_len - 6), max(0, m_len - 1))] if m_len > 1 else []
            history_lines = []
            for m in context_history:
                role = "User" if m.get("role") == "user" else "Assistant"
                history_lines.append(f"{role}: {m.get('content')}")
            if history_lines:
                context_str = "Conversation History:\n" + "\n".join(history_lines) + "\n\n"

        prompt = (
            f"{context_str}"
            "Extract parameter values from the user message, cross-referencing with the conversation history if provided.\n"
            "CRITICAL: You MUST use the exact keys from the 'Parameters needed' list below for the JSON keys.\n"
            "CRITICAL: Do NOT extract values that are generic or part of the user's intent framing (e.g. do not extract 'leave' from 'i want to apply for leave'). Only extract specific, concrete values provided by the user (e.g. 'sick', 'annual', '2023-12-01').\n"
            "Return valid JSON only.\n\n"
            f"Parameters needed: {param_names}\n"
            f'Current User message: "{user_message}"\n\n'
            'Return format: {"param_name": "value_or_null", ...}'
        )
        try:
            raw = llm_client.chat(
                prompt,
                system="Extract only the listed parameters. Return strict JSON.",
                temperature=0.0,
                max_tokens=512,
            )
            raw_clean = raw.strip()
            if raw_clean.startswith("```"):
                raw_clean = raw_clean.strip("`")
                raw_clean = raw_clean.replace("json", "", 1).strip()
            parsed = json.loads(raw_clean)
            if isinstance(parsed, dict):
                return {
                    str(k): (str(v).strip() if v not in (None, "", "null", "None") else None)
                    for k, v in parsed.items()
                }
            return {}
        except Exception:
            return {}

    @staticmethod
    def _norm_param_key(value: str) -> str:
        txt = "".join(ch for ch in str(value).lower() if ch.isalnum())
        if txt.endswith("s") and len(txt) > 3:
            txt = cast(Any, txt)[:-1]
        return txt

    def _recent_workflow_context_candidates(
        self,
        state: ConversationState,
    ) -> list[str]:
        candidates: list[str] = []

        def add_candidate(value: Any) -> None:
            clean = str(value or "").strip()
            if not clean:
                return
            if clean.lower() in {"unknown", "this workflow", "this bot", "that workflow", "that bot"}:
                return
            if clean not in candidates:
                candidates.append(clean)

        def scan_payload(payload: Any) -> None:
            queue: list[Any] = [payload]
            seen: set[int] = set()

            while queue:
                current = queue.pop(0)
                ident = id(current)
                if ident in seen:
                    continue
                seen.add(ident)

                if isinstance(current, dict):
                    lower_keys = {str(k).lower() for k in current.keys()}
                    if any(key in lower_keys for key in ("workflow_name", "workflowname")):
                        add_candidate(current.get("workflow_name") or current.get("workflowName"))
                    if "workflow" in current and isinstance(current.get("workflow"), str):
                        add_candidate(current.get("workflow"))
                    if "name" in current and any(
                        key in lower_keys for key in ("workflow_id", "workflowid", "workflow_name", "workflowname")
                    ):
                        add_candidate(current.get("name"))

                    workflow_meta = (
                        current.get("workflowConfiguration")
                        or current.get("workflow_configuration")
                        or current.get("workflow")
                    )
                    if isinstance(workflow_meta, dict):
                        add_candidate(
                            workflow_meta.get("name")
                            or workflow_meta.get("workflowName")
                            or workflow_meta.get("workflow_name")
                        )
                        queue.append(workflow_meta)

                    raw_payload = current.get("raw")
                    if isinstance(raw_payload, (dict, list, tuple)):
                        queue.append(raw_payload)

                    for value in current.values():
                        if isinstance(value, (dict, list, tuple)):
                            queue.append(value)
                elif isinstance(current, (list, tuple)):
                    queue.extend(list(current))

        add_candidate((state.param_collection or {}).get("workflow_name"))
        add_candidate((state.suspended_flow or {}).get("workflow_name"))
        pending_action = state.pending_action or {}
        if isinstance(pending_action, dict):
            pending_args = pending_action.get("args") or {}
            if isinstance(pending_args, dict):
                add_candidate(pending_args.get("workflow_name") or pending_args.get("workflow"))

        for workflow_name in reversed(state.affected_workflows or []):
            add_candidate(workflow_name)

        for call in reversed(state.tool_call_log[-10:]):
            if not isinstance(call, dict):
                continue
            scan_payload(call.get("params") or {})
            scan_payload(call.get("result") or {})

        return candidates

    def _resolve_recent_workflow_reference(self, state: ConversationState) -> str:
        client = get_ae_client()
        org_code = self._state_org_code(state)
        user_id = state.user_id

        for candidate in self._recent_workflow_context_candidates(state):
            try:
                resolved = client.resolve_cached_workflow_name(
                    candidate,
                    user_id=user_id,
                    org_code=org_code,
                )
            except TypeError:
                resolved = client.resolve_cached_workflow_name(candidate)
            if resolved:
                return str(resolved).strip()
        return ""

    def _resolve_workflow_name_from_message(
        self,
        user_message: str,
        state: ConversationState,
    ) -> str:
        """Try exact catalog resolution from the user's wording before semantic search."""
        message = str(user_message or "").strip()
        if not message:
            return ""

        client = get_ae_client()
        org_code = self._state_org_code(state)
        user_id = state.user_id

        candidates: list[str] = []
        specificity_candidates: list[str] = []

        def add_candidate(value: str, *, count_for_specificity: bool = True) -> None:
            clean = re.sub(r"\s+", " ", str(value or "").strip(" \t\r\n`'\".,:;!?()[]{}")).strip()
            if clean and clean not in candidates:
                candidates.append(clean)
            if clean and count_for_specificity and clean not in specificity_candidates:
                specificity_candidates.append(clean)

        def is_specific_query(value: str) -> bool:
            checker = getattr(client, "is_specific_workflow_lookup_query", None)
            if callable(checker):
                try:
                    return bool(checker(value))
                except Exception:
                    return False
            return bool(str(value or "").strip())

        add_candidate(message)

        for pattern in (r"`([^`]+)`", r"'([^']+)'", r"\"([^\"]+)\""):
            for match in re.findall(pattern, message):
                add_candidate(match)

        lowered = message.lower()
        stripped = re.sub(
            r"^(please\s+)?(can you\s+)?(could you\s+)?(kindly\s+)?"
            r"(trigger|run|start|execute|launch|kick off|submit|rerun|re-run)\s+",
            "",
            lowered,
        )
        add_candidate(stripped)

        stop_words = {
            "a", "an", "the", "this", "that", "my", "our", "please",
            "bot", "workflow", "process", "job", "agent",
            "trigger", "run", "start", "execute", "launch", "kick", "off",
            "submit", "rerun", "re", "again",
        }
        tokens = [tok for tok in re.split(r"[^a-zA-Z0-9_]+", lowered) if tok]
        filtered = [tok for tok in tokens if tok not in stop_words]
        if filtered:
            add_candidate(" ".join(filtered), count_for_specificity=False)

        for candidate in candidates:
            resolved = client.resolve_cached_workflow_name(
                candidate,
                user_id=user_id,
                org_code=org_code,
            )
            if resolved:
                return resolved

        has_specific_candidate = any(is_specific_query(candidate) for candidate in specificity_candidates)
        if not has_specific_candidate:
            recent_workflow = self._resolve_recent_workflow_reference(state)
            if recent_workflow:
                return recent_workflow

            # If the current turn does not contain a workflow-specific identifier
            # or a compact workflow-like noun phrase, fail closed here. This
            # prevents long follow-up sentences from being semantically remapped
            # to unrelated workflows based on generic nouns buried in the text.
            return ""

        resolver = getattr(client, "resolve_workflow_name_from_text", None)
        if callable(resolver):
            try:
                resolved = resolver(
                    message,
                    user_id=user_id,
                    org_code=org_code,
                    require_execute=True,
                )
            except TypeError:
                resolved = resolver(
                    message,
                    user_id=user_id,
                    org_code=org_code,
                )
            if resolved:
                return str(resolved).strip()

        return ""

    @staticmethod
    def _workflow_suggestions_from_hits(hits: list[dict], limit: int = 5) -> list[str]:
        suggestions: list[str] = []
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
            name = str(
                metadata.get("workflow_name")
                or hit.get("workflow_name")
                or hit.get("name")
                or ""
            ).strip()
            if not name or name in suggestions:
                continue
            suggestions.append(name)
            if len(suggestions) >= limit:
                break
        return suggestions

    @staticmethod
    def _build_workflow_resolution_message(
        *,
        workflow_label: str,
        suggestions: list[str] | None = None,
    ) -> str:
        lines = [
            f"I’m unable to verify **{workflow_label or 'that request'}** as a workflow your account can trigger right now.",
            "",
            "Please select the workflow from your authorized workflow list, or ask me to show the workflows currently available to your account.",
        ]
        if suggestions:
            lines.extend([
                "",
                "Here are a few workflows currently available to your account:",
                *[f"- {name}" for name in suggestions[:5]],
            ])
        return "\n".join(lines)

    def _start_or_update_param_collection(
        self,
        *,
        state: ConversationState,
        workflow_name: str,
        missing_params: list[str],
        tool_name: str,
        tool_args: dict,
        auto_execute: bool,
    ) -> None:
        """Merge workflow params from prior state + latest tool args to avoid loops."""
        existing = state.param_collection or {}
        same_workflow = str(existing.get("workflow_name") or "").strip() == workflow_name
        collected = dict(existing.get("collected_params") or {}) if same_workflow else {}

        schema = get_ae_client().get_cached_workflow_parameters(workflow_name)
        required = [
            p.get("name")
            for p in schema
            if isinstance(p, dict)
            and p.get("name")
            and (
                p.get("required")
                or p.get("is_required")
                or p.get("optional") is False
                or (
                    isinstance(p.get("optional"), str)
                    and p.get("optional").strip().lower() in {"false", "0", "no", "n"}
                )
            )
        ]
        if not required:
            required = list(dict.fromkeys(missing_params))

        # Seed from args payload.
        for carrier in ("parameters", "params"):
            payload = tool_args.get(carrier)
            if isinstance(payload, dict):
                collected.update({k: v for k, v in payload.items() if v not in (None, "", {}, [])})

        for p in required:
            if tool_args.get(p) not in (None, "", {}, []):
                collected[p] = tool_args.get(p)

        # Also mine the recent conversation for already-known values so we do not
        # ask the user to repeat inputs that were just discussed.
        latest_user_message = ""
        for msg in reversed(state.messages):
            if str(msg.get("role") or "") == "user" and str(msg.get("content") or "").strip():
                latest_user_message = str(msg.get("content") or "").strip()
                break
        extracted = self._extract_params_from_user_message(
            user_message=latest_user_message,
            param_names=required,
            messages=state.messages,
        )
        normalized_required = {self._norm_param_key(p): p for p in required}
        for key, value in extracted.items():
            if value in (None, "", "null", "None"):
                continue
            if key in required and key not in collected:
                collected[key] = str(value).strip()
                continue
            mapped = normalized_required.get(self._norm_param_key(key))
            if mapped and mapped not in collected:
                collected[mapped] = str(value).strip()

        state.param_collection = {
            "workflow_name": workflow_name,
            "required_params": required,
            "collected_params": collected,
            "execution_tool": self._canonicalize_tool_name(tool_name or "trigger_workflow"),
            "execution_template": tool_args or {},
            "auto_execute": bool(auto_execute),
        }

    def _build_action_args_for_collection(
        self,
        pc: dict,
        workflow_name: str,
        collected: dict,
        state: ConversationState,
    ) -> dict:
        tool_name = self._canonicalize_tool_name(str(pc.get("execution_tool") or "trigger_workflow"))
        template = dict(pc.get("execution_template") or {})
        org_code = self._state_org_code(state)

        if tool_name == "t4_execute_and_poll":
            args = {
                "workflow_name": workflow_name,
                "workflow_id": (
                    template.get("workflow_id")
                    or get_ae_client().get_cached_workflow_id(
                        workflow_name,
                        user_id=state.user_id,
                        org_code=org_code,
                    )
                    or workflow_name
                ),
                "params": {},
            }
            if isinstance(template.get("params"), dict):
                args["params"].update(template.get("params"))
            args["params"].update(collected)
            if state.user_id:
                args["user_id"] = state.user_id
            if org_code:
                args["org_code"] = org_code
            return args

        # Default path for trigger_workflow and most execution tools.
        args = {
            "workflow_name": workflow_name,
            "parameters": {},
        }
        if isinstance(template.get("parameters"), dict):
            args["parameters"].update(template.get("parameters"))
        args["parameters"].update(collected)
        if state.user_id:
            args["user_id"] = state.user_id
        if org_code:
            args["org_code"] = org_code
        return args

    def _format_rag_context(self, tool_hits, kb_hits, sop_hits, incident_hits=None) -> str:
        sections = []
        # Feature: Prioritize tools so LLM sees them as the primary action source
        if tool_hits:
            tool_text = "\n".join(
                f"- {str(h.get('content', ''))}" for h in [tool_hits[i] for i in range(min(len(tool_hits), 5))]
            )
            sections.append(f"## Relevant Tools\n{tool_text}")
        if kb_hits:
            kb_text = "\n".join(
                f"- {str(h.get('content', ''))}" for h in [kb_hits[i] for i in range(min(len(kb_hits), 3))]
            )
            sections.append(f"## Knowledge Base\n{kb_text}")
        if sop_hits:
            sop_text = "\n".join(
                f"- {str(h.get('content', ''))}" for h in [sop_hits[i] for i in range(min(len(sop_hits), 3))]
            )
            sections.append(f"## SOPs\n{sop_text}")
        if incident_hits:
            inc_text = "\n".join(
                f"- {str(h.get('content', ''))}" for h in [incident_hits[i] for i in range(min(len(incident_hits), 3))]
            )
            sections.append(f"## Past Incidents & Resolutions\n{inc_text}")
        return "\n\n".join(sections) if sections else ""

    def _filter_for_persona(self, response: str,
                            state: ConversationState) -> str:
        if state.user_role != "business":
            return response
        text = str(response or "").strip()
        if not text:
            return response

        lowered = text.lower()
        technical_markers = (
            "workflow",
            "request id",
            "execution id",
            "error code",
            "stack trace",
            "agent id",
            "schedule id",
            "exception",
            "failed because",
            "api",
            "endpoint",
            "risk level",
            "parameters:",
            "workflow_name",
            "workflow_id",
            "user_id",
            "org_code",
            "t4_execute_and_poll",
            "trigger_workflow",
            "linked issue",
        )
        if not any(marker in lowered for marker in technical_markers):
            return response

        try:
            rewrite_prompt = (
                "Rewrite this for a non-technical business user. "
                "Remove workflow names, request IDs, error codes, and deep technical terms. "
                "Focus on impact, timing, status, and next actions. "
                "Use simple, clear business language that is easy to understand. "
                "Avoid code-heavy or deeply technical wording, but you may use simple operational terms when useful, such as 'file missing', 'system unavailable', 'agent not running', or 'waiting for another process'. "
                "Do not include code-related details, stack traces, raw internal errors, or backend troubleshooting steps unless the user explicitly asked for them. "
                "If a file is missing from a shared path or shared location, explain it simply like: 'The required file is not available in the shared location. Please check or upload the file.' "
                "For any issue, explain what the business user needs to do next instead of focusing on system internals. "
                "Do not shorten the response unnecessarily. "
                "Keep the response complete and well-structured. "
                "Start with one clear sentence that answers the user's question or states the current status. "
                "Replace awkward internal phrases like 'linked issue', 'tool', 'risk level', 'parameter collection', or raw field names with normal business language. "
                "If helpful, use at most three short sections such as 'What happened', 'What this means', and 'Next options'. "
                "Sound like a helpful chatbot, not an internal system note. "
                "CRITICAL: This is a chatbot reply, not an email, memo, or letter. "
                "Do NOT add a subject line, greeting line, salutation, sign-off, placeholder name, or email-style sections. "
                "Write as a direct conversational chat response only. "
                "CRITICAL: Keep the 'Next Steps' or 'What would you like to do next?' section, but ensure the suggestions themselves are also non-technical "
                "(e.g., 'Should I check the overall system health?' instead of 'Check agent CPU logs').\n\n"
                f"Original Response:\n{response}"
            )
            filtered = llm_client.chat(
                rewrite_prompt,
                system=(
                    "Rewrite technical text for business audiences while preserving proactive calls to action. "
                    "Never format the answer as an email, memo, letter, or template unless the user explicitly asked for that."
                ),
                max_tokens=32000,
            )
            filtered_text = str(filtered or "").strip()
            if self._looks_like_email_or_memo(filtered_text):
                filtered = llm_client.chat(
                    "Convert the following into a normal chatbot reply. "
                    "Remove any subject line, salutation, placeholder name, sign-off, and memo or email formatting. "
                    "Keep it conversational, direct, and business-friendly.\n\n"
                    f"Text:\n{filtered_text}",
                    system="You convert drafted email-style text into natural chatbot responses.",
                    max_tokens=32000,
                )
            return filtered
        except Exception:
            return response

    @staticmethod
    def _looks_like_email_or_memo(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        markers = (
            "subject:",
            "dear ",
            "hi [",
            "hi team",
            "hello team",
            "regards,",
            "best regards",
            "sincerely,",
            "action needed",
        )
        if any(marker in lowered for marker in markers):
            return True
        if "status & impact:" in lowered and "what happened:" in lowered:
            return True
        return False

    @staticmethod
    def _prettify_param_name(name: str) -> str:
        text = str(name or "").strip().replace("_", " ")
        if text.lower() == "emp id":
            return "employee ID"
        return text

    @staticmethod
    def _clean_param_description(desc: str, name: str) -> str:
        value = str(desc or "").strip()
        if not value:
            return ""
        if value.lower() in {"none", "null", "n/a", "na"}:
            return ""
        if value.strip().lower() == str(name or "").strip().lower():
            return ""
        return value

    def _build_param_request_message(
        self,
        *,
        workflow_name: str,
        items: list[tuple[str, str]],
        intro: str = "",
        sop_guidance: list[str] | None = None,
    ) -> str:
        """LLM-based natural prompting inspired by the provided reference code."""
        friendly_wf = self._humanize_workflow_name(workflow_name)
        if not items:
            prefix = f"{intro.strip()} " if intro and intro.strip() else ""
            return f"{prefix}I'm ready to continue with {friendly_wf}."

        single_item = len(items) == 1
        param_details = "\n".join([f"- {label}: {desc}" if desc else f"- {label}" for label, desc in items])
        
        sop_str = ""
        if sop_guidance:
            sop_str = "\nGuidelines from SOPs:\n" + "\n".join([f"- {g}" for g in [sop_guidance[i] for i in range(min(len(sop_guidance), 3))]])

        prompt = (
            f"You are a helpful RPA automation assistant. You are helping the user with: '{friendly_wf}'.\n"
            f"{intro}\n\n"
            "Start with one clear sentence explaining what is needed to continue.\n"
            "Ask only for the missing required user inputs in a warm, conversational tone.\n"
            "Sound professional, friendly, and direct.\n"
            "Do not sound robotic or like a system form.\n"
            "Never ask whether the user wants to provide anything.\n"
            "Never use the words parameter, parameters, JSON, payload, API, or values.\n"
            "Do not mention optional inputs.\n"
            "If exactly one item is missing, ask it as a direct natural question.\n"
            "If multiple items are missing, ask for them directly in a short list.\n"
            "If a description is provided, use it to clarify the expected format.\n"
            "End with a short encouraging line.\n\n"
            f"Required Information:\n{param_details}\n{sop_str}\n\n"
            "Write the message now:"
        )

        try:
            # Using temperature 0.4 for a slightly more natural/varied but still professional feel
            response = llm_client.chat(
                prompt,
                system="You are a warm, helpful automation assistant providing a premium experience.",
                temperature=0.4,
                max_tokens=32000
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"LLM prompting failed: {e}. Falling back to static template.")
            prefix = f"{intro.strip()} " if intro and intro.strip() else ""
            if single_item:
                label, desc = items[0]
                msg = f"{prefix}To continue with {friendly_wf}, I just need {label}."
                if desc:
                    msg += f" Please share {desc[0].lower() + desc[1:] if desc[:1].isupper() else desc}."
                msg += " Once you share it, I'll take it from there."
            else:
                lines = [f"- {label}: {desc}" if desc else f"- {label}" for label, desc in items]
                msg = (
                    f"{prefix}To continue with {friendly_wf}, please share:\n"
                    + "\n".join(lines)
                    + "\n\nOnce you send these, I'll keep things moving."
                )
            if sop_guidance:
                msg += "\n\nHelpful guidance:\n" + "\n".join(f"- {g}" for g in [sop_guidance[i] for i in range(min(len(sop_guidance), 3))])
            return msg

    @staticmethod
    def _humanize_workflow_name(workflow_name: str) -> str:
        name = str(workflow_name or "").strip()
        if name.upper().startswith("WF_"):
            name = str(name)[3:] if len(str(name)) > 3 else ""
        name = name.replace("_", " ").replace("-", " ").strip()
        return name.title() if name else "this workflow"

    def _extract_sop_param_hints(
        self,
        *,
        workflow_name: str,
        required_params: list[str],
        sop_hits: list[dict],
    ) -> list[str]:
        """Extract concise SOP-backed hints tied to required params."""
        if not sop_hits or not required_params:
            return []

        wf_norm = workflow_name.lower()
        hints: list[str] = []
        required_norm = {self._norm_param_key(p): p for p in required_params}

        for hit in [sop_hits[i] for i in range(min(len(sop_hits), 3))]:
            content = str(hit.get("content") or "")
            if not content:
                continue
            lower = content.lower()
            # Prefer SOPs clearly related to this workflow when detectable.
            if wf_norm not in lower and "leave" not in lower and "payslip" not in lower and "employee" not in lower:
                continue
            for raw_line in content.splitlines():
                line = raw_line.strip(" -*\t")
                if not line:
                    continue
                line_lower = line.lower()
                # Keep only lines that likely guide input formatting or required values.
                if not any(tok in line_lower for tok in ("format", "date", "required", "must", "value", "start", "end", "id", "type")):
                    continue
                for p_norm, p_name in required_norm.items():
                    if p_norm and p_norm in self._norm_param_key(line_lower):
                        cleaned = line.strip()
                        if cleaned and cleaned not in hints:
                            hints.append(cleaned)
                        break

        return [hints[i] for i in range(min(len(hints), 3))]

    def _build_action_failure_response(
        self,
        *,
        action_tool: str,
        action_args: dict,
        error_text: str,
        error_data: dict | None = None,
    ) -> str:
        """Natural fallback message with SOP-guided steps."""
        if isinstance(error_data, dict) and error_data:
            if any(
                key in error_data
                for key in (
                    "error",
                    "report",
                    "message",
                    "request_id",
                    "agent_name",
                    "agent_state",
                    "last_status",
                    "waited_seconds",
                )
            ):
                return self._format_completion_message(action_tool, error_data)

        workflow_name = str(
            (action_args or {}).get("workflow_name")
            or (action_args or {}).get("workflow")
            or ""
        ).strip()
        wf_label = self._humanize_workflow_name(workflow_name) if workflow_name else "this request"

        if self._is_support_ticket_tool(action_tool):
            title = str((action_args or {}).get("title") or "Support Incident").strip()
            if self._canonicalize_tool_name(action_tool) == "create_support_ticket":
                title = str((action_args or {}).get("process_name") or title).strip()
            guidance = self._get_sop_troubleshooting_steps(f"{title} {error_text}")
            msg = (
                "I couldn't reach the incident system automatically, but I can still help you resolve this.\n"
                f"Issue: {title}"
            )
            if guidance:
                msg += "\n\nRecommended troubleshooting steps:\n" + "\n".join(f"- {g}" for g in guidance)
            msg += (
                "\n\nIf you'd like, I can retry ticket creation or prepare an escalation summary for manual handoff."
            )
            return msg

        guidance = self._get_sop_troubleshooting_steps(f"{workflow_name or ''} {error_text}")
        
        if error_text:
            msg = error_text.strip()
        else:
            msg = f"I couldn't complete the action for **{wf_label}** automatically."

        normalized_error = error_text.lower() if error_text else ""
        if (
            action_tool in ("restart_execution", "resubmit_execution")
            and "completed" in normalized_error
        ):
            return (
                f"{msg}\n\n"
                "Restart and resubmit are only available for failed executions. "
                "Use Fresh Run to run this workflow again."
            )
            
        # Only add generic guidance if the error is short/generic AND doesn't look like a formal API rejection.
        # Specific errors like 'agent offline' or 'malicious code' don't need generic SOP steps.
        is_formal_rejection = any(k in normalized_error for k in ("malicious", "unauthorized", "connection", "not found", "offline"))
        if guidance and (not error_text or len(error_text) < 50) and not is_formal_rejection:
            msg += "\n\nRecommended troubleshooting steps:\n" + "\n".join(f"- {g}" for g in guidance)
            
        msg += "\n\nIf you'd like, I can retry this, create a support ticket, or help escalate it."
        return msg

    @staticmethod
    def _get_recent_completed_execution_context(state: ConversationState) -> dict:
        for call in reversed(state.tool_call_log[-10:]):
            result = call.get("result") or {}
            if not isinstance(result, dict):
                continue

            status = str(result.get("status") or result.get("state") or "").upper()
            error_text = str(result.get("error") or result.get("message") or "").lower()
            hint_text = str(result.get("hint") or "").lower()
            workflow_name = str(
                result.get("workflow_name")
                or (call.get("params") or {}).get("workflow_name")
                or ""
            ).strip()
            execution_id = str(
                result.get("execution_id")
                or result.get("request_id")
                or (call.get("params") or {}).get("execution_id")
                or (call.get("params") or {}).get("request_id")
                or ""
            ).strip()

            is_completed_guard = (
                status == "COMPLETED"
                or (
                    "completed" in error_text
                    and (
                        "trigger a new execution instead" in (error_text + " " + hint_text)
                        or "fresh run" in (error_text + " " + hint_text)
                        or "use fresh run" in (error_text + " " + hint_text)
                    )
                )
            )
            if is_completed_guard and workflow_name:
                return {
                    "workflow_name": workflow_name,
                    "execution_id": execution_id,
                }

        return {}

    @staticmethod
    def _extract_requested_execution_id(user_message: str, tool_args: dict) -> str:
        args = tool_args or {}
        for key in ("execution_id", "request_id", "id"):
            value = str(args.get(key) or "").strip()
            if value and value.isdigit():
                return value

        message = str(user_message or "")
        match = re.search(r"\b\d{4,}\b", message)
        return match.group(0) if match else ""

    def _build_fresh_run_parameters(
        self,
        *,
        workflow_name: str,
        source_execution_id: str,
        state: ConversationState,
    ) -> dict:
        clean_workflow = str(workflow_name or "").strip()
        clean_execution_id = str(source_execution_id or "").strip()
        if not clean_workflow:
            return {}

        try:
            schema = get_ae_client().get_cached_workflow_parameters(clean_workflow)
        except Exception as exc:
            logger.debug("Could not load workflow schema for fresh-run trigger %s: %s", clean_workflow, exc)
            return {}

        required = [
            p.get("name")
            for p in schema
            if isinstance(p, dict)
            and p.get("name")
            and (
                p.get("required")
                or p.get("is_required")
                or p.get("optional") is False
                or (
                    isinstance(p.get("optional"), str)
                    and p.get("optional").strip().lower() in {"false", "0", "no", "n"}
                )
            )
        ]
        if not required:
            return {}

        extraction_message = str(state.messages[-1].get("content") or "").strip() if state.messages else ""
        if clean_execution_id:
            extraction_message = (
                f"{extraction_message}\nKnown completed execution ID: {clean_execution_id}"
                if extraction_message
                else f"Known completed execution ID: {clean_execution_id}"
            )

        extracted = self._extract_params_from_user_message(
            user_message=extraction_message,
            param_names=[str(name) for name in required if name],
            messages=state.messages,
        )
        params = {}
        normalized_required = {self._norm_param_key(p): p for p in required if p}
        for key, value in extracted.items():
            if value in (None, "", "null", "None"):
                continue
            if key in required:
                params[key] = str(value).strip()
                continue
            mapped = normalized_required.get(self._norm_param_key(key))
            if mapped:
                params[mapped] = str(value).strip()

        return params

    def _rewrite_completed_execution_followup(
        self,
        *,
        user_message: str,
        state: ConversationState,
        tool_name: str,
        tool_args: dict,
    ) -> tuple[str, dict]:
        clean_tool = str(tool_name or "").strip()
        if (
            not clean_tool
            or (
                "resubmit" not in clean_tool.lower()
                and "restart" not in clean_tool.lower()
            )
        ):
            return clean_tool, tool_args

        recent = self._get_recent_completed_execution_context(state)
        workflow_name = str(recent.get("workflow_name") or "").strip()
        if not workflow_name:
            return clean_tool, tool_args

        requested_execution_id = self._extract_requested_execution_id(user_message, tool_args)
        recent_execution_id = str(recent.get("execution_id") or "").strip()
        if (
            requested_execution_id
            and recent_execution_id
            and requested_execution_id != recent_execution_id
        ):
            logger.info(
                "Skipping completed-execution rewrite for %s because requested execution=%s differs from recent completed execution=%s",
                clean_tool,
                requested_execution_id,
                recent_execution_id,
            )
            return clean_tool, tool_args

        logger.info(
            "Rewriting completed-execution follow-up from %s to trigger_workflow for workflow=%s execution=%s",
            clean_tool,
            workflow_name,
            recent_execution_id,
        )
        return (
            "trigger_workflow",
            {
                "workflow_name": workflow_name,
                "parameters": self._build_fresh_run_parameters(
                    workflow_name=workflow_name,
                    source_execution_id=recent_execution_id,
                    state=state,
                ),
            },
        )

    def _get_sop_troubleshooting_steps(self, query: str) -> list[str]:
        """Extract short SOP-like action steps for user-facing recovery guidance."""
        try:
            rag = get_rag_engine()
            hits = rag.search_sops(query, top_k=3)
        except Exception:
            return []

        steps: list[str] = []
        for hit in hits:
            content = str(hit.get("content") or "")
            for raw in content.splitlines():
                line = raw.strip(" -*\t")
                if not line:
                    continue
                lower = line.lower()
                if not any(k in lower for k in ("check", "verify", "ensure", "restart", "retry", "validate", "confirm", "collect", "contact")):
                    continue
                if len(line) < 18:
                    continue
                if line not in steps:
                    steps.append(line)
                if len(steps) >= 3:
                    return steps
        return steps

    @staticmethod
    @staticmethod
    def _should_use_sop_fallback(tool_hits: list[dict], sop_hits: list[dict], threshold: float = 0.008) -> bool:
        """Use SOP fallback when no strong tool match exists.

        NOTE: tool_hits use RRF (Reciprocal Rank Fusion) scores, NOT cosine similarity.
        RRF scores are typically in the range 0.01–0.05, not 0.0–1.0.
        Threshold is calibrated for RRF: 0.008 ≈ tool ranked in top-5 of 60.
        """
        if not sop_hits:
            return False
        best_tool_similarity = 0.0
        for hit in tool_hits or []:
            try:
                best_tool_similarity = max(
                    best_tool_similarity,
                    float(hit.get("rrf_score", hit.get("similarity", 0.0)) or 0.0),
                )
            except Exception:
                continue
        logger.debug(
            "SOP fallback check: best_tool_score=%.4f threshold=%.4f sop_hits=%d",
            best_tool_similarity, threshold, len(sop_hits),
        )
        if best_tool_similarity < 0.001:
            # RAG found nothing useful at all.
            # Only fall back to SOP if we have a moderately high-confidence SOP hit.
            best_sop_similarity = 0.0
            for hit in sop_hits or []:
                best_sop_similarity = max(best_sop_similarity, float(hit.get("rrf_score", hit.get("similarity", 0.0)) or 0.0))
            logger.debug(
                "Tool RAG score near-zero. best_sop_score=%.4f (threshold > 0.003)",
                best_sop_similarity,
            )
            return best_sop_similarity > 0.003

        return best_tool_similarity < threshold

    def _build_friendly_sop_fallback_response(self, user_message: str, sop_hits: list[dict]) -> str:
        """Provide polished SOP-based guidance for user-facing fallback responses."""
        steps = self._get_sop_troubleshooting_steps(user_message)
        if not steps:
            for hit in [sop_hits[i] for i in range(min(len(sop_hits), 3))]:
                content = str(hit.get("content") or "")
                for raw in content.splitlines():
                    line = raw.strip(" -*\t")
                    if not line:
                        continue
                    if len(line) < 18:
                        continue
                    if any(
                        key in line.lower()
                        for key in ("check", "verify", "ensure", "restart", "retry", "validate", "contact")
                    ):
                        steps.append(line)
                    if len(steps) >= 3:
                        break
                if len(steps) >= 3:
                    break

        if steps:
            return (
                "I couldn't find a direct automation action for this request yet, but these steps should help:\n"
                + "\n".join(f"- {step}" for step in [steps[i] for i in range(min(len(steps), 3))])
                + "\n\nIf you'd like, I can also create a support ticket or help narrow this down further."
            )

        return (
            "I couldn't match this request to a direct action or a strong SOP yet. "
            "Please share one more detail, such as the system name, error message, or workflow name, and I'll guide you from there."
        )

    def _build_sop_fallback_response(self, user_message: str, sop_hits: list[dict]) -> str:
        """Provide direct SOP-based guidance when tool coverage is missing."""
        steps = self._get_sop_troubleshooting_steps(user_message)
        if not steps:
            # Secondary parse directly from provided SOP hits.
            for hit in [sop_hits[i] for i in range(min(len(sop_hits), 3))]:
                content = str(hit.get("content") or "")
                for raw in content.splitlines():
                    line = raw.strip(" -*\t")
                    if not line:
                        continue
                    if len(line) < 18:
                        continue
                    if any(k in line.lower() for k in ("check", "verify", "ensure", "restart", "retry", "validate", "contact")):
                        steps.append(line)
                    if len(steps) >= 3:
                        break
                if len(steps) >= 3:
                    break

        if steps:
            return (
                "I couldn’t find a direct automation tool for this request, but here are SOP-based steps to resolve it:\n"
                + "\n".join(f"- {s}" for s in [steps[i] for i in range(min(len(steps), 3))])
                + "\n\nIf you want, I can also create an incident ticket for follow-up."
            )

        return (
            "I couldn’t find a matching tool or a strong SOP for this request yet. "
            "Please share one more detail (system name, error message, or workflow name), and I’ll guide you step-by-step."
        )

    def _sanitize_ticket_args(self, args: dict) -> dict:
        """Strip all special characters from ticket arguments to avoid WAF rejections."""
        import re
        def _clean(v):
            if not isinstance(v, str): return v
            # Replace underscores, colons, slashes and hyphens with spaces
            v = v.replace("_", " ").replace(":", " ").replace("/", " ").replace("\\", " ").replace("-", " ")
            # Strip everything else except alphanumeric and space
            return " ".join(re.sub(r'[^a-zA-Z0-9 ]', '', v).split())

        new_args = dict(args)
        if "process_name" in new_args:
            new_args["process_name"] = _clean(new_args["process_name"])
        if "description" in new_args:
            new_args["description"] = _clean(new_args["description"])
        if "request_type" in new_args:
            new_args["request_type"] = _clean(new_args["request_type"])
        return new_args
