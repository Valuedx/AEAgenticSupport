"""
Lightweight per-user memory store.
Persists small context across conversations (e.g., last agent name).
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Optional

from config.settings import CONFIG


class UserMemory:
    def __init__(self, path: Optional[str] = None):
        raw_path = path or CONFIG.get("USER_MEMORY_PATH", "state/user_memory.json")
        self.path = Path(raw_path)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parent.parent / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self):
        if self.path.exists():
            return
        seed = {"version": 1, "users": {}}
        self.path.write_text(json.dumps(seed, indent=2), encoding="utf-8")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "users": {}}

    def _save(self, payload: dict):
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_last_agent_name(self, user_id: str) -> str:
        uid = str(user_id or "").strip()
        if not uid:
            return ""
        with self._lock:
            data = self._load()
            user = (data.get("users") or {}).get(uid) or {}
            return str(user.get("last_agent_name") or "")

    def set_last_agent_name(self, user_id: str, agent_name: str) -> None:
        uid = str(user_id or "").strip()
        name = str(agent_name or "").strip()
        if not uid or not name:
            return
        with self._lock:
            data = self._load()
            users = data.get("users") or {}
            user = users.get(uid) or {}
            user["last_agent_name"] = name
            users[uid] = user
            data["users"] = users
            self._save(data)


_user_memory: Optional[UserMemory] = None


def get_user_memory() -> UserMemory:
    global _user_memory
    if _user_memory is None:
        _user_memory = UserMemory()
    return _user_memory
