"""
Sync user-workflow access from AutomationEdge into the local access tables.

This script:
1. Reads Teams users from `user_registry`
2. Resolves each Teams user to AE identity by Teams username, then exact email, then email local-part
3. Persists AE linkage fields on `user_registry`
4. Reconciles `user_workflow_access` grants per user
5. Removes stale grants when permissions are revoked upstream

Usage:
    python scripts/sync_user_workflow_access.py
    python scripts/sync_user_workflow_access.py --user "kirtibala.gujar"
    python scripts/sync_user_workflow_access.py --user-id "29:1abc..."
    python scripts/sync_user_workflow_access.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from config.settings import CONFIG

logger = logging.getLogger("sync_user_workflow_access")


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_username(value: Any) -> str:
    return _normalize(value)


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _email_local_part(value: Any) -> str:
    email = _normalize_email(value)
    if "@" not in email:
        return ""
    return email.split("@", 1)[0].strip()


def _ae_email_candidates(ae_user: dict[str, Any]) -> set[str]:
    emails: set[str] = set()
    for candidate in (
        ae_user.get("email"),
        ae_user.get("userEmail"),
        ae_user.get("emailId"),
        ae_user.get("mail"),
        ae_user.get("userPrincipalName"),
        ae_user.get("upn"),
    ):
        clean = _normalize_email(candidate)
        if clean:
            emails.add(clean)
    return emails


def _safe_list(payload: Any, keys: tuple[str, ...] = ("data", "items", "results", "workflows", "permissions")) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_teams_identity(teams_user: dict[str, Any]) -> dict[str, str]:
    metadata = teams_user.get("metadata") if isinstance(teams_user.get("metadata"), dict) else {}
    channel_data = metadata.get("channel_data") if isinstance(metadata.get("channel_data"), dict) else {}
    channel_user = channel_data.get("user") if isinstance(channel_data.get("user"), dict) else {}
    entities = metadata.get("entities") if isinstance(metadata.get("entities"), list) else []

    aad_object_id = ""
    for candidate in (
        channel_user.get("aadObjectId"),
        channel_user.get("aad_object_id"),
        metadata.get("aadObjectId"),
        metadata.get("aad_object_id"),
    ):
        if candidate:
            aad_object_id = str(candidate).strip()
            break
    if not aad_object_id:
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            candidate = entity.get("aadObjectId") or entity.get("aad_object_id")
            if candidate:
                aad_object_id = str(candidate).strip()
                break

    user_id = str(teams_user.get("user_id") or "").strip()
    user_name = str(teams_user.get("user_name") or "").strip()
    user_email = str(teams_user.get("user_email") or channel_user.get("email") or "").strip()

    return {
        "user_id": user_id,
        "user_name": user_name,
        "user_email": user_email,
        "aad_object_id": aad_object_id,
        "teams_id": aad_object_id or user_id,
    }


def _is_admin_user(user: dict[str, Any]) -> bool:
    direct_markers = (
        user.get("isAdmin"),
        user.get("admin"),
        user.get("is_admin"),
    )
    if any(bool(marker) for marker in direct_markers if marker is not None):
        return True

    for role in user.get("roles") or []:
        if not isinstance(role, dict):
            continue
        role_name = _normalize(role.get("roleName") or role.get("name"))
        if role_name == "admin":
            return True
    return False


def _build_catalog(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT workflow_id, org_code, workflow_name
            FROM workflow_catalog
            WHERE active = TRUE
            """
        )
        rows = cur.fetchall()

    all_rows: list[dict[str, str]] = []
    by_id: dict[str, list[dict[str, str]]] = {}
    for workflow_id, org_code, workflow_name in rows:
        item = {
            "workflow_id": str(workflow_id or "").strip(),
            "org_code": str(org_code or "").strip(),
            "workflow_name": str(workflow_name or "").strip(),
        }
        if not item["workflow_id"]:
            continue
        all_rows.append(item)
        by_id.setdefault(item["workflow_id"], []).append(item)

    return {"all": all_rows, "by_id": by_id}


def _get_teams_users(conn, *, user_filter: str = "", user_id: str = "") -> list[dict]:
    where_clauses = ["(COALESCE(user_name, '') != '' OR COALESCE(user_email, '') != '')"]
    params: list[Any] = []
    if user_filter:
        where_clauses.append("user_name = %s")
        params.append(user_filter)
    if user_id:
        where_clauses.append("user_id = %s")
        params.append(user_id)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT user_id, user_name, user_email, user_team, metadata
            FROM user_registry
            WHERE {' AND '.join(where_clauses)}
            ORDER BY updated_at DESC NULLS LAST, user_id ASC
            """,
            tuple(params),
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_ae_users(ae_client: Any) -> list[dict]:
    params = {"admin": "false", "offset": 0, "size": 1000, "order": "desc"}
    attempts: list[tuple[str, dict[str, Any]]] = [
        ("post", {"path": "/users", "json_body": {}, "params": params}),
        ("get", {"path": "/users", "params": params}),
    ]
    last_exc: Exception | None = None
    for method_name, kwargs in attempts:
        method = getattr(ae_client, method_name, None)
        if not callable(method):
            continue
        try:
            raw = method(**kwargs)
            users = _safe_list(raw)
            if users:
                return users
        except Exception as exc:
            last_exc = exc
    if last_exc:
        raise last_exc
    return []


def _candidate_records(teams_identity: dict[str, str], ae_users: list[dict]) -> list[dict[str, Any]]:
    teams_username = _normalize_username(teams_identity.get("user_name"))
    teams_email = _normalize_email(teams_identity.get("user_email"))
    teams_email_local = _email_local_part(teams_email)

    records: list[dict[str, Any]] = []
    for ae_user in ae_users:
        ae_username = _normalize_username(ae_user.get("userName"))
        ae_emails = _ae_email_candidates(ae_user)

        if teams_username and ae_username and teams_username == ae_username:
            records.append(
                {
                    "method": "username_exact",
                    "confidence": 1.0,
                    "ae_user": ae_user,
                }
            )
            continue

        if teams_email:
            if teams_email == ae_username or teams_email in ae_emails:
                records.append(
                    {
                        "method": "email_exact",
                        "confidence": 0.95,
                        "ae_user": ae_user,
                    }
                )
                continue

        if teams_email_local:
            if teams_email_local == ae_username:
                records.append(
                    {
                        "method": "email_local_part",
                        "confidence": 0.9,
                        "ae_user": ae_user,
                    }
                )
                continue
            if any(_email_local_part(candidate) == teams_email_local for candidate in ae_emails):
                records.append(
                    {
                        "method": "email_local_part",
                        "confidence": 0.9,
                        "ae_user": ae_user,
                    }
                )

    return sorted(records, key=lambda item: item["confidence"], reverse=True)


def _resolve_match(teams_user: dict[str, Any], ae_users: list[dict]) -> dict[str, Any]:
    identity = _extract_teams_identity(teams_user)
    if not identity["user_name"] and not identity["user_email"]:
        return {
            "status": "missing_teams_identity",
            "identity": identity,
            "ae_user": None,
            "confidence": 0.0,
            "method": "unknown",
        }

    candidates = _candidate_records(identity, ae_users)
    if not candidates:
        return {
            "status": "no_ae_match",
            "identity": identity,
            "ae_user": None,
            "confidence": 0.0,
            "method": "unknown",
        }

    best = candidates[0]
    tied = [
        candidate
        for candidate in candidates
        if abs(float(candidate["confidence"]) - float(best["confidence"])) < 0.0001
    ]
    if len(tied) > 1:
        return {
            "status": "multiple_ae_candidates",
            "identity": identity,
            "ae_user": None,
            "confidence": float(best["confidence"]),
            "method": str(best["method"]),
        }

    return {
        "status": "matched",
        "identity": identity,
        "ae_user": best["ae_user"],
        "confidence": float(best["confidence"]),
        "method": str(best["method"]),
    }


def _permission_from_value(raw_value: Any) -> str:
    value = _normalize(raw_value)
    if value in {"admin", "owner"}:
        return "admin"
    if "read" in value and "execute" not in value and "write" not in value:
        return "read"
    if any(token in value for token in ("write", "execute", "run", "trigger", "owner", "admin")):
        return "execute"
    return "execute"


def _catalog_org_for_workflow(catalog: dict[str, Any], workflow_id: str, org_code: str = "") -> str:
    clean_org = str(org_code or "").strip()
    if clean_org:
        return clean_org
    matches = catalog["by_id"].get(str(workflow_id or "").strip(), [])
    if len(matches) == 1:
        return str(matches[0]["org_code"] or "").strip()
    default_org = str(CONFIG.get("AE_ORG_CODE", "") or "").strip()
    if default_org:
        preferred_matches = [row for row in matches if str(row.get("org_code") or "").strip() == default_org]
        if preferred_matches:
            return default_org
        if not matches:
            logger.info(
                "Falling back to default org_code=%s for workflow_id=%s because catalog has no matching row",
                default_org,
                workflow_id,
            )
            return default_org
    return ""


def _fetch_grants_for_user(
    ae_client: Any,
    ae_user: dict[str, Any],
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    ae_user_id = int(ae_user.get("id", 0))
    ae_username = str(ae_user.get("userName") or "").strip()
    match_rows: list[dict[str, Any]] = []

    try:
        raw_workflows = ae_client.get_user_workflows(str(ae_user_id))
    except Exception as exc:
        logger.warning(
            "get_user_workflows failed for AE user %s (%s); falling back to permissions API",
            ae_user_id,
            exc,
        )
        raw_workflows = []

    for item in _safe_list(raw_workflows, keys=("workflows", "items", "data")):
        workflow_id = str(
            item.get("workflowId")
            or item.get("workflow_id")
            or item.get("id")
            or ""
        ).strip()
        if not workflow_id:
            continue
        org_code = _catalog_org_for_workflow(
            catalog,
            workflow_id,
            item.get("orgCode") or item.get("org_code") or "",
        )
        if not org_code:
            logger.warning(
                "Skipping workflow grant for AE user %s workflow_id=%s because org_code is ambiguous or missing",
                ae_user_id,
                workflow_id,
            )
            continue
        match_rows.append(
            {
                "workflow_id": workflow_id,
                "org_code": org_code,
                "permission": _permission_from_value(
                    item.get("permission")
                    or item.get("computedPermission")
                    or item.get("access")
                ),
                "ae_user_id": ae_user_id,
                "ae_username": ae_username,
            }
        )

    if match_rows:
        return match_rows

    try:
        raw_permissions = ae_client.get(f"/user/{ae_user_id}/all/permissions")
    except Exception as exc:
        logger.warning(
            "permissions fallback failed for AE user %s: %s",
            ae_user_id,
            exc,
        )
        raw_permissions = []

    for block in _safe_list(raw_permissions):
        block_type = _normalize(block.get("type"))
        if block_type and block_type != "workflow":
            continue
        permissions = block.get("permissions") if isinstance(block.get("permissions"), list) else []
        for permission in permissions:
            if not isinstance(permission, dict):
                continue
            workflow_id = str(
                permission.get("workflowId")
                or permission.get("workflow_id")
                or permission.get("id")
                or ""
            ).strip()
            if not workflow_id:
                continue
            org_code = _catalog_org_for_workflow(
                catalog,
                workflow_id,
                permission.get("orgCode") or permission.get("org_code") or "",
            )
            if not org_code:
                logger.warning(
                    "Skipping permission grant for AE user %s workflow_id=%s because org_code is ambiguous or missing",
                    ae_user_id,
                    workflow_id,
                )
                continue
            match_rows.append(
                {
                    "workflow_id": workflow_id,
                    "org_code": org_code,
                    "permission": _permission_from_value(permission.get("permission") or "execute"),
                    "ae_user_id": ae_user_id,
                    "ae_username": ae_username,
                }
            )

    return match_rows


def _update_user_registry_link(
    conn,
    *,
    user_id: str,
    user_name: str,
    user_email: str,
    ae_user_id: int | None,
    ae_username: str,
    ae_is_admin: bool,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_registry
                (user_id, user_role, user_name, user_email, ae_user_id, ae_username, ae_is_admin, updated_at)
            VALUES (%s, 'technical', %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                user_name = COALESCE(NULLIF(EXCLUDED.user_name, ''), user_registry.user_name),
                user_email = COALESCE(NULLIF(EXCLUDED.user_email, ''), user_registry.user_email),
                ae_user_id = EXCLUDED.ae_user_id,
                ae_username = EXCLUDED.ae_username,
                ae_is_admin = EXCLUDED.ae_is_admin,
                updated_at = NOW()
            """,
            (
                user_id,
                str(user_name or "").strip(),
                str(user_email or "").strip(),
                ae_user_id,
                ae_username or None,
                ae_is_admin,
            ),
        )


def _reconcile_access_rows(
    conn,
    *,
    user_id: str,
    teams_id: str,
    match_confidence: float,
    match_method: str,
    access_rows: list[dict[str, Any]],
    dry_run: bool,
) -> tuple[int, int]:
    desired_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for row in access_rows:
        key = (
            str(row.get("workflow_id") or "").strip(),
            str(row.get("org_code") or "").strip(),
        )
        if not key[0] or not key[1]:
            continue
        desired_rows[key] = {
            "user_id": user_id,
            "teams_id": teams_id,
            "workflow_id": key[0],
            "org_code": key[1],
            "permission": str(row.get("permission") or "execute").strip() or "execute",
            "ae_user_id": row.get("ae_user_id"),
            "ae_username": str(row.get("ae_username") or "").strip(),
            "match_confidence": match_confidence,
            "match_method": match_method or "unknown",
        }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT workflow_id, org_code
            FROM user_workflow_access
            WHERE user_id = %s
            """,
            (user_id,),
        )
        existing_keys = {
            (str(row[0]).strip(), str(row[1] or "").strip())
            for row in cur.fetchall()
            if row and row[0]
        }

    desired_keys = set(desired_rows)
    stale_keys = existing_keys - desired_keys

    if not dry_run:
        with conn.cursor() as cur:
            for row in desired_rows.values():
                cur.execute(
                    """
                    INSERT INTO user_workflow_access
                        (user_id, teams_id, workflow_id, org_code, permission,
                         ae_user_id, ae_username, match_confidence, match_method,
                         synced_at, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (user_id, workflow_id, org_code)
                    DO UPDATE SET
                        teams_id = EXCLUDED.teams_id,
                        permission = EXCLUDED.permission,
                        ae_user_id = EXCLUDED.ae_user_id,
                        ae_username = EXCLUDED.ae_username,
                        match_confidence = EXCLUDED.match_confidence,
                        match_method = EXCLUDED.match_method,
                        synced_at = NOW(),
                        last_seen_at = NOW()
                    """,
                    (
                        row["user_id"],
                        row["teams_id"],
                        row["workflow_id"],
                        row["org_code"],
                        row["permission"],
                        row["ae_user_id"],
                        row["ae_username"],
                        row["match_confidence"],
                        row["match_method"],
                    ),
                )
            for workflow_id, org_code in stale_keys:
                cur.execute(
                    """
                    DELETE FROM user_workflow_access
                    WHERE user_id = %s AND workflow_id = %s AND org_code = %s
                    """,
                    (user_id, workflow_id, org_code),
                )
        conn.commit()

    return (len(desired_rows), len(stale_keys))


def sync_single_user(
    teams_user: dict[str, Any],
    ae_users: list[dict],
    ae_client: Any,
    workflow_catalog: dict[str, Any],
    *,
    dry_run: bool = False,
    conn=None,
) -> dict[str, Any]:
    identity = _extract_teams_identity(teams_user)
    match = _resolve_match(teams_user, ae_users)
    ae_user = match.get("ae_user") if isinstance(match.get("ae_user"), dict) else None

    result = {
        "user_id": identity["user_id"],
        "user_name": identity["user_name"],
        "user_email": identity["user_email"],
        "teams_id": identity["teams_id"],
        "match_found": False,
        "match_confidence": float(match.get("confidence") or 0.0),
        "match_method": str(match.get("method") or "unknown"),
        "ae_username": "",
        "ae_user_id": None,
        "is_admin": False,
        "grants_upserted": 0,
        "grants_removed": 0,
        "skipped_reason": str(match.get("status") or ""),
    }

    if not ae_user:
        if conn:
            _update_user_registry_link(
                conn,
                user_id=identity["user_id"],
                user_name=identity["user_name"],
                user_email=identity["user_email"],
                ae_user_id=None,
                ae_username="",
                ae_is_admin=False,
                dry_run=dry_run,
            )
            _, removed = _reconcile_access_rows(
                conn,
                user_id=identity["user_id"],
                teams_id=identity["teams_id"],
                match_confidence=float(match.get("confidence") or 0.0),
                match_method=str(match.get("method") or "unknown"),
                access_rows=[],
                dry_run=dry_run,
            )
            result["grants_removed"] = removed
        logger.warning(
            "Skipping user_id=%s user_name=%r due to %s",
            identity["user_id"],
            identity["user_name"],
            result["skipped_reason"],
        )
        return result

    ae_user_id = int(ae_user.get("id", 0))
    ae_username = str(ae_user.get("userName") or "").strip()
    ae_is_admin = _is_admin_user(ae_user)

    result.update(
        {
            "match_found": True,
            "ae_username": ae_username,
            "ae_user_id": ae_user_id,
            "is_admin": ae_is_admin,
            "skipped_reason": "",
        }
    )

    access_rows: list[dict[str, Any]]
    if ae_is_admin:
        access_rows = [
            {
                "workflow_id": row["workflow_id"],
                "org_code": row["org_code"],
                "permission": "admin",
                "ae_user_id": ae_user_id,
                "ae_username": ae_username,
            }
            for row in workflow_catalog["all"]
        ]
    else:
        try:
            access_rows = _fetch_grants_for_user(ae_client, ae_user, workflow_catalog)
        except Exception as exc:
            logger.warning(
                "grant fetch failed for matched AE user %s (%s); preserving AE identity link with zero grants",
                ae_user_id,
                exc,
            )
            access_rows = []

    if conn:
        _update_user_registry_link(
            conn,
            user_id=identity["user_id"],
            user_name=identity["user_name"],
            user_email=identity["user_email"],
            ae_user_id=ae_user_id,
            ae_username=ae_username,
            ae_is_admin=ae_is_admin,
            dry_run=dry_run,
        )
        upserted, removed = _reconcile_access_rows(
            conn,
            user_id=identity["user_id"],
            teams_id=identity["teams_id"],
            match_confidence=float(match.get("confidence") or 0.0),
            match_method=str(match.get("method") or "unknown"),
            access_rows=access_rows,
            dry_run=dry_run,
        )
        result["grants_upserted"] = upserted
        result["grants_removed"] = removed
    else:
        result["grants_upserted"] = len(access_rows)

    return result


def sync_all_users(*, dry_run: bool = False, user_filter: str = "", user_id: str = "") -> dict[str, Any]:
    from config.db import get_conn
    from mcp_server.ae_client import get_ae_client

    summary = {
        "users_synced": 0,
        "users_skipped": 0,
        "grants_upserted": 0,
        "grants_removed": 0,
        "errors": 0,
        "details": [],
    }

    ae_client = get_ae_client()
    with get_conn() as conn:
        teams_users = _get_teams_users(conn, user_filter=user_filter, user_id=user_id)
        workflow_catalog = _build_catalog(conn)

    if not teams_users:
        logger.warning("No Teams users found for workflow access sync")
        return summary

    ae_users = _fetch_ae_users(ae_client)
    logger.info(
        "Starting workflow access sync for %d Teams users against %d AE users",
        len(teams_users),
        len(ae_users),
    )

    with get_conn() as conn:
        for teams_user in teams_users:
            try:
                result = sync_single_user(
                    teams_user,
                    ae_users,
                    ae_client,
                    workflow_catalog,
                    dry_run=dry_run,
                    conn=conn,
                )
                summary["details"].append(result)
                summary["grants_upserted"] += int(result.get("grants_upserted", 0) or 0)
                summary["grants_removed"] += int(result.get("grants_removed", 0) or 0)
                if result.get("match_found"):
                    summary["users_synced"] += 1
                else:
                    summary["users_skipped"] += 1
            except Exception as exc:
                logger.error("Error syncing user_id=%s: %s", teams_user.get("user_id"), exc)
                summary["errors"] += 1

    logger.info(
        "Workflow access sync complete: users_synced=%d users_skipped=%d grants_upserted=%d grants_removed=%d errors=%d",
        summary["users_synced"],
        summary["users_skipped"],
        summary["grants_upserted"],
        summary["grants_removed"],
        summary["errors"],
    )
    return summary


def sync_user_by_id(
    user_id: str,
    user_name: str = "",
    user_email: str = "",
    *,
    conn=None,
) -> dict[str, Any]:
    from config.db import get_conn
    from mcp_server.ae_client import get_ae_client

    own_context = conn is None
    if own_context:
        conn_manager = get_conn()
        conn = conn_manager.__enter__()
    else:
        conn_manager = None

    try:
        teams_users = _get_teams_users(conn, user_id=user_id)
        if not teams_users:
            fallback_email = str(user_email or "").strip()
            fallback_name = str(user_name or "").strip()
            if not fallback_email and "@" in fallback_name:
                fallback_email = fallback_name
            teams_users = [{
                "user_id": user_id,
                "user_name": fallback_name,
                "user_email": fallback_email,
                "metadata": {},
            }]

        workflow_catalog = _build_catalog(conn)
        ae_client = get_ae_client()
        ae_users = _fetch_ae_users(ae_client)
        return sync_single_user(
            teams_users[0],
            ae_users,
            ae_client,
            workflow_catalog,
            dry_run=False,
            conn=conn,
        )
    except Exception as exc:
        logger.error("sync_user_by_id failed for user_id=%s: %s", user_id, exc)
        return {"match_found": False, "error": str(exc)}
    finally:
        if conn_manager is not None:
            conn_manager.__exit__(None, None, None)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Sync user workflow access from AutomationEdge")
    parser.add_argument("--user", help="Filter by exact user_name (case-sensitive)")
    parser.add_argument("--user-id", help="Sync a single user_id")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    args = parser.parse_args()

    result = sync_all_users(
        dry_run=args.dry_run,
        user_filter=args.user or "",
        user_id=args.user_id or "",
    )

    import json

    summary = {key: value for key, value in result.items() if key != "details"}
    print(json.dumps(summary, indent=2, default=str))
    if result.get("details"):
        print("\nDetails:")
        for detail in result["details"]:
            status = "matched" if detail.get("match_found") else f"skipped:{detail.get('skipped_reason', 'unknown')}"
            print(
                f"  - user_id={detail.get('user_id')} status={status} "
                f"method={detail.get('match_method')} confidence={detail.get('match_confidence', 0):.2f} "
                f"grants_upserted={detail.get('grants_upserted', 0)} grants_removed={detail.get('grants_removed', 0)}"
            )
