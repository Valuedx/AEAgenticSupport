"""
File-backed catalog for agent/usecase definitions and tool interactions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any, Optional

from config.settings import CONFIG


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentCatalog:
    """Persisted store for agent definitions + interaction events."""

    def __init__(self, path: Optional[str] = None, max_events: Optional[int] = None):
        raw_path = path or CONFIG.get("AGENT_CATALOG_PATH", "state/agent_catalog.json")
        self.path = Path(raw_path)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parent.parent / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_events = int(max_events or CONFIG.get("AGENT_INTERACTION_LOG_LIMIT", 500))
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self):
        if self.path.exists():
            return
        seed = {
            "version": 1,
            "agents": [
                {
                    "agentId": "ops_orchestrator",
                    "name": "Ops Orchestrator",
                    "description": "Default orchestrator for AutomationEdge ops support.",
                    "usecase": "default_ops_support",
                    "status": "active",
                    "persona": "technical",
                    "linkedTools": [],
                    "tags": ["default", "orchestrator"],
                    "createdAt": _utc_now_iso(),
                    "updatedAt": _utc_now_iso(),
                }
            ],
            "interactions": [],
        }
        self.path.write_text(json.dumps(seed, indent=2), encoding="utf-8")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "agents": [], "interactions": []}

    def _save(self, payload: dict):
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_agents(self) -> list[dict]:
        with self._lock:
            store = self._load()
            agents = store.get("agents", [])
            return sorted(agents, key=lambda a: a.get("agentId", ""))

    def get_agent(self, agent_id: str) -> Optional[dict]:
        with self._lock:
            store = self._load()
            for agent in store.get("agents", []):
                if agent.get("agentId") == agent_id:
                    return agent
            return None

    def upsert_agent(self, payload: dict) -> dict:
        with self._lock:
            store = self._load()
            agents = store.get("agents", [])

            agent_id = str(payload.get("agentId", "")).strip()
            if not agent_id:
                raise ValueError("agentId is required")

            linked_tools = payload.get("linkedTools", [])
            if isinstance(linked_tools, str):
                linked_tools = [t.strip() for t in linked_tools.split(",") if t.strip()]
            if not isinstance(linked_tools, list):
                linked_tools = []

            tags = payload.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            if not isinstance(tags, list):
                tags = []

            now = _utc_now_iso()
            candidate = {
                "agentId": agent_id,
                "name": str(payload.get("name", agent_id)).strip() or agent_id,
                "description": str(payload.get("description", "")).strip(),
                "usecase": str(payload.get("usecase", "")).strip(),
                "status": str(payload.get("status", "active")).strip() or "active",
                "persona": str(payload.get("persona", "technical")).strip() or "technical",
                "linkedTools": sorted({str(t).strip() for t in linked_tools if str(t).strip()}),
                "tags": sorted({str(t).strip() for t in tags if str(t).strip()}),
                "updatedAt": now,
            }

            existing = next((a for a in agents if a.get("agentId") == agent_id), None)
            if existing:
                candidate["createdAt"] = existing.get("createdAt", now)
                for idx, row in enumerate(agents):
                    if row.get("agentId") == agent_id:
                        agents[idx] = candidate
                        break
            else:
                candidate["createdAt"] = now
                agents.append(candidate)

            store["agents"] = agents
            self._save(store)
            return candidate

    def delete_agent(self, agent_id: str) -> bool:
        with self._lock:
            store = self._load()
            agents = store.get("agents", [])
            kept = [a for a in agents if a.get("agentId") != agent_id]
            if len(kept) == len(agents):
                return False
            store["agents"] = kept
            self._save(store)
            return True

    def get_agent_tool_map(self) -> dict[str, list[str]]:
        with self._lock:
            store = self._load()
            mapping: dict[str, list[str]] = {}
            for agent in store.get("agents", []):
                agent_id = str(agent.get("agentId", "")).strip()
                if not agent_id:
                    continue
                tools = agent.get("linkedTools", [])
                if isinstance(tools, list):
                    mapping[agent_id] = [str(t).strip() for t in tools if str(t).strip()]
                else:
                    mapping[agent_id] = []
            return mapping

    def ensure_default_agent_links(self, tool_names: list[str]):
        with self._lock:
            store = self._load()
            agents = store.get("agents", [])
            if not agents:
                agents = [
                    {
                        "agentId": "ops_orchestrator",
                        "name": "Ops Orchestrator",
                        "description": "Default orchestrator for AutomationEdge ops support.",
                        "usecase": "default_ops_support",
                        "status": "active",
                        "persona": "technical",
                        "linkedTools": sorted(set(tool_names)),
                        "tags": ["default", "orchestrator"],
                        "createdAt": _utc_now_iso(),
                        "updatedAt": _utc_now_iso(),
                    }
                ]
            else:
                for idx, agent in enumerate(agents):
                    if agent.get("agentId") == "ops_orchestrator":
                        existing = set(agent.get("linkedTools", []))
                        merged = sorted(existing | set(tool_names))
                        agents[idx]["linkedTools"] = merged
                        agents[idx]["updatedAt"] = _utc_now_iso()
                        break
            store["agents"] = agents
            self._save(store)

    def log_tool_interaction(
        self,
        *,
        tool_name: str,
        params: dict[str, Any],
        success: bool,
        error: str = "",
        conversation_id: str = "",
    ):
        with self._lock:
            store = self._load()
            interactions = store.get("interactions", [])
            mapping: dict[str, list[str]] = {}
            for agent in store.get("agents", []):
                aid = str(agent.get("agentId", "")).strip()
                if not aid:
                    continue
                tools = agent.get("linkedTools", [])
                if isinstance(tools, list):
                    mapping[aid] = [
                        str(tool).strip()
                        for tool in tools
                        if str(tool).strip()
                    ]
                else:
                    mapping[aid] = []
            matched_agents = [aid for aid, tools in mapping.items() if tool_name in tools]
            if not matched_agents:
                matched_agents = ["unmapped"]

            for agent_id in matched_agents:
                interactions.append(
                    {
                        "timestamp": _utc_now_iso(),
                        "agentId": agent_id,
                        "toolName": tool_name,
                        "success": bool(success),
                        "error": str(error or ""),
                        "conversationId": conversation_id,
                        "params": self._trim_params(params),
                    }
                )

            if len(interactions) > self.max_events:
                interactions = interactions[-self.max_events :]

            store["interactions"] = interactions
            self._save(store)

        # Dual-write to Postgres tool_execution_log (best-effort)
        self._log_to_postgres(
            tool_name=tool_name,
            params=params,
            success=success,
            error=error,
            conversation_id=conversation_id,
            matched_agents=matched_agents,
        )

    def _log_to_postgres(
        self,
        *,
        tool_name: str,
        params: dict,
        success: bool,
        error: str = "",
        conversation_id: str = "",
        matched_agents: list[str] | None = None,
    ):
        """Write tool execution event to Postgres tool_execution_log.

        Best-effort — never raises. Falls back silently if DB unavailable.
        """
        try:
            from config.db import get_conn
            from psycopg2.extras import Json as PgJson

            agents = matched_agents or ["unmapped"]
            trimmed_params = self._trim_params(params)

            with get_conn() as conn:
                with conn.cursor() as cur:
                    for agent_id in agents:
                        cur.execute("""
                            INSERT INTO tool_execution_log
                                (conversation_id, agent_id, tool_name,
                                 params, success, error_message)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (
                            conversation_id or "",
                            agent_id,
                            tool_name,
                            PgJson(trimmed_params),
                            bool(success),
                            str(error or ""),
                        ))
                conn.commit()
        except Exception:
            # Never let DB errors block tool execution logging
            pass


    def list_interactions(self, agent_id: str = "", limit: int = 100) -> list[dict]:
        with self._lock:
            store = self._load()
            rows = store.get("interactions", [])
            if agent_id:
                rows = [r for r in rows if r.get("agentId") == agent_id]
            rows = sorted(rows, key=lambda r: r.get("timestamp", ""), reverse=True)
            return rows[: max(limit, 1)]

    def summarize_tool_feedback(
        self,
        tool_names: list[str] | None = None,
        *,
        agent_id: str = "",
        limit: int | None = None,
        half_life_days: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, dict]:
        with self._lock:
            store = self._load()
            rows = list(store.get("interactions", []))

        if agent_id:
            rows = [
                row for row in rows
                if str(row.get("agentId", "")).strip() == str(agent_id).strip()
            ]

        if limit and limit > 0:
            rows = rows[-limit:]

        decay_half_life = max(
            float(
                half_life_days
                or CONFIG.get("TOOL_FEEDBACK_HALF_LIFE_DAYS", 7.0)
                or 7.0
            ),
            0.1,
        )
        ref_now = now or datetime.now(timezone.utc)

        allowed = None
        if tool_names:
            allowed = {
                str(tool_name).strip()
                for tool_name in tool_names
                if str(tool_name).strip()
            }

        summary: dict[str, dict] = {}
        if allowed:
            for tool_name in allowed:
                summary[tool_name] = {
                    "success_count": 0,
                    "failure_count": 0,
                    "total_count": 0,
                    "success_rate": 0.0,
                    "weighted_success_count": 0.0,
                    "weighted_failure_count": 0.0,
                    "weighted_total_count": 0.0,
                    "weighted_success_rate": 0.0,
                    "last_success_at": "",
                    "last_failure_at": "",
                }

        for row in rows:
            tool_name = str(row.get("toolName", "")).strip()
            if not tool_name:
                continue
            if allowed is not None and tool_name not in allowed:
                continue

            stats = summary.setdefault(
                tool_name,
                {
                    "success_count": 0,
                    "failure_count": 0,
                    "total_count": 0,
                    "success_rate": 0.0,
                    "weighted_success_count": 0.0,
                    "weighted_failure_count": 0.0,
                    "weighted_total_count": 0.0,
                    "weighted_success_rate": 0.0,
                    "last_success_at": "",
                    "last_failure_at": "",
                },
            )
            success = bool(row.get("success"))
            weight = self._feedback_weight(
                row.get("timestamp"),
                ref_now=ref_now,
                half_life_days=decay_half_life,
            )
            stats["total_count"] += 1
            stats["weighted_total_count"] += weight
            if success:
                stats["success_count"] += 1
                stats["weighted_success_count"] += weight
                stats["last_success_at"] = str(
                    row.get("timestamp") or stats["last_success_at"]
                )
            else:
                stats["failure_count"] += 1
                stats["weighted_failure_count"] += weight
                stats["last_failure_at"] = str(
                    row.get("timestamp") or stats["last_failure_at"]
                )

        for stats in summary.values():
            total = int(stats.get("total_count", 0) or 0)
            weighted_total = float(stats.get("weighted_total_count", 0.0) or 0.0)
            stats["success_rate"] = round(
                (int(stats.get("success_count", 0) or 0) / total) if total else 0.0,
                3,
            )
            stats["weighted_success_count"] = round(
                float(stats.get("weighted_success_count", 0.0) or 0.0),
                3,
            )
            stats["weighted_failure_count"] = round(
                float(stats.get("weighted_failure_count", 0.0) or 0.0),
                3,
            )
            stats["weighted_total_count"] = round(weighted_total, 3)
            stats["weighted_success_rate"] = round(
                (
                    float(stats.get("weighted_success_count", 0.0) or 0.0)
                    / weighted_total
                )
                if weighted_total
                else 0.0,
                3,
            )

        return summary

    @staticmethod
    def _feedback_weight(
        timestamp,
        *,
        ref_now: datetime,
        half_life_days: float,
    ) -> float:
        raw = str(timestamp or "").strip()
        if not raw:
            return 1.0
        try:
            observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age_seconds = max(
                (ref_now - observed.astimezone(timezone.utc)).total_seconds(),
                0.0,
            )
            age_days = age_seconds / 86400.0
            return 0.5 ** (age_days / half_life_days)
        except Exception:
            return 1.0

    @staticmethod
    def _trim_params(payload: dict[str, Any], max_len: int = 400) -> dict:
        compact: dict[str, Any] = {}
        for key, value in (payload or {}).items():
            text = json.dumps(value, default=str)
            compact[key] = text if len(text) <= max_len else text[:max_len] + "..."
        return compact


_agent_catalog: Optional[AgentCatalog] = None


def get_agent_catalog() -> AgentCatalog:
    global _agent_catalog
    if _agent_catalog is None:
        _agent_catalog = AgentCatalog()
    return _agent_catalog
