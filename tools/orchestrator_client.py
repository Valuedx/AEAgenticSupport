"""
HTTP client for the AE AI Hub visual orchestrator.

Wraps the three operations Studio needs:
  - list_workflows()   → discover saved DAGs by name / description
  - execute()          → fire a workflow, returns instance metadata (status='queued')
  - get_context()      → poll current instance state (InstanceContextOut)
  - run_and_wait()     → execute + block until terminal status, return context
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from config.settings import CONFIG

logger = logging.getLogger("ops_agent.orchestrator_client")

_POLL_INTERVAL = 2   # seconds between status checks


class OrchestratorClient:
    """Thin synchronous wrapper around the orchestrator backend API."""

    def __init__(
        self,
        base_url: str | None = None,
        tenant_id: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url or CONFIG.get("ORCHESTRATOR_BASE_URL", "http://localhost:8001")
        ).rstrip("/")
        self.tenant_id = tenant_id or CONFIG.get("ORCHESTRATOR_TENANT_ID", "default")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=30)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Tenant-Id": self.tenant_id,
        }
        token = str(CONFIG.get("ORCHESTRATOR_API_TOKEN", "") or "").strip()
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_workflows(self) -> list[dict[str, Any]]:
        """Return all workflow definitions for this tenant."""
        resp = self._client.get(self._url("/workflows"), headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def execute(
        self,
        workflow_id: str,
        trigger_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /workflows/{id}/execute.  Returns InstanceOut (status='queued')."""
        body = json.dumps({"trigger_payload": trigger_payload or {}})
        resp = self._client.post(
            self._url(f"/workflows/{workflow_id}/execute"),
            content=body,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def get_context(
        self,
        workflow_id: str,
        instance_id: str,
    ) -> dict[str, Any]:
        """GET /workflows/{wf}/instances/{inst}/context → InstanceContextOut."""
        resp = self._client.get(
            self._url(f"/workflows/{workflow_id}/instances/{instance_id}/context"),
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def run_and_wait(
        self,
        workflow_id: str,
        trigger_payload: dict[str, Any] | None = None,
        timeout: int = 120,
        *,
        return_on_suspended: bool = False,
    ) -> dict[str, Any]:
        """Execute a workflow and block until it reaches a terminal state.

        Returns the InstanceContextOut-shaped dict (includes ``status``, ``context_json``, …).
        Raises RuntimeError on failure.
        If ``return_on_suspended`` is False (default), raises RuntimeError when status is
        ``suspended``. If True, returns the context snapshot immediately so the caller can
        surface HITL instructions (e.g. AI Studio proxy).
        Raises TimeoutError if the workflow doesn't finish within *timeout* seconds
        (unless suspended and ``return_on_suspended`` is True).
        """
        instance = self.execute(workflow_id, trigger_payload)
        instance_id = instance["id"]
        logger.info("Workflow %s queued, instance=%s", workflow_id, instance_id)

        deadline = time.monotonic() + timeout
        ctx: dict[str, Any] = {}
        while time.monotonic() < deadline:
            ctx = self.get_context(workflow_id, instance_id)
            status = ctx.get("status")
            if status == "completed":
                return ctx
            if status == "failed":
                snippet = json.dumps(ctx.get("context_json", {}), default=str)[:400]
                raise RuntimeError(
                    f"Workflow {workflow_id} (instance {instance_id}) failed. "
                    f"Context: {snippet}"
                )
            if status == "suspended":
                if return_on_suspended:
                    return ctx
                raise RuntimeError(
                    f"Workflow {workflow_id} (instance {instance_id}) is suspended "
                    "awaiting human approval — cannot auto-resume from Studio."
                )
            time.sleep(_POLL_INTERVAL)

        raise TimeoutError(
            f"Workflow {workflow_id} (instance {instance_id}) did not complete "
            f"within {timeout}s (last status: {ctx.get('status', 'unknown')})"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


# ---------------------------------------------------------------------------
# Module-level singleton — reused across tool calls within a process lifetime
# ---------------------------------------------------------------------------
_singleton: OrchestratorClient | None = None


def get_orchestrator_client() -> OrchestratorClient:
    global _singleton
    if _singleton is None:
        _singleton = OrchestratorClient()
    return _singleton
