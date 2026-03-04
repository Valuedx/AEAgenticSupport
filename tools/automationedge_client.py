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
            CONFIG.get("AE_EXECUTE_ENDPOINT", "/execute")
        ).strip() or "/execute"
        self.workflows_endpoint = str(
            CONFIG.get("AE_WORKFLOWS_ENDPOINT", "/workflows")
        ).strip() or "/workflows"
        self.workflows_method = str(
            CONFIG.get("AE_WORKFLOWS_METHOD", "GET")
        ).strip().upper() or "GET"
        self.workflow_details_endpoint = str(
            CONFIG.get(
                "AE_WORKFLOW_DETAILS_ENDPOINT",
                "/workflows/{workflow_identifier}",
            )
        ).strip() or "/workflows/{workflow_identifier}"
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
        """Authenticate against AE and cache session token."""
        if not self.use_session_auth:
            return ""

        with self._auth_lock:
            if not force and self._is_token_valid():
                return self._session_token

            auth_path = self._rest_path(self.auth_endpoint)
            response = self._client.post(
                auth_path,
                data={"username": self.username, "password": self.password},
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
        params: Optional[dict] = None,
        org_code: str = "",
        user_id: str = "",
        source: str = "ae-agentic-support",
        **extra_fields,
    ) -> dict:
        if not workflow_name:
            raise ValueError("workflow_name is required")

        payload = {
            "orgCode": org_code or self.default_org_code,
            "workflowName": workflow_name,
            "userId": user_id or self.default_user_id,
            "source": source,
            "params": self._build_param_array(params or {}),
        }
        payload.update({k: v for k, v in extra_fields.items() if v is not None})

        return self._authorized_request(
            "POST",
            self.execute_endpoint,
            payload=payload,
            use_rest_prefix=True,
        )

    def list_workflows(self) -> list[dict]:
        payload = None
        first_method = self.workflows_method
        second_method = "POST" if first_method != "POST" else "GET"

        try:
            payload = self._authorized_request(
                first_method,
                self.workflows_endpoint,
                use_rest_prefix=True,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {400, 404, 405, 500, 501}:
                logger.info(
                    "Workflow list via %s failed (%s). Retrying with %s.",
                    first_method,
                    status,
                    second_method,
                )
                payload = self._authorized_request(
                    second_method,
                    self.workflows_endpoint,
                    use_rest_prefix=True,
                )
            else:
                raise

        workflows = self._extract_workflow_list(payload)
        if not workflows:
            logger.warning("AE workflow list API returned no workflows.")
        return workflows

    def get_workflow_details(self, workflow_identifier: str) -> dict:
        if not workflow_identifier:
            raise ValueError("workflow_identifier is required")

        endpoint = self.workflow_details_endpoint.format(
            workflow_identifier=workflow_identifier,
            workflow_id=workflow_identifier,
            workflow_name=workflow_identifier,
        )
        return self._authorized_request(
            self.workflow_details_method,
            endpoint,
            use_rest_prefix=True,
        )

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
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []

        for key in ("workflows", "items", "results", "data", "records"):
            val = payload.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]

        # Some APIs return {"data": {"workflows": [...]}}
        data_block = payload.get("data")
        if isinstance(data_block, dict):
            for key in ("workflows", "items", "results", "records"):
                val = data_block.get(key)
                if isinstance(val, list):
                    return [item for item in val if isinstance(item, dict)]

        return []

    def close(self):
        self._client.close()


_automationedge_client: Optional[AutomationEdgeClient] = None


def get_automationedge_client() -> AutomationEdgeClient:
    """Lazy singleton used by tool handlers."""
    global _automationedge_client
    if _automationedge_client is None:
        _automationedge_client = AutomationEdgeClient()
    return _automationedge_client
