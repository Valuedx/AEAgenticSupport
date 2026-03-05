"""
Reusable AutomationEdge REST client.

Supports two auth modes:
1) Session-token flow via /authenticate (preferred for AE REST).
2) API-key bearer token (legacy compatibility for existing tools/tests).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any, Optional

import httpx
import urllib3

from config.settings import CONFIG

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("ops_agent.tools.ae_client")


@dataclass
class AEWorkflowParameter:
    name: str
    value: Any
    type: str = "String"


class AutomationEdgeClient:
    """HTTP client for AutomationEdge APIs with auto re-auth handling."""

    def __init__(self, client: Optional[httpx.Client] = None):
        self.base_url = str(CONFIG["AE_BASE_URL"]).rstrip("/")
        self.timeout = int(CONFIG.get("AE_TIMEOUT_SECONDS", 30))
        self.verify_ssl = False

        self.api_key = str(CONFIG.get("AE_API_KEY", "")).strip()
        self.username = str(CONFIG.get("AE_USERNAME", "")).strip()
        self.password = str(CONFIG.get("AE_PASSWORD", "")).strip()

        self.rest_base_path = str(
            CONFIG.get("AE_REST_BASE_PATH", "/aeengine/rest")
        ).strip() or "/aeengine/rest"
        self.auth_endpoint = str(
            CONFIG.get("AE_AUTH_ENDPOINT", "/authenticate")
        ).strip() or "/authenticate"
        self.execute_endpoint = str(
            CONFIG.get("AE_EXECUTE_ENDPOINT", "/{org_code}/execute")
        ).strip() or "/{org_code}/execute"
        # T4 /workflows/catalogue provides rich param metadata (displayName, optional, etc.)
        # Ref: User-provided T4 Catalogue JSON
        self.workflows_endpoint = str(
            CONFIG.get("AE_WORKFLOWS_ENDPOINT", "/workflows/catalogue")
        ).strip() or "/workflows/catalogue"
        self.workflows_runtime_endpoint = "/workflows/runtime"
        self.workflows_method = str(
            CONFIG.get("AE_WORKFLOWS_METHOD", "GET")
        ).strip().upper() or "GET"
        self.workflow_details_endpoint = str(
            CONFIG.get(
                "AE_WORKFLOW_DETAILS_ENDPOINT",
                "/{org_code}/workflows/{workflow_identifier}/config",
            )
        ).strip() or "/{org_code}/workflows/{workflow_identifier}/config"
        self.workflow_details_method = str(
            CONFIG.get("AE_WORKFLOW_DETAILS_METHOD", "GET")
        ).strip().upper() or "GET"
        self.session_header = str(
            CONFIG.get("AE_SESSION_HEADER", "X-session-token")
        ).strip() or "X-session-token"
        self.token_field = str(CONFIG.get("AE_TOKEN_FIELD", "token")).strip() or "token"
        self.token_ttl_seconds = int(CONFIG.get("AE_TOKEN_TTL_SECONDS", 1800))
        self.default_org_code = str(CONFIG.get("AE_ORG_CODE", "")).strip()
        self.default_user_id = str(CONFIG.get("AE_DEFAULT_USERID", "ops_agent")).strip()

        self._session_token = ""
        self._token_expiry: Optional[datetime] = None
        self._auth_lock = threading.Lock()

        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

    @property
    def use_session_auth(self) -> bool:
        return bool(self.username and self.password)

    def _is_token_valid(self) -> bool:
        if not self._session_token or not self._token_expiry:
            return False
        return datetime.now(timezone.utc) < self._token_expiry

    @staticmethod
    def _normalize_path(path: str) -> str:
        clean = (path or "").strip()
        if not clean:
            return "/"
        if clean.startswith("http://") or clean.startswith("https://"):
            return clean
        return clean if clean.startswith("/") else f"/{clean}"

    def _rest_path(self, endpoint: str) -> str:
        endpoint_norm = self._normalize_path(endpoint)
        if endpoint_norm.startswith("http://") or endpoint_norm.startswith("https://"):
            return endpoint_norm
        base = self._normalize_path(self.rest_base_path).rstrip("/")
        if endpoint_norm.startswith(base + "/") or endpoint_norm == base:
            return endpoint_norm
        return f"{base}{endpoint_norm}"

    def _json_or_text(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def authenticate(self, force: bool = False) -> str:
        """Authenticate against AE and cache session token.

        T4 /authenticate expects username+password as QUERY PARAMS (not form body).
        Ref: code_ref.py t4_authenticate()
        """
        if not self.use_session_auth:
            return ""

        with self._auth_lock:
            if not force and self._is_token_valid():
                return self._session_token

            auth_path = self._rest_path(self.auth_endpoint)
            # T4 uses query params for auth, not form data
            response = self._client.post(
                auth_path,
                params={"username": self.username, "password": self.password},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = self._json_or_text(response) or {}
            token = str(payload.get(self.token_field, "")).strip()
            if not token:
                for alt_field in ("sessionToken", "session_token", "token", "sessionId"):
                    candidate = str(payload.get(alt_field, "")).strip()
                    if candidate:
                        token = candidate
                        break
            if not token:
                raise RuntimeError(
                    f"AE authentication succeeded but '{self.token_field}' not found."
                )
            self._session_token = token
            ttl = max(self.token_ttl_seconds - 30, 30)
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl)
            logger.info("AE authentication successful; session token cached.")
            return token

    def _build_auth_headers(self, extra_headers: Optional[dict] = None) -> dict:
        headers = {"Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        if self.use_session_auth:
            if not self._is_token_valid():
                self.authenticate()
            if self._session_token:
                headers[self.session_header] = self._session_token
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers.setdefault("Content-Type", "application/json")
        return headers

    def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        payload: Optional[dict] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        use_rest_prefix: bool = False,
        retry_on_401: bool = True,
    ) -> Any:
        request_path = self._rest_path(path) if use_rest_prefix else self._normalize_path(path)
        request_headers = self._build_auth_headers(headers)
        response = self._client.request(
            method.upper(),
            request_path,
            params=params,
            json=payload,
            data=data,
            headers=request_headers,
        )

        if (
            response.status_code == 401
            and retry_on_401
            and self.use_session_auth
        ):
            logger.info("AE returned 401, re-authenticating and retrying once.")
            self.authenticate(force=True)
            retry_headers = self._build_auth_headers(headers)
            response = self._client.request(
                method.upper(),
                request_path,
                params=params,
                json=payload,
                data=data,
                headers=retry_headers,
            )

        response.raise_for_status()
        return self._json_or_text(response)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        payload: Optional[dict] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        use_rest_prefix: bool = False,
    ) -> Any:
        return self._authorized_request(
            method,
            path,
            params=params,
            payload=payload,
            data=data,
            headers=headers,
            use_rest_prefix=use_rest_prefix,
        )

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        return self._authorized_request("GET", path, params=params)

    def post(self, path: str, payload: Optional[dict] = None) -> Any:
        return self._authorized_request("POST", path, payload=payload)

    def execute_workflow(
        self,
        *,
        workflow_name: str,
        workflow_id: str = "",
        params: Optional[dict] = None,
        org_code: str = "",
        user_id: str = "",
        source: str = "ae-agentic-support",
        mail_subject: str = "null",
        **extra_fields,
    ) -> dict:
        if not workflow_name:
            raise ValueError("workflow_name is required")

        payload = {
            "orgCode": org_code or self.default_org_code,
            "workflowName": workflow_name,
            "userId": user_id or self.default_user_id,
            "source": source,
            "responseMailSubject": mail_subject or "null",
            "params": self._build_param_array(params or {}),
        }
        payload.update({k: v for k, v in extra_fields.items() if v is not None})

        endpoint = self.execute_endpoint.format(
            org_code=org_code or self.default_org_code
        )

        # code_ref.py sends workflow_name and workflow_id as query params too
        q_params = {"workflow_name": workflow_name}
        if workflow_id:
            q_params["workflow_id"] = workflow_id

        return self._authorized_request(
            "POST",
            endpoint,
            payload=payload,
            params=q_params,
            use_rest_prefix=True,
        )

    def list_workflows(
        self,
        offset: int = 0,
        page_size: int = 200,
        all_pages: bool = True,
    ) -> list[dict]:
        """Fetch workflows from T4.
        
        Prioritizes /workflows/catalogue (rich metadata as per user request).
        Falls back to /workflows/runtime if needed.
        """
        all_workflows: list[dict] = []
        current_offset = offset

        # Try Catalogue first (T4 specific richness)
        while True:
            try:
                endpoint = self._rest_path(self.workflows_endpoint)
                payload = self._authorized_request(
                    "GET",
                    endpoint,
                    params={"offset": current_offset, "size": page_size},
                )
            except httpx.HTTPStatusError as exc:
                if current_offset == 0:
                    logger.warning("T4 Catalogue GET failed (%s). Trying Runtime fallback.", exc.response.status_code)
                    return self._list_workflows_runtime(offset, page_size, all_pages)
                raise

            batch = self._extract_workflow_list(payload)
            if not batch:
                break
            all_workflows.extend(batch)
            logger.info("Workflows fetched (Catalogue) offset=%d -> %d records", current_offset, len(batch))
            if not all_pages or len(batch) < page_size:
                break
            current_offset += page_size

        return all_workflows

    def _list_workflows_runtime(self, offset: int, page_size: int, all_pages: bool) -> list[dict]:
        """Fallback to /workflows/runtime if Catalogue is unavailable."""
        all_workflows: list[dict] = []
        current_offset = offset
        while True:
            try:
                endpoint = self._rest_path(self.workflows_runtime_endpoint)
                payload = self._authorized_request(
                    "GET",
                    endpoint,
                    params={"offset": current_offset, "size": page_size},
                )
            except httpx.HTTPStatusError as exc:
                logger.warning("Workflow Runtime GET failed (%s). Trying POST.", exc.response.status_code)
                payload = self._authorized_request(
                    "POST",
                    endpoint,
                    params={"offset": current_offset, "size": page_size},
                )
            
            batch = self._extract_workflow_list(payload)
            if not batch:
                break
            all_workflows.extend(batch)
            logger.info("Workflows fetched (Runtime) offset=%d -> %d records", current_offset, len(batch))
            if not all_pages or len(batch) < page_size:
                break
            current_offset += page_size
        return all_workflows

    def get_workflow_details(self, workflow_identifier: str) -> dict:
        if not workflow_identifier:
            raise ValueError("workflow_identifier is required")

        org = self.default_org_code
        endpoint = self.workflow_details_endpoint.format(
            org_code=org,
            workflow_identifier=workflow_identifier,
            workflow_id=workflow_identifier,
            workflow_name=workflow_identifier,
        )
        fallback_paths = [
            endpoint,
            f"/{org}/workflows/{workflow_identifier}/config" if org else "",
            f"/workflows/{workflow_identifier}/config",
            f"/{org}/workflows/{workflow_identifier}" if org else "",
            f"/workflows/{workflow_identifier}",
        ]
        fallback_paths = [p for p in fallback_paths if p]

        last_exc: Optional[Exception] = None
        for use_prefix in (True, False):
            for path in fallback_paths:
                try:
                    return self._authorized_request(
                        self.workflow_details_method,
                        path,
                        use_rest_prefix=use_prefix,
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        last_exc = exc
                        continue
                    raise
        raise last_exc or RuntimeError(f"Could not fetch workflow details for {workflow_identifier}")

    def get_execution_status(self, execution_id: str) -> dict:
        """Get the status of a specific workflow execution.

        Tries both paths per ref: code_ref.py t4_poll_status():
        1. /workflowinstances/{id}  (global)
        2. /{org_code}/workflowinstances/{id}  (org-scoped)
        """
        if not execution_id:
            raise ValueError("execution_id is required")

        paths = [
            f"/workflowinstances/{execution_id}",
            f"/{self.default_org_code}/workflowinstances/{execution_id}",
        ]
        last_exc: Optional[Exception] = None
        
        # T4 variability: try with and without /aeengine/rest prefix
        for use_prefix in (False, True):
            for path in paths:
                try:
                    result = self._authorized_request("GET", path, use_rest_prefix=use_prefix)
                    return result
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        last_exc = exc
                        continue
                    raise
        raise last_exc or RuntimeError(f"Could not fetch status for execution {execution_id}")

    def get_workflow_latest_instance(self, workflow_name: str, org_code: str = "") -> dict:
        """Get latest workflow instance for a workflow.

        Tries org-scoped and global variants, with and without REST prefix.
        Returns the first item when API returns a list.
        """
        name = str(workflow_name or "").strip()
        if not name:
            raise ValueError("workflow_name is required")

        org = (org_code or self.default_org_code or "").strip()
        paths = []
        if org:
            paths.append(f"/{org}/workflows/{name}/instances")
        paths.append(f"/workflows/{name}/instances")

        last_exc: Optional[Exception] = None
        for use_prefix in (False, True):
            for path in paths:
                try:
                    result = self._authorized_request("GET", path, use_rest_prefix=use_prefix)
                    if isinstance(result, list):
                        return result[0] if result else {}
                    return result if isinstance(result, dict) else {}
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        last_exc = exc
                        continue
                    raise

        raise last_exc or RuntimeError(
            f"Could not fetch latest instance for workflow '{workflow_name}'"
        )

    def get_workflow_instances(self, workflow_name: str, limit: int = 10, org_code: str = "") -> list[dict]:
        """Get recent workflow instances with endpoint fallbacks."""
        name = str(workflow_name or "").strip()
        if not name:
            raise ValueError("workflow_name is required")

        org = (org_code or self.default_org_code or "").strip()
        paths = []
        if org:
            paths.append(f"/{org}/workflows/{name}/instances")
        paths.append(f"/workflows/{name}/instances")

        last_exc: Optional[Exception] = None
        for use_prefix in (False, True):
            for path in paths:
                try:
                    result = self._authorized_request("GET", path, use_rest_prefix=use_prefix)
                    if isinstance(result, list):
                        return result[: max(limit, 1)]
                    if isinstance(result, dict):
                        items = result.get("instances") or result.get("executions") or []
                        if isinstance(items, list):
                            return items[: max(limit, 1)]
                        return [result]
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        last_exc = exc
                        continue
                    raise
        raise last_exc or RuntimeError(f"Could not fetch instances for workflow '{workflow_name}'")

    def get_execution_logs(self, execution_id: str, tail: int = 100) -> dict:
        """Get execution logs by execution id with T4 fallback paths."""
        if not execution_id:
            raise ValueError("execution_id is required")

        paths = [
            f"/workflowinstances/{execution_id}/logs",
            f"/{self.default_org_code}/workflowinstances/{execution_id}/logs",
            f"/executions/{execution_id}/logs",
        ]
        last_exc: Optional[Exception] = None
        for use_prefix in (False, True):
            for path in paths:
                try:
                    return self._authorized_request(
                        "GET",
                        path,
                        params={"tail": tail},
                        use_rest_prefix=use_prefix,
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        last_exc = exc
                        continue
                    raise
        raise last_exc or RuntimeError(f"Could not fetch logs for execution {execution_id}")

    def poll_execution_status(
        self,
        execution_id: str,
        poll_interval_sec: int = 3,
        max_attempts: int = 100,
        terminal_statuses: tuple = ("Complete", "Failure", "Error"),
    ) -> dict:
        """Poll execution status until terminal or max_attempts reached.

        Ref: code_ref.py t4_poll_status()
        Returns dict with 'status', 'execution_id', 'raw'.
        """
        import time

        no_agent_threshold = 10
        no_agent_counter = 0
        raw = None

        for attempt in range(max_attempts):
            try:
                raw = self.get_execution_status(execution_id)
            except Exception as exc:
                logger.warning("Poll attempt %d failed: %s", attempt + 1, exc)
                if attempt > 5:
                    raise
                time.sleep(poll_interval_sec)
                continue

            status = raw.get("status", "pending") if isinstance(raw, dict) else "pending"
            logger.info("Poll #%d execution_id=%s status=%s", attempt + 1, execution_id, status)

            # Detect "no agent" condition (status stays New without an agent)
            if status == "New" and not (raw or {}).get("agentName"):
                no_agent_counter += 1
                if no_agent_counter >= no_agent_threshold:
                    status = "no_agent"
            else:
                no_agent_counter = 0

            if status in terminal_statuses or status == "no_agent":
                break

            time.sleep(poll_interval_sec)

        return {
            "status": status if raw else "timeout",
            "execution_id": execution_id,
            "raw": raw,
        }

    def check_agent_status(self, org_code: str = "") -> list[dict]:
        """Check T4 agent health via monitoring endpoint.

        Ref: code_ref.py t4_check_agent_status() / t4_get_agent_monitoring()
        Returns list of agent dicts with 'agentName', 'agentState', 'agentId'.
        """
        org = org_code or self.default_org_code
        if not org:
            logger.error("T4: org_code not configured — cannot check agents.")
            return []

        path = f"/{org}/monitoring/agents"
        try:
            result = self._authorized_request(
                "POST",
                path,
                params={"type": "AGENT", "offset": 0, "size": 100},
                use_rest_prefix=True,
            )
            agents: list[dict] = []
            if isinstance(result, dict):
                agents = result.get("data") or result.get("agents") or [result]
            elif isinstance(result, list):
                agents = result

            logger.info("T4: agent check returned %d agent(s).", len(agents))
            return agents
        except Exception as exc:
            logger.error("T4: agent status check failed: %s", exc)
            return []

    def sync_workflow_catalog(self, workflows: Optional[list[dict]] = None) -> int:
        """Persist fetched T4 workflows to the Postgres workflow_catalog table.

        Upserts by (workflow_id, org_code) so repeated calls stay idempotent.
        Returns the number of rows upserted. Best-effort — never raises.
        Ref: code_ref.py t4_fetch_all_workflows() \u2014 call after list_workflows().
        """
        try:
            from psycopg2.extras import Json as PgJson, execute_values
            from config.db import get_conn

            if workflows is None:
                workflows = self.list_workflows()

            if not workflows:
                return 0

            org = self.default_org_code
            rows = []
            for wf in workflows:
                wf_id = str(wf.get("workflowId") or wf.get("id") or "").strip()
                wf_name = str(wf.get("workflowName") or wf.get("name") or "").strip()
                if not wf_id or not wf_name:
                    continue
                rows.append((
                    wf_id,
                    org,
                    wf_name,
                    str(wf.get("description") or ""),
                    str(wf.get("category") or ""),
                    bool(wf.get("active", True)),
                    PgJson(wf.get("parameters") or []),
                    PgJson(wf),
                ))

            if not rows:
                return 0

            with get_conn() as conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """
                        INSERT INTO workflow_catalog
                            (workflow_id, org_code, workflow_name, description,
                             category, active, parameters, raw_data, fetched_at)
                        VALUES %s
                        ON CONFLICT (workflow_id, org_code) DO UPDATE SET
                            workflow_name = EXCLUDED.workflow_name,
                            description   = EXCLUDED.description,
                            category      = EXCLUDED.category,
                            active        = EXCLUDED.active,
                            parameters    = EXCLUDED.parameters,
                            raw_data      = EXCLUDED.raw_data,
                            fetched_at    = NOW()
                        """,
                        rows,
                        template=(
                            "(%s, %s, %s, %s, %s, %s, %s, %s, NOW())"
                        ),
                    )
                conn.commit()

            logger.info("T4: synced %d workflows to workflow_catalog.", len(rows))
            return len(rows)

        except Exception as exc:
            logger.warning("T4: workflow_catalog sync failed (non-fatal): %s", exc)
            return 0

    def index_workflows_to_rag(
        self,
        workflows: Optional[list[dict]] = None,
    ) -> int:
        """Index T4 workflows into the RAG rag_documents table (collection='tools').

        Each workflow becomes a searchable embedding document so the orchestrator's
        RAG search can surface T4 workflows by name, description, or category.
        Returns the number of documents indexed.

        Ref: code_ref.py t4_fetch_all_workflows() + rag.engine.index_tools()
        """
        try:
            from rag.engine import get_rag_engine
            from tools.ae_dynamic_tools import extract_dynamic_tool_mapping

            if workflows is None:
                workflows = self.list_workflows()

            if not workflows:
                return 0

            docs: list[dict] = []
            for wf in workflows:
                wf_id = str(wf.get("workflowId") or wf.get("id") or "").strip()
                wf_name = str(wf.get("workflowName") or wf.get("name") or "").strip()
                if not wf_name:
                    continue

                # Try to extract configured tool name for stable ID
                mapping = extract_dynamic_tool_mapping(wf)
                
                # FALLBACK: If no explicit mapping, use raw metadata so it's still searchable
                display_name = mapping.tool_name if mapping else wf_name
                description = mapping.description if mapping else str(wf.get("description") or f"Execute workflow {wf_name}")
                category = mapping.category if mapping else str(wf.get("category") or "automationedge")
                tags = mapping.tags if mapping else (wf.get("tags") or [])
                params = mapping.parameter_meta if mapping else (wf.get("parameters") or [])
                active = mapping.active if mapping else bool(wf.get("active", True))
                tier = mapping.tier if mapping else "medium_risk"

                # Build rich content for semantic search
                param_parts = []
                for p in params:
                    if isinstance(p, dict):
                        p_name = p.get("name", "")
                        disp = p.get("displayName") or p.get("displayname") or p.get("description")
                        # Catalogue uses 'optional': false for required
                        req = p.get("required") or p.get("is_required") or p.get("optional") is False
                        req_str = "Required" if req else "Optional"
                        
                        p_desc = p.get("description") or p.get("helpText") or req_str
                        label = f"{p_name} ({disp})" if disp and disp != p_name else p_name
                        
                        if p_name:
                            param_parts.append(f"  • {label}: {p_desc}")
                
                param_text = "\n".join(param_parts) if param_parts else "None"

                content = (
                    f"Workflow Tool: {display_name}\n"
                    f"Technical Name: {wf_name}\n"
                    f"Description: {description}\n"
                    f"Category: {category}\n"
                    f"Required Parameters:\n{param_text}\n"
                )
                if tags:
                    content += f"Tags: {', '.join(str(t) for t in tags)}\n"

                # UNIFIED ID: tool-{tool_name} matches ToolDefinition.to_rag_document()
                doc_id = f"tool-{display_name}"
                docs.append({
                    "id": doc_id,
                    "content": content,
                    "collection": "tools",
                    "metadata": {
                        "tool_name": display_name,
                        "workflow_id": wf_id,
                        "workflow_name": wf_name,
                        "category": category,
                        "source": "automationedge",
                        "dynamic": True,
                        "active": active,
                        "tags": tags,
                        "tier": tier,
                        "parameters": params, # STORE PARAMETERS FOR UI DISCOVERY
                    },
                })

            if not docs:
                return 0

            rag = get_rag_engine()
            rag.index_documents(docs, collection="tools")
            logger.info(
                "T4: indexed %d workflows into RAG rag_documents(tools).", len(docs)
            )
            return len(docs)

        except Exception as exc:
            logger.warning(
                "T4: workflow RAG indexing failed (non-fatal): %s", exc
            )
            return 0

    def get_cached_workflow_parameters(self, workflow_name: str) -> list[dict]:
        """Fetch workflow parameter schema from the local Postgres catalog.
        
        Returns a list of dicts: [{'name': '...', 'type': '...', 'required': bool, ...}]
        Returns empty list if not found or DB error.
        """
        try:
            from config.db import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT parameters FROM workflow_catalog WHERE workflow_name = %s",
                        (workflow_name,)
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        return list(row[0])
            return []
        except Exception as exc:
            logger.debug("T4: cached param lookup failed for %s: %s", workflow_name, exc)
            return []

    def get_cached_workflow_id(self, workflow_name: str) -> str:
        """Fetch workflow_id from local workflow_catalog for a workflow name."""
        try:
            from config.db import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT workflow_id FROM workflow_catalog WHERE workflow_name = %s",
                        (workflow_name,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        return str(row[0])
            return ""
        except Exception as exc:
            logger.debug("T4: cached workflow_id lookup failed for %s: %s", workflow_name, exc)
            return ""

    def sync_and_index_workflows(
        self,
        workflows: Optional[list[dict]] = None,
    ) -> dict:
        """Fetch T4 workflows once, then sync to DB and index for RAG in one shot.

        Call this on startup or whenever dynamic tools are reloaded.
        Returns counts: {'db_synced': N, 'rag_indexed': M}
        """
        if workflows is None:
            workflows = self.list_workflows()

        db_count = self.sync_workflow_catalog(workflows)
        rag_count = self.index_workflows_to_rag(workflows)
        logger.info(
            "T4: sync_and_index done — db_synced=%d rag_indexed=%d",
            db_count, rag_count,
        )
        return {"db_synced": db_count, "rag_indexed": rag_count}


    @staticmethod
    def _build_param_array(params: dict[str, Any]) -> list[dict]:
        entries: list[dict] = []
        for key, value in params.items():
            entries.append(
                {
                    "name": key,
                    "value": value,
                    "type": AutomationEdgeClient._infer_ae_type(value),
                }
            )
        return entries

    @staticmethod
    def _infer_ae_type(value: Any) -> str:
        if isinstance(value, bool):
            return "Boolean"
        if isinstance(value, int) and not isinstance(value, bool):
            return "Number"
        if isinstance(value, float):
            return "Number"
        if isinstance(value, list):
            return "List"
        if isinstance(value, dict):
            return "Object"
        return "String"

    @staticmethod
    def _extract_workflow_list(payload: Any) -> list[dict]:
        raw_list = []
        if isinstance(payload, list):
            raw_list = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            for key in ("workflows", "items", "results", "data", "records"):
                val = payload.get(key)
                if isinstance(val, list):
                    raw_list = [item for item in val if isinstance(item, dict)]
                    break
            
            if not raw_list:
                data_block = payload.get("data")
                if isinstance(data_block, dict):
                    for key in ("workflows", "items", "results", "records"):
                        val = data_block.get(key)
                        if isinstance(val, list):
                            raw_list = [item for item in val if isinstance(item, dict)]
                            break

        # Normalize keys: T4 uses 'name' and 'id', but agent expects 'workflowName' and 'workflowId'
        # Also map 'params' (Catalogue) to 'parameters' (Runtime/Agent)
        normalized = []
        for item in raw_list:
            norm_item = dict(item)
            if "name" in item and "workflowName" not in item:
                norm_item["workflowName"] = item["name"]
            if "id" in item and "workflowId" not in item:
                norm_item["workflowId"] = item["id"]
            
            # Catalogue uses 'params', Runtime/Agent expects 'parameters'
            if "params" in item and "parameters" not in item:
                norm_item["parameters"] = item["params"]
            
            normalized.append(norm_item)

        return normalized

    def close(self):
        self._client.close()


_automationedge_client: Optional[AutomationEdgeClient] = None


def get_automationedge_client() -> AutomationEdgeClient:
    """Lazy singleton used by tool handlers."""
    global _automationedge_client
    if _automationedge_client is None:
        _automationedge_client = AutomationEdgeClient()
    return _automationedge_client
