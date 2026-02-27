"""
REST client for calling AE workflows exposed as tools.
Used by the Extension's planner/executor for tool invocations.
"""

import json
import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("support_agent.tools_rest")


class ToolError(Exception):
    pass


class RestToolClient:

    def __init__(self, base_url: str, auth_token: str, timeout_s: int = 30):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout_s = timeout_s

    def call(self, tool_ref: str, payload: Dict[str, Any],
             idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{tool_ref.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        resp = requests.post(
            url, headers=headers,
            data=json.dumps(payload),
            timeout=self.timeout_s,
        )
        if resp.status_code >= 300:
            raise ToolError(
                f"{tool_ref} failed: {resp.status_code} {resp.text[:500]}"
            )
        return resp.json()
