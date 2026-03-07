"""
P1 dependency_probe tools — support preflight checks.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_server.ae_client import get_ae_client

logger = logging.getLogger("ae_mcp.tools.dependency")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


async def dependency_check_input_file_exists(workflow_id: str, request_id: str = "", path_param: str = "") -> str:
    """Check whether the input file expected by the workflow exists (e.g. from request params)."""
    client = get_ae_client()
    path_checked = path_param
    if request_id and not path_checked:
        try:
            req = client.get_request(request_id)
            params = req.get("params") or req.get("parameters") or []
            if isinstance(params, list):
                for p in params:
                    if isinstance(p, dict) and "path" in str(p.get("name", "")).lower() or "file" in str(p.get("name", "")).lower():
                        path_checked = p.get("value") or path_checked
                        break
        except Exception:
            pass
    return _safe_json({
        "workflow_id": workflow_id,
        "request_id": request_id,
        "input_path_checked": path_checked,
        "note": "Actual file existence check would require agent or file-share access; this reports the path from request/config.",
    })


async def dependency_check_output_folder_writable(workflow_id: str, path_param: str = "") -> str:
    """Validate that the output folder/path is writable (conceptual check; actual check needs agent)."""
    return _safe_json({
        "workflow_id": workflow_id,
        "output_path": path_param,
        "note": "Write access validation typically runs on the agent; ensure workflow output path is configured and agent has write permission.",
    })


async def dependency_run_full_preflight_for_workflow(workflow_id: str, request_id: str = "") -> str:
    """Run a complete support preflight: workflow active, assigned agent, credentials, inputs."""
    client = get_ae_client()
    findings: list[str] = []
    wf = {}
    try:
        wf = client.get_workflow(workflow_id)
        if not wf.get("active", True):
            findings.append("Workflow is INACTIVE.")
        agents = wf.get("assignedAgents") or wf.get("agents") or []
        if not agents:
            findings.append("No agents assigned to workflow.")
        else:
            findings.append(f"Assigned agent(s): {agents}")
    except Exception as e:
        findings.append(f"Could not load workflow: {e}")

    if request_id:
        try:
            req = client.get_request(request_id)
            status = req.get("status", "UNKNOWN")
            findings.append(f"Request status: {status}")
            err = req.get("errorMessage") or req.get("errorDetails")
            if err:
                findings.append(f"Request error: {err[:200]}")
        except Exception as e:
            findings.append(f"Could not load request: {e}")

    pool_ref = wf.get("credentialPool") or wf.get("credentialPoolId")
    if pool_ref:
        try:
            pool = client.get_credential_pool(str(pool_ref))
            avail = pool.get("availableCredentials") or pool.get("available")
            if avail is not None and int(avail) == 0:
                findings.append("Credential pool has zero available credentials.")
            else:
                findings.append(f"Credential pool has {avail} available.")
        except Exception:
            findings.append("Could not check credential pool.")

    if not findings:
        findings.append("No obvious preflight issues found.")
    return _safe_json({"workflow_id": workflow_id, "request_id": request_id, "findings": findings})
