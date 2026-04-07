from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import time
from typing import Any

from config.db import get_conn
from config.settings import CONFIG

logger = logging.getLogger("ops_agent.security.workflow_access")

_access_refresh_lock = threading.Lock()
_access_refresh_attempted_at: dict[str, float] = {}
_ACCESS_REFRESH_COOLDOWN_SECONDS = 30.0


def is_read_enforced() -> bool:
    return bool(CONFIG.get("WF_ACCESS_ENFORCE_READ", False))


def is_execute_enforced() -> bool:
    return bool(CONFIG.get("WF_ACCESS_ENFORCE_EXECUTE", False))


def execute_auth_mode() -> str:
    mode = _normalize_text(CONFIG.get("WF_ACCESS_EXECUTE_AUTH_MODE", "service_account")).lower()
    if mode in {"service_account", "user_scope", "user_admin"}:
        return mode
    return "service_account"


def default_org_code() -> str:
    return str(CONFIG.get("AE_ORG_CODE", "") or "").strip()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_org_code(org_code: str = "") -> str:
    return _normalize_text(org_code) or default_org_code()


def _get_user_registry_identity(user_id: str) -> dict[str, str]:
    clean_user_id = _normalize_text(user_id)
    identity = {"user_name": "", "user_email": "", "ae_username": ""}
    if not clean_user_id:
        return identity

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_name, user_email, ae_username
                    FROM user_registry
                    WHERE user_id = %s
                    """,
                    (clean_user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return identity
                user_name = _normalize_text(row[0]) if len(row) > 0 else ""
                user_email = _normalize_text(row[1]) if len(row) > 1 else ""
                ae_username = _normalize_text(row[2]) if len(row) > 2 else ""
                return {
                    "user_name": user_name,
                    "user_email": user_email,
                    # Username is the canonical exact-match identity across Teams and webchat.
                    "ae_username": ae_username or user_name,
                }
    except Exception as exc:
        logger.warning("user registry identity lookup failed for user_id=%r: %s", clean_user_id, exc)
        return identity


def _get_ae_username_for_user(user_id: str) -> str:
    return _get_user_registry_identity(user_id).get("ae_username", "")


def _load_sync_user_by_id():
    try:
        from scripts.sync_user_workflow_access import sync_user_by_id

        return sync_user_by_id
    except ModuleNotFoundError:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        module_path = os.path.join(root, "scripts", "sync_user_workflow_access.py")
        spec = importlib.util.spec_from_file_location("sync_user_workflow_access_runtime", module_path)
        if not spec or not spec.loader:
            raise
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.sync_user_by_id


def _refresh_user_access_rows(user_id: str, identity: dict[str, str] | None = None) -> bool:
    clean_user_id = _normalize_text(user_id)
    if not clean_user_id or not CONFIG.get("WF_ACCESS_ENABLE_TARGETED_SYNC", True):
        return False

    resolved_identity = identity or _get_user_registry_identity(clean_user_id)
    user_name = _normalize_text(resolved_identity.get("user_name"))
    user_email = _normalize_text(resolved_identity.get("user_email"))
    if not user_name and not user_email:
        return False

    now = time.monotonic()
    with _access_refresh_lock:
        last_attempt = _access_refresh_attempted_at.get(clean_user_id, 0.0)
        if now - last_attempt < _ACCESS_REFRESH_COOLDOWN_SECONDS:
            return False
        _access_refresh_attempted_at[clean_user_id] = now

    try:
        sync_user_by_id = _load_sync_user_by_id()
        result = sync_user_by_id(
            user_id=clean_user_id,
            user_name=user_name,
            user_email=user_email,
        )
        logger.info(
            "Lazy workflow access refresh completed for user_id=%s match_found=%s grants_upserted=%s error=%s",
            clean_user_id,
            bool(isinstance(result, dict) and result.get("match_found")),
            int(result.get("grants_upserted", 0) or 0) if isinstance(result, dict) else 0,
            str(result.get("error") or "") if isinstance(result, dict) else "",
        )
        return True
    except Exception as exc:
        logger.warning("Lazy workflow access refresh failed for user_id=%r: %s", clean_user_id, exc)
        return False


def _workflow_name_variants(name: str) -> list[str]:
    base = _normalize_text(name)
    if not base:
        return []

    normalized = base.replace("-", "_").replace(" ", "_").strip()
    variants: list[str] = []

    def _add(candidate: str) -> None:
        clean = _normalize_text(candidate)
        if clean and clean.lower() not in {item.lower() for item in variants}:
            variants.append(clean)

    _add(base)
    _add(normalized)
    if normalized.upper().startswith("WF_"):
        _add(normalized[3:])
    else:
        _add(f"WF_{normalized}")
    return variants


def _is_workflow_backed_hit(hit: dict[str, Any]) -> bool:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    source = _normalize_text(metadata.get("source") or hit.get("source")).lower()
    workflow_name = _normalize_text(metadata.get("workflow_name") or hit.get("workflow_name"))
    workflow_id = _normalize_text(metadata.get("workflow_id") or hit.get("workflow_id"))
    dynamic = bool(metadata.get("dynamic") or hit.get("dynamic"))
    return bool(workflow_id or workflow_name or dynamic or source == "automationedge")


def _resolve_workflow_identity(
    *,
    workflow_id: str = "",
    workflow_name: str = "",
    org_code: str = "",
) -> tuple[str, str, str]:
    clean_id = _normalize_text(workflow_id)
    clean_name = _normalize_text(workflow_name)
    resolved_org = _normalize_org_code(org_code)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if clean_id:
                    if resolved_org:
                        cur.execute(
                            """
                            SELECT workflow_id, workflow_name, org_code
                            FROM workflow_catalog
                            WHERE workflow_id = %s AND org_code = %s
                            ORDER BY active DESC, fetched_at DESC
                            LIMIT 1
                            """,
                            (clean_id, resolved_org),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT workflow_id, workflow_name, org_code
                            FROM workflow_catalog
                            WHERE workflow_id = %s
                            ORDER BY active DESC, fetched_at DESC
                            LIMIT 1
                            """,
                            (clean_id,),
                        )
                    row = cur.fetchone()
                    if row:
                        return (
                            _normalize_text(row[0]),
                            _normalize_text(row[1]),
                            _normalize_text(row[2]),
                        )
                    return (clean_id, clean_name, resolved_org)

                variants = _workflow_name_variants(clean_name)
                if not variants:
                    return ("", "", resolved_org)

                placeholders = ",".join(["%s"] * len(variants))
                sql = f"""
                    SELECT workflow_id, workflow_name, org_code
                    FROM workflow_catalog
                    WHERE lower(workflow_name) IN ({placeholders})
                """
                params: list[Any] = [value.lower() for value in variants]
                if resolved_org:
                    sql += " AND org_code = %s"
                    params.append(resolved_org)
                sql += " ORDER BY active DESC, fetched_at DESC"
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                if rows:
                    preferred = {value.lower(): idx for idx, value in enumerate(variants)}
                    rows = sorted(
                        rows,
                        key=lambda row: (
                            preferred.get(_normalize_text(row[1]).lower(), 999),
                            0 if _normalize_text(row[2]) == resolved_org else 1,
                        ),
                    )
                    best = rows[0]
                    return (
                        _normalize_text(best[0]),
                        _normalize_text(best[1]),
                        _normalize_text(best[2]),
                    )
    except Exception as exc:
        logger.warning(
            "workflow identity resolution failed for workflow_id=%r workflow_name=%r org_code=%r: %s",
            workflow_id,
            workflow_name,
            org_code,
            exc,
        )

    return (clean_id, clean_name, resolved_org)


def _fetch_allowed_workflow_pairs(
    cur,
    *,
    user_id: str,
    ae_username: str,
    resolved_org: str,
    permissions: tuple[str, ...],
) -> set[tuple[str, str]]:
    if resolved_org and ae_username:
        cur.execute(
            """
            SELECT workflow_id, org_code
            FROM user_workflow_access
            WHERE (user_id = %s OR ae_username = %s)
              AND org_code = %s
              AND permission = ANY(%s)
            """,
            (user_id, ae_username, resolved_org, list(permissions)),
        )
    elif resolved_org:
        cur.execute(
            """
            SELECT workflow_id, org_code
            FROM user_workflow_access
            WHERE user_id = %s
              AND org_code = %s
              AND permission = ANY(%s)
            """,
            (user_id, resolved_org, list(permissions)),
        )
    elif ae_username:
        cur.execute(
            """
            SELECT workflow_id, org_code
            FROM user_workflow_access
            WHERE (user_id = %s OR ae_username = %s)
              AND permission = ANY(%s)
            """,
            (user_id, ae_username, list(permissions)),
        )
    else:
        cur.execute(
            """
            SELECT workflow_id, org_code
            FROM user_workflow_access
            WHERE user_id = %s
              AND permission = ANY(%s)
            """,
            (user_id, list(permissions)),
        )
    return {
        (_normalize_text(row[0]), _normalize_text(row[1]))
        for row in cur.fetchall()
        if row and row[0]
    }


def is_workflow_admin(user_id: str) -> bool:
    clean_user_id = _normalize_text(user_id)
    if not clean_user_id:
        return False

    identity = _get_user_registry_identity(clean_user_id)
    ae_username = _normalize_text(identity.get("ae_username"))
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if ae_username:
                    cur.execute(
                        """
                        SELECT COALESCE(bool_or(ae_is_admin), FALSE)
                        FROM user_registry
                        WHERE user_id = %s OR ae_username = %s OR user_name = %s
                        """,
                        (clean_user_id, ae_username, ae_username),
                    )
                else:
                    cur.execute(
                        "SELECT ae_is_admin FROM user_registry WHERE user_id = %s",
                        (clean_user_id,),
                    )
                row = cur.fetchone()
                return bool(row and row[0])
    except Exception as exc:
        logger.warning("workflow admin lookup failed for user_id=%r: %s", clean_user_id, exc)
        return False


def _get_allowed_workflow_pairs(
    user_id: str,
    org_code: str = "",
    *,
    require_execute: bool,
) -> set[tuple[str, str]]:
    clean_user_id = _normalize_text(user_id)
    resolved_org = _normalize_org_code(org_code)
    if not clean_user_id:
        return set()

    identity = _get_user_registry_identity(clean_user_id)
    ae_username = _normalize_text(identity.get("ae_username"))
    permissions = ("execute", "admin") if require_execute else ("read", "execute", "admin")

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if is_workflow_admin(clean_user_id):
                    if resolved_org:
                        cur.execute(
                            """
                            SELECT workflow_id, org_code
                            FROM workflow_catalog
                            WHERE active = TRUE AND org_code = %s
                            """,
                            (resolved_org,),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT workflow_id, org_code
                            FROM workflow_catalog
                            WHERE active = TRUE
                            """
                        )
                    return {
                        (_normalize_text(row[0]), _normalize_text(row[1]))
                        for row in cur.fetchall()
                        if row and row[0]
                    }

                allowed_pairs = _fetch_allowed_workflow_pairs(
                    cur,
                    user_id=clean_user_id,
                    ae_username=ae_username,
                    resolved_org=resolved_org,
                    permissions=permissions,
                )
                if allowed_pairs:
                    return allowed_pairs
    except Exception as exc:
        logger.warning(
            "allowed workflow lookup failed for user_id=%r org_code=%r require_execute=%s: %s",
            clean_user_id,
            resolved_org,
            require_execute,
            exc,
        )

    if _refresh_user_access_rows(clean_user_id, identity):
        refreshed_identity = _get_user_registry_identity(clean_user_id)
        refreshed_username = _normalize_text(refreshed_identity.get("ae_username"))
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    return _fetch_allowed_workflow_pairs(
                        cur,
                        user_id=clean_user_id,
                        ae_username=refreshed_username,
                        resolved_org=resolved_org,
                        permissions=permissions,
                    )
        except Exception as exc:
            logger.warning(
                "allowed workflow recheck failed for user_id=%r org_code=%r require_execute=%s: %s",
                clean_user_id,
                resolved_org,
                require_execute,
                exc,
            )

    return set()


def get_allowed_workflows(user_id: str, org_code: str = "") -> set[str]:
    return {
        workflow_id
        for workflow_id, _ in _get_allowed_workflow_pairs(
            user_id,
            org_code,
            require_execute=False,
        )
    }


def get_user_accessible_workflow_names(
    user_id: str,
    org_code: str = "",
    *,
    require_execute: bool = False,
    limit: int = 15,
) -> list[str]:
    """Return a sorted list of workflow *names* the user is allowed to access.

    Used to build helpful denial messages that show what the user *can* do.
    """
    pairs = _get_allowed_workflow_pairs(
        user_id,
        _normalize_org_code(org_code),
        require_execute=require_execute,
    )
    if not pairs:
        return []

    wf_ids = [wf_id for wf_id, _ in pairs]
    if not wf_ids:
        return []

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(wf_ids))
                cur.execute(
                    f"""
                    SELECT DISTINCT workflow_name
                    FROM workflow_catalog
                    WHERE workflow_id IN ({placeholders})
                      AND active = TRUE
                    ORDER BY workflow_name
                    """,
                    tuple(wf_ids),
                )
                names = [
                    _normalize_text(row[0])
                    for row in cur.fetchall()
                    if row and row[0]
                ]
                return names[:limit]
    except Exception as exc:
        logger.warning(
            "get_user_accessible_workflow_names failed for user_id=%r: %s",
            user_id, exc,
        )
        return []


def can_view_workflow(user_id: str, workflow_id: str, org_code: str = "") -> bool:
    clean_user_id = _normalize_text(user_id)
    clean_workflow_id = _normalize_text(workflow_id)
    resolved_org = _normalize_org_code(org_code)
    if not clean_user_id or not clean_workflow_id:
        return False
    if is_workflow_admin(clean_user_id):
        return True
    return (clean_workflow_id, resolved_org) in _get_allowed_workflow_pairs(
        clean_user_id,
        resolved_org,
        require_execute=False,
    )


def can_execute_workflow(user_id: str, workflow_id: str, org_code: str = "") -> bool:
    clean_user_id = _normalize_text(user_id)
    clean_workflow_id = _normalize_text(workflow_id)
    if not clean_user_id or not clean_workflow_id:
        return False

    mode = execute_auth_mode()
    if mode == "service_account":
        return bool(
            _normalize_text(CONFIG.get("AE_API_KEY"))
            or (
                _normalize_text(CONFIG.get("AE_USERNAME"))
                and _normalize_text(CONFIG.get("AE_PASSWORD"))
            )
        )

    if mode == "user_scope":
        resolved_org = _normalize_org_code(org_code)
        if is_workflow_admin(clean_user_id):
            return True
        return (clean_workflow_id, resolved_org) in _get_allowed_workflow_pairs(
            clean_user_id,
            resolved_org,
            require_execute=True,
        )

    return is_workflow_admin(clean_user_id)


def filter_tool_hits_for_user(
    user_id: str,
    org_code: str,
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not hits:
        return []

    clean_user_id = _normalize_text(user_id)
    resolved_org = _normalize_org_code(org_code)
    admin = is_workflow_admin(clean_user_id)
    allowed_pairs = _get_allowed_workflow_pairs(
        clean_user_id,
        resolved_org,
        require_execute=False,
    )

    filtered: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        if not _is_workflow_backed_hit(hit):
            filtered.append(hit)
            continue

        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        workflow_id, workflow_name, hit_org = _resolve_workflow_identity(
            workflow_id=_normalize_text(metadata.get("workflow_id") or hit.get("workflow_id")),
            workflow_name=_normalize_text(metadata.get("workflow_name") or hit.get("workflow_name")),
            org_code=_normalize_text(metadata.get("org_code") or hit.get("org_code") or resolved_org),
        )

        if admin and (workflow_id or workflow_name):
            filtered.append(hit)
            continue

        candidate_org = _normalize_org_code(hit_org or resolved_org)
        if workflow_id and (workflow_id, candidate_org) in allowed_pairs:
            filtered.append(hit)

    return filtered
