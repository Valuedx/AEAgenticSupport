"""
Reusable AutomationEdge REST client.

Supports two auth modes:
1) Session-token flow via /authenticate (preferred for AE REST).
2) API-key bearer token (legacy compatibility for existing tools/tests).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
import logging
import re
import threading
from typing import Any, List, Optional, cast

import httpx
import urllib3

from config.settings import CONFIG
from security.workflow_access import can_execute_workflow, can_view_workflow, execute_auth_mode, is_read_enforced
from state.app_config import get_runtime_value

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("ops_agent.tools.ae_client")


@dataclass
class AEWorkflowParameter:
    name: str
    value: Any
    type: str = "String"


class AutomationEdgeClient:
    """HTTP client for AutomationEdge APIs with auto re-auth handling."""

    _WORKFLOW_LOOKUP_STOPWORDS = {
        "a", "an", "the", "this", "that", "my", "our", "your",
        "please", "can", "could", "would", "kindly", "me",
        "i", "we", "you", "he", "she", "they", "it",
        "have", "has", "had", "am", "is", "are", "was", "were", "be", "been",
        "bot", "workflow", "process", "job", "agent", "automation",
        "automationedge", "ae", "trigger", "run", "start", "execute",
        "launch", "submit", "rerun", "retry", "kick", "off", "for",
        "to", "of", "on", "in", "with", "and", "now",
    }

    def __init__(self, client: Optional[httpx.Client] = None):
        self.base_url = str(
            get_runtime_value("AE_BASE_URL", CONFIG["AE_BASE_URL"])
        ).rstrip("/")
        self.timeout = int(
            get_runtime_value(
                "AE_TIMEOUT_SECONDS",
                CONFIG.get("AE_TIMEOUT_SECONDS", 30),
            )
        )
        self.verify_ssl = False

        self.api_key = str(CONFIG.get("AE_API_KEY", "")).strip()
        self.username = str(CONFIG.get("AE_USERNAME", "")).strip()
        self.password = str(CONFIG.get("AE_PASSWORD", "")).strip()

        self.rest_base_path = str(
            get_runtime_value(
                "AE_REST_BASE_PATH",
                CONFIG.get("AE_REST_BASE_PATH", "/aeengine/rest"),
            )
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
        self.default_user_id = str(
            get_runtime_value(
                "AE_DEFAULT_USERID",
                CONFIG.get("AE_DEFAULT_USERID", "ops_agent"),
            )
        ).strip()

        self._session_token = ""
        self._token_expiry: Optional[datetime] = None
        self._metadata_fail_cache: set[str] = set()
        self._auth_lock = threading.Lock()

        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        
        # Silence httpx INFO logs which clutter the terminal during trial loops
        logging.getLogger("httpx").setLevel(logging.WARNING)
        
        # Cache for successful path prefixes to avoid redundant trials in the same session
        self._successful_paths: dict[str, str] = {}

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
        
        prefix = self.rest_base_path
        if endpoint_norm.startswith(prefix):
            return endpoint_norm
        return f"{prefix}{endpoint_norm}"

    def _auth_token_fields(self) -> list[str]:
        fields: list[str] = []
        seen: set[str] = set()
        for field in (
            self.token_field,
            "sessionToken",
            "session_token",
            "token",
            "sessionId",
        ):
            clean = str(field or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                fields.append(clean)
        return fields

    def _extract_auth_token(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        for field in self._auth_token_fields():
            token = str(payload.get(field, "")).strip()
            if token:
                return token
        return ""

    def _auth_request_candidates(self) -> list[dict[str, Any]]:
        credentials = {"username": self.username, "password": self.password}
        headers = {"Accept": "application/json"}
        rest_path = self._rest_path(self.auth_endpoint)
        raw_path = self._normalize_path(self.auth_endpoint)
        paths = [rest_path]
        if raw_path != rest_path:
            paths.append(raw_path)

        candidates: list[dict[str, Any]] = []
        for path in paths:
            candidates.append(
                {
                    "path": path,
                    "label": "query-params+ep",
                    "params": {**credentials, "ep": "true"},
                    "headers": headers,
                }
            )
            candidates.append(
                {
                    "path": path,
                    "label": "query-params",
                    "params": credentials,
                    "headers": headers,
                }
            )
            candidates.append(
                {
                    "path": path,
                    "label": "form-body+ep",
                    "data": {**credentials, "ep": "true"},
                    "headers": {
                        **headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                }
            )
            candidates.append(
                {
                    "path": path,
                    "label": "form-body",
                    "data": credentials,
                    "headers": {
                        **headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                }
            )
            candidates.append(
                {
                    "path": path,
                    "label": "json-body",
                    "payload": credentials,
                    "headers": headers,
                }
            )
        return candidates

    def authenticate(self, force: bool = False) -> str:
        """Authenticate with AE and cache a session token."""
        if not force and self._is_token_valid():
            return self._session_token

        with self._auth_lock:
            # Double-check after acquiring lock
            if not force and self._is_token_valid():
                return self._session_token

            last_error: Optional[Exception] = None
            checked_fields = ", ".join(self._auth_token_fields())
            retryable_statuses = {
                400, 401, 403, 404, 405, 415, 422, 429, 500, 501, 502, 503, 504
            }

            for attempt in self._auth_request_candidates():
                logger.info(
                    "Authenticating with AutomationEdge at %s (%s)",
                    attempt["path"],
                    attempt["label"],
                )
                try:
                    response = self._client.post(
                        attempt["path"],
                        params=attempt.get("params"),
                        data=attempt.get("data"),
                        json=attempt.get("payload"),
                        headers=attempt.get("headers"),
                    )
                    response.raise_for_status()
                    data = self._json_or_text(response)
                    token = self._extract_auth_token(data)
                    if not token:
                        raise RuntimeError(
                            f"Auth failed: token field missing in response. Checked {checked_fields}"
                        )

                    self._session_token = token
                    self._token_expiry = datetime.now(timezone.utc) + timedelta(
                        seconds=self.token_ttl_seconds
                    )
                    logger.info(
                        "AE Authentication successful. Token expires in %ds",
                        self.token_ttl_seconds,
                    )
                    return self._session_token
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code in retryable_statuses:
                        logger.debug(
                            "AE auth attempt failed with status %d for %s (%s)",
                            exc.response.status_code,
                            attempt["path"],
                            attempt["label"],
                        )
                        continue
                    logger.error("AE Authentication failed: %s", exc)
                    raise
                except Exception as exc:
                    last_error = exc
                    logger.debug(
                        "AE auth attempt failed for %s (%s): %s",
                        attempt["path"],
                        attempt["label"],
                        exc,
                    )
                    continue

            if last_error is not None:
                logger.error("AE Authentication failed: %s", last_error)
                raise last_error
            raise RuntimeError("AE Authentication failed: no auth request candidates were available")

    def _build_auth_headers(self, extra_headers: Optional[dict] = None) -> dict:
        headers = (extra_headers or {}).copy()
        if self.use_session_auth:
            if not self._is_token_valid():
                self.authenticate()
            if self._session_token:
                headers[self.session_header] = self._session_token
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers.setdefault("Content-Type", "application/json")
        return headers

    def _json_or_text(self, response: httpx.Response) -> Any:
        content_type = str(response.headers.get("Content-Type", "") or "").lower()
        if "zip" in content_type or "octet-stream" in content_type:
            return {
                "is_zip": True,
                "log_zip_content": response.content,
            }
        try:
            return response.json()
        except Exception:
            return response.text

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
        silent_on_status: Optional[list[int]] = None,
    ) -> Any:
        import time
        request_path = self._rest_path(path) if use_rest_prefix else self._normalize_path(path)
        request_headers = self._build_auth_headers(headers)
        
        # Ensure JSON content type for writable methods with payload
        if method.upper() in ("POST", "PUT") and payload is not None:
             request_headers.setdefault("Content-Type", "application/json")

        response = None
        max_retries = 2  # Total 3 attempts
        for attempt in range(max_retries + 1):
            try:
                # Construct full URL for logging
                full_url = str(self._client.base_url).rstrip("/") + request_path
                
                response = self._client.request(
                    method.upper(),
                    request_path,
                    params=params,
                    json=payload,
                    data=data,
                    headers=request_headers,
                )
                
                # Debug logging for troubleshooting paths
                if response.status_code >= 400:
                    logger.debug(f"DEBUG API CALL: {method} {full_url} | PARAMS: {params} | PAYLOAD: {payload} | STATUS: {response.status_code}")

                if response.status_code == 401 and retry_on_401 and self.use_session_auth and attempt < max_retries:
                    logger.info("AE returned 401, re-authenticating and retrying once.")
                    self.authenticate(force=True)
                    request_headers = self._build_auth_headers(headers)
                    continue
                
                if response.status_code == 429 and attempt < max_retries:
                    wait_seconds = 5
                    try:
                        err_json = response.json()
                        msg = str(err_json.get("message", ""))
                        if "try after" in msg.lower():
                            # Extract number from "Please try after 51 seconds."
                            words = msg.split()
                            for i, word in enumerate(words):
                                if word.lower() == "after":
                                    wait_seconds = int(words[i+1])
                                    break
                    except Exception:
                        pass
                        
                    logger.warning("AE returned 429 Too Many Requests for %s. Backing off for %ds.", request_path, wait_seconds)
                    time.sleep(wait_seconds)
                    continue
                    
                break
            except httpx.RequestError as exc:
                if attempt < max_retries:
                    logger.warning("Request error for %s (attempt %d): %s. Retrying after 2s.", request_path, attempt+1, exc)
                    time.sleep(2)
                    continue
                raise exc

        if response is None:
            raise RuntimeError(f"Request to {request_path} failed after all retries.")

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            silent = silent_on_status or []
            
            # Extract error body safely
            try:
                content_type = response.headers.get("Content-Type", "").lower()
                is_json = "application/json" in content_type
                
                if is_json:
                    error_body = response.text
                else:
                    # Truncate non-JSON (likely HTML) bodies to avoid log spam
                    error_body = "".join([response.text[i] for i in range(min(len(response.text), 200))])
                    if len(response.text) > 200:
                        error_body += "... [truncated]"
            except Exception:
                error_body = "<could not read body>"

            if response is not None and response.status_code in silent:
                # T4 discovery often hits 400/404 on workflows without specific configs.
                # Use DEBUG for these known common errors to avoid log spam.
                if response.status_code in (silent_on_status or []):
                    # Use full_url for more informative logging
                    full_url = str(self._client.base_url).rstrip("/") + request_path
                    # Use DEBUG level for trial loops to avoid user confusion
                    text_short = "".join([response.text[i] for i in range(min(len(response.text), 200))])
                    logger.debug("AE API Silent Failure %d for %s: %s", response.status_code, full_url, text_short)
                    return None
            else:
                status = response.status_code if response is not None else 0
                logger.error("AE API Error %d for %s: %s", status, request_path, error_body)
            raise exc

        return self._json_or_text(response)

    def _extract_body_safe(self, exc: httpx.HTTPStatusError) -> str:
        """Safely extract the response body from an HTTPStatusError."""
        try:
            response = exc.response
            content_type = response.headers.get("Content-Type", "").lower()
            if "application/json" in content_type:
                return response.text
            # Truncate non-JSON bodies
            body = "".join([response.text[i] for i in range(min(len(response.text), 200))])
            if len(body) > 200:
                body += "... [truncated]"
            return body
        except Exception:
            return "<could not read body>"

    def restart_request(self, execution_id: str, reason: str = "") -> dict:
        """Restart a workflow instance using the T4 PUT /restart endpoint.

        T4 uses PUT to /restart with the execution_id in the path to resume
        an instance using its original state.
        
        Raises RestartLimitError (a subclass of RuntimeError) with error code AE-2624
        when the restart limit of 10 has been reached.
        """
        paths = [
            f"/{self.default_org_code}/workflowinstances/{execution_id}/restart" if self.default_org_code else None,
            f"/workflowinstances/{execution_id}/restart",
        ]
        paths = [p for p in paths if p]
        last_exc = None
        ae2624_exc = None
        # T4 variability: try with and without /aeengine/rest prefix
        for use_prefix in (True, False):
            for path in paths:
                try:
                    return self._authorized_request(
                        "PUT",
                        path,
                        payload={"reason": reason},
                        use_rest_prefix=use_prefix
                    )
                except httpx.HTTPStatusError as exc:
                    # Detect AE-2624 restart limit reached - remember it specifically
                    if exc.response.status_code == 500:
                        try:
                            err_body = exc.response.json()
                            if str(err_body.get("errorCode", "")).strip() == "AE-2624":
                                ae2624_exc = RuntimeError(
                                    f"AE-2624: {err_body.get('message', 'Maximum restart limit reached for execution ' + str(execution_id))}. "
                                    f"Consider using resubmit instead."
                                )
                                # Continue trying other paths to be thorough, but track this
                                last_exc = ae2624_exc
                                continue
                        except Exception:
                            pass
                    last_exc = exc
                    logger.debug(f"Restart attempt failed for {path} (prefix={use_prefix}): {exc}")
                    continue
                except Exception as exc:
                    last_exc = exc
                    logger.debug(f"Restart attempt failed for {path} (prefix={use_prefix}): {exc}")
                    continue
        # If we saw AE-2624, surface it clearly (not the last 403)
        if ae2624_exc and isinstance(ae2624_exc, Exception):
            raise ae2624_exc
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Could not restart {execution_id}")

    def terminate_request(self, request_id: str, reason: str = "") -> dict:
        """Terminate a running instance."""
        paths = [
            f"/{self.default_org_code}/workflowinstances/{request_id}/terminate" if self.default_org_code else None,
            f"/workflowinstances/{request_id}/terminate",
        ]
        paths = [p for p in paths if p]
        last_exc = None
        for path in paths:
            try:
                return self._authorized_request("POST", path, payload={"reason": reason}, use_rest_prefix=True)
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Could not terminate {request_id}")

    def resubmit_request(self, request_id: str, reason: str = "", from_failure_point: bool = True) -> dict:
        """Resubmit a failed instance (either from failure point or start)."""
        paths = [
            f"/{self.default_org_code}/workflowinstances/{request_id}/resubmit" if self.default_org_code else None,
            f"/workflowinstances/{request_id}/resubmit",
        ]
        paths = [p for p in paths if p]
        payload = {"reason": reason, "fromFailurePoint": from_failure_point}
        last_exc = None
        for path in paths:
            try:
                return self._authorized_request("POST", path, payload=payload, use_rest_prefix=True)
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Could not resubmit {request_id}")

    def disable_schedule(self, schedule_id: str, reason: str = "") -> dict:
        """Disable or pause a schedule using T4 PUT /disable endpoint."""
        paths = [
            f"/tenants/{self.default_org_code}/workflows/schedules/{schedule_id}/disable" if self.default_org_code else None,
            f"/{self.default_org_code}/schedules/{schedule_id}/disable" if self.default_org_code else None,
            f"/schedules/{schedule_id}/disable",
        ]
        paths = [p for p in paths if p]
        last_exc = None
        for use_prefix in (True, False):
            for path in paths:
                try:
                    return self._authorized_request("PUT", path, payload={"reason": reason}, use_rest_prefix=use_prefix)
                except Exception as exc:
                    last_exc = exc
                    continue
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Could not disable schedule {schedule_id}")

    def enable_schedule(self, schedule_id: str, reason: str = "") -> dict:
        """Enable or resume a schedule using T4 PUT /enable endpoint."""
        paths = [
            f"/tenants/{self.default_org_code}/workflows/schedules/{schedule_id}/enable" if self.default_org_code else None,
            f"/{self.default_org_code}/schedules/{schedule_id}/enable" if self.default_org_code else None,
            f"/schedules/{schedule_id}/enable",
        ]
        paths = [p for p in paths if p]
        last_exc = None
        for use_prefix in (True, False):
            for path in paths:
                try:
                    return self._authorized_request("PUT", path, payload={"reason": reason}, use_rest_prefix=use_prefix)
                except Exception as exc:
                    last_exc = exc
                    continue
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Could not enable schedule {schedule_id}")

    def get_schedule(self, schedule_id: str) -> dict:
        """Get schedule configuration details."""
        paths = [
            f"/tenants/{self.default_org_code}/workflows/schedules/{schedule_id}" if self.default_org_code else None,
            f"/{self.default_org_code}/schedules/{schedule_id}" if self.default_org_code else None,
            f"/schedules/{schedule_id}",
        ]
        paths = [p for p in paths if p]
        last_exc = None
        for use_prefix in (True, False):
            for path in paths:
                try:
                    return self._authorized_request("GET", path, use_rest_prefix=use_prefix)
                except Exception as exc:
                    last_exc = exc
                    continue
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Could not fetch schedule {schedule_id}")

    def search_schedules(self, workflow_id: str = "", filters: Optional[dict] = None) -> list[dict]:
        """Search schedules using T4 POST /schedules endpoint."""
        params: dict[str, Any] = {"offset": 0, "size": 100, "order": "desc"}
        body: dict[str, Any] = {}
        
        if workflow_id:
            try:
                body["workflowId"] = int(workflow_id)
            except (ValueError, TypeError):
                body["workflowId"] = workflow_id
                
        if filters:
            body.update(filters)
            if "offset" in filters: params["offset"] = filters["offset"]
            if "size" in filters: params["size"] = filters["size"]
            
        paths = [
            f"/tenants/{self.default_org_code}/workflows/schedules" if self.default_org_code else None,
            f"/{self.default_org_code}/schedules" if self.default_org_code else None,
            "/schedules",
        ]
        paths = [p for p in paths if p]
        
        last_exc = None
        for use_prefix in (True, False):
            for path in paths:
                try:
                    raw = self._authorized_request("POST", path, params=params, payload=body, use_rest_prefix=use_prefix)
                    return self._extract_list(raw, keys=("data", "schedules", "items"))
                except Exception as exc:
                    last_exc = exc
                    continue
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        return []

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
        silent_on_status: Optional[list[int]] = None,
    ) -> Any:
        return self._authorized_request(
            method,
            path,
            params=params,
            payload=payload,
            data=data,
            headers=headers,
            use_rest_prefix=use_rest_prefix,
            silent_on_status=silent_on_status,
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
        agent_id: str = "",
        agent_name: str = "",
        **extra_fields,
    ) -> dict:
        if not workflow_name:
            raise ValueError("workflow_name is required")

        caller_user_id = str(user_id or "").strip()
        mode = execute_auth_mode()
        if mode == "service_account":
            effective_user_id = (
                str(self.default_user_id or "").strip()
                or str(self.username or "").strip()
                or "ops_agent"
            )
        else:
            effective_user_id = caller_user_id or self.default_user_id

        payload = {
            "orgCode": org_code or self.default_org_code,
            "workflowName": workflow_name,
            "userId": effective_user_id,
            "source": source,
            "responseMailSubject": mail_subject or "null",
            "params": self._build_param_array(params or {}),
        }
        # When a specific running agent is identified, include it so AE
        # dispatches to that agent instead of picking an offline one.
        if agent_id:
            payload["agentId"] = str(agent_id)
        if agent_name:
            payload["agentName"] = str(agent_name)
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
        page_size: int = 50,
        all_pages: bool = True,
    ) -> list[dict]:
        """Fetch workflows from T4.
        
        Prioritizes /workflows/catalogue (rich metadata as per user request).
        Falls back to /workflows/runtime if needed.
        """
        all_workflows: list[dict] = []
        current_offset = offset

        # Try configured workflow endpoint first, honoring configured method.
        endpoint = self._rest_path(self.workflows_endpoint)
        while True:
            try:
                payload = self._request_workflow_page(
                    endpoint,
                    offset=current_offset,
                    page_size=page_size,
                    source_label="workflow endpoint",
                )
            except httpx.HTTPStatusError as exc:
                if current_offset == 0:
                    logger.warning(
                        "Primary workflow endpoint failed (%s). Trying runtime fallback.",
                        exc.response.status_code,
                    )
                    return self._list_workflows_runtime(offset, page_size, all_pages)
                raise

            batch = self._extract_workflow_list(payload)
            if not batch:
                break
            all_workflows.extend(batch)
            logger.info(
                "Workflows fetched (primary) offset=%d -> %d records",
                current_offset,
                len(batch),
            )
            if not all_pages or len(batch) < page_size:
                break
            current_offset = int(current_offset) + int(page_size)

        return all_workflows

    def _workflow_request_methods(self) -> list[str]:
        preferred = str(self.workflows_method or "GET").strip().upper()
        if preferred not in {"GET", "POST"}:
            preferred = "GET"
        secondary = "POST" if preferred == "GET" else "GET"
        return [preferred, secondary]

    def _request_workflow_page(
        self,
        endpoint: str,
        *,
        offset: int,
        page_size: int,
        source_label: str,
    ) -> Any:
        params = {"offset": offset, "size": page_size}
        last_exc: Optional[httpx.HTTPStatusError] = None
        methods = self._workflow_request_methods()
        for idx, method in enumerate(methods):
            try:
                return self._authorized_request(method, endpoint, params=params)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if idx < len(methods) - 1:
                    logger.warning(
                        "%s %s failed (%s). Trying %s.",
                        source_label,
                        method,
                        exc.response.status_code,
                        methods[idx + 1],
                    )
                    continue
                raise
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        return {}

    def _list_workflows_runtime(self, offset: int, page_size: int, all_pages: bool) -> list[dict]:
        """Fallback to /workflows/runtime if Catalogue is unavailable."""
        all_workflows: list[dict] = []
        current_offset = offset
        endpoint = self._rest_path(self.workflows_runtime_endpoint)
        while True:
            payload = self._request_workflow_page(
                endpoint,
                offset=current_offset,
                page_size=page_size,
                source_label="workflow runtime",
            )
             
            batch = self._extract_workflow_list(payload)
            if not batch:
                break
            all_workflows.extend(batch)
            logger.info("Workflows fetched (Runtime) offset=%d -> %d records", current_offset, len(batch))
            if not all_pages or len(batch) < page_size:
                break
            current_offset = int(current_offset) + int(page_size)
        return all_workflows

    def resolve_cached_workflow_name(
        self,
        workflow_name: str,
        *,
        user_id: str = "",
        org_code: str = "",
    ) -> str:
        """Resolve workflow name from local catalog using safe variants and fuzzy fallbacks."""
        import re
        name = str(workflow_name or "").strip()
        if not name:
            return ""

        # 1. Build prioritized variants
        # Primary: Original name (trimmed)
        # Secondary: Name without "(ID: 1234)" suffix (common in UI lists)
        # Tertiary: Underscore/Slug variants (legacy support)
        variants = []
        variants.append(name)
        
        # Strip ID suffix if present: "My Workflow (ID: 101)" -> "My Workflow"
        no_id = re.sub(r"\s*\(ID:\s*\d+\)\s*$", "", name, flags=re.IGNORECASE).strip()
        if no_id and no_id != name:
            variants.append(no_id)

        # Slug/Underline variants for legacy shell-style matching
        base = name.replace("-", "_").replace(" ", "_").strip()
        if base and base != name and base != no_id:
            variants.append(base)
            if not base.upper().startswith("WF_") and len(base) > 2:
                variants.append(f"WF_{base}")

        seen = set()
        ordered = []
        for v in variants:
            key = v.lower()
            if key not in seen:
                seen.add(key)
                ordered.append(v)

        if not ordered:
            return ""

        try:
            from config.db import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    target_org = str(org_code or "").strip()

                    if name.isdigit():
                        sql_id = (
                            "SELECT workflow_id, workflow_name, org_code "
                            "FROM workflow_catalog WHERE workflow_id = %s"
                        )
                        id_params: list[Any] = [name]
                        if target_org:
                            sql_id += " AND org_code = %s"
                            id_params.append(target_org)
                        sql_id += " ORDER BY active DESC, fetched_at DESC"
                        cur.execute(sql_id, tuple(id_params))
                        for row in cur.fetchall():
                            row_id = str(row[0]).strip()
                            row_name = str(row[1]).strip()
                            row_org = str(row[2]).strip()
                            if user_id and not can_view_workflow(user_id, row_id, row_org or target_org):
                                continue
                            return row_name

                    # Phase 1: Exact search across prioritized variants
                    placeholders = ",".join(["%s"] * len(ordered))
                    sql = (
                        "SELECT workflow_id, workflow_name, org_code "
                        f"FROM workflow_catalog WHERE lower(workflow_name) IN ({placeholders})"
                    )
                    params: list[Any] = [value.lower() for value in ordered]
                    if target_org:
                        sql += " AND org_code = %s"
                        params.append(target_org)
                    
                    sql += " ORDER BY active DESC, fetched_at DESC"
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()
                    
                    # Return first exact match found in variant order
                    for candidate in ordered:
                        for row in rows:
                            row_id = str(row[0]).strip()
                            row_name = str(row[1]).strip()
                            row_org = str(row[2]).strip()
                            if row_name.lower() == candidate.lower():
                                if user_id and not can_view_workflow(user_id, row_id, row_org or target_org):
                                    continue
                                return row_name

                    # Phase 2: Partial/Contains fallback if no exact match
                    # (Helpful for long names with trailing version codes or spaces)
                    clean_query = re.sub(r"[^a-zA-Z0-9]", "%", no_id or name)
                    if len(clean_query) > 3:
                        sql_fuzzy = (
                            "SELECT workflow_id, workflow_name, org_code "
                            "FROM workflow_catalog WHERE lower(workflow_name) LIKE %s "
                        )
                        like_params: list[Any] = [f"%{clean_query.lower()}%"]
                        if target_org:
                            sql_fuzzy += " AND org_code = %s"
                            like_params.append(target_org)
                        
                        sql_fuzzy += " ORDER BY length(workflow_name) ASC, active DESC LIMIT 5"
                        cur.execute(sql_fuzzy, tuple(like_params))
                        fuzzy_rows = cur.fetchall()
                        for f_row in fuzzy_rows:
                            f_id, f_name, f_org = str(f_row[0]), str(f_row[1]), str(f_row[2])
                            if user_id and not can_view_workflow(user_id, f_id, f_org or target_org):
                                continue
                            logger.info(f"T4 Resolver: Fuzzy match '{name}' -> '{f_name}'")
                            return f_name

            return ""
        except Exception as exc:
            logger.debug("T4: cached workflow name resolution failed for %s: %s", workflow_name, exc)
        live_match = self._resolve_live_workflow_record(
            name,
            user_id=user_id,
            org_code=org_code,
            require_execute=False,
            min_score=0.72,
        )
        if live_match:
            live_name = str(
                live_match.get("workflowName")
                or live_match.get("name")
                or ""
            ).strip()
            if live_name:
                try:
                    self.sync_workflow_catalog([live_match])
                except Exception:
                    pass
                logger.info("T4 Resolver: Live match '%s' -> '%s'", name, live_name)
                return live_name
        return ""

    @staticmethod
    def _extract_workflow_params_from_payload(payload: Any) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        for key in ("parameters", "configurationParameters", "params"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        wf_cfg = payload.get("workflowConfiguration")
        if isinstance(wf_cfg, dict):
            for key in ("parameters", "configurationParameters", "params"):
                value = wf_cfg.get(key)
                if isinstance(value, list):
                    return [dict(item) for item in value if isinstance(item, dict)]
        return []

    def _resolve_live_workflow_record(
        self,
        workflow_name: str,
        *,
        user_id: str = "",
        org_code: str = "",
        require_execute: bool = False,
        min_score: float = 0.72,
    ) -> Optional[dict]:
        text = str(workflow_name or "").strip()
        if not text:
            return None

        target_org = str(org_code or "").strip()
        query_norm = self._normalize_workflow_lookup_text(text)
        predicate = can_execute_workflow if require_execute else can_view_workflow

        try:
            workflows = self.list_workflows(page_size=200, all_pages=True)
        except Exception as exc:
            logger.debug("T4: live workflow lookup failed for %s: %s", workflow_name, exc)
            return None

        best_score = 0.0
        best_match: Optional[dict] = None
        for item in workflows:
            if not isinstance(item, dict):
                continue
            candidate_name = str(
                item.get("workflowName")
                or item.get("name")
                or ""
            ).strip()
            if not candidate_name:
                continue
            candidate_id = str(item.get("workflowId") or item.get("id") or "").strip()
            candidate_org = str(
                item.get("orgCode")
                or item.get("org_code")
                or self.default_org_code
                or target_org
            ).strip()
            if target_org and candidate_org and candidate_org != target_org:
                continue
            if user_id and candidate_id and not predicate(user_id, candidate_id, candidate_org or target_org):
                continue

            if text.isdigit() and candidate_id == text:
                return dict(item)

            candidate_norm = self._normalize_workflow_lookup_text(candidate_name)
            if candidate_name.lower() == text.lower() or (query_norm and candidate_norm == query_norm):
                return dict(item)

            score = self._workflow_match_score(text, candidate_name)
            if score >= min_score and score > best_score:
                best_score = score
                best_match = dict(item)

        return best_match

    def _hydrate_live_workflow_record(self, workflow: dict) -> dict:
        hydrated = dict(workflow or {})
        params = self._extract_workflow_params_from_payload(hydrated)
        if params:
            hydrated["parameters"] = params
            return hydrated

        workflow_id = str(hydrated.get("workflowId") or hydrated.get("id") or "").strip()
        workflow_name = str(hydrated.get("workflowName") or hydrated.get("name") or "").strip()
        for identifier in (workflow_id, workflow_name):
            if not identifier:
                continue
            try:
                details = self.get_workflow_details(identifier)
            except Exception:
                continue
            if not isinstance(details, dict):
                continue
            params = self._extract_workflow_params_from_payload(details)
            if params:
                hydrated["parameters"] = params
            if not hydrated.get("workflowName") and details.get("workflowName"):
                hydrated["workflowName"] = details.get("workflowName")
            if not hydrated.get("name") and details.get("name"):
                hydrated["name"] = details.get("name")
            if not hydrated.get("description") and details.get("description"):
                hydrated["description"] = details.get("description")
            if "active" not in hydrated and "active" in details:
                hydrated["active"] = details.get("active")
            if hydrated.get("parameters"):
                break
        return hydrated

    @classmethod
    def _normalize_workflow_lookup_text(cls, text: str) -> str:
        # Strip "(ID: 1234)" or similar suffixes before normalizing
        import re
        clean = re.sub(r"\s*\(ID:\s*\d+\)\s*", " ", str(text or ""), flags=re.IGNORECASE)
        clean = clean.strip().lower().replace("_", " ").replace("-", " ")
        clean = re.sub(r"\bwf\s+", "", clean)
        clean = re.sub(r"[^a-z0-9\s]", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    @classmethod
    def _workflow_lookup_tokens(cls, text: str) -> list[str]:
        normalized = cls._normalize_workflow_lookup_text(text)
        if not normalized:
            return []
        return [
            token
            for token in normalized.split()
            if token and token not in cls._WORKFLOW_LOOKUP_STOPWORDS
        ]

    @classmethod
    def is_specific_workflow_lookup_query(cls, text: str) -> bool:
        raw_text = str(text or "").strip()
        normalized = cls._normalize_workflow_lookup_text(raw_text)
        if not normalized:
            return False

        raw_words = [word for word in normalized.split() if word]
        lookup_tokens = cls._workflow_lookup_tokens(raw_text)
        if not lookup_tokens:
            return False

        has_identifier_shape = (
            any(ch in raw_text for ch in ("_", "-"))
            or bool(re.search(r"\d", raw_text))
            or bool(re.search(r"`[^`]+`|'[^']+'|\"[^\"]+\"", raw_text))
        )
        if has_identifier_shape:
            return True

        token_ratio = len(lookup_tokens) / max(len(raw_words), 1)

        if len(lookup_tokens) >= 3:
            return token_ratio > 0.6
        if len(lookup_tokens) == 2:
            return len(raw_words) <= 3 or token_ratio >= 0.75
        if len(lookup_tokens) == 1:
            return len(raw_words) == 1 and len(lookup_tokens[0]) >= 5
        return False

    @classmethod
    def _workflow_match_score(cls, query: str, workflow_name: str) -> float:
        query_norm = cls._normalize_workflow_lookup_text(query)
        workflow_norm = cls._normalize_workflow_lookup_text(workflow_name)
        if not query_norm or not workflow_norm:
            return 0.0

        query_tokens = cls._workflow_lookup_tokens(query)
        workflow_tokens = cls._workflow_lookup_tokens(workflow_name)
        if not query_tokens or not workflow_tokens:
            return 0.0

        query_phrase = " ".join(query_tokens)
        workflow_phrase = " ".join(workflow_tokens)
        if not query_phrase or not workflow_phrase:
            return 0.0

        if query_phrase == workflow_phrase:
            return 1.0
        if set(query_tokens) == set(workflow_tokens):
            return 0.97

        common = set(query_tokens) & set(workflow_tokens)
        if not common:
            return 0.0

        workflow_coverage = len(common) / max(len(set(workflow_tokens)), 1)
        query_coverage = len(common) / max(len(set(query_tokens)), 1)
        seq_ratio = SequenceMatcher(None, query_phrase, workflow_phrase).ratio()
        contains_bonus = 0.0
        if query_phrase in workflow_phrase or workflow_phrase in query_phrase:
            contains_bonus = 0.15
        all_workflow_tokens_present = set(workflow_tokens).issubset(set(query_tokens))
        subset_bonus = 0.1 if all_workflow_tokens_present else 0.0

        score = (
            0.45 * workflow_coverage
            + 0.25 * query_coverage
            + 0.25 * seq_ratio
            + contains_bonus
            + subset_bonus
        )
        return min(score, 1.0)

    def suggest_cached_workflow_names(
        self,
        query: str,
        *,
        user_id: str = "",
        org_code: str = "",
        require_execute: bool = False,
        limit: int = 5,
        min_score: float = 0.45,
    ) -> list[str]:
        text = str(query or "").strip()
        if not text:
            return []
        if not self.is_specific_workflow_lookup_query(text):
            return []

        try:
            from config.db import get_conn

            target_org = str(org_code or "").strip()
            with get_conn() as conn:
                with conn.cursor() as cur:
                    sql = (
                        "SELECT workflow_id, workflow_name, org_code "
                        "FROM workflow_catalog WHERE active = TRUE"
                    )
                    params: list[Any] = []
                    if target_org:
                        sql += " AND org_code = %s"
                        params.append(target_org)
                    sql += " ORDER BY fetched_at DESC"
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()
        except Exception as exc:
            logger.debug("T4: cached workflow suggestions failed for %s: %s", query, exc)
            return []

        predicate = can_execute_workflow if require_execute else can_view_workflow
        best_by_name: dict[str, tuple[float, str]] = {}
        for row in rows:
            workflow_id = str(row[0] or "").strip() if row else ""
            workflow_name = str(row[1] or "").strip() if row and len(row) > 1 else ""
            workflow_org = str(row[2] or "").strip() if row and len(row) > 2 else target_org
            if not workflow_name:
                continue
            if user_id and workflow_id and not predicate(user_id, workflow_id, workflow_org or target_org):
                continue
            score = self._workflow_match_score(text, workflow_name)
            if score < min_score:
                continue
            key = workflow_name.lower()
            prev = best_by_name.get(key)
            if not prev or score > prev[0]:
                best_by_name[key] = (score, workflow_name)

        ranked = sorted(best_by_name.values(), key=lambda item: (-item[0], item[1].lower()))
        return [name for _, name in ranked[: max(int(limit or 5), 1)]]

    def resolve_workflow_name_from_text(
        self,
        query: str,
        *,
        user_id: str = "",
        org_code: str = "",
        require_execute: bool = False,
        min_score: float = 0.72,
    ) -> str:
        suggestions = self.suggest_cached_workflow_names(
            query,
            user_id=user_id,
            org_code=org_code,
            require_execute=require_execute,
            limit=1,
            min_score=min_score,
        )
        return suggestions[0] if suggestions else ""

    def get_cached_workflow_id(self, workflow_name: str) -> Optional[str]:
        """Resolve numeric workflow_id from local catalog for a given technical name."""
        name = str(workflow_name or "").strip()
        if not name:
            return None
        try:
            from config.db import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT workflow_id FROM workflow_catalog WHERE lower(workflow_name) = %s",
                        (name.lower(),),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        id_val = str(row[0]).strip()
                        logger.info(f"T4 Catalog: Resolved name '{name}' to ID '{id_val}'")
                        return id_val
            return None
        except Exception as exc:
            logger.debug("T4: cached workflow ID resolution failed for %s: %s", name, exc)
            return None
    def resolve_workflow_via_rag(
        self,
        query: str,
        top_k: int = 3,
        *,
        user_id: str = "",
        org_code: str = "",
    ) -> str:
        """Use RAG semantic search to resolve a bot name from a user query."""
        try:
            from rag.engine import get_rag_engine
            rag = get_rag_engine()
            if user_id:
                results = rag.search_tools_for_user(
                    query,
                    user_id=user_id,
                    org_code=org_code,
                    top_k=top_k,
                )
            elif is_read_enforced():
                results = []
            else:
                results = rag.search_tools(query, top_k=top_k)
            if results:
                # Pick the best match that has a workflow name in metadata
                for res in results:
                    meta = res.get("metadata") or {}
                    wf_name = meta.get("workflow_name")
                    if wf_name:
                        logger.info(f"RAG resolved '{query}' to '{wf_name}' (score: {res.get('rrf_score', 'N/A')})")
                        return wf_name
            return ""
        except Exception as exc:
            logger.warning(f"RAG workflow resolution failed for '{query}': {exc}")
            return ""

    def get_workflow_details(self, workflow_identifier: str) -> dict:
        if not workflow_identifier:
            raise ValueError("workflow_identifier is required")
        
        wf_ident = str(workflow_identifier).strip()
        if wf_ident in self._metadata_fail_cache:
            logger.debug("Skipping workflow details for %s (marked in fail cache)", wf_ident)
            return {}

        if not wf_ident.isdigit():
            resolved = self.resolve_cached_workflow_name(wf_ident)
            if resolved:
                wf_ident = resolved
            elif not wf_ident.upper().startswith("WF_"):
                raise ValueError(
                    f"Workflow '{workflow_identifier}' was not found in catalog. "
                    "Please use the exact workflow name (usually starts with WF_)."
                )

        org = self.default_org_code
        endpoint = self.workflow_details_endpoint.format(
            org_code=org,
            workflow_identifier=wf_ident,
            workflow_id=wf_ident,
            workflow_name=wf_ident,
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
                        silent_on_status=[400, 404, 500],
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        last_exc = exc
                        # If it's a definitive "unsupported" error, mark it in negative cache
                        if exc.response.status_code == 400 and "AE-1005" in self._extract_body_safe(exc):
                            self._metadata_fail_cache.add(wf_ident)
                        continue
                    raise
        if last_exc and isinstance(last_exc, Exception):
            # Also catch the final failure to mark cache
            if isinstance(last_exc, httpx.HTTPStatusError):
                 self._metadata_fail_cache.add(wf_ident)
            raise last_exc
        raise RuntimeError(f"Could not fetch workflow details for {workflow_identifier}")

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
        
        # T4 variability: try with /aeengine/rest prefix first for T4 consistency
        for use_prefix in (True, False):
            for path in paths:
                try:
                    result = self._authorized_request(
                        "GET", path, 
                        use_rest_prefix=use_prefix,
                        silent_on_status=[400, 404, 500]
                    )
                    return result
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        last_exc = exc
                        continue
                    raise
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Could not fetch status for execution {execution_id}")

    def get_workflow_latest_instance(self, workflow_name: str, org_code: str = "") -> dict:
        """Get latest workflow instance for a workflow.

        Tries modern local/mock status endpoints first, then org-scoped/global T4 variants
        with and without REST prefix.
        Returns the first item when API returns a list.
        """
        name = str(workflow_name or "").strip()
        if not name:
            raise ValueError("workflow_name is required")

        resolved = self.resolve_cached_workflow_name(name)
        candidate_names: list[str] = []
        for candidate in (
            resolved,
            name,
            f"WF_{name}" if not resolved and not name.upper().startswith("WF_") else "",
        ):
            candidate = str(candidate or "").strip()
            if candidate and candidate.lower() not in {item.lower() for item in candidate_names}:
                candidate_names.append(candidate)

        org = (org_code or self.default_org_code or "").strip()
        modern_paths = [f"/api/v1/workflows/{candidate}/instances" for candidate in candidate_names]
        t4_paths = []
        for candidate in candidate_names:
            if org:
                t4_paths.append(f"/{org}/workflows/{candidate}/instances")
            t4_paths.append(f"/workflows/{candidate}/instances")

        last_exc: Optional[Exception] = None
        for path in modern_paths:
            try:
                result = self._authorized_request(
                    "GET", path, 
                    use_rest_prefix=False,
                    silent_on_status=[400, 404, 500]
                )
                if isinstance(result, list):
                    return result[0] if result else {}
                if isinstance(result, dict):
                    items = result.get("instances") or result.get("executions") or []
                    if isinstance(items, list):
                        return items[0] if items else result
                    return result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {400, 404, 500}:
                    last_exc = exc
                    continue
                raise

        for use_prefix in (False, True):
            for path in t4_paths:
                try:
                    result = self._authorized_request(
                        "GET", path, 
                        use_rest_prefix=use_prefix,
                        silent_on_status=[400, 404, 500]
                    )
                    if isinstance(result, list):
                        return result[0] if result else {}
                    if isinstance(result, dict):
                        items = result.get("instances") or result.get("executions") or []
                        if isinstance(items, list):
                            return items[0] if items else result
                        return result
                    return {}
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        last_exc = exc
                        continue
                    raise

        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(
            f"Could not fetch latest instance for workflow '{workflow_name}'"
        )

    def get_workflow_instance_by_id(self, instance_id: str | int) -> dict:
        """Fetch a specific workflow instance by its numeric ID using T4 GET /workflowinstances/{id}."""
        ident = str(instance_id).strip()
        if not ident:
            return {}
            
        # T4 usually supports /workflowinstances/{id} directly
        paths = [
            f"/workflowinstances/{ident}",
            f"/tenants/{self.default_org_code}/workflowinstances/{ident}" if self.default_org_code else None,
            f"/{self.default_org_code}/workflowinstances/{ident}" if self.default_org_code else None,
        ]
        paths = [p for p in paths if p]
        
        last_exc = None
        for use_prefix in (True, False):
            for path in paths:
                try:
                    return self._authorized_request("GET", path, use_rest_prefix=use_prefix, silent_on_status=[404])
                except Exception as exc:
                    last_exc = exc
                    continue
        
        if last_exc and isinstance(last_exc, Exception):
            raise last_exc
        return {}

    def get_workflow_instances(
        self, 
        workflow_name: str = "", 
        limit: int = 100, 
        org_code: str = "",
        status_filter: Optional[str] = None
    ) -> list[dict]:
        """Get recent workflow instances with modern API and T4 fallback paths.
        
        Supports status_filter (e.g. 'Complete', 'Failure') for T4 POST path.
        If workflow_name is empty, returns global instances/executions for the tenant.
        """
        name = str(workflow_name or "").strip()
        
        candidate_names: list[str] = []
        candidate_ids: list[str] = []
        if name:
            resolved = self.resolve_cached_workflow_name(name)
            for candidate in (
                resolved,
                name,
                f"WF_{name}" if not resolved and not name.upper().startswith("WF_") else "",
            ):
                candidate = str(candidate or "").strip()
                if candidate and candidate.lower() not in {item.lower() for item in candidate_names}:
                    candidate_names.append(candidate)
                    
                    # Try to get numeric ID for T4 path fallbacks
                    wf_id = self.get_cached_workflow_id(candidate)
                    if wf_id and wf_id not in candidate_ids:
                        candidate_ids.append(wf_id)
                
            # If name is numeric, consider it a potential workflowId candidate as well
            if name.isdigit() and name not in candidate_ids:
                candidate_ids.append(name)

            logger.info(f"T4 Deep Search: Resolved candidates names={candidate_names}, ids={candidate_ids}")

        org = (org_code or self.default_org_code or "").strip()
        modern_paths = [f"/api/v1/workflows/{candidate}/executions" for candidate in candidate_names]
        
        # Phase 1: Try modern endpoints
        last_exc: Optional[Exception] = None
        for path in modern_paths:
            try:
                # Modern API usually takes status in query params if supported
                request_params: dict[str, Any] = {"limit": max(limit, 1)}
                if status_filter:
                    request_params["status"] = status_filter
                
                result = self._authorized_request(
                    "GET", path, 
                    params=request_params, 
                    use_rest_prefix=False,
                    silent_on_status=[400, 404, 500]
                )
                if isinstance(result, list) and result:
                    limit_val = max(limit, 1)
                    return [result[i] for i in range(min(len(result), limit_val))]
                if isinstance(result, dict):
                    items = (
                        result.get("instances")
                        or result.get("executions")
                        or result.get("data")
                        or []
                    )
                    if isinstance(items, list) and items:
                        if candidate_names:
                            norm_candidates = {c.lower() for c in candidate_names}
                            any_named_items = any(
                                (
                                    it.get("workflowName")
                                    or (it.get("workflowConfiguration") or {}).get("name")
                                )
                                for it in items
                                if isinstance(it, dict)
                            )
                            filtered = [
                                it for it in items 
                                if (it.get("workflowName") or (it.get("workflowConfiguration") or {}).get("name") or "").lower() in norm_candidates
                            ]
                            if filtered or not any_named_items:
                                limit_val = max(limit, 1)
                                source_items = filtered if filtered else items
                                return [source_items[i] for i in range(min(len(source_items), limit_val))]
                        else:
                            limit_val = max(limit, 1)
                            return [items[i] for i in range(min(len(items), limit_val))]
                    if result and not isinstance(result, list) and not items:
                         # Single object result - check if it matches
                         if candidate_names:
                             wfn = (result.get("workflowName") or (result.get("workflowConfiguration") or {}).get("name") or "").lower()
                             if wfn in {c.lower() for c in candidate_names}:
                                 return [result]
                         else:
                             return [result]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {400, 404, 500}:
                    last_exc = exc
                    continue
                raise

        # Phase 2: T4 POST /workflowinstances with broad filtering support
        t4_path = "/workflowinstances"
        # Favor prefix=True first for T4 as it's the standard /aeengine/rest standard
        for use_prefix in (True, False):
            try:
                all_t4_results = []
                current_offset = 0
                # Increase depth if searching for a specific bot in a busy tenant
                max_to_fetch = max(limit, 1000 if candidate_names else 1)
                
                # We'll use a fixed page size of 50 for T4 stability (server limit)
                page_size = 50
                
                while len(all_t4_results) < max_to_fetch:
                    fetch_size = min(page_size, max_to_fetch - len(all_t4_results))
                    # T4 often expects pagination in query params even for POST
                    params = {"offset": current_offset, "size": fetch_size, "order": "desc"}
                    payload = {}
                    if candidate_names:
                        # Use the specific T4 Advance Search format for precise filtering
                        payload = {
                            "advanceSearch": {
                                "conditions": [
                                    {
                                        "column": {
                                            "columnName": "workflowConfiguration.name",
                                            "dataType": "string"
                                        },
                                        "operator": "like",
                                        "values": [candidate_names[0]],
                                        "hasError": False
                                    }
                                ],
                                "conditionType": ""
                            }
                        }
                    elif name:
                        # If name looks like an ID (numeric), use the precise ID filter column
                        column_name = "workflowConfiguration.id" if name.isdigit() else "workflowConfiguration.name"
                        column_type = "int" if name.isdigit() else "string"
                        val = int(name) if name.isdigit() else name
                        
                        payload = {
                            "advanceSearch": {
                                "conditions": [
                                    {
                                        "column": {
                                            "columnName": column_name,
                                            "dataType": column_type
                                        },
                                        "operator": "eq" if name.isdigit() else "like",
                                        "values": [val],
                                        "hasError": False
                                    }
                                ],
                                "conditionType": ""
                            }
                        }
                    
                    if status_filter:
                         # Append status filter to advanceSearch if present
                         if "advanceSearch" in payload:
                             payload["advanceSearch"]["conditions"].append({
                                 "column": {
                                     "columnName": "status",
                                     "dataType": "string"
                                 },
                                 "operator": "eq",
                                 "values": [status_filter],
                                 "hasError": False
                             })
                         else:
                             payload["status"] = status_filter

                    result = self._authorized_request(
                        "POST", t4_path, 
                        params=params,
                        payload=payload, 
                        use_rest_prefix=use_prefix,
                        silent_on_status=[400, 404, 500]
                    )
                    items = self._extract_list(result)
                    if not items:
                        break
                        
                    all_t4_results.extend(items)
                    
                    if len(items) < fetch_size:
                        break
                        
                    current_offset += len(items)
                
                if all_t4_results:
                    if candidate_names:
                        # Strict filtering to avoid global results being returned if name not found.
                        # T4 name-based POST /workflowinstances often returns ALL instances if the name isn't found.
                        norm_candidates = {c.lower() for c in candidate_names}
                        filtered = []
                        for res in all_t4_results:
                            # T4 can return name in workflowName OR workflowConfiguration.name
                            wfn = (res.get("workflowName") or 
                                   (res.get("workflowConfiguration") or {}).get("name") or 
                                   "").lower()
                            if wfn in norm_candidates:
                                filtered.append(res)
                        
                        logger.info("Phase 2: Retrieved %d instances, %d match candidates %s", len(all_t4_results), len(filtered), candidate_names)
                        if filtered:
                            limit_val = int(max_to_fetch)
                            return [filtered[i] for i in range(min(len(filtered), limit_val))]
                    else:
                        logger.info("Phase 2: Retrieved %d instances (no filter)", len(all_t4_results))
                        limit_val = int(max_to_fetch)
                        return [all_t4_results[i] for i in range(min(len(all_t4_results), limit_val))]
            except Exception as exc:
                last_exc = exc
                continue

        # Phase 3: T4 GET fallbacks
        t4_get_paths = []
        # T4 often requires numeric IDs for /workflows/{id}/instances
        # But we also try names just in case some T4 version supports it.
        for candidate in (candidate_ids + candidate_names):
            if org:
                t4_get_paths.append(f"/{org}/workflows/{candidate}/instances")
            t4_get_paths.append(f"/workflows/{candidate}/instances")
            
        if not t4_get_paths and not name:
            # If no workflow name, Phase 3 falls back to a global T4 instances list
            t4_get_paths.append("/workflowinstances")
            if org:
                t4_get_paths.append(f"/{org}/workflowinstances")

        for use_prefix in (False, True):
            for path in t4_get_paths:
                try:
                    all_get_results = []
                    current_offset = 0
                    max_to_fetch = max(limit, 1)
                    page_size = 50
                    
                    while len(all_get_results) < max_to_fetch:
                        fetch_size = min(page_size, max_to_fetch - len(all_get_results))
                        
                        # T4 specific-workflow instances endpoint does NOT support paging/order via GET.
                        # Only global /workflowinstances supports them.
                        use_params = "/workflowinstances" in path
                        params: dict[str, Any] = {}
                        if use_params:
                            params = {"offset": current_offset, "size": fetch_size, "order": "desc"}
                            if status_filter:
                                params["status"] = status_filter
                            
                        try:
                            result = self._authorized_request(
                                "GET", path, 
                                params=params if use_params else None,
                                use_rest_prefix=use_prefix,
                                silent_on_status=[400, 404, 500]
                            )
                            items = []
                            if isinstance(result, list):
                                items = result
                            elif isinstance(result, dict):
                                items = result.get("instances") or result.get("executions") or result.get("data") or []
                                if not isinstance(items, list) and result:
                                    items = [result]
                                    
                            if not items or not isinstance(items, list):
                                break
                                
                            all_get_results.extend(items)
                            if len(items) < fetch_size:
                                break
                            current_offset += len(items)
                        except httpx.HTTPStatusError as exc:
                            body = self._extract_body_safe(exc)
                            logger.info(f"Phase 3: Path {path} failed with {exc.response.status_code}. Body: {body}")
                            if "AE-1005" in str(body):
                                # Skip this broken path and try next one in t4_get_paths
                                break 
                            last_exc = exc
                            break
                        
                    if all_get_results:
                        if candidate_names:
                            norm_candidates = {c.lower() for c in candidate_names}
                            filtered = []
                            for res in all_get_results:
                                # T4 can return name in workflowName OR workflowConfiguration.name
                                wfn = (res.get("workflowName") or 
                                       (res.get("workflowConfiguration") or {}).get("name") or 
                                       "").lower()
                                if wfn in norm_candidates:
                                    filtered.append(res)
                            
                            logger.info("Phase 3: Retrieved %d instances from %s, %d match candidates", len(all_get_results), path, len(filtered))
                            if filtered:
                                limit_val = int(max_to_fetch)
                                return [filtered[i] for i in range(min(len(filtered), limit_val))]
                        else:
                            logger.info("Phase 3: Retrieved %d instances from %s (no filter)", len(all_get_results), path)
                            limit_val = int(max_to_fetch)
                            return [all_get_results[i] for i in range(min(len(all_get_results), limit_val))]
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 404, 500}:
                        # Capture error body for the final raise if all paths fail
                        try:
                            error_text = exc.response.text
                        except Exception:
                            error_text = "N/A"
                        
                        msg = f"Phase 3: Path {path} failed with {exc.response.status_code}. Body: {error_text}"
                        last_exc = RuntimeError(msg)
                        logger.info(msg)
                        continue
                    raise
        if last_exc and isinstance(last_exc, Exception):
            # If we were searching for a specific bot and found nothing, return empty rather than erroring 
            # if the error was a 400/404/500 which often means "no data" for specific workflow paths.
            logger.warning("All status retrieval paths failed/empty for candidates %s. Returning []. Last error: %s", candidate_names or name, last_exc)
            return []
        return []

    def get_running_instances(self, workflow_name: str = "") -> list[dict]:
        """
        Query /workflowinstances individually for each running state.
        States: 'InProgress', 'ExecutionStarted', 'New'.
        Returns the first match found. This avoids API limitations with multi-value status searches.
        """
        active_statuses = ["InProgress", "ExecutionStarted", "New"]
        org = self.default_org_code
        
        # Determine candidate names for searching
        candidate_names = []
        if workflow_name:
            resolved = self.resolve_cached_workflow_name(workflow_name)
            candidate_names = [resolved] if resolved else [workflow_name]

        # T4 /workflowinstances often requires /aeengine/rest prefix
        for status in active_statuses:
            payload = {
                "advanceSearch": {
                    "conditionType": "AND",
                    "conditions": [
                        {
                            "column": {"columnName": "status", "dataType": "enum"},
                            "operator": "eq", "values": [status]
                        }
                    ]
                }
            }
            if candidate_names:
                payload["advanceSearch"]["conditions"].append({
                    "column": {
                        "columnName": "workflowConfiguration.name",
                        "dataType": "string"
                    },
                    "operator": "eq",
                    "values": [candidate_names[0]]
                })

            paths = [
                f"/{org}/workflowinstances" if org else None,
                "/workflowinstances"
            ]
            paths = [p for p in paths if p]

            for use_prefix in (True, False):
                for path in paths:
                    try:
                        # Fetch a small page (size=5) since we only need to know if ANY are running
                        params = {"offset": 0, "size": 5, "order": "desc"}
                        raw = self._authorized_request(
                            "POST", path, 
                            params=params, 
                            payload=payload, 
                            use_rest_prefix=use_prefix,
                            silent_on_status=[400, 404, 500]
                        )
                        items = self._extract_list(raw)
                        if items:
                            logger.info("Found %d instances in state '%s' for %s", len(items), status, workflow_name)
                            return items
                    except Exception as exc:
                        logger.debug("Running check failed for %s status %s (prefix=%s): %s", path, status, use_prefix, exc)
                        continue
        return []

    @staticmethod
    def _extract_execution_ref(instance: dict | None) -> str:
        if not isinstance(instance, dict):
            return ""
        return str(
            instance.get("id")
            or instance.get("automationRequestId")
            or instance.get("requestId")
            or instance.get("executionId")
            or ""
        ).strip()

    @staticmethod
    def _extract_workflow_ref(instance: dict | None) -> str:
        if not isinstance(instance, dict):
            return ""
        workflow_meta = instance.get("workflowConfiguration") or instance.get("workflow") or {}
        return str(
            instance.get("workflowName")
            or instance.get("workflow_name")
            or workflow_meta.get("name")
            or ""
        ).strip()

    @staticmethod
    def _extract_agent_ref(instance: dict | None) -> tuple[str, str]:
        if not isinstance(instance, dict):
            return ("", "")
        agent_meta = instance.get("agentDetails") or instance.get("agent") or {}
        agent_name = str(
            instance.get("agentName")
            or instance.get("agent_name")
            or agent_meta.get("agentName")
            or agent_meta.get("name")
            or ""
        ).strip()
        agent_id = str(
            instance.get("agentId")
            or instance.get("agent_id")
            or instance.get("uuid")
            or agent_meta.get("agentId")
            or agent_meta.get("id")
            or agent_meta.get("uuid")
            or ""
        ).strip()
        return (agent_name, agent_id)

    @staticmethod
    def _has_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True

    @classmethod
    def _parse_workflow_response_payload(cls, raw_value: Any) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            return dict(raw_value)
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except Exception:
                return {"message": text}
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"outputParameters": parsed}
            return {"message": text}
        return {}

    @classmethod
    def extract_workflow_response_message(cls, instance: dict | None) -> str:
        if not isinstance(instance, dict):
            return ""

        direct_message = str(
            instance.get("workflowResponseMessage")
            or instance.get("workflow_response_message")
            or ""
        ).strip()
        if direct_message:
            return direct_message

        payload = cls._parse_workflow_response_payload(instance.get("workflowResponse"))
        for key in ("message", "currentStatus", "error"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value

        output_parameters = payload.get("outputParameters")
        if isinstance(output_parameters, list):
            for item in output_parameters:
                if not isinstance(item, dict):
                    continue
                for key in ("message", "value", "displayValue", "status"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        return value
        return ""

    @classmethod
    def _prefer_richer_execution_value(cls, current: Any, candidate: Any) -> Any:
        if not cls._has_meaningful_value(candidate):
            return current
        if not cls._has_meaningful_value(current):
            return candidate
        if isinstance(current, dict) and isinstance(candidate, dict):
            return cls._merge_execution_payloads(current, candidate)
        if isinstance(current, list) and isinstance(candidate, list):
            return candidate if len(candidate) >= len(current) else current
        if isinstance(current, str) and isinstance(candidate, str):
            return candidate if len(candidate.strip()) >= len(current.strip()) else current
        return candidate

    @classmethod
    def _merge_execution_payloads(
        cls,
        primary: dict | None,
        secondary: dict | None,
    ) -> dict[str, Any]:
        merged = dict(primary or {})
        if not isinstance(secondary, dict):
            return merged

        preferred_keys = {
            "status",
            "workflowResponse",
            "workflowResponseMessage",
            "workflow_response_message",
            "message",
            "errorMessage",
            "errorDetails",
            "completedDate",
            "completedTime",
            "completedAt",
            "executionEndTime",
            "endTime",
            "executionStartTime",
            "startTime",
            "lastUpdatedDate",
            "outputParameters",
        }

        for key, value in secondary.items():
            if not cls._has_meaningful_value(value):
                continue

            current = merged.get(key)
            if key in preferred_keys:
                merged[key] = cls._prefer_richer_execution_value(current, value)
                continue

            if isinstance(current, dict) and isinstance(value, dict):
                merged[key] = cls._merge_execution_payloads(current, value)
                continue

            if isinstance(current, list) and isinstance(value, list):
                if len(value) > len(current):
                    merged[key] = value
                continue

            if not cls._has_meaningful_value(current):
                merged[key] = value

        return merged

    def refresh_execution_payload(
        self,
        execution_id: str,
        record: dict | None = None,
        *,
        workflow_name: str = "",
        recent_limit: int = 25,
    ) -> dict[str, Any]:
        target_id = str(execution_id or self._extract_execution_ref(record) or "").strip()
        merged = dict(record or {}) if isinstance(record, dict) else {}
        if not target_id:
            return merged

        workflow_label = str(workflow_name or self._extract_workflow_ref(merged) or "").strip()

        def merge_candidate(candidate: dict | None) -> None:
            nonlocal merged
            if not isinstance(candidate, dict):
                return
            if self._extract_execution_ref(candidate) not in {"", target_id}:
                return
            merged = self._merge_execution_payloads(merged, candidate)

        parsed = self._parse_workflow_response_payload(merged.get("workflowResponse"))
        if parsed:
            merged["workflowResponseParsed"] = parsed
            summary = self.extract_workflow_response_message(merged)
            if summary:
                merged["workflowResponseMessage"] = summary

        try:
            merge_candidate(self.get_workflow_instance_by_id(target_id))
        except Exception as exc:
            logger.debug("Direct execution refresh failed for %s: %s", target_id, exc)

        if not self.extract_workflow_response_message(merged):
            search_scopes = []
            if workflow_label:
                search_scopes.append(workflow_label)
            search_scopes.append("")

            seen_scopes: set[str] = set()
            for scope in search_scopes:
                norm_scope = str(scope or "").strip().lower()
                if norm_scope in seen_scopes:
                    continue
                seen_scopes.add(norm_scope)

                try:
                    instances = self.get_workflow_instances(
                        workflow_name=scope,
                        limit=max(int(recent_limit), 10),
                    )
                except Exception as exc:
                    logger.debug(
                        "Recent execution refresh failed for %s (scope=%s): %s",
                        target_id,
                        scope or "global",
                        exc,
                    )
                    continue

                match = next(
                    (
                        item for item in instances
                        if self._extract_execution_ref(item) == target_id
                    ),
                    None,
                )
                if match:
                    merge_candidate(match)
                    if self.extract_workflow_response_message(merged):
                        break

        parsed = self._parse_workflow_response_payload(merged.get("workflowResponse"))
        if parsed:
            merged["workflowResponseParsed"] = parsed
            summary = self.extract_workflow_response_message(merged)
            if summary:
                merged["workflowResponseMessage"] = summary
        return merged

    @staticmethod
    def _is_running_agent_state(state: Any) -> bool:
        normalized = str(state or "").strip().upper()
        return normalized in {"RUNNING", "CONNECTED", "ACTIVE"}

    @staticmethod
    def _is_actively_running_execution_status(status: Any) -> bool:
        normalized = str(status or "").strip().replace(" ", "").upper()
        return normalized in {"INPROGRESS", "EXECUTIONSTARTED", "RUNNING", "PROCESSING"}

    @staticmethod
    def _assigned_agent_summary(agents: list[dict]) -> str:
        labels: list[str] = []
        for agent in agents or []:
            if not isinstance(agent, dict):
                continue
            name = str(
                agent.get("agentName")
                or agent.get("name")
                or agent.get("agentId")
                or agent.get("id")
                or "Unknown"
            ).strip()
            state = str(agent.get("agentState") or agent.get("state") or "UNKNOWN").strip().upper()
            labels.append(f"{name} ({state})")
        return ", ".join(labels)

    def _assigned_agents_for_workflow(self, workflow_name: str) -> list[dict]:
        normalized = str(workflow_name or "").strip().lower()
        if not normalized:
            return []

        try:
            entries = self.get_workflow_agents() or []
        except Exception as exc:
            logger.debug("Could not load workflow agents for %s: %s", workflow_name, exc)
            return []

        if not isinstance(entries, list):
            return []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            workflow_meta = entry.get("workflow") or entry.get("workflowConfiguration") or {}
            candidate = str(
                workflow_meta.get("name")
                or entry.get("workflowName")
                or ""
            ).strip().lower()
            if candidate != normalized:
                continue
            agents = entry.get("agents") or []
            return [agent for agent in agents if isinstance(agent, dict)]
        return []

    def diagnose_new_execution(self, execution_payload: dict | None, execution_id: str = "") -> dict[str, Any]:
        """Explain why a request is still NEW/QUEUED/PENDING."""
        if not isinstance(execution_payload, dict):
            return {}

        raw_status = str(
            execution_payload.get("status")
            or execution_payload.get("state")
            or ""
        ).strip()
        if raw_status.upper() not in {"NEW", "QUEUED", "PENDING"}:
            return {}

        current_execution_id = str(execution_id or self._extract_execution_ref(execution_payload)).strip()
        workflow_name = self._extract_workflow_ref(execution_payload)
        if not workflow_name:
            return {}

        assigned_agents = self._assigned_agents_for_workflow(workflow_name)
        assigned_agent_names = {
            str(agent.get("agentName") or agent.get("name") or "").strip().lower()
            for agent in assigned_agents
            if isinstance(agent, dict) and str(agent.get("agentName") or agent.get("name") or "").strip()
        }
        assigned_agent_ids = {
            str(agent.get("agentId") or agent.get("id") or agent.get("uuid") or "").strip()
            for agent in assigned_agents
            if isinstance(agent, dict) and str(agent.get("agentId") or agent.get("id") or agent.get("uuid") or "").strip()
        }

        try:
            active_instances = self.get_running_instances("") or []
        except Exception as exc:
            logger.debug("Could not inspect running instances while diagnosing NEW request %s: %s", current_execution_id, exc)
            active_instances = []

        for instance in active_instances:
            if not isinstance(instance, dict):
                continue
            instance_id = self._extract_execution_ref(instance)
            if current_execution_id and instance_id and instance_id == current_execution_id:
                continue
            if not self._is_actively_running_execution_status(instance.get("status")):
                continue

            instance_workflow = self._extract_workflow_ref(instance) or workflow_name
            instance_agent_name, instance_agent_id = self._extract_agent_ref(instance)
            agent_matches_assignment = (
                (instance_agent_name and instance_agent_name.strip().lower() in assigned_agent_names)
                or (instance_agent_id and instance_agent_id in assigned_agent_ids)
            )
            same_workflow = instance_workflow.strip().lower() == workflow_name.strip().lower()
            if not (agent_matches_assignment or same_workflow):
                continue

            agent_suffix = f" on agent **{instance_agent_name}**" if instance_agent_name else ""
            return {
                "reason": "other_process_running",
                "execution_id": current_execution_id,
                "workflow_name": workflow_name,
                "other_execution_id": instance_id,
                "other_workflow_name": instance_workflow,
                "other_status": str(instance.get("status") or "").strip(),
                "other_agent_name": instance_agent_name,
                "assigned_agents": assigned_agents,
                "summary": (
                    f"Execution `{current_execution_id or 'unknown'}` for **{workflow_name}** is still **{raw_status or 'New'}** "
                    f"because another process is currently running: **{instance_workflow}** "
                    f"(Execution ID: `{instance_id or 'unknown'}`, status: `{instance.get('status') or 'Unknown'}`){agent_suffix}. "
                    "Please wait some time and check again."
                ),
            }

        running_assigned_agents = [
            agent for agent in assigned_agents
            if self._is_running_agent_state(agent.get("agentState") or agent.get("state"))
        ]
        if assigned_agents and not running_assigned_agents:
            assigned_summary = self._assigned_agent_summary(assigned_agents) or "No assigned agents found"
            return {
                "reason": "agent_unavailable",
                "execution_id": current_execution_id,
                "workflow_name": workflow_name,
                "assigned_agents": assigned_agents,
                "summary": (
                    f"Execution `{current_execution_id or 'unknown'}` for **{workflow_name}** is still **{raw_status or 'New'}** "
                    f"because its assigned agent is not running ({assigned_summary}). "
                    "Please restart the agent and try again."
                ),
            }

        try:
            live_agents = self.check_agent_status() or []
        except Exception as exc:
            logger.debug("Could not inspect live agent status for NEW request %s: %s", current_execution_id, exc)
            live_agents = []

        if not any(self._is_running_agent_state(agent.get("agentState") or agent.get("state")) for agent in live_agents):
            return {
                "reason": "agent_unavailable",
                "execution_id": current_execution_id,
                "workflow_name": workflow_name,
                "assigned_agents": assigned_agents,
                "summary": (
                    f"Execution `{current_execution_id or 'unknown'}` for **{workflow_name}** is still **{raw_status or 'New'}** "
                    "and no active automation agent was detected. Please restart the agent and try again."
                ),
            }

        return {
            "reason": "queued_waiting",
            "execution_id": current_execution_id,
            "workflow_name": workflow_name,
            "assigned_agents": assigned_agents,
            "summary": (
                f"Execution `{current_execution_id or 'unknown'}` for **{workflow_name}** is currently **{raw_status or 'New'}**. "
                "An automation agent is available, so please wait some time and check again."
            ),
        }

    def get_execution_logs(self, execution_id: str, tail: int = 100) -> dict:
        """Get execution logs by execution id with T4 fallback paths and debug log flow."""
        if not execution_id:
            raise ValueError("execution_id is required")

        # Phase 1: Try a small set of direct paths to avoid 429 rate limits
        # Only Accept header is needed for GET logs
        headers = {"Accept": "*/*"}
        
        # We'll try the most likely combinations on T4 and modern AE
        attempts = [
            # T4 standard (with prefix) - PRIMARY T4 PATH
            ("GET", f"/workflowinstances/{execution_id}/logs", True),
            # T4 standard (no prefix)
            ("GET", f"/workflowinstances/{execution_id}/logs", False),
            # T4 org-scoped (no prefix)
            ("GET", f"/{self.default_org_code}/workflowinstances/{execution_id}/logs", False) if self.default_org_code else None,
            # Modern AE (no prefix)
            ("GET", f"/api/v1/executions/{execution_id}/logs", False),
        ]
        attempts = [a for a in attempts if a]
        
        last_exc: Optional[Exception] = None
        
        for method, path, use_prefix in attempts:
            try:
                # Use tail only if explicitly requested and > 0
                params = {"tail": tail} if tail > 0 else {}
                result = self._authorized_request(
                    method,
                    path,
                    params=params,
                    headers=headers,
                    use_rest_prefix=use_prefix,
                    silent_on_status=[400, 404, 429, 500],
                )
                if result:
                    # If it's a list or dict with logs, return it
                    if isinstance(result, list) or (isinstance(result, dict) and (result.get("logs") or result.get("is_zip"))):
                        logger.info(f"Phase 1: Successfully retrieved logs via {path}")
                        if isinstance(result, dict):
                            result["source_info"] = f"Phase 1 direct path: {path}"
                        return result
                # If silent failure or empty result, continue to next path/phase
                logger.debug(f"Phase 1: Path {path} returned no data, trying next...")
                continue
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                # If we hit 400 (unsupported) or 429 (rate limit), stop Phase 1 early and try Phase 2
                if exc.response.status_code in (400, 429):
                    logger.warning(f"Phase 1 path {path} failed with {exc.response.status_code}. Moving to Phase 2.")
                    break
                # On 404, just continue to next attempt
                continue
            except Exception as exc:
                last_exc = exc
                continue

        # Phase 2: T4 Debug Log Flow (Request-Poll-Download)
        # This is the "fallback of last resort" for T4 where /logs is restricted
        logger.info(f"Phase 2: Initiating T4 debug log flow for execution {execution_id}")
        try:
            # 1. Check if a COMPLETE debug log request already exists for this execution_id
            # This avoids creating redundant "NEW" requests which the server might pick first.
            existing_requests = self._list_debug_log_requests()
            best_existing = self._find_debug_log_request_for_execution(existing_requests, execution_id)
            best_existing_id = best_existing.get("id") if best_existing else None
            if best_existing_id and best_existing.get("logFileLink"):
                logger.info(
                    "Found existing COMPLETE debug log request %s for execution %s",
                    best_existing_id,
                    execution_id,
                )
            
            req_id = best_existing_id
            if not req_id:
                # 2. Get execution status for metadata (dates) only if we need to request a new one
                status_data = self.get_execution_status(execution_id)
                # T4 Date extraction
                from_date = status_data.get("startTime") or status_data.get("createdDate")
                to_date = status_data.get("endTime") or status_data.get("lastUpdatedDate")
                
                # Start/End dates are required for debug log post
                if not from_date:
                    # Fallback to current time - 1h if missing (in ms)
                    from_date = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)
                if not to_date:
                    to_date = int(datetime.now(timezone.utc).timestamp() * 1000)

                # 3. Request debug log generation
                debug_req = self.request_debug_logs(execution_id, from_date, to_date)
                req_id = debug_req.get("id")
                if not req_id:
                     raise RuntimeError(f"T4 debug log request failed: {debug_req}")
                logger.info(f"Created new T4 debug log request {req_id} for execution {execution_id}")

            # 4. Poll for logFileLink (or download immediately if we found an existing one)
            import time
            for poll_attempt in range(12): # Max ~95s with first longer wait
                # T4 can take several seconds to register a debug log request
                # 10s initial wait avoids AE-1603 (Invalid log request id) on first poll
                wait = 10 if poll_attempt == 0 else 5
                time.sleep(wait)
                # T4 SUCCESS PATH: /agent/debuglogs/{id} returns the ZIP bytes directly.
                # _json_or_text encodes this as {"is_zip": True, "log_zip_content": <bytes>}
                updated = self.get_debug_log_request(str(req_id))
                if not isinstance(updated, dict):
                    logger.warning(
                        "T4 debug log request %s returned non-dict payload type=%s; treating as not-ready.",
                        req_id,
                        type(updated).__name__,
                    )
                    updated = {"raw_text": str(updated), "status": "PENDING"}
                if updated.get("is_zip") or updated.get("log_zip_content"):
                    logger.info(f"T4 debug log request {req_id}: received binary ZIP content directly.")
                    if isinstance(updated, dict):
                        updated["source_info"] = f"T4 debug log request {req_id} (reused existing)" if best_existing_id else f"T4 debug log request {req_id} (newly created)"
                    return updated

                link = updated.get("logFileLink")
                if link:
                    logger.info(f"T4 debug log ready via link: {link}")
                    # 4. Download (use rest prefix False as it's typically an absolute-ish or full path)
                    res = self._authorized_request("GET", link, use_rest_prefix=False)
                    if isinstance(res, dict):
                        res["source_info"] = f"T4 debug log request {req_id} (ready via link)"
                    return res
                
                if (updated.get("status") or "").upper() in ("FAILED", "ERROR"):
                    raise RuntimeError(f"T4 debug log request {req_id} failed on server.")
                
                # AE-1603: server hasn't registered the request yet - treat as retryable
                error_code = str(updated.get("errorCode") or "").strip() if isinstance(updated, dict) else ""
                if error_code == "AE-1603":
                    logger.info(
                        "Poll %d: AE-1603 received for request %s. Checking debug log list for execution %s.",
                        poll_attempt + 1,
                        req_id,
                        execution_id,
                    )

                listed_requests = self._list_debug_log_requests()
                listed_match = self._find_debug_log_request_for_execution(listed_requests, execution_id)
                if listed_match:
                    listed_id = str(listed_match.get("id") or "").strip()
                    if listed_id and listed_id != str(req_id):
                        logger.info(
                            "Switching debug log request id from %s to %s for execution %s based on list polling.",
                            req_id,
                            listed_id,
                            execution_id,
                        )
                        req_id = listed_id

                    listed_link = str(listed_match.get("logFileLink") or "").strip()
                    if listed_link:
                        logger.info("T4 debug log ready via list poll link: %s", listed_link)
                        res = self._authorized_request("GET", listed_link, use_rest_prefix=False)
                        if isinstance(res, dict):
                            res["source_info"] = f"T4 debug log request {req_id} (ready via list poll)"
                        return res

                    listed_status = str(listed_match.get("status") or "").upper()
                    if listed_status in {"FAILED", "ERROR"}:
                        raise RuntimeError(f"T4 debug log request {req_id} failed on server.")

                if error_code == "AE-1603" and poll_attempt < 8:
                    continue
                
                logger.debug(f"T4 debug log request {req_id} still not ready (attempt {poll_attempt+1}), polling again...")
            
            raise RuntimeError(f"T4 debug log request {req_id} timed out waiting for link.")

        except Exception as flow_err:
             logger.error(f"Phase 2 Flow failed for {execution_id}: {flow_err}")
             # If Phase 2 fails, raise the flow error but keep Phase 1 error as context
             if last_exc:
                 # Check if last_exc is an actual exception type Pyre likes
                 if isinstance(last_exc, BaseException):
                      # Use cast(Any, flow_err) to avoid possible type conflicts when chaining
                      raise cast(Any, flow_err) from last_exc
             raise flow_err

    def request_debug_logs(self, execution_id: str, from_date: Any = None, to_date: Any = None) -> dict:
        """Request T4 agent debug logs for a workflow instance."""
        try:
            val = int(execution_id)
        except (ValueError, TypeError):
            val = execution_id

        payload = {
            "workflowInstanceId": val,
            "fromDate": from_date,
            "toDate": to_date,
        }
        return self._authorized_request(
            "POST",
            "/agent/debuglogs",
            payload=payload,
            use_rest_prefix=True
        )

    def request_agent_debug_logs(self, agent_uuid: str, from_date: int, to_date: int) -> dict:
        """Request T4 agent debug logs for a specific agent (not limited to a workflow)."""
        payload = {
            "agentInfoDto": {"uuid": agent_uuid},
            "fromDate": from_date,
            "toDate": to_date,
        }
        return self._authorized_request(
            "POST",
            "/agent/debuglogs",
            payload=payload,
            use_rest_prefix=True
        )

    def get_agent_debug_logs(self) -> list[dict]:
        """List all agent debug log requests."""
        raw = self._authorized_request(
            "GET",
            "/agent/debuglogs",
            use_rest_prefix=True
        )
        return self._extract_list(raw)

    def get_debug_log_request(self, request_id: str) -> dict:
        """Get status of a T4 debug log request.
        
        Returns the response dict. Returns error dict on AE-1603 (request not yet available)
        so the poll loop can retry gracefully without raising.
        """
        try:
            return self._authorized_request(
                "GET",
                f"/agent/debuglogs/{request_id}",
                use_rest_prefix=True
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 500:
                try:
                    err_body = exc.response.json()
                    if str(err_body.get("errorCode", "")).strip() == "AE-1603":
                        # T4 hasn't registered the request yet - return as retryable dict
                        return {"errorCode": "AE-1603", "status": "PENDING", "id": request_id}
                except Exception:
                    pass
            raise

    @staticmethod
    def _debug_log_request_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        raw_id = str(item.get("id") or "").strip()
        try:
            return (int(raw_id), raw_id)
        except (TypeError, ValueError):
            return (-1, raw_id)

    def _list_debug_log_requests(self) -> list[dict]:
        try:
            raw = self.get_agent_debug_logs()
        except Exception as exc:
            logger.info("Could not list existing debug log requests: %s", exc)
            return []
        return self._extract_list(raw, keys=("data", "items", "results"))

    def _find_debug_log_request_for_execution(self, entries: list[dict], execution_id: str) -> dict[str, Any]:
        target = str(execution_id or "").strip()
        if not target:
            return {}
        matches = [
            item
            for item in entries
            if isinstance(item, dict)
            and str(item.get("workflowInstanceId") or item.get("workflow_instance_id") or "").strip() == target
        ]
        if not matches:
            return {}
        matches.sort(key=self._debug_log_request_sort_key, reverse=True)

        for status in ("COMPLETE", "COMPLETED"):
            for item in matches:
                if str(item.get("status") or "").upper() == status and str(item.get("logFileLink") or "").strip():
                    return item

        for item in matches:
            if str(item.get("id") or "").strip():
                return item
        return {}

    def poll_execution_status(
        self,
        execution_id: str,
        poll_interval_sec: int = 3,
        max_attempts: int = 15,
        terminal_statuses: tuple = ("Complete", "Failure", "Error"),
    ) -> dict:
        """Poll execution status until terminal or max_attempts reached.

        Default max_attempts=15 limits blocking (~45s at 3s interval). Returns dict with
        'status', 'execution_id', 'raw'; if capped without terminal status, status is
        'in_progress' and 'in_progress_hint' suggests checking status for execution_id.
        """
        import time

        no_agent_threshold = 10
        no_agent_counter = 0
        raw = None
        status = "timeout"

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

            diagnosis: dict[str, Any] = {}
            if isinstance(raw, dict) and str(status or "").strip().upper() in {"NEW", "QUEUED", "PENDING"}:
                try:
                    diagnosis = self.diagnose_new_execution(raw, execution_id=execution_id)
                except Exception as exc:
                    logger.debug("NEW-status diagnosis failed for %s: %s", execution_id, exc)
                    diagnosis = {}

                if diagnosis:
                    raw = dict(raw)
                    raw["newExecutionDiagnosis"] = diagnosis
                    reason = str(diagnosis.get("reason") or "").strip().lower()
                    if reason == "other_process_running":
                        status = "waiting_other_process"
                    elif reason == "agent_unavailable":
                        status = "no_agent"

                if status == "New" and not (raw or {}).get("agentName") and not diagnosis.get("reason") == "queued_waiting":
                    no_agent_counter += 1
                    if no_agent_counter >= no_agent_threshold:
                        status = "no_agent"
                else:
                    no_agent_counter = 0
            else:
                no_agent_counter = 0

            normalized_status = str(status or "").strip().upper()
            terminal_upper = {str(item or "").strip().upper() for item in terminal_statuses}
            if normalized_status in terminal_upper:
                try:
                    raw = self.refresh_execution_payload(
                        execution_id,
                        record=raw if isinstance(raw, dict) else None,
                        workflow_name=self._extract_workflow_ref(raw if isinstance(raw, dict) else None),
                    )
                    if isinstance(raw, dict):
                        refreshed_status = str(raw.get("status") or "").strip()
                        if refreshed_status:
                            status = refreshed_status
                except Exception as exc:
                    logger.debug("Could not enrich terminal execution payload for %s: %s", execution_id, exc)

            normalized_status = str(status or "").strip().upper()
            if normalized_status in terminal_upper or status in {"no_agent", "waiting_other_process"}:
                break

            time.sleep(poll_interval_sec)

        out = {
            "status": status if raw else "timeout",
            "execution_id": execution_id,
            "raw": raw,
        }
        if status not in (*terminal_statuses, "no_agent", "waiting_other_process", "timeout"):
            out["status"] = "in_progress"
            out["in_progress_hint"] = (
                f"Execution still running after {max_attempts} checks. "
                f"Use get_execution_status or check_workflow_status for request_id {execution_id} to see when it completes."
            )
        return out

    def check_agent_status(self, org_code: str = "") -> list[dict]:
        """Check T4 agent health via monitoring endpoint.

        Ref: code_ref.py t4_check_agent_status() / t4_get_agent_monitoring()
        Returns list of agent dicts with 'agentName', 'agentState', 'agentId'.
        """
        org = org_code or self.default_org_code
        modern_paths = [
            "/api/v1/agents/status",
            "/api/v1/agents/resources",
        ]
        last_exc: Optional[Exception] = None
        for path in modern_paths:
            try:
                result = self._authorized_request(
                    "GET",
                    path,
                    use_rest_prefix=False,
                    silent_on_status=[400, 404],
                )
                raw_agents: list[dict] = []
                if isinstance(result, dict):
                    raw_agents = result.get("agents") or result.get("data") or []
                    if not raw_agents:
                        raw_agents = [result]
                elif isinstance(result, list):
                    raw_agents = result

                normalized: list[dict[str, Any]] = []
                for agent in raw_agents:
                    if not isinstance(agent, dict):
                        continue
                    normalized.append(
                        {
                            "agentName": agent.get("agentName")
                            or agent.get("name")
                            or agent.get("agent"),
                            "agentState": str(
                                agent.get("agentState")
                                or agent.get("status")
                                or "UNKNOWN"
                            ).upper(),
                            "agentId": agent.get("agentId") or agent.get("id"),
                            **agent,
                        }
                    )
                if normalized:
                    logger.info(
                        "Agent status resolved via modern endpoint %s (%d agent(s)).",
                        path,
                        len(normalized),
                    )
                    return normalized
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {400, 404, 500}:
                    last_exc = exc
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                continue

        if not org:
            if last_exc:
                logger.warning("Modern agent status fallback failed: %s", last_exc)
            logger.error("T4: org_code not configured — cannot check agents.")
            return []

        path = f"/{org}/monitoring/agents"
        try:
            result = self._authorized_request(
                "POST",
                path,
                params={"type": "AGENT", "offset": 0, "size": 50},
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

    def get_workflow_agents(self) -> list[dict]:
        """Fetch workflow-to-agent mapping from T4.
        
        Endpoint: GET /workflows/agents
        Returns list of dicts with 'workflow' and 'agents' list.
        """
        path = f"/{self.default_org_code}/workflows/agents" if self.default_org_code else "/workflows/agents"
        try:
            return self._authorized_request(
                "GET",
                path,
                use_rest_prefix=True,
                silent_on_status=[404]
            ) or []
        except Exception as exc:
            logger.error(f"T4: get_workflow_agents failed: {exc}")
            return []

    def get_agents_workflows(self) -> list[dict]:
        """Fetch agent-to-workflow mapping from T4.
        
        Endpoint: GET /agents/workflows
        Returns list of dicts with 'agent' and 'workflows' list.
        """
        path = f"/{self.default_org_code}/agents/workflows" if self.default_org_code else "/agents/workflows"
        try:
            return self._authorized_request(
                "GET",
                path,
                use_rest_prefix=True,
                silent_on_status=[404]
            ) or []
        except Exception as exc:
            logger.error(f"T4: get_agents_workflows failed: {exc}")
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

            rows = []
            for wf in workflows:
                wf_id = str(wf.get("workflowId") or wf.get("id") or "").strip()
                wf_name = str(wf.get("workflowName") or wf.get("name") or "").strip()
                org = str(
                    wf.get("orgCode")
                    or wf.get("org_code")
                    or self.default_org_code
                    or ""
                ).strip()
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
            logger.warning(
                "T4: workflow_catalog sync failed (non-fatal): %s. "
                "If this is a constraint error, please run 'python setup_db.py' to fix the database schema.", 
                exc
            )
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

            docs: list[dict[str, Any]] = []
            for wf in workflows:
                wf_id = str(wf.get("workflowId") or wf.get("id") or "").strip()
                wf_name = str(wf.get("workflowName") or wf.get("name") or "").strip()
                org = str(
                    wf.get("orgCode")
                    or wf.get("org_code")
                    or self.default_org_code
                    or ""
                ).strip()
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
                required_names = []
                for p in params:
                    if isinstance(p, dict):
                        p_name = p.get("name", "")
                        disp = p.get("displayName") or p.get("displayname") or p.get("description")
                        opt = p.get("optional")
                        is_explicitly_optional = (
                            opt is True or 
                            (isinstance(opt, str) and str(opt).strip().lower() in {"true", "1", "yes", "y"}) or
                            p.get("is_optional") is True or
                            p.get("required") is False or
                            p.get("is_required") is False
                        )
                        req = not is_explicitly_optional
                        
                        req_label = "[MANDATORY]" if req else "[Optional]"
                        if req and p_name:
                            required_names.append(p_name)
                        
                        p_desc = p.get("description") or p.get("helpText") or ""

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
                required_names = []
                for p in params:
                    if isinstance(p, dict):
                        p_name = p.get("name", "")
                        disp = p.get("displayName") or p.get("displayname") or p.get("description")
                        opt = p.get("optional")
                        is_explicitly_optional = (
                            opt is True or 
                            (isinstance(opt, str) and str(opt).strip().lower() in {"true", "1", "yes", "y"}) or
                            p.get("is_optional") is True or
                            p.get("required") is False or
                            p.get("is_required") is False
                        )
                        req = not is_explicitly_optional
                        
                        req_label = "[MANDATORY]" if req else "[Optional]"
                        if req and p_name:
                            required_names.append(p_name)
                        
                        p_desc = p.get("description") or p.get("helpText") or ""
                        label = f"{p_name} ({disp})" if disp and disp != p_name else p_name
                        
                        if p_name:
                            param_parts.append(f"  • {req_label} {label}: {p_desc}")
                
                param_text = "\n".join(param_parts) if param_parts else "None"

                content = (
                    f"Workflow Tool: {display_name}\n"
                    f"Technical Name: {wf_name}\n"
                    f"Description: {description}\n"
                    f"Category: {category}\n\n"
                    f"Required Parameters:\n{param_text}\n\n"
                )
                if tags:
                    content += f"Tags: {', '.join(str(t) for t in tags)}\n\n"

                # UNIFIED ID: tool-{workflow_name} as requested by user
                doc_id = f"tool-{wf_name}"
                # Cast to Any to avoid strict dict type mismatch on tags/metadata
                docs.append(cast(Any, {
                    "id": doc_id,
                    "content": content,
                    "collection": "tools",
                    "metadata": {
                        "tool_name": display_name,
                        "workflow_id": wf_id,
                        "workflow_name": wf_name,
                        "org_code": org,
                        "category": category,
                        "source": "automationedge",
                        "dynamic": True,
                        "active": active,
                        "required_params": required_names,
                        "tags": tags,
                        "tier": tier,
                        "parameters": params, # STORE PARAMETERS FOR UI DISCOVERY
                    },
                }))

            if not docs:
                return 0

            rag = get_rag_engine()
            rag.index_documents(docs, collection="tools")
            logger.info("T4: indexed %d workflows into RAG.", len(docs))
            return len(docs)

        except Exception as exc:
            logger.warning("T4: workflow RAG indexing failed: %s", exc)
            return 0

    def get_cached_workflow_parameters(self, workflow_name: str) -> list[dict]:
        """Fetch workflow parameter schema from the local Postgres catalog.
        
        Returns a list of dicts: [{'name': '...', 'type': '...', 'required': bool, ...}]
        Returns empty list if not found or DB error.
        """
        _, params = self.get_cached_workflow_info(workflow_name)
        return params

    def get_required_parameters(self, workflow_name: str) -> list[str]:
        """Identify which parameters are strictly required based on catalog metadata.
        
        Handles various T4/modern AE flags: 'required', 'is_required', 'optional'.
        """
        schema = self.get_cached_workflow_parameters(workflow_name)
        required = []
        for p in schema:
            name = p.get("name")
            if not name:
                continue
            
            # A parameter is REQUIRED unless it is explicitly marked as optional.
            # We look for 'optional': True, 'is_optional': True, or 'required': False.
            opt = p.get("optional")
            is_explicitly_optional = (
                opt is True or 
                (isinstance(opt, str) and str(opt).strip().lower() in {"true", "1", "yes", "y"}) or
                p.get("is_optional") is True or
                p.get("required") is False or
                p.get("is_required") is False
            )
            
            if not is_explicitly_optional:
                required.append(name)
            
        return required

    def get_cached_workflow_id(
        self,
        workflow_name: str,
        *,
        user_id: str = "",
        org_code: str = "",
    ) -> str:
        """Fetch workflow_id from local workflow_catalog for a workflow name."""
        wf_id, _ = self.get_cached_workflow_info(
            workflow_name,
            user_id=user_id,
            org_code=org_code,
        )
        return wf_id

    def get_cached_workflow_info(
        self,
        workflow_name: str,
        *,
        user_id: str = "",
        org_code: str = "",
    ) -> tuple[str, list[dict]]:
        """Fetch workflow_id and parameters in one query. Returns (workflow_id, parameters)."""
        name = str(workflow_name or "").strip()
        if not name:
            return ("", [])

        # Step 1: Resolve to the actual technical name in the catalog (WF_ prefix, case-insensitive, etc.)
        try:
            resolved = self.resolve_cached_workflow_name(
                name,
                user_id=user_id,
                org_code=org_code,
            )
        except TypeError:
            resolved = self.resolve_cached_workflow_name(name)
        if user_id and not resolved:
            return ("", [])
        lookup_name = resolved if resolved else name
        target_org = str(org_code or "").strip()

        try:
            from config.db import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    sql = (
                        "SELECT workflow_id, parameters, org_code "
                        "FROM workflow_catalog WHERE workflow_name = %s"
                    )
                    params: list[Any] = [lookup_name]
                    if target_org:
                        sql += " AND org_code = %s"
                        params.append(target_org)
                    sql += " ORDER BY active DESC, fetched_at DESC"
                    cur.execute(sql, tuple(params))
                    for row in cur.fetchall():
                        wf_id = str(row[0]) if row and row[0] else ""
                        wf_org = str(row[2]) if row and len(row) > 2 and row[2] else target_org
                        if user_id and not can_view_workflow(user_id, wf_id, wf_org):
                            continue
                        wf_params = list(row[1]) if row and row[1] else []
                        return (wf_id, wf_params)
        except Exception as exc:
            logger.debug("T4: cached workflow info lookup failed for %s: %s", workflow_name, exc)

        live_match = self._resolve_live_workflow_record(
            lookup_name,
            user_id=user_id,
            org_code=org_code,
            require_execute=False,
            min_score=0.72,
        )
        if not live_match:
            return ("", [])

        hydrated = self._hydrate_live_workflow_record(live_match)
        try:
            self.sync_workflow_catalog([hydrated])
        except Exception:
            pass

        wf_id = str(hydrated.get("workflowId") or hydrated.get("id") or "").strip()
        wf_org = str(
            hydrated.get("orgCode")
            or hydrated.get("org_code")
            or target_org
            or self.default_org_code
        ).strip()
        if user_id and wf_id and not can_view_workflow(user_id, wf_id, wf_org or target_org):
            return ("", [])
        wf_params = self._extract_workflow_params_from_payload(hydrated)
        return (wf_id, wf_params)

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
        raw_list = AutomationEdgeClient._extract_list(payload, keys=("workflows", "items", "results", "data", "records"))
        
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

    def list_agents(self, filters: Optional[dict] = None) -> list[dict]:
        """List all agents using the monitoring endpoint."""
        params = {"type": "AGENT", "offset": 0, "size": 50}
        if filters:
            params.update(filters)
        
        # T4 confirmed: POST /monitoring/agents
        paths = [
            f"/{self.default_org_code}/monitoring/agents" if self.default_org_code else None,
            "/monitoring/agents",
        ]
        paths = [p for p in paths if p]
        
        last_exc = None
        for use_prefix in (True, False):
            for path in paths:
                try:
                    raw = self._authorized_request("POST", path, params=params, use_rest_prefix=use_prefix)
                    return self._extract_list(raw, keys=("data", "agents", "items"))
                except Exception as e:
                    last_exc = e
                    continue
        return []

    def get_agent(self, agent_id: str) -> dict:
        """Get details for a specific agent."""
        paths = [
            f"/{self.default_org_code}/agents/{agent_id}" if self.default_org_code else None,
            f"/agents/{agent_id}",
        ]
        paths = [p for p in paths if p]
        
        for use_prefix in (True, False):
            for path in paths:
                try:
                    return self._authorized_request("GET", path, use_rest_prefix=use_prefix)
                except:
                    continue
        raise RuntimeError(f"Could not fetch agent {agent_id}")

    def request_agent_debug_logs(self, agent_uuid: str, from_date: int, to_date: int) -> dict:
        """Initiate an agent debug log extraction request."""
        # T4 confirmed: POST /aeengine/rest/agent/debuglogs
        return self._authorized_request(
            "POST",
            "/agent/debuglogs",
            payload={
                "agentInfoDto": {"uuid": agent_uuid},
                "fromDate": from_date,
                "toDate": to_date,
            },
            use_rest_prefix=True
        )

    def get_agent_debug_logs(self, request_id: str = "") -> Any:
        """List or get status of agent debug log requests."""
        # T4 confirmed: GET /aeengine/rest/agent/debuglogs
        path = "/agent/debuglogs"
        if request_id:
            path = f"{path}/{request_id}"
        return self._authorized_request("GET", path, use_rest_prefix=True)

    def close(self):
        self._client.close()


_automationedge_client: Optional[AutomationEdgeClient] = None


def get_automationedge_client() -> AutomationEdgeClient:
    """Lazy singleton used by tool handlers."""
    global _automationedge_client
    if _automationedge_client is None:
        _automationedge_client = AutomationEdgeClient()
    return _automationedge_client


def reset_automationedge_client() -> None:
    """Drop the cached client so new requests pick up updated settings."""
    global _automationedge_client
    if _automationedge_client is not None:
        try:
            _automationedge_client.close()
        except Exception:
            pass
    _automationedge_client = None
