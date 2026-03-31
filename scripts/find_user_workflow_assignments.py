from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.ae_client import get_ae_client


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _full_name(user: dict[str, Any]) -> str:
    return " ".join(
        part for part in [
            str(user.get("firstName") or "").strip(),
            str(user.get("lastName") or "").strip(),
        ]
        if part
    ).strip()


def _score_user_match(user: dict[str, Any], query: str) -> int:
    q = _normalize(query)
    if not q:
        return 0

    user_id = str(user.get("id") or "").strip()
    username = _normalize(str(user.get("userName") or ""))
    full_name = _normalize(_full_name(user))

    if q == user_id:
        return 110
    if q == username:
        return 100
    if q == full_name:
        return 95
    if q and q in username:
        return 70
    return 0


def _find_matching_users(users: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    scored = []
    for user in users:
        score = _score_user_match(user, query)
        if score > 0:
            enriched = dict(user)
            enriched["_match_score"] = score
            enriched["_full_name"] = _full_name(user)
            scored.append(enriched)
    scored.sort(
        key=lambda item: (
            int(item.get("_match_score", 0)),
            _normalize(str(item.get("userName") or "")),
        ),
        reverse=True,
    )
    return scored


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "workflows", "permissions"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _get_users(client: Any, *, size: int) -> list[dict[str, Any]]:
    raw = client.post(
        "/users",
        params={"admin": "false", "offset": 0, "size": size, "order": "desc"},
        json_body={},
    )
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        return [item for item in raw["data"] if isinstance(item, dict)]
    return _extract_list(raw)


def _get_workflow_grants(client: Any) -> dict[int, dict[str, Any]]:
    raw = client.get(f"/tenants/{client.org}/users/workflowgrants")
    grants = {}
    for item in _extract_list(raw):
        user_id = item.get("id")
        if user_id is not None:
            grants[int(user_id)] = item
    return grants


def _get_categories(client: Any) -> dict[int, str]:
    raw = client.get(f"/tenants/{client.org}/category")
    categories = {}
    for item in _extract_list(raw):
        cat_id = item.get("id")
        if cat_id is not None:
            categories[int(cat_id)] = str(item.get("categoryName") or item.get("name") or cat_id)
    return categories


def _normalize_workflow_params(params_payload: Any) -> list[dict[str, Any]]:
    params = []
    for item in params_payload or []:
        if not isinstance(item, dict):
            continue
        params.append(
            {
                "name": str(item.get("name") or ""),
                "type": str(item.get("type") or ""),
                "display_name": str(item.get("displayName") or item.get("name") or ""),
                "required": not bool(item.get("optional", False)),
                "secret": bool(item.get("secret", False)),
                "ui_control_type": str(item.get("uiControlType") or ""),
                "description": str(item.get("description") or ""),
            }
        )
    params.sort(key=lambda item: item["name"].lower())
    return params


def _get_workflow_catalog(client: Any, *, active_only: bool, size: int = 100) -> dict[int, dict[str, Any]]:
    workflow_catalog: dict[int, dict[str, Any]] = {}
    offset = 0

    while True:
        params = {"offset": offset, "size": size, "categoryId": ""}
        if active_only:
            params["active"] = "true"

        raw = client.get("/workflows/catalogue", params=params)
        items = _extract_list(raw)
        if not items:
            break

        for item in items:
            wf_id = item.get("id")
            if wf_id is None:
                continue
            wf_id_int = int(wf_id)
            workflow_catalog[wf_id_int] = {
                "id": wf_id_int,
                "name": str(item.get("workflowName") or item.get("name") or wf_id_int),
                "description": str(item.get("description") or ""),
                "params": _normalize_workflow_params(item.get("params") or []),
            }

        if len(items) < size:
            break
        offset += size

    return workflow_catalog


def _is_admin_user(user: dict[str, Any]) -> bool:
    for role in user.get("roles") or []:
        if not isinstance(role, dict):
            continue
        if _normalize(str(role.get("roleName") or "")) == "admin":
            return True
    return False


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_workflow_id_by_name(
    workflow_catalog: dict[int, dict[str, Any]],
    workflow_name: str,
) -> int | None:
    normalized_name = _normalize(workflow_name)
    if not normalized_name:
        return None
    for workflow_id, workflow in workflow_catalog.items():
        candidate = str(workflow.get("name") or "")
        if _normalize(candidate) == normalized_name:
            return workflow_id
    return None


def _resolve_workflow_details(
    client: Any,
    workflow_catalog: dict[int, dict[str, Any]],
    workflow_id: int,
) -> dict[str, Any]:
    cached = workflow_catalog.get(workflow_id)
    if cached and cached.get("params") and cached.get("description") is not None:
        return cached

    details = {
        "id": workflow_id,
        "name": f"Unknown Workflow {workflow_id}",
        "description": "",
        "params": [],
    }
    if cached:
        details.update(
            {
                "name": str(cached.get("name") or details["name"]),
                "description": str(cached.get("description") or ""),
                "params": list(cached.get("params") or []),
            }
        )

    try:
        wf = client.get_workflow(str(workflow_id))
        if isinstance(wf, dict):
            resolved_name = str(
                wf.get("workflowName")
                or wf.get("name")
                or wf.get("workflowNameValue")
                or details["name"]
            )
            if not details["name"] or details["name"].startswith("Unknown Workflow "):
                details["name"] = resolved_name
            if not details["description"]:
                details["description"] = str(wf.get("description") or "")
            params = _normalize_workflow_params(wf.get("params") or [])
            if params:
                details["params"] = params
    except Exception:
        pass

    if not details["params"]:
        try:
            runtime_params = client.get_workflow_runtime_params(str(workflow_id))
            if isinstance(runtime_params, dict):
                details["params"] = _normalize_workflow_params(
                    runtime_params.get("params")
                    or runtime_params.get("parameters")
                    or runtime_params.get("data")
                    or []
                )
            elif isinstance(runtime_params, list):
                details["params"] = _normalize_workflow_params(runtime_params)
        except Exception:
            pass

    workflow_catalog[workflow_id] = details
    return details


def _build_admin_workflow_permissions(
    client: Any,
    workflow_catalog: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    workflow_permissions: list[dict[str, Any]] = []
    for workflow_id in sorted(workflow_catalog):
        workflow_details = _resolve_workflow_details(client, workflow_catalog, workflow_id)
        workflow_permissions.append(
            {
                "id": workflow_id,
                "name": workflow_details["name"],
                "description": workflow_details.get("description", ""),
                "params": workflow_details.get("params", []),
                "param_count": len(workflow_details.get("params", [])),
                "permission": "rwx",
                "computed_permission": "rwx",
            }
        )
    workflow_permissions.sort(key=lambda item: (item["name"].lower(), item["id"]))
    return workflow_permissions


def _build_user_workflow_permissions(
    client: Any,
    workflow_catalog: dict[int, dict[str, Any]],
    user_id: int,
    fallback_permissions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    workflow_permissions: list[dict[str, Any]] = []
    seen_workflow_ids: set[int] = set()

    try:
        assigned_workflows = client.get_user_workflows(str(user_id))
    except Exception:
        assigned_workflows = []

    for item in _extract_list(assigned_workflows) if not isinstance(assigned_workflows, list) else assigned_workflows:
        if not isinstance(item, dict):
            continue

        workflow_id = _coerce_int(
            item.get("id")
            or item.get("workflowId")
            or item.get("workflow_id")
        )
        if workflow_id is None:
            workflow_id = _find_workflow_id_by_name(
                workflow_catalog,
                str(item.get("workflowName") or item.get("name") or ""),
            )
        if workflow_id is None or workflow_id in seen_workflow_ids:
            continue

        workflow_details = _resolve_workflow_details(client, workflow_catalog, workflow_id)
        permission = str(
            item.get("permission")
            or item.get("computedPermission")
            or item.get("access")
            or "assigned"
        )
        computed_permission = str(
            item.get("computedPermission")
            or item.get("permission")
            or item.get("access")
            or permission
        )
        workflow_permissions.append(
            {
                "id": workflow_id,
                "name": workflow_details["name"],
                "description": workflow_details.get("description", ""),
                "params": workflow_details.get("params", []),
                "param_count": len(workflow_details.get("params", [])),
                "permission": permission,
                "computed_permission": computed_permission,
            }
        )
        seen_workflow_ids.add(workflow_id)

    if workflow_permissions:
        workflow_permissions.sort(key=lambda item: (item["name"].lower(), item["id"]))
        return workflow_permissions

    return fallback_permissions


def _parse_permission_blocks(
    permissions_payload: list[dict[str, Any]] | dict[str, Any],
    workflow_catalog: dict[int, dict[str, Any]],
    category_map: dict[int, str],
    client: Any,
) -> dict[str, list[dict[str, Any]]]:
    workflow_permissions: list[dict[str, Any]] = []
    category_permissions: list[dict[str, Any]] = []

    for block in _extract_list(permissions_payload):
        block_type = str(block.get("type") or "").strip().lower()
        for perm in block.get("permissions") or []:
            if not isinstance(perm, dict):
                continue
            perm_id = perm.get("id")
            if perm_id is None:
                continue
            perm_id_int = int(perm_id)
            if block_type == "workflow":
                workflow_details = _resolve_workflow_details(client, workflow_catalog, perm_id_int)
                workflow_permissions.append(
                    {
                        "id": perm_id_int,
                        "name": workflow_details["name"],
                        "description": workflow_details.get("description", ""),
                        "params": workflow_details.get("params", []),
                        "param_count": len(workflow_details.get("params", [])),
                        "permission": str(perm.get("permission") or ""),
                        "computed_permission": str(perm.get("computedPermission") or ""),
                    }
                )
            elif block_type == "category":
                category_permissions.append(
                    {
                        "id": perm_id_int,
                        "name": category_map.get(perm_id_int, f"Unknown Category {perm_id_int}"),
                        "permission": str(perm.get("permission") or ""),
                        "computed_permission": str(perm.get("computedPermission") or ""),
                    }
                )

    workflow_permissions.sort(key=lambda item: (item["name"].lower(), item["id"]))
    category_permissions.sort(key=lambda item: (item["name"].lower(), item["id"]))
    return {
        "workflow_permissions": workflow_permissions,
        "category_permissions": category_permissions,
    }


def build_assignment_report(query: str, *, active_only: bool = True, size: int = 200) -> dict[str, Any]:
    client = get_ae_client()
    users = _get_users(client, size=size)
    matches = _find_matching_users(users, query)

    report: dict[str, Any] = {
        "query": query,
        "tenant": client.org,
        "active_only": active_only,
        "matched_users": [],
    }
    if not matches:
        report["message"] = f"No users matched query '{query}'."
        return report

    grant_map = _get_workflow_grants(client)
    category_map = _get_categories(client)
    workflow_catalog = _get_workflow_catalog(client, active_only=active_only)

    for user in matches:
        user_id = int(user["id"])
        grant_info = grant_map.get(user_id, {})
        is_admin_user = _is_admin_user(user)
        permissions_payload: list[dict[str, Any]] | dict[str, Any] = []
        parsed = {"workflow_permissions": [], "category_permissions": []}

        if not is_admin_user:
            try:
                permissions_payload = client.get(f"/user/{user_id}/all/permissions")
            except Exception:
                permissions_payload = []
            parsed = _parse_permission_blocks(permissions_payload, workflow_catalog, category_map, client)
            workflow_permissions = _build_user_workflow_permissions(
                client,
                workflow_catalog,
                user_id,
                parsed["workflow_permissions"],
            )
        else:
            workflow_permissions = _build_admin_workflow_permissions(client, workflow_catalog)

        report["matched_users"].append(
            {
                "id": user_id,
                "user_name": str(user.get("userName") or ""),
                "first_name": str(user.get("firstName") or ""),
                "last_name": str(user.get("lastName") or ""),
                "full_name": _full_name(user),
                "state": str(user.get("state") or ""),
                "role_names": [str(r.get("roleName") or "") for r in (user.get("roles") or []) if isinstance(r, dict)],
                "has_admin_role": is_admin_user,
                "group_names": [str(g) for g in (grant_info.get("groupNames") or []) if g],
                "workflow_permissions": workflow_permissions,
                "category_permissions": parsed["category_permissions"],
                "workflow_count": len(workflow_permissions),
                "category_count": len(parsed["category_permissions"]),
                "match_score": int(user.get("_match_score", 0)),
            }
        )

    return report


def _format_text_report(report: dict[str, Any]) -> str:
    lines = [
        f"Tenant: {report.get('tenant', '')}",
        f"Query: {report.get('query', '')}",
        f"Active workflows only: {report.get('active_only', True)}",
    ]

    matches = report.get("matched_users") or []
    if not matches:
        lines.append(report.get("message", "No matches found."))
        return "\n".join(lines)

    for user in matches:
        lines.append("")
        lines.append(f"User: {user['full_name'] or user['user_name']} [ID: {user['id']}]")
        lines.append(f"Username: {user['user_name']}")
        lines.append(f"State: {user['state']}")
        lines.append(f"Roles: {', '.join(user['role_names']) if user['role_names'] else '(none)'}")
        lines.append(f"Groups: {', '.join(user['group_names']) if user['group_names'] else '(none)'}")
        if user.get("has_admin_role"):
            lines.append("Access model: Admin role detected, treating all catalog workflows as accessible.")
        lines.append(f"Assigned workflows: {user['workflow_count']}")

        if user["workflow_permissions"]:
            for wf in user["workflow_permissions"]:
                lines.append(
                    f"  - {wf['name']} [ID: {wf['id']}] permission={wf['permission']} computed={wf['computed_permission']}"
                )
                if wf["params"]:
                    for param in wf["params"]:
                        required = "required" if param["required"] else "optional"
                        secret = " secret" if param["secret"] else ""
                        lines.append(
                            f"      param: {param['name']} [{param['type']}] {required}{secret} display='{param['display_name']}'"
                        )
                else:
                    lines.append("      param: (none)")
        else:
            lines.append("  - No direct workflow permissions found.")

        if user["category_permissions"]:
            lines.append("Category permissions:")
            for cat in user["category_permissions"]:
                lines.append(
                    f"  - {cat['name']} [ID: {cat['id']}] permission={cat['permission']} computed={cat['computed_permission']}"
                )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find user-wise assigned AutomationEdge workflows using admin credentials "
            "from the repo .env file."
        )
    )
    parser.add_argument("query", help="User ID, username, exact full name, or partial username to match a user.")
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Also try to resolve workflow names outside the active workflow list.",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=200,
        help="How many users to fetch from AutomationEdge (default: 200).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as JSON instead of formatted text.",
    )
    args = parser.parse_args()

    report = build_assignment_report(
        args.query,
        active_only=not args.include_inactive,
        size=max(1, args.size),
    )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_format_text_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
