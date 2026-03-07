"""
P0 credential_read tools — 3 tools.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.credential")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


async def credential_pool_get_availability(pool_id: str) -> str:
    """Check available vs in-use credentials in a pool."""
    data = get_ae_client().get_credential_pool(pool_id)
    return _safe_json({
        "pool_id": pool_id,
        "pool_name": data.get("poolName") or data.get("name"),
        "total_credentials": data.get("totalCredentials") or data.get("total"),
        "available": data.get("availableCredentials") or data.get("available"),
        "in_use": data.get("inUseCredentials") or data.get("inUse") or data.get("borrowed"),
        "locked": data.get("lockedCredentials") or data.get("locked"),
        "max_concurrent": data.get("maxConcurrent"),
    })


async def credential_pool_get_waiting_requests(pool_id: str) -> str:
    """Get requests waiting on a credential pool."""
    data = get_ae_client().get_credential_pool(pool_id)
    pool_name = data.get("poolName") or data.get("name") or pool_id
    waiting = data.get("waitingRequests") or data.get("queuedRequests") or []

    if not waiting:
        try:
            retrying = get_ae_client().search_requests(
                filters={"status": "Retry"},
                limit=100,
            )
            for r in retrying:
                err = (r.get("errorMessage") or "").lower()
                if pool_name.lower() in err or "credential" in err:
                    waiting.append({
                        "request_id": r.get("id") or r.get("automationRequestId"),
                        "workflow_name": r.get("workflowName") or (r.get("workflowConfiguration") or {}).get("name"),
                        "status": r.get("status"),
                        "error": r.get("errorMessage"),
                        "created": r.get("createdDate"),
                    })
        except Exception:
            pass

    items = []
    for w in waiting:
        if isinstance(w, dict):
            items.append({
                "request_id": w.get("request_id") or w.get("requestId") or w.get("id"),
                "workflow_name": w.get("workflow_name") or w.get("workflowName"),
                "status": w.get("status"),
                "error": w.get("error") or w.get("errorMessage"),
                "waiting_since": w.get("created") or w.get("createdDate") or w.get("waitingSince"),
            })
    return _safe_json({
        "pool_id": pool_id,
        "pool_name": pool_name,
        "waiting_requests": items,
        "count": len(items),
    })


async def credential_pool_diagnose_retry_state(
    request_id: str = "",
    pool_id: str = "",
) -> str:
    """One-shot diagnosis of credential-related Retry state."""
    client = get_ae_client()
    findings: list[str] = []
    request_info: dict[str, Any] = {}
    pool_info: dict[str, Any] = {}

    if request_id:
        try:
            req = client.get_request(request_id)
            request_info = {
                "request_id": request_id,
                "status": req.get("status"),
                "workflow_name": req.get("workflowName") or (req.get("workflowConfiguration") or {}).get("name"),
                "error": req.get("errorMessage") or req.get("errorDetails"),
                "retry_count": req.get("retryCount"),
            }
            err = (req.get("errorMessage") or "").lower()
            if "credential" in err or "pool" in err:
                findings.append("Error message indicates a credential pool issue.")
            if req.get("status") != "Retry":
                findings.append(f"Request is in '{req.get('status')}' state, not Retry.")
        except Exception as e:
            findings.append(f"Could not fetch request: {e}")

    if pool_id:
        try:
            pool = client.get_credential_pool(pool_id)
            pool_info = {
                "pool_id": pool_id,
                "pool_name": pool.get("poolName") or pool.get("name"),
                "total": pool.get("totalCredentials") or pool.get("total"),
                "available": pool.get("availableCredentials") or pool.get("available"),
                "in_use": pool.get("inUseCredentials") or pool.get("inUse"),
            }
            avail = pool_info.get("available")
            if avail is not None and int(avail) == 0:
                findings.append("Credential pool has ZERO available credentials — all are in use or locked.")
            elif avail is not None and int(avail) > 0:
                findings.append(f"Pool has {avail} available credentials — retry may be from a transient lock.")
        except Exception as e:
            findings.append(f"Could not fetch pool info: {e}")

    if not findings:
        findings.append("Unable to determine root cause. Verify pool_id or check request error details.")

    return _safe_json({
        "request_info": request_info,
        "pool_info": pool_info,
        "findings": findings,
    })


# ── P1 support: validate_for_workflow ───────────────────────────────────

async def credential_pool_validate_for_workflow(workflow_id: str, pool_id: str = "") -> str:
    """Validate that a credential pool is usable by a workflow. If pool_id is empty, use workflow's configured pool."""
    client = get_ae_client()
    wf = client.get_workflow(workflow_id)
    wf_pool_ref = wf.get("credentialPool") or wf.get("credentialPoolId") or wf.get("credentialPoolName")
    pid = pool_id or wf_pool_ref
    if not pid:
        return _safe_json({
            "workflow_id": workflow_id,
            "pool_id": None,
            "workflow_uses_pool": None,
            "valid": False,
            "note": "Workflow has no credential pool configured.",
        })
    pool = client.get_credential_pool(pid)
    pool_name = pool.get("poolName") or pool.get("name")
    valid = str(wf_pool_ref or "") == str(pid) or str(wf_pool_ref) == str(pool_name)
    return _safe_json({
        "pool_id": pid,
        "workflow_id": workflow_id,
        "workflow_uses_pool": wf_pool_ref,
        "valid": valid,
        "pool_available": pool.get("availableCredentials") or pool.get("available"),
    })
