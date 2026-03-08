"""
Persisted reference document catalog for the public documentation UI.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any

from config.settings import CONFIG


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt"}


DEFAULT_DOCUMENTS: list[dict[str, Any]] = [
    {
        "id": "part2",
        "title": "Implementation Guide Part 2",
        "badge": "P2",
        "summary": "Technical implementation guidance for the agentic support platform.",
        "audience": "Platform and support engineering",
        "path": "AE_Agentic_OpsSupport_Implementation_Guide_Part2.md",
        "displayOrder": 10,
        "active": True,
    },
    {
        "id": "control_center",
        "title": "Operations Control Center Guide",
        "badge": "CC",
        "summary": "Business-friendly guide to the admin workspace, live settings, tools, and case history.",
        "audience": "Operations leads and platform administrators",
        "path": "CONTROL_CENTER_GUIDE.md",
        "displayOrder": 15,
        "active": True,
    },
    {
        "id": "stepbystep",
        "title": "Step-by-Step Configuration",
        "badge": "SS",
        "summary": "Hands-on setup guide for AI Studio and on-prem support workflows.",
        "audience": "Administrators and implementers",
        "path": "AI_Studio_OnPrem_Agentic_Support_StepByStep(1).md",
        "displayOrder": 20,
        "active": True,
    },
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return clean or "document"


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


class DocsCatalogStore:
    def __init__(self, path: str | None = None, root_dir: str | Path | None = None):
        raw_path = path or CONFIG.get("DOCS_CATALOG_PATH", "state/docs_catalog.json")
        self.path = Path(raw_path)
        if not self.path.is_absolute():
            self.path = PROJECT_ROOT / self.path
        raw_root = Path(root_dir) if root_dir else PROJECT_ROOT
        self.root_dir = raw_root if raw_root.is_absolute() else PROJECT_ROOT / raw_root
        self.root_dir = self.root_dir.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_file()

    def _seed_documents(self) -> list[dict[str, Any]]:
        return [self._normalize_document(item, existing=None) for item in DEFAULT_DOCUMENTS]

    def _ensure_file(self) -> None:
        if self.path.exists():
            return
        payload = {
            "version": 1,
            "updatedAt": _utc_now_iso(),
            "documents": self._seed_documents(),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("catalog payload must be an object")
            payload.setdefault("documents", [])
            if not isinstance(payload["documents"], list):
                payload["documents"] = []
            return payload
        except Exception:
            return {
                "version": 1,
                "updatedAt": _utc_now_iso(),
                "documents": self._seed_documents(),
            }

    def _save(self, payload: dict[str, Any]) -> None:
        payload["updatedAt"] = _utc_now_iso()
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _resolve_content_path(self, raw_path: str) -> tuple[Path, str]:
        candidate = Path(str(raw_path or "").strip())
        if not str(candidate).strip():
            raise ValueError("Document file path is required.")
        resolved = (candidate if candidate.is_absolute() else self.root_dir / candidate).resolve()
        if resolved.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError("Document file must be a markdown or text file.")
        try:
            relative = resolved.relative_to(self.root_dir)
        except ValueError as exc:
            raise ValueError("Document file path must stay within the project workspace.") from exc
        return resolved, relative.as_posix()

    def _normalize_document(
        self,
        payload: dict[str, Any],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = deepcopy(existing or {})
        title = str(payload.get("title", current.get("title", ""))).strip()
        if not title:
            raise ValueError("Document title is required.")

        raw_id = str(payload.get("id", current.get("id", ""))).strip()
        doc_id = _slugify(raw_id or title)

        badge = str(payload.get("badge", current.get("badge", ""))).strip().upper()
        if not badge:
            badge = re.sub(r"[^A-Z0-9]", "", title.upper())[:2] or "DOC"
        badge = badge[:4]

        summary = str(payload.get("summary", current.get("summary", ""))).strip()
        audience = str(payload.get("audience", current.get("audience", ""))).strip()
        display_order = int(payload.get("displayOrder", current.get("displayOrder", 100)))
        active = _normalize_bool(payload.get("active", current.get("active", True)))
        _, stored_path = self._resolve_content_path(
            str(payload.get("path", current.get("path", ""))).strip()
        )

        return {
            "id": doc_id,
            "title": title,
            "badge": badge,
            "summary": summary,
            "audience": audience,
            "path": stored_path,
            "displayOrder": display_order,
            "active": active,
            "updatedAt": _utc_now_iso(),
        }

    def _decorate_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        resolved, stored_path = self._resolve_content_path(doc.get("path", ""))
        decorated = deepcopy(doc)
        decorated["path"] = stored_path
        decorated["available"] = resolved.exists()
        return decorated

    def list_documents(self, include_inactive: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._load()
            documents = payload.get("documents", [])
        clean: list[dict[str, Any]] = []
        for item in documents:
            if not isinstance(item, dict):
                continue
            try:
                doc = self._decorate_document(item)
            except ValueError:
                continue
            if not include_inactive and not doc.get("active", True):
                continue
            clean.append(doc)
        return sorted(clean, key=lambda item: (item.get("displayOrder", 100), item.get("title", "")))

    def get_document(self, doc_id: str, include_inactive: bool = True) -> dict[str, Any]:
        clean_id = str(doc_id or "").strip()
        for item in self.list_documents(include_inactive=include_inactive):
            if item.get("id") == clean_id:
                return item
        raise KeyError(clean_id)

    def upsert_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Document payload must be an object.")
        with self._lock:
            current = self._load()
            documents = current.setdefault("documents", [])
            raw_id = str(payload.get("id", "")).strip()
            existing_index = next(
                (index for index, item in enumerate(documents) if item.get("id") == raw_id),
                None,
            )
            existing = documents[existing_index] if existing_index is not None else None
            normalized = self._normalize_document(payload, existing=existing)
            if existing_index is None:
                documents.append(normalized)
            else:
                documents[existing_index] = normalized
            current["documents"] = sorted(
                documents,
                key=lambda item: (int(item.get("displayOrder", 100)), str(item.get("title", ""))),
            )
            self._save(current)
        return self.get_document(normalized["id"], include_inactive=True)

    def delete_document(self, doc_id: str) -> bool:
        clean_id = str(doc_id or "").strip()
        with self._lock:
            current = self._load()
            documents = current.setdefault("documents", [])
            next_documents = [item for item in documents if item.get("id") != clean_id]
            removed = len(next_documents) != len(documents)
            if removed:
                current["documents"] = next_documents
                self._save(current)
        return removed

    def read_document_content(self, doc_id: str, include_inactive: bool = False) -> dict[str, Any]:
        document = self.get_document(doc_id, include_inactive=include_inactive)
        resolved, stored_path = self._resolve_content_path(document.get("path", ""))
        if not resolved.exists():
            raise FileNotFoundError(str(resolved))
        return {
            **document,
            "path": stored_path,
            "content": resolved.read_text(encoding="utf-8"),
        }


_docs_catalog_store: DocsCatalogStore | None = None


def get_docs_catalog_store() -> DocsCatalogStore:
    global _docs_catalog_store
    if _docs_catalog_store is None:
        _docs_catalog_store = DocsCatalogStore()
    return _docs_catalog_store
