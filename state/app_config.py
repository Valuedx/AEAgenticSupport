"""
Persisted application control-center configuration.

This store keeps admin-editable settings in a JSON file so the app can be
managed through UI controls instead of direct code edits. Environment
variables remain the source of truth for secrets.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

from config.settings import CONFIG


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_lines_or_csv(value: str) -> list[str]:
    text = str(value or "").replace("\r", "\n")
    raw_parts: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "," in line:
            raw_parts.extend(part.strip() for part in line.split(","))
        else:
            raw_parts.append(line)
    return [part for part in raw_parts if part]


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = _split_lines_or_csv(value)
    else:
        items = []
    clean = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        clean.append(text)
    return clean


def _normalize_int_map(value: Any) -> dict[str, int]:
    pairs: dict[str, int] = {}
    if isinstance(value, dict):
        source = value.items()
    elif isinstance(value, str):
        lines = _split_lines_or_csv(value)
        source = []
        for line in lines:
            if "=" in line:
                key, raw_val = line.split("=", 1)
            elif ":" in line:
                key, raw_val = line.split(":", 1)
            else:
                continue
            source.append((key, raw_val))
    else:
        source = []

    for raw_key, raw_val in source:
        key = str(raw_key or "").strip()
        if not key:
            continue
        try:
            pairs[key] = int(raw_val)
        except (TypeError, ValueError):
            continue
    return pairs


SECTION_ORDER = [
    "workspace",
    "operations_policy",
    "approval_policy",
    "monitoring",
    "integrations",
]


DEFAULT_SECTIONS: dict[str, dict[str, Any]] = {
    "workspace": {
        "adminConsoleTitle": "Operations Control Center",
        "adminConsoleSubtitle": (
            "Adjust the words, safeguards, and operational defaults that "
            "power the support assistant without editing code."
        ),
        "documentationTitle": "Operations Knowledge Library",
        "documentationSubtitle": (
            "Share setup guides, reference notes, and implementation playbooks "
            "with support teams and business stakeholders."
        ),
        "assistantName": "AutomationEdge Ops Agent",
        "technicalRoleLabel": "Operations / IT",
        "businessRoleLabel": "Business user",
        "inputPlaceholder": "Describe what is blocked, delayed, or failing...",
        "technicalWelcomeMessage": (
            "The assistant is ready. Share the workflow, symptom, or request ID "
            "you want investigated."
        ),
        "businessWelcomeMessage": (
            "The assistant is ready. Describe the business issue and it will "
            "guide you in plain language."
        ),
        "quickActions": [
            "Policy_Renewal_Batch workflow is failing",
            "Check system health",
            "What workflows are having issues?",
            "status",
        ],
    },
    "operations_policy": {
        "maxAgentIterations": int(CONFIG.get("MAX_AGENT_ITERATIONS", 15)),
        "maxKnowledgeMatches": int(CONFIG.get("MAX_RAG_TOOLS", 12)),
        "staleIssueMinutes": int(CONFIG.get("STALE_ISSUE_MINUTES", 30)),
        "recurrenceEscalationThreshold": int(
            CONFIG.get("RECURRENCE_ESCALATION_THRESHOLD", 3)
        ),
        "protectedWorkflows": list(CONFIG.get("PROTECTED_WORKFLOWS", [])),
        "approvalSignals": ["approve", "reject", "yes", "no", "go ahead", "proceed"],
        "continueSignals": [
            "same workflow",
            "same one",
            "related to that",
            "on the same topic",
            "regarding that",
            "about that",
            "for that same",
            "going back to",
        ],
        "newIssueSignals": [
            "different issue",
            "new problem",
            "something else",
            "unrelated",
            "separate issue",
            "by the way",
            "changing topic",
            "another issue",
            "on a different note",
        ],
        "recurrenceSignals": [
            "happened again",
            "same error again",
            "still failing",
            "back again",
            "recurring",
            "keeps failing",
            "not fixed",
            "failed again",
            "same issue",
            "it's back",
        ],
        "followupSignals": [
            "did it work",
            "is it fixed",
            "did the restart work",
            "how did it go",
            "any update",
            "what happened after",
            "is it running now",
            "did it complete",
        ],
        "statusCheckSignals": [
            "what's the status",
            "status update",
            "where are we",
            "any progress",
            "how's it going",
            "current status",
            "what's happening",
            "status check",
            "show status",
            "case status",
            "all cases",
            "open cases",
        ],
        "cancelSignals": [
            "cancel",
            "never mind",
            "nevermind",
            "stop",
            "forget it",
            "forget about it",
            "don't bother",
            "abort",
            "scratch that",
            "disregard",
        ],
        "progressMinIntervalSeconds": 3,
    },
    "approval_policy": {
        "rbacEnabled": bool(CONFIG.get("RBAC_ENABLED", True)),
        "roleRank": dict(CONFIG.get("ROLE_RANK", {})),
        "tierRank": dict(CONFIG.get("TIER_RANK", {})),
        "safeTiers": ["read_only"],
        "autoApproveTiers": ["low_risk"],
        "approvalRequiredTiers": ["medium_risk", "high_risk"],
    },
    "monitoring": {
        "enableProactiveMonitoring": bool(
            CONFIG.get("ENABLE_PROACTIVE_MONITORING", True)
        ),
        "healthCheckIntervalSeconds": int(
            CONFIG.get("HEALTH_CHECK_INTERVAL_SECONDS", 300)
        ),
        "enableDailySummary": bool(CONFIG.get("ENABLE_DAILY_SUMMARY", True)),
        "dailySummaryHour": int(CONFIG.get("DAILY_SUMMARY_HOUR", 8)),
        "monitoredWorkflows": list(CONFIG.get("MONITORED_WORKFLOWS", [])),
        "sessionTtlDays": int(CONFIG.get("SESSION_TTL_DAYS", 30)),
        "escalationMaxAttempts": 3,
        "defaultEscalationTier": "L2",
        "defaultTicketPriority": "P3",
    },
    "integrations": {
        "aeBaseUrl": str(CONFIG.get("AE_BASE_URL", "")).strip(),
        "aeRestBasePath": str(CONFIG.get("AE_REST_BASE_PATH", "/aeengine/rest")).strip(),
        "aeDefaultUserId": str(CONFIG.get("AE_DEFAULT_USERID", "ops_agent")).strip(),
        "aeTimeoutSeconds": int(CONFIG.get("AE_TIMEOUT_SECONDS", 30)),
        "toolGatewayUrl": str(CONFIG.get("TOOL_BASE_URL", "http://localhost:9999")).strip(),
        "cognibotBaseUrl": str(CONFIG.get("COGNIBOT_BASE_URL", "http://localhost:3978")).strip(),
        "googleCloudLocation": str(CONFIG.get("GOOGLE_CLOUD_LOCATION", "us-central1")).strip(),
        "vertexAiModel": str(CONFIG.get("VERTEX_AI_MODEL", "gemini-2.0-flash")).strip(),
        "embeddingModel": str(CONFIG.get("EMBEDDING_MODEL", "text-embedding-004")).strip(),
    },
}


SECTION_SCHEMAS: dict[str, dict[str, Any]] = {
    "workspace": {
        "id": "workspace",
        "title": "User Experience",
        "summary": (
            "Control the names, welcome text, and quick actions that business "
            "and technical users see in chat and in the admin workspace."
        ),
        "saveHint": "Applies immediately to the web chat and control center.",
        "requiresRestart": False,
        "fields": [
            {"key": "adminConsoleTitle", "label": "Control center title", "type": "text", "help": "Main heading shown to administrators."},
            {"key": "adminConsoleSubtitle", "label": "Control center intro", "type": "textarea", "help": "Short, business-friendly explanation shown under the title."},
            {"key": "documentationTitle", "label": "Documentation page title", "type": "text", "help": "Heading shown in the public documentation experience."},
            {"key": "documentationSubtitle", "label": "Documentation page intro", "type": "textarea", "help": "Short explanation shown to people browsing reference documents."},
            {"key": "assistantName", "label": "Assistant name", "type": "text", "help": "Displayed in chat headers and welcome messages."},
            {"key": "technicalRoleLabel", "label": "Technical audience label", "type": "text", "help": "Friendly name for technical users in chat selectors."},
            {"key": "businessRoleLabel", "label": "Business audience label", "type": "text", "help": "Friendly name for business users in chat selectors."},
            {"key": "inputPlaceholder", "label": "Chat input hint", "type": "text", "help": "Prompt shown before the user starts typing."},
            {"key": "technicalWelcomeMessage", "label": "Technical welcome message", "type": "textarea", "help": "Shown when a technical user opens the chat."},
            {"key": "businessWelcomeMessage", "label": "Business welcome message", "type": "textarea", "help": "Shown when a business user opens the chat."},
            {"key": "quickActions", "label": "Quick action prompts", "type": "string_list", "help": "One prompt per line. These become the quick-start buttons in chat."},
        ],
    },
    "operations_policy": {
        "id": "operations_policy",
        "title": "Operations Rules",
        "summary": (
            "Set investigation depth, protected workflows, and the language the "
            "assistant uses to recognize follow-ups, status checks, and new issues."
        ),
        "saveHint": "Applies immediately to new conversations.",
        "requiresRestart": False,
        "fields": [
            {"key": "maxAgentIterations", "label": "Maximum investigation steps", "type": "number", "help": "How many tool or reasoning cycles the assistant can use before it wraps up."},
            {"key": "maxKnowledgeMatches", "label": "Maximum knowledge results", "type": "number", "help": "How many knowledge or tool matches can be surfaced per turn."},
            {"key": "staleIssueMinutes", "label": "Issue stale timeout (minutes)", "type": "number", "help": "When an inactive issue should be treated as stale."},
            {"key": "recurrenceEscalationThreshold", "label": "Repeat failure escalation threshold", "type": "number", "help": "How many recurrences are tolerated before escalation becomes likely."},
            {"key": "protectedWorkflows", "label": "Protected workflows", "type": "string_list", "help": "One workflow name per line. These always require explicit approval."},
            {"key": "approvalSignals", "label": "Approval phrases", "type": "string_list", "help": "Words or phrases that mean the user is approving an action."},
            {"key": "continueSignals", "label": "Continue-the-same-issue phrases", "type": "string_list", "help": "Signals that a user is still talking about the same issue."},
            {"key": "newIssueSignals", "label": "New issue phrases", "type": "string_list", "help": "Signals that the user has changed to a new topic or problem."},
            {"key": "recurrenceSignals", "label": "Recurrence phrases", "type": "string_list", "help": "Signals that a previously resolved issue has returned."},
            {"key": "followupSignals", "label": "Follow-up phrases", "type": "string_list", "help": "Signals that the user is checking whether the prior fix worked."},
            {"key": "statusCheckSignals", "label": "Status check phrases", "type": "string_list", "help": "Signals that the user wants a progress update rather than a new action."},
            {"key": "cancelSignals", "label": "Cancel phrases", "type": "string_list", "help": "Signals that the user wants to stop or abandon the current request."},
            {"key": "progressMinIntervalSeconds", "label": "Minimum progress update gap (seconds)", "type": "number", "help": "How frequently long-running investigations can send progress updates."},
        ],
    },
    "approval_policy": {
        "id": "approval_policy",
        "title": "Approvals And Access",
        "summary": (
            "Set who can approve risky actions and how the assistant classifies "
            "read-only, low-risk, medium-risk, and high-risk work."
        ),
        "saveHint": "Applies immediately to new approval checks.",
        "requiresRestart": False,
        "fields": [
            {"key": "rbacEnabled", "label": "Require role-based approval checks", "type": "boolean", "help": "If turned off, the assistant will skip role-rank enforcement."},
            {"key": "safeTiers", "label": "Always safe tiers", "type": "string_list", "help": "Risk tiers that never require approval."},
            {"key": "autoApproveTiers", "label": "Auto-approve tiers", "type": "string_list", "help": "Risk tiers that are automatically allowed for authorized users."},
            {"key": "approvalRequiredTiers", "label": "Approval required tiers", "type": "string_list", "help": "Risk tiers that should always pause for explicit approval."},
            {"key": "roleRank", "label": "Role rank matrix", "type": "map_number", "help": "Enter one role per line using role=rank. Higher numbers mean more authority."},
            {"key": "tierRank", "label": "Risk tier matrix", "type": "map_number", "help": "Enter one tier per line using tier=rank. Higher numbers require stronger authority."},
        ],
    },
    "monitoring": {
        "id": "monitoring",
        "title": "Monitoring And Automation",
        "summary": (
            "Configure proactive checks, daily summaries, escalation defaults, "
            "and housekeeping windows."
        ),
        "saveHint": "Applies immediately to newly scheduled or evaluated work.",
        "requiresRestart": False,
        "fields": [
            {"key": "enableProactiveMonitoring", "label": "Enable proactive monitoring", "type": "boolean", "help": "Allows the scheduler to poll health and workflow signals automatically."},
            {"key": "healthCheckIntervalSeconds", "label": "Health check interval (seconds)", "type": "number", "help": "How often the monitoring scheduler should run its health checks."},
            {"key": "enableDailySummary", "label": "Enable daily summary", "type": "boolean", "help": "Creates a once-daily operational summary if the scheduler is active."},
            {"key": "dailySummaryHour", "label": "Daily summary hour", "type": "number", "help": "24-hour clock value used for the daily summary task."},
            {"key": "monitoredWorkflows", "label": "Monitored workflows", "type": "string_list", "help": "Workflows that should be watched proactively for failures or delays."},
            {"key": "sessionTtlDays", "label": "Session retention (days)", "type": "number", "help": "How long inactive session state is kept before cleanup."},
            {"key": "escalationMaxAttempts", "label": "Escalation after failed attempts", "type": "number", "help": "Number of unsuccessful attempts before the assistant should lean toward escalation."},
            {"key": "defaultEscalationTier", "label": "Default escalation team", "type": "enum", "options": ["L1", "L2", "L3", "BUSINESS"], "help": "Fallback team when a more specific escalation target is not identified."},
            {"key": "defaultTicketPriority", "label": "Default escalation priority", "type": "enum", "options": ["P1", "P2", "P3", "P4"], "help": "Default ticket priority used for escalations created by the assistant."},
        ],
    },
    "integrations": {
        "id": "integrations",
        "title": "Integrations",
        "summary": (
            "Manage non-secret connection settings for AutomationEdge, the tool "
            "gateway, and AI services. Secrets remain in environment variables."
        ),
        "saveHint": "New requests pick these values up immediately. Secret rotation still belongs in environment or secret storage.",
        "requiresRestart": False,
        "fields": [
            {"key": "aeBaseUrl", "label": "AutomationEdge base URL", "type": "text", "help": "Root URL for the AutomationEdge platform."},
            {"key": "aeRestBasePath", "label": "AutomationEdge REST path", "type": "text", "help": "Base path used for REST requests after the host name."},
            {"key": "aeDefaultUserId", "label": "Default AutomationEdge user ID", "type": "text", "help": "Used when no end-user identity is provided."},
            {"key": "aeTimeoutSeconds", "label": "AutomationEdge timeout (seconds)", "type": "number", "help": "How long the app waits for AutomationEdge before timing out."},
            {"key": "toolGatewayUrl", "label": "Tool gateway URL", "type": "text", "help": "Base URL for the external tool gateway if one is used."},
            {"key": "cognibotBaseUrl", "label": "AI Studio Cognibot URL", "type": "text", "help": "Base URL for the Cognibot Direct Line service used by the AI Studio chat page."},
            {"key": "googleCloudLocation", "label": "Google Cloud location", "type": "text", "help": "Region used for Vertex AI requests."},
            {"key": "vertexAiModel", "label": "Vertex AI chat model", "type": "text", "help": "Model used for orchestration and answer generation."},
            {"key": "embeddingModel", "label": "Embedding model", "type": "text", "help": "Model used when indexing and searching knowledge collections."},
        ],
    },
}


RUNTIME_VALUE_MAP: dict[str, tuple[str, str]] = {
    "AE_BASE_URL": ("integrations", "aeBaseUrl"),
    "AE_REST_BASE_PATH": ("integrations", "aeRestBasePath"),
    "AE_DEFAULT_USERID": ("integrations", "aeDefaultUserId"),
    "AE_TIMEOUT_SECONDS": ("integrations", "aeTimeoutSeconds"),
    "TOOL_BASE_URL": ("integrations", "toolGatewayUrl"),
    "COGNIBOT_BASE_URL": ("integrations", "cognibotBaseUrl"),
    "GOOGLE_CLOUD_LOCATION": ("integrations", "googleCloudLocation"),
    "VERTEX_AI_MODEL": ("integrations", "vertexAiModel"),
    "EMBEDDING_MODEL": ("integrations", "embeddingModel"),
    "MAX_AGENT_ITERATIONS": ("operations_policy", "maxAgentIterations"),
    "MAX_RAG_TOOLS": ("operations_policy", "maxKnowledgeMatches"),
    "STALE_ISSUE_MINUTES": ("operations_policy", "staleIssueMinutes"),
    "RECURRENCE_ESCALATION_THRESHOLD": ("operations_policy", "recurrenceEscalationThreshold"),
    "PROTECTED_WORKFLOWS": ("operations_policy", "protectedWorkflows"),
    "RBAC_ENABLED": ("approval_policy", "rbacEnabled"),
    "ROLE_RANK": ("approval_policy", "roleRank"),
    "TIER_RANK": ("approval_policy", "tierRank"),
    "ENABLE_PROACTIVE_MONITORING": ("monitoring", "enableProactiveMonitoring"),
    "HEALTH_CHECK_INTERVAL_SECONDS": ("monitoring", "healthCheckIntervalSeconds"),
    "ENABLE_DAILY_SUMMARY": ("monitoring", "enableDailySummary"),
    "DAILY_SUMMARY_HOUR": ("monitoring", "dailySummaryHour"),
    "MONITORED_WORKFLOWS": ("monitoring", "monitoredWorkflows"),
    "SESSION_TTL_DAYS": ("monitoring", "sessionTtlDays"),
}


def _normalize_field(field: dict[str, Any], value: Any) -> Any:
    field_type = field.get("type", "text")
    if field_type in {"text", "textarea"}:
        return str(value or "").strip()
    if field_type == "number":
        return int(value)
    if field_type == "boolean":
        return _normalize_bool(value)
    if field_type == "string_list":
        return _normalize_string_list(value)
    if field_type == "map_number":
        return _normalize_int_map(value)
    if field_type == "enum":
        text = str(value or "").strip()
        options = set(field.get("options", []))
        if text not in options:
            raise ValueError(f"{field['label']} must be one of: {', '.join(sorted(options))}")
        return text
    return value


def _normalize_section(section_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    schema = SECTION_SCHEMAS.get(section_name)
    if not schema:
        raise KeyError(section_name)
    normalized = deepcopy(DEFAULT_SECTIONS[section_name])
    field_map = {field["key"]: field for field in schema.get("fields", [])}
    for key, value in (payload or {}).items():
        field = field_map.get(key)
        if not field:
            continue
        normalized[key] = _normalize_field(field, value)
    return normalized


class AppConfigStore:
    """File-backed store for admin-editable application settings."""

    def __init__(self, path: str | None = None):
        raw_path = path or CONFIG.get(
            "APP_CONTROL_CENTER_PATH", "state/app_control_center.json"
        )
        self.path = Path(raw_path)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parent.parent / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if self.path.exists():
            return
        payload = {"version": 1, "updatedAt": _utc_now_iso(), "sections": {}}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "updatedAt": _utc_now_iso(), "sections": {}}

    def _save(self, payload: dict[str, Any]) -> None:
        payload["updatedAt"] = _utc_now_iso()
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_schema(self) -> list[dict[str, Any]]:
        return [deepcopy(SECTION_SCHEMAS[name]) for name in SECTION_ORDER]

    def get_all_sections(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            payload = self._load()
            stored = payload.get("sections", {}) or {}
        sections: dict[str, dict[str, Any]] = {}
        for name in SECTION_ORDER:
            current = deepcopy(DEFAULT_SECTIONS[name])
            override = stored.get(name, {})
            if isinstance(override, dict):
                current.update(override)
            sections[name] = current
        return sections

    def get_section(self, section_name: str) -> dict[str, Any]:
        all_sections = self.get_all_sections()
        if section_name not in all_sections:
            raise KeyError(section_name)
        return all_sections[section_name]

    def update_section(self, section_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_section(section_name, payload)
        with self._lock:
            current = self._load()
            sections = current.setdefault("sections", {})
            sections[section_name] = normalized
            self._save(current)
        return normalized

    def reset_section(self, section_name: str) -> dict[str, Any]:
        if section_name not in DEFAULT_SECTIONS:
            raise KeyError(section_name)
        with self._lock:
            current = self._load()
            sections = current.setdefault("sections", {})
            sections.pop(section_name, None)
            self._save(current)
        return deepcopy(DEFAULT_SECTIONS[section_name])


_app_config_store: AppConfigStore | None = None


def get_app_config_store() -> AppConfigStore:
    global _app_config_store
    if _app_config_store is None:
        _app_config_store = AppConfigStore()
    return _app_config_store


def get_runtime_value(key: str, default: Any = None) -> Any:
    section_field = RUNTIME_VALUE_MAP.get(key)
    if not section_field:
        return CONFIG.get(key, default)
    section_name, field_name = section_field
    try:
        section = get_app_config_store().get_section(section_name)
    except Exception:
        return CONFIG.get(key, default)
    if field_name in section:
        return section.get(field_name)
    return CONFIG.get(key, default)


def get_workspace_ui_config() -> dict[str, Any]:
    return get_app_config_store().get_section("workspace")


def get_classification_signal_groups() -> dict[str, list[str]]:
    section = get_app_config_store().get_section("operations_policy")
    return {
        "approval": list(section.get("approvalSignals", [])),
        "continue": list(section.get("continueSignals", [])),
        "new_issue": list(section.get("newIssueSignals", [])),
        "recurrence": list(section.get("recurrenceSignals", [])),
        "followup": list(section.get("followupSignals", [])),
        "status_check": list(section.get("statusCheckSignals", [])),
        "cancel": list(section.get("cancelSignals", [])),
    }


def get_progress_min_interval() -> float:
    section = get_app_config_store().get_section("operations_policy")
    try:
        return float(section.get("progressMinIntervalSeconds", 3))
    except (TypeError, ValueError):
        return 3.0


def get_approval_tier_sets() -> dict[str, set[str]]:
    section = get_app_config_store().get_section("approval_policy")
    return {
        "safe": set(section.get("safeTiers", [])),
        "auto": set(section.get("autoApproveTiers", [])),
        "required": set(section.get("approvalRequiredTiers", [])),
    }


def get_monitoring_overrides() -> dict[str, Any]:
    return get_app_config_store().get_section("monitoring")


def get_public_chat_config() -> dict[str, Any]:
    section = get_workspace_ui_config()
    cognibot_ready = bool(
        str(CONFIG.get("COGNIBOT_DIRECTLINE_SECRET", "")).strip()
        and str(get_runtime_value("COGNIBOT_BASE_URL", "")).strip()
    )
    return {
        "assistantName": section.get("assistantName", DEFAULT_SECTIONS["workspace"]["assistantName"]),
        "technicalRoleLabel": section.get("technicalRoleLabel", DEFAULT_SECTIONS["workspace"]["technicalRoleLabel"]),
        "businessRoleLabel": section.get("businessRoleLabel", DEFAULT_SECTIONS["workspace"]["businessRoleLabel"]),
        "inputPlaceholder": section.get("inputPlaceholder", DEFAULT_SECTIONS["workspace"]["inputPlaceholder"]),
        "technicalWelcomeMessage": section.get("technicalWelcomeMessage", DEFAULT_SECTIONS["workspace"]["technicalWelcomeMessage"]),
        "businessWelcomeMessage": section.get("businessWelcomeMessage", DEFAULT_SECTIONS["workspace"]["businessWelcomeMessage"]),
        "quickActions": list(section.get("quickActions", [])),
        "aistudioConfigured": cognibot_ready,
    }


def get_public_docs_config() -> dict[str, Any]:
    section = get_workspace_ui_config()
    defaults = DEFAULT_SECTIONS["workspace"]
    return {
        "documentationTitle": section.get(
            "documentationTitle", defaults["documentationTitle"]
        ),
        "documentationSubtitle": section.get(
            "documentationSubtitle", defaults["documentationSubtitle"]
        ),
    }
