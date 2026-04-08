from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from config.settings import CONFIG

logger = logging.getLogger("ops_agent.client_policy")


def _policy_dir() -> Path:
    configured = str(CONFIG.get("CLIENT_POLICY_DIR") or "").strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / configured
        return path
    return Path(__file__).resolve().parent / "client_policies"


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _read_policy_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to read client policy file %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _extract_candidate_ids(org_code: str = "", metadata: dict[str, Any] | None = None) -> list[str]:
    md = metadata if isinstance(metadata, dict) else {}
    values: list[str] = []
    for key in (
        "client_code",
        "clientCode",
        "tenant",
        "tenantName",
        "customer",
        "customerCode",
        "org_code",
        "orgCode",
        "tenant_org_code",
        "tenantOrgCode",
    ):
        value = md.get(key)
        if value:
            values.append(str(value))
    if org_code:
        values.append(str(org_code))

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_identifier(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def load_client_policy(org_code: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    policy_dir = _policy_dir()
    loaded_files: list[str] = []
    merged: dict[str, Any] = {}

    default_path = policy_dir / "default.json"
    if default_path.exists():
        merged = _deep_merge(merged, _read_policy_file(default_path))
        loaded_files.append(default_path.name)

    for identifier in _extract_candidate_ids(org_code=org_code, metadata=metadata):
        candidate = policy_dir / f"{identifier}.json"
        if not candidate.exists():
            continue
        merged = _deep_merge(merged, _read_policy_file(candidate))
        loaded_files.append(candidate.name)

    merged["_loaded_files"] = loaded_files
    merged["_policy_dir"] = str(policy_dir)
    return merged


def format_client_message(
    message_key: str,
    fallback: str,
    *,
    org_code: str = "",
    metadata: dict[str, Any] | None = None,
    **kwargs,
) -> str:
    policy = load_client_policy(org_code=org_code, metadata=metadata)
    messages = policy.get("messages") if isinstance(policy.get("messages"), dict) else {}
    template = str(messages.get(message_key) or "").strip()
    if not template:
        template = fallback

    try:
        return template.format(**kwargs)
    except Exception as exc:
        logger.debug("Failed to format policy message %s with %s: %s", message_key, kwargs, exc)
        try:
            return str(fallback or "").format(**kwargs)
        except Exception:
            return str(template or fallback or "").strip()


def build_client_prompt_addendum(org_code: str = "", metadata: dict[str, Any] | None = None) -> str:
    policy = load_client_policy(org_code=org_code, metadata=metadata)
    prompt = policy.get("prompt") if isinstance(policy.get("prompt"), dict) else {}

    append_rules = [
        str(item).strip()
        for item in (prompt.get("append_system_rules") or [])
        if str(item).strip()
    ]
    response_notes = [
        str(item).strip()
        for item in (prompt.get("response_notes") or [])
        if str(item).strip()
    ]
    suffix = str(prompt.get("system_prompt_suffix") or "").strip()
    title = str(prompt.get("title") or policy.get("client_name") or "Client Policy").strip()

    parts: list[str] = []
    if append_rules:
        parts.append(f"## {title}")
        parts.extend(f"- {item}" for item in append_rules)
    if response_notes:
        if not append_rules:
            parts.append(f"## {title}")
        parts.append("Response guidance:")
        parts.extend(f"- {item}" for item in response_notes)
    if suffix:
        parts.append(suffix)
    return "\n".join(parts).strip()
