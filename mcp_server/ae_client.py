"""
Standalone AutomationEdge REST client for the MCP server.

Supports session-token auth via /authenticate and API-key bearer auth.
Designed to be self-contained — no dependency on the parent project.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from mcp_server.config import MCP_CONFIG

logger = logging.getLogger("ae_mcp.client")


class AEClient:
    """HTTP client for AutomationEdge REST APIs."""

    def __init__(self) -> None:
        self.base_url = MCP_CONFIG["AE_BASE_URL"].rstrip("/")
        self.rest_base = MCP_CONFIG["AE_REST_BASE_PATH"].rstrip("/")
        self.org = MCP_CONFIG["AE_ORG_CODE"]
        self.default_user = MCP_CONFIG["AE_DEFAULT_USERID"]
        self.timeout = MCP_CONFIG["AE_TIMEOUT_SECONDS"]
        self.verify_ssl = MCP_CONFIG["AE_VERIFY_SSL"]

        self._api_key = MCP_CONFIG["AE_API_KEY"]
        self._username = MCP_CONFIG["AE_USERNAME"]
        self._password = MCP_CONFIG["AE_PASSWORD"]
        self._session_header = MCP_CONFIG["AE_SESSION_HEADER"]
        self._token_field = MCP_CONFIG["AE_TOKEN_FIELD"]
        self._token_ttl = MCP_CONFIG["AE_TOKEN_TTL_SECONDS"]
        self._auth_endpoint = MCP_CONFIG["AE_AUTH_ENDPOINT"]

        self._token: str = ""
        self._token_expiry: Optional[datetime] = None
        self._lock = threading.Lock()
        # Cache (method, paths_tuple) -> (path_index, use_rest) to try winning path first
        self._path_cache: dict[tuple[str, tuple[str, ...]], tuple[int, bool]] = {}
        self._cache_lock = threading.Lock()

        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

    @property
    def _use_session_auth(self) -> bool:
        return bool(self._username and self._password)

    def _token_valid(self) -> bool:
        return bool(
            self._token
            and self._token_expiry
            and datetime.now(timezone.utc) < self._token_expiry
        )

    def _rest(self, path: str) -> str:
        p = path if path.startswith("/") else f"/{path}"
        if p.startswith(self.rest_base):
            return p
        return f"{self.rest_base}{p}"

    def authenticate(self, force: bool = False) -> str:
        if not self._use_session_auth:
            return ""
        with self._lock:
            if not force and self._token_valid():
                return self._token
            resp = self._http.post(
                self._rest(self._auth_endpoint),
                params={"username": self._username, "password": self._password},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            token = str(data.get(self._token_field, "")).strip()
            if not token:
                for alt in ("sessionToken", "session_token", "token", "sessionId"):
                    token = str(data.get(alt, "")).strip()
                    if token:
                        break
            if not token:
                raise RuntimeError("AE auth succeeded but no token returned")
            self._token = token
            ttl = max(self._token_ttl - 30, 30)
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl)
            return token

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h: dict[str, str] = {"Accept": "application/json"}
        if extra:
            h.update(extra)
        if self._use_session_auth:
            if not self._token_valid():
                self.authenticate()
            if self._token:
                h[self._session_header] = self._token
        elif self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
            h.setdefault("Content-Type", "application/json")
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[Any] = None,
        use_rest: bool = True,
        retry_401: bool = True,
    ) -> Any:
        url = self._rest(path) if use_rest else (path if path.startswith("/") else f"/{path}")
        resp = self._http.request(
            method, url, params=params, json=json_body, headers=self._headers()
        )
        if resp.status_code == 401 and retry_401 and self._use_session_auth:
            self.authenticate(force=True)
            resp = self._http.request(
                method, url, params=params, json=json_body, headers=self._headers()
            )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"raw_text": resp.text}

    def get(self, path: str, *, params: Optional[dict] = None, use_rest: bool = True) -> Any:
        return self._request("GET", path, params=params, use_rest=use_rest)

    def post(self, path: str, *, json_body: Optional[Any] = None, params: Optional[dict] = None, use_rest: bool = True) -> Any:
        return self._request("POST", path, json_body=json_body, params=params, use_rest=use_rest)

    def put(self, path: str, *, json_body: Optional[Any] = None, params: Optional[dict] = None, use_rest: bool = True) -> Any:
        return self._request("PUT", path, json_body=json_body, params=params, use_rest=use_rest)

    def delete(self, path: str, *, params: Optional[dict] = None, use_rest: bool = True) -> Any:
        return self._request("DELETE", path, params=params, use_rest=use_rest)

    # ── Resilient helpers with endpoint fallback ──

    def _try_paths(
        self,
        method: str,
        paths: list[str],
        *,
        params: Optional[dict] = None,
        json_body: Optional[Any] = None,
    ) -> Any:
        paths_tuple = tuple(paths)
        cache_key = (method, paths_tuple)
        last_exc: Optional[Exception] = None

        # Try cached winning path first (cache success only, so no stale failures)
        with self._cache_lock:
            cached = self._path_cache.get(cache_key)
        if cached is not None:
            path_idx, use_rest = cached
            if 0 <= path_idx < len(paths):
                try:
                    out = self._request(
                        method, paths[path_idx], params=params, json_body=json_body, use_rest=use_rest
                    )
                    return out
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 405, 500}:
                        last_exc = exc
                        with self._cache_lock:
                            self._path_cache.pop(cache_key, None)
                    else:
                        raise

        for use_rest in (True, False):
            for path_idx, path in enumerate(paths):
                try:
                    out = self._request(
                        method, path, params=params, json_body=json_body, use_rest=use_rest
                    )
                    with self._cache_lock:
                        self._path_cache[cache_key] = (path_idx, use_rest)
                    return out
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 405, 500}:
                        last_exc = exc
                        continue
                    raise
        raise last_exc or RuntimeError(f"All endpoint paths failed: {paths}")

    def get_request(self, request_id: str) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/workflowinstances/{request_id}",
            f"/workflowinstances/{request_id}",
        ])

    def search_requests(
        self,
        *,
        filters: Optional[dict] = None,
        offset: int = 0,
        limit: int = 50,
        order: str = "desc",
    ) -> list[dict]:
        params = {"offset": offset, "size": limit, "order": order}
        if filters:
            params.update(filters)
        raw = self._try_paths("POST", [
            f"/{self.org}/workflowinstances",
            "/workflowinstances",
        ], params=params, json_body=filters or {})
        return self._extract_list(raw)

    def get_request_logs(self, request_id: str, tail: int = 200) -> Any:
        try:
            return self._try_paths("GET", [
                f"/{self.org}/workflowinstances/{request_id}/logs",
                f"/workflowinstances/{request_id}/logs",
                f"/executions/{request_id}/logs",
            ], params={"tail": tail})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                logger.info("Standard logs 400, trying agent/debuglogs fallback for %s", request_id)
                try:
                    # 1. Trigger
                    self.post("/agent/debuglogs", params={"workflowInstanceId": request_id})
                    
                    # 2. Poll (simplified for MCP)
                    import time
                    for _ in range(5):
                        time.sleep(2)
                        logs_resp = self.get("/agent/debuglogs")
                        if isinstance(logs_resp, list):
                            match = next((l for l in logs_resp if str(l.get("workflowInstanceId")) == str(request_id)), None)
                            if match and match.get("status") == "COMPLETE":
                                log_id = match.get("id")
                                # 3. Download
                                dl_resp = self._http.request("GET", self._rest(f"/agent/debuglogs/{log_id}"), headers=self._headers())
                                dl_resp.raise_for_status()
                                
                                # 4. Decompress ZIP + GZIP
                                import io
                                import zipfile
                                import gzip
                                try:
                                    with zipfile.ZipFile(io.BytesIO(dl_resp.content)) as z:
                                        all_lines = []
                                        for name in z.namelist():
                                            if name.lower().endswith(".gz"):
                                                with z.open(name) as fz:
                                                    with gzip.GzipFile(fileobj=fz) as f:
                                                        text = f.read().decode("utf-8", errors="ignore")
                                                        # Wrap lines in dicts for tool compatibility
                                                        all_lines.extend([{"message": line.strip(), "details": line.strip()} for line in text.splitlines()[-tail:]])
                                            elif name.lower().endswith(".log"):
                                                with z.open(name) as f:
                                                    text = f.read().decode("utf-8", errors="ignore")
                                                    all_lines.extend([{"message": line.strip(), "details": line.strip()} for line in text.splitlines()[-tail:]])
                                        return all_lines
                                except Exception as zip_exc:
                                    logger.warning("MCP log ZIP extraction failed: %s", zip_exc)
                except Exception as fb_exc:
                    logger.warning("agent/debuglogs fallback failed: %s", fb_exc)
            raise

    def get_request_audit(self, request_id: str) -> Any:
        return self._try_paths("GET", [
            f"/{self.org}/workflowinstances/{request_id}/audit",
            f"/workflowinstances/{request_id}/audit",
            f"/{self.org}/workflowinstances/{request_id}/logs",
            f"/workflowinstances/{request_id}/logs",
        ])

    def get_request_steps(self, request_id: str) -> Any:
        return self._try_paths("GET", [
            f"/{self.org}/workflowinstances/{request_id}/steps",
            f"/workflowinstances/{request_id}/steps",
            f"/{self.org}/workflowinstances/{request_id}/logs",
            f"/workflowinstances/{request_id}/logs",
        ])

    def restart_request(self, request_id: str, reason: str = "") -> dict:
        """Restart a workflow instance.

        T4 confirmed: uses PUT on the global (no-org-prefix) path.
        Ref: PUT /aeengine/rest/workflowinstances/{id}/restart -> 200 OK
        """
        return self._try_paths("PUT", [
            # Global path — T4 confirmed this works with PUT
            f"/workflowinstances/{request_id}/restart",
            # Org-scoped fallback
            f"/{self.org}/workflowinstances/{request_id}/restart",
        ], json_body={"reason": reason})

    def terminate_request(self, request_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflowinstances/{request_id}/terminate",
            f"/workflowinstances/{request_id}/terminate",
        ], json_body={"reason": reason})

    def resubmit_request(self, request_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflowinstances/{request_id}/resubmit",
            f"/workflowinstances/{request_id}/resubmit",
        ], json_body={"reason": reason, "fromFailurePoint": True})

    def add_request_comment(self, request_id: str, comment: str) -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflowinstances/{request_id}/comments",
            f"/workflowinstances/{request_id}/comments",
        ], json_body={"comment": comment})

    def cancel_request(self, request_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflowinstances/{request_id}/cancel",
            f"/workflowinstances/{request_id}/cancel",
        ], json_body={"reason": reason})

    def resubmit_request_from_start(self, request_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflowinstances/{request_id}/resubmit",
            f"/workflowinstances/{request_id}/resubmit",
        ], json_body={"reason": reason, "fromFailurePoint": False})

    def enable_workflow(self, workflow_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflows/{workflow_id}/enable",
            f"/workflows/{workflow_id}/enable",
        ], json_body={"reason": reason})

    def enable_schedule(self, schedule_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/schedules/{schedule_id}/enable",
            f"/schedules/{schedule_id}/enable",
        ], json_body={"reason": reason})

    def run_schedule_now(self, schedule_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/schedules/{schedule_id}/run",
            f"/schedules/{schedule_id}/run",
            f"/{self.org}/schedules/{schedule_id}/runNow",
            f"/schedules/{schedule_id}/runNow",
        ], json_body={"reason": reason})

    def search_workflows(self, query: str = "", filters: Optional[dict] = None, limit: int = 50) -> list[dict]:
        params = {"offset": 0, "size": limit}
        if query:
            params["query"] = query
        body = filters or {}
        raw = self._try_paths("GET", [
            f"/{self.org}/workflows/catalogue",
            "/workflows/catalogue",
            f"/{self.org}/workflows",
            "/workflows",
        ], params=params)
        return self._extract_list(raw, keys=("workflows", "items", "results", "data"))

    def get_workflow(self, workflow_id: str) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/workflows/{workflow_id}/config",
            f"/workflows/{workflow_id}/config",
            f"/{self.org}/workflows/{workflow_id}",
            f"/workflows/{workflow_id}",
        ])

    def get_workflow_runtime_params(self, workflow_id: str) -> Any:
        return self._try_paths("GET", [
            f"/{self.org}/workflows/{workflow_id}/parameters",
            f"/workflows/{workflow_id}/parameters",
            f"/{self.org}/workflows/{workflow_id}/config",
            f"/workflows/{workflow_id}/config",
        ])

    def disable_workflow(self, workflow_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflows/{workflow_id}/disable",
            f"/workflows/{workflow_id}/disable",
        ], json_body={"reason": reason})

    def assign_workflow_to_agent(self, workflow_id: str, agent_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflows/{workflow_id}/assign",
            f"/workflows/{workflow_id}/assign",
        ], json_body={"agentId": agent_id, "reason": reason})

    def update_workflow_permissions(self, workflow_id: str, permissions: dict, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflows/{workflow_id}/permissions",
            f"/workflows/{workflow_id}/permissions",
        ], json_body={**permissions, "reason": reason})

    def rollback_workflow_version(self, workflow_id: str, version: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/workflows/{workflow_id}/rollback",
            f"/workflows/{workflow_id}/rollback",
        ], json_body={"version": version, "reason": reason})

    def list_agents(self, filters: Optional[dict] = None) -> list[dict]:
        params = {"type": "AGENT", "offset": 0, "size": 200}
        if filters:
            params.update(filters)
        raw = self._try_paths("POST", [
            f"/{self.org}/monitoring/agents",
        ], params=params)
        return self._extract_list(raw, keys=("data", "agents", "items"))

    def get_agent(self, agent_id: str) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/agents/{agent_id}",
            f"/agents/{agent_id}",
        ])

    def get_agent_requests(self, agent_id: str, limit: int = 50) -> list[dict]:
        raw = self._try_paths("GET", [
            f"/{self.org}/agents/{agent_id}/requests",
            f"/agents/{agent_id}/requests",
        ], params={"size": limit, "status": "Running"})
        return self._extract_list(raw)

    def restart_agent(self, agent_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/agents/{agent_id}/restart",
            f"/agents/{agent_id}/restart",
        ], json_body={"reason": reason})

    def clear_agent_rdp(self, agent_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/agents/{agent_id}/rdp/clear",
            f"/agents/{agent_id}/rdp/clear",
        ], json_body={"reason": reason})

    def get_schedule(self, schedule_id: str) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/schedules/{schedule_id}",
            f"/schedules/{schedule_id}",
        ])

    def search_schedules(self, workflow_id: str = "", filters: Optional[dict] = None) -> list[dict]:
        params: dict[str, Any] = {"offset": 0, "size": 100}
        if workflow_id:
            params["workflowId"] = workflow_id
        if filters:
            params.update(filters)
        raw = self._try_paths("GET", [
            f"/{self.org}/schedules",
            "/schedules",
        ], params=params)
        return self._extract_list(raw, keys=("schedules", "items", "data"))

    def disable_schedule(self, schedule_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/schedules/{schedule_id}/disable",
            f"/schedules/{schedule_id}/disable",
        ], json_body={"reason": reason})

    def get_tasks(self, filters: Optional[dict] = None) -> list[dict]:
        params: dict[str, Any] = {"offset": 0, "size": 100}
        if filters:
            params.update(filters)
        raw = self._try_paths("GET", [
            f"/{self.org}/tasks",
            "/tasks",
        ], params=params)
        return self._extract_list(raw, keys=("tasks", "items", "data"))

    def get_task(self, task_id: str) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/tasks/{task_id}",
            f"/tasks/{task_id}",
        ])

    def cancel_task(self, task_id: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/tasks/{task_id}/cancel",
            f"/tasks/{task_id}/cancel",
        ], json_body={"reason": reason})

    def reassign_task(self, task_id: str, target_user_or_group: str, reason: str = "") -> dict:
        return self._try_paths("POST", [
            f"/{self.org}/tasks/{task_id}/reassign",
            f"/tasks/{task_id}/reassign",
        ], json_body={"target": target_user_or_group, "reason": reason})

    def get_credential_pool(self, pool_id: str) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/credentialpools/{pool_id}",
            f"/credentialpools/{pool_id}",
        ])

    def get_user(self, user_id: str) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/users/{user_id}",
            f"/users/{user_id}",
        ])

    def get_user_workflows(self, user_id: str) -> list[dict]:
        raw = self._try_paths("GET", [
            f"/{self.org}/users/{user_id}/workflows",
            f"/users/{user_id}/workflows",
        ])
        return self._extract_list(raw, keys=("workflows", "items", "data"))

    def get_workflow_permissions(self, workflow_id: str) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/workflows/{workflow_id}/permissions",
            f"/workflows/{workflow_id}/permissions",
        ])

    def get_license(self) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/platform/license",
            "/platform/license",
            f"/{self.org}/system/license",
        ])

    def get_queue_depth(self) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/platform/queues",
            f"/{self.org}/system/health",
            "/platform/queues",
        ])

    def get_system_health(self) -> dict:
        return self._try_paths("GET", [
            f"/{self.org}/system/health",
            "/system/health",
        ])

    def close(self) -> None:
        self._http.close()

    @staticmethod
    def _extract_list(payload: Any, keys: tuple[str, ...] = ("data", "instances", "executions", "items", "results")) -> list[dict]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for k in keys:
                val = payload.get(k)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
            inner = payload.get("data")
            if isinstance(inner, dict):
                for k in keys:
                    val = inner.get(k)
                    if isinstance(val, list):
                        return [x for x in val if isinstance(x, dict)]
        return []


_client: Optional[AEClient] = None


def get_ae_client() -> AEClient:
    global _client
    if _client is None:
        _client = AEClient()
    return _client
