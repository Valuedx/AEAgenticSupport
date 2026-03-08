"""
Persisted admin overrides for tool metadata and availability.
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


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.replace("\r", "\n").replace(",", "\n").split("\n")]
    elif isinstance(value, list):
        items = [str(item or "").strip() for item in value]
    else:
        items = []
    clean: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        clean.append(item)
    return clean


_ALLOWED_FIELDS: dict[str, str] = {
    "title": "text",
    "description": "text",
    "category": "text",
    "tier": "enum",
    "safety": "text",
    "tags": "string_list",
    "useWhen": "text",
    "avoidWhen": "text",
    "alwaysAvailable": "boolean",
    "active": "boolean",
    "allowedAgents": "string_list",
}

_TIER_OPTIONS = {"read_only", "low_risk", "medium_risk", "high_risk"}


class ToolOverrideStore:
    def __init__(self, path: str | None = None):
        raw_path = path or CONFIG.get("TOOL_OVERRIDE_PATH", "state/tool_overrides.json")
        self.path = Path(raw_path)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parent.parent / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if self.path.exists():
            return
        self.path.write_text(
            json.dumps({"version": 1, "updatedAt": _utc_now_iso(), "overrides": {}}, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "updatedAt": _utc_now_iso(), "overrides": {}}

    def _save(self, payload: dict[str, Any]) -> None:
        payload["updatedAt"] = _utc_now_iso()
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_overrides(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            payload = self._load()
        data = payload.get("overrides", {}) or {}
        return {
            str(tool_name): deepcopy(override)
            for tool_name, override in data.items()
            if isinstance(override, dict)
        }

    def get_override(self, tool_name: str) -> dict[str, Any]:
        return deepcopy(self.list_overrides().get(str(tool_name or "").strip(), {}))

    def has_override(self, tool_name: str) -> bool:
        return bool(self.get_override(tool_name))

    def update_override(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean_name = str(tool_name or "").strip()
        if not clean_name:
            raise ValueError("tool_name is required")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        normalized: dict[str, Any] = {}
        for key, raw_value in payload.items():
            field_type = _ALLOWED_FIELDS.get(key)
            if not field_type:
                continue
            if field_type == "text":
                normalized[key] = str(raw_value or "").strip()
            elif field_type == "boolean":
                normalized[key] = _normalize_bool(raw_value)
            elif field_type == "string_list":
                normalized[key] = _normalize_string_list(raw_value)
            elif field_type == "enum":
                text = str(raw_value or "").strip()
                if text not in _TIER_OPTIONS:
                    raise ValueError(
                        f"tier must be one of: {', '.join(sorted(_TIER_OPTIONS))}"
                    )
                normalized[key] = text

        with self._lock:
            current = self._load()
            overrides = current.setdefault("overrides", {})
            overrides[clean_name] = normalized
            self._save(current)
        return deepcopy(normalized)

    def reset_override(self, tool_name: str) -> None:
        clean_name = str(tool_name or "").strip()
        with self._lock:
            current = self._load()
            overrides = current.setdefault("overrides", {})
            overrides.pop(clean_name, None)
            self._save(current)


_tool_override_store: ToolOverrideStore | None = None


def get_tool_override_store() -> ToolOverrideStore:
    global _tool_override_store
    if _tool_override_store is None:
        _tool_override_store = ToolOverrideStore()
    return _tool_override_store
