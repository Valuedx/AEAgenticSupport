"""
Helpers for loading and querying client-specific related application mappings.

The retry health gate should stay generic in code. Client/application-specific
markers and health-check workflow names belong in configuration.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from config.llm_client import llm_client
from config.settings import CONFIG

logger = logging.getLogger("ops_agent.related_applications")


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
    return normalized


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return text.strip("_")


def _normalize_application_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    label = str(item.get("label") or item.get("issue_label") or "").strip()
    app_id = str(item.get("id") or item.get("issue_type") or "").strip()
    if not app_id and label:
        app_id = _slugify(label)
    if not label and app_id:
        label = app_id.replace("_", " ").replace("-", " ").title()
    if not app_id or not label:
        return None

    workflow_name = str(
        item.get("health_check_workflow")
        or item.get("workflow_name")
        or ""
    ).strip()
    if not workflow_name:
        return None

    markers = _normalize_text_list(
        item.get("markers")
        or item.get("aliases")
        or [label]
    )
    contexts = _normalize_text_list(
        item.get("contexts")
        or item.get("context_keywords")
        or []
    )
    workflow_names = _normalize_text_list(
        item.get("workflow_names")
        or item.get("dependent_workflows")
        or []
    )

    return {
        "issue_type": app_id,
        "issue_label": label,
        "health_check_label": str(
            item.get("health_check_label") or f"{label} health check"
        ).strip(),
        "health_check_workflow": workflow_name,
        "markers": markers,
        "contexts": contexts,
        "workflow_names": workflow_names,
    }


def _load_registry_payload() -> dict[str, Any]:
    raw_json = str(CONFIG.get("RELATED_APPLICATIONS_CONFIG_JSON") or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            return parsed if isinstance(parsed, dict) else {"applications": parsed}
        except Exception as exc:
            logger.warning("Could not parse RELATED_APPLICATIONS_CONFIG_JSON: %s", exc)

    raw_path = str(CONFIG.get("RELATED_APPLICATIONS_CONFIG_PATH") or "").strip()
    if not raw_path:
        return {}

    config_path = Path(raw_path)
    if not config_path.is_absolute():
        config_path = _project_root() / raw_path
    if not config_path.exists():
        logger.warning("Related applications config file not found: %s", config_path)
        return {}

    try:
        parsed = json.loads(config_path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {"applications": parsed}
    except Exception as exc:
        logger.warning("Could not read related applications config %s: %s", config_path, exc)
        return {}


def get_related_applications() -> list[dict[str, Any]]:
    payload = _load_registry_payload()
    applications = payload.get("applications")
    if not isinstance(applications, list):
        applications = []

    normalized: list[dict[str, Any]] = []
    for item in applications:
        entry = _normalize_application_entry(item)
        if entry:
            normalized.append(entry)

    return normalized


def get_related_application(issue_type: str) -> dict[str, Any] | None:
    clean_issue_type = str(issue_type or "").strip().lower()
    if not clean_issue_type:
        return None
    for item in get_related_applications():
        if str(item.get("issue_type") or "").strip().lower() == clean_issue_type:
            return item
    return None


def _normalize_phrase(text: str) -> str:
    normalized = (
        str(text or "")
        .strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
    )
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _extract_json(raw: str):
    text = str(raw or "").strip()
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


def _candidate_related_applications_for_query(query_name: str) -> list[dict[str, Any]]:
    normalized_query = _normalize_phrase(query_name)
    if not normalized_query:
        return []

    candidates: list[dict[str, Any]] = []
    for item in get_related_applications():
        for marker in item.get("markers") or []:
            marker_text = _normalize_phrase(str(marker))
            if not marker_text:
                continue
            if marker_text in normalized_query:
                candidates.append(dict(item))
                break
    return candidates


def classify_direct_related_application_query(query_name: str) -> dict[str, Any]:
    candidates = _candidate_related_applications_for_query(query_name)
    if not candidates:
        return {
            "decision": "not_alias",
            "issue_type": "",
            "confidence": 0.0,
            "reason": "no_candidate_markers",
            "candidates": [],
            "related_application": None,
        }

    prompt_candidates = [
        {
            "issue_type": str(item.get("issue_type") or "").strip(),
            "issue_label": str(item.get("issue_label") or "").strip(),
            "markers": [str(value) for value in (item.get("markers") or []) if str(value).strip()][:6],
        }
        for item in candidates
    ]
    prompt = (
        "Classify whether this operations query is referring to one configured related application alias.\n"
        f"Candidate applications: {json.dumps(prompt_candidates, ensure_ascii=True)}\n\n"
        f"User query: {query_name}\n\n"
        "Decision rules:\n"
        "- alias: the user is referring to the application label itself using generic terms like status, workflow, process, bot, or application.\n"
        "- not_alias: the user is naming a specific workflow/process beyond the application label, or the query is about something else.\n"
        "- uncertain: you cannot decide safely.\n\n"
        "Return JSON with fields: decision, issue_type, confidence, reason.\n"
        "decision must be one of: alias, not_alias, uncertain.\n"
        "issue_type must be one of the provided candidate issue_type values when decision is alias, otherwise use an empty string."
    )
    system = (
        "You classify short operations queries against a provided related-application registry. "
        "Use only the provided candidates. "
        "Respond only with a JSON object."
    )

    try:
        raw = llm_client.chat(prompt, system=system, temperature=0.0, max_tokens=220)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("LLM did not return a JSON object")

        decision = str(data.get("decision", "")).strip().lower()
        if decision not in {"alias", "not_alias", "uncertain"}:
            decision = "uncertain"

        issue_type = str(data.get("issue_type", "")).strip().lower()
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        related_application = None
        if decision == "alias" and issue_type:
            related_application = next(
                (
                    dict(item)
                    for item in candidates
                    if str(item.get("issue_type") or "").strip().lower() == issue_type
                ),
                None,
            )
            if related_application is None:
                decision = "uncertain"
                issue_type = ""

        return {
            "decision": decision,
            "issue_type": issue_type,
            "confidence": confidence,
            "reason": str(data.get("reason", "")).strip(),
            "candidates": candidates,
            "related_application": related_application,
        }
    except Exception as exc:
        logger.warning(
            "Related application alias classification failed for %r: %s",
            query_name,
            exc,
        )
        return {
            "decision": "uncertain",
            "issue_type": "",
            "confidence": 0.0,
            "reason": "classifier_error",
            "candidates": candidates,
            "related_application": None,
        }

def find_matching_related_application(
    text: str,
    *,
    workflow_name: str = "",
) -> dict[str, Any] | None:
    lowered = str(text or "").strip().lower()
    clean_workflow_name = str(workflow_name or "").strip().lower()
    if not lowered and not clean_workflow_name:
        return None

    best_match: tuple[int, int, dict[str, Any]] | None = None
    for item in get_related_applications():
        markers = [str(value).lower() for value in item.get("markers") or [] if str(value).strip()]
        contexts = [str(value).lower() for value in item.get("contexts") or [] if str(value).strip()]
        workflow_names = [str(value).lower() for value in item.get("workflow_names") or [] if str(value).strip()]

        marker_hits = [value for value in markers if lowered and value in lowered]
        context_hits = [value for value in contexts if lowered and value in lowered]
        workflow_hits = [value for value in workflow_names if clean_workflow_name and value == clean_workflow_name]
        if not marker_hits and not workflow_hits:
            continue

        confidence = 3 if context_hits else 2 if marker_hits else 1
        specificity = max((len(value) for value in marker_hits), default=0)
        candidate = (confidence, specificity, item)
        if best_match is None or candidate[:2] > best_match[:2]:
            best_match = candidate

    return dict(best_match[2]) if best_match else None
