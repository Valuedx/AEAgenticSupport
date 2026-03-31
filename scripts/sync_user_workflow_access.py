"""
Sync user-workflow access from AutomationEdge API into user_workflow_access table.

This script:
1. Reads all users from user_registry (Teams users)
2. Fetches all users from AE API
3. Matches Teams user_name → AE fullName (fuzzy, case-insensitive)
4. For each matched user, fetches their workflow grants from AE
5. Upserts rows into user_workflow_access with teams_id + match_confidence

Usage:
    python scripts/sync_user_workflow_access.py             # Sync all users
    python scripts/sync_user_workflow_access.py --user "Kirtibala Gujar"  # Single user
    python scripts/sync_user_workflow_access.py --dry-run   # Preview without writing
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

logger = logging.getLogger("sync_user_workflow_access")

# ── Matching utilities (reused from find_user_workflow_assignments.py) ──

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


def _score_user_match(ae_user: dict[str, Any], teams_user_name: str) -> float:
    """Score how well a Teams user_name matches an AE user. Returns 0.0-1.0."""
    q = _normalize(teams_user_name)
    if not q:
        return 0.0

    username = _normalize(str(ae_user.get("userName") or ""))
    full_name = _normalize(_full_name(ae_user))

    # Exact match on full name
    if q == full_name and full_name:
        return 1.0
    # Exact match on username
    if q == username and username:
        return 0.95
    # Full name contains query (or vice versa)
    if full_name and q in full_name:
        return 0.8
    if full_name and full_name in q:
        return 0.75
    # Username contains query
    if username and q in username:
        return 0.7
    # Partial first/last name match
    q_parts = set(q.split())
    name_parts = set(full_name.split()) if full_name else set()
    if q_parts and name_parts:
        overlap = len(q_parts & name_parts)
        if overlap > 0:
            return min(0.65, 0.3 + 0.2 * overlap)
    return 0.0


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "workflows", "permissions"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _is_admin_user(user: dict[str, Any]) -> bool:
    for role in user.get("roles") or []:
        if not isinstance(role, dict):
            continue
        if _normalize(str(role.get("roleName") or "")) == "admin":
            return True
    return False


# ── Database helpers ──

def _get_teams_users(conn) -> list[dict]:
    """Fetch all users from user_registry."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, user_name, user_email, user_team, metadata "
            "FROM user_registry WHERE user_name IS NOT NULL AND user_name != ''"
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _get_all_workflow_ids(conn) -> dict[str, str]:
    """Fetch all workflow_id → workflow_name from workflow_catalog."""
    with conn.cursor() as cur:
        cur.execute("SELECT workflow_id, org_code, workflow_name FROM workflow_catalog WHERE active = TRUE")
        result = {}
        for row in cur.fetchall():
            result[f"{row[0]}:{row[1]}"] = row[2]
        return result


def _extract_teams_id(metadata: Any) -> str:
    """Extract AAD Object ID from user_registry metadata."""
    if not isinstance(metadata, dict):
        return ""
    # Check channel_data.tenant.id or entities for AAD object ID
    channel_data = metadata.get("channel_data", {})
    if isinstance(channel_data, dict):
        user_info = channel_data.get("user", {})
        if isinstance(user_info, dict):
            aad_id = user_info.get("aadObjectId") or user_info.get("aad_object_id") or ""
            if aad_id:
                return str(aad_id)
    # Check entities
    entities = metadata.get("entities", [])
    if isinstance(entities, list):
        for ent in entities:
            if isinstance(ent, dict):
                aad_id = ent.get("aadObjectId") or ent.get("aad_object_id") or ""
                if aad_id:
                    return str(aad_id)
    return ""


def _upsert_access(conn, rows: list[dict], *, dry_run: bool = False) -> int:
    """Upsert rows into user_workflow_access. Returns count of upserted rows."""
    if not rows:
        return 0
    if dry_run:
        logger.info("[DRY RUN] Would upsert %d rows", len(rows))
        return len(rows)

    sql = """
        INSERT INTO user_workflow_access
            (user_id, teams_id, workflow_id, org_code, permission,
             ae_user_id, ae_username, match_confidence, synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id, workflow_id, org_code)
        DO UPDATE SET
            teams_id = EXCLUDED.teams_id,
            permission = EXCLUDED.permission,
            ae_user_id = EXCLUDED.ae_user_id,
            ae_username = EXCLUDED.ae_username,
            match_confidence = EXCLUDED.match_confidence,
            synced_at = NOW()
    """
    count = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, (
                row["user_id"],
                row.get("teams_id", ""),
                row["workflow_id"],
                row.get("org_code", ""),
                row.get("permission", "execute"),
                row.get("ae_user_id"),
                row.get("ae_username", ""),
                row.get("match_confidence", 0.0),
            ))
            count += 1
        conn.commit()
    return count


# ── Core sync logic ──

MIN_MATCH_CONFIDENCE = 0.7  # Skip matches below this threshold


def sync_single_user(
    teams_user: dict,
    ae_users: list[dict],
    ae_client: Any,
    workflow_catalog_ids: dict[str, str],
    *,
    dry_run: bool = False,
    conn=None,
) -> dict:
    """Sync workflow access for a single Teams user."""
    user_id = teams_user["user_id"]
    user_name = teams_user.get("user_name", "")
    teams_id = _extract_teams_id(teams_user.get("metadata"))

    # Find best matching AE user
    best_match = None
    best_score = 0.0
    for ae_user in ae_users:
        score = _score_user_match(ae_user, user_name)
        if score > best_score:
            best_score = score
            best_match = ae_user

    result = {
        "user_id": user_id,
        "user_name": user_name,
        "teams_id": teams_id,
        "match_found": False,
        "match_confidence": best_score,
        "ae_username": "",
        "ae_user_id": None,
        "is_admin": False,
        "grants_upserted": 0,
        "skipped_reason": "",
    }

    if not best_match or best_score < MIN_MATCH_CONFIDENCE:
        result["skipped_reason"] = (
            f"No AE user match (best score: {best_score:.2f}, "
            f"threshold: {MIN_MATCH_CONFIDENCE})"
        )
        logger.warning(
            "SKIP user '%s' (%s): %s",
            user_name, user_id, result["skipped_reason"],
        )
        return result

    ae_user_id = int(best_match.get("id", 0))
    ae_username = str(best_match.get("userName") or _full_name(best_match))
    is_admin = _is_admin_user(best_match)

    result["match_found"] = True
    result["ae_username"] = ae_username
    result["ae_user_id"] = ae_user_id
    result["is_admin"] = is_admin

    logger.info(
        "MATCH user '%s' → AE '%s' (id=%d, score=%.2f, admin=%s)",
        user_name, ae_username, ae_user_id, best_score, is_admin,
    )

    # Build access rows
    access_rows: list[dict] = []

    if is_admin:
        # Admin users get access to ALL workflows
        for key, wf_name in workflow_catalog_ids.items():
            wf_id, org = key.split(":", 1) if ":" in key else (key, "")
            access_rows.append({
                "user_id": user_id,
                "teams_id": teams_id,
                "workflow_id": wf_id,
                "org_code": org,
                "permission": "admin",
                "ae_user_id": ae_user_id,
                "ae_username": ae_username,
                "match_confidence": best_score,
            })
    else:
        # Fetch user's specific workflow grants from AE
        try:
            assigned_workflows = ae_client.get_user_workflows(str(ae_user_id))
            items = _extract_list(assigned_workflows) if not isinstance(assigned_workflows, list) else assigned_workflows
            for item in items:
                if not isinstance(item, dict):
                    continue
                wf_id = item.get("id") or item.get("workflowId") or item.get("workflow_id")
                if wf_id is None:
                    continue
                wf_id_str = str(wf_id).strip()
                permission_raw = str(
                    item.get("permission")
                    or item.get("computedPermission")
                    or item.get("access")
                    or "execute"
                ).strip().lower()
                # Map AE permission to our permission model
                if "w" in permission_raw or "x" in permission_raw or "execute" in permission_raw:
                    permission = "execute"
                elif "r" in permission_raw or "read" in permission_raw:
                    permission = "read"
                else:
                    permission = "execute"

                access_rows.append({
                    "user_id": user_id,
                    "teams_id": teams_id,
                    "workflow_id": wf_id_str,
                    "org_code": "",
                    "permission": permission,
                    "ae_user_id": ae_user_id,
                    "ae_username": ae_username,
                    "match_confidence": best_score,
                })
        except Exception as exc:
            logger.warning(
                "Failed to fetch workflow grants for AE user %d: %s",
                ae_user_id, exc,
            )

        # Also try the permissions API
        if not access_rows:
            try:
                perms_raw = ae_client.get(
                    f"/user/{ae_user_id}/all/permissions"
                )
                for block in _extract_list(perms_raw):
                    block_type = str(block.get("type") or "").strip().lower()
                    if block_type != "workflow":
                        continue
                    for perm in block.get("permissions") or []:
                        if not isinstance(perm, dict):
                            continue
                        wf_id = perm.get("id")
                        if wf_id is None:
                            continue
                        access_rows.append({
                            "user_id": user_id,
                            "teams_id": teams_id,
                            "workflow_id": str(wf_id),
                            "org_code": "",
                            "permission": "execute",
                            "ae_user_id": ae_user_id,
                            "ae_username": ae_username,
                            "match_confidence": best_score,
                        })
            except Exception as exc:
                logger.warning(
                    "Failed to fetch permissions for AE user %d: %s",
                    ae_user_id, exc,
                )

    if conn and access_rows:
        result["grants_upserted"] = _upsert_access(conn, access_rows, dry_run=dry_run)
    else:
        result["grants_upserted"] = len(access_rows)

    return result


def sync_all_users(*, dry_run: bool = False, user_filter: str = "") -> dict:
    """
    Main sync function. Called by CLI and scheduler.
    Returns summary dict with users_synced, grants_upserted, etc.
    """
    from config.db import get_conn
    from mcp_server.ae_client import get_ae_client

    ae_client = get_ae_client()

    # 1. Fetch Teams users from user_registry
    with get_conn() as conn:
        teams_users = _get_teams_users(conn)
        workflow_catalog_ids = _get_all_workflow_ids(conn)

    if user_filter:
        teams_users = [
            u for u in teams_users
            if _normalize(user_filter) in _normalize(u.get("user_name", ""))
        ]

    logger.info(
        "Sync starting: %d Teams users, %d workflows in catalog",
        len(teams_users), len(workflow_catalog_ids),
    )

    if not teams_users:
        logger.warning("No Teams users found in user_registry")
        return {"users_synced": 0, "grants_upserted": 0, "errors": 0}

    # 2. Fetch all AE users
    try:
        raw = ae_client.post(
            "/users",
            params={"admin": "false", "offset": 0, "size": 500, "order": "desc"},
            json_body={},
        )
        if isinstance(raw, dict) and isinstance(raw.get("data"), list):
            ae_users = [item for item in raw["data"] if isinstance(item, dict)]
        else:
            ae_users = _extract_list(raw)
    except Exception as exc:
        logger.error("Failed to fetch AE users: %s", exc)
        return {"users_synced": 0, "grants_upserted": 0, "errors": 1, "error": str(exc)}

    logger.info("Fetched %d AE users for matching", len(ae_users))

    # 3. Sync each Teams user
    summary = {
        "users_synced": 0,
        "users_skipped": 0,
        "grants_upserted": 0,
        "errors": 0,
        "details": [],
    }

    with get_conn() as conn:
        for teams_user in teams_users:
            try:
                result = sync_single_user(
                    teams_user, ae_users, ae_client, workflow_catalog_ids,
                    dry_run=dry_run, conn=conn,
                )
                if result.get("match_found"):
                    summary["users_synced"] += 1
                    summary["grants_upserted"] += result.get("grants_upserted", 0)
                else:
                    summary["users_skipped"] += 1
                summary["details"].append(result)
            except Exception as exc:
                logger.error(
                    "Error syncing user '%s': %s",
                    teams_user.get("user_name", "?"), exc,
                )
                summary["errors"] += 1

    logger.info(
        "Sync complete: %d users synced, %d skipped, %d grants, %d errors",
        summary["users_synced"], summary["users_skipped"],
        summary["grants_upserted"], summary["errors"],
    )
    return summary


def sync_user_by_id(user_id: str, user_name: str, *, conn=None) -> dict:
    """
    Lightweight sync for a single user by their user_id.
    Called from conversation_state on first interaction.
    """
    from config.db import get_conn
    from mcp_server.ae_client import get_ae_client

    ae_client = get_ae_client()
    own_conn = conn is None
    if own_conn:
        conn = get_conn().__enter__()

    try:
        workflow_catalog_ids = _get_all_workflow_ids(conn)

        # Fetch AE users
        try:
            raw = ae_client.post(
                "/users",
                params={"admin": "false", "offset": 0, "size": 500, "order": "desc"},
                json_body={},
            )
            if isinstance(raw, dict) and isinstance(raw.get("data"), list):
                ae_users = [item for item in raw["data"] if isinstance(item, dict)]
            else:
                ae_users = _extract_list(raw)
        except Exception as exc:
            logger.warning("sync_user_by_id: cannot fetch AE users: %s", exc)
            return {"match_found": False, "error": str(exc)}

        # Get user metadata from registry for teams_id
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata FROM user_registry WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            metadata = row[0] if row else {}

        teams_user = {
            "user_id": user_id,
            "user_name": user_name,
            "metadata": metadata or {},
        }
        return sync_single_user(
            teams_user, ae_users, ae_client, workflow_catalog_ids,
            conn=conn,
        )
    except Exception as exc:
        logger.error("sync_user_by_id failed for %s: %s", user_id, exc)
        return {"match_found": False, "error": str(exc)}
    finally:
        if own_conn:
            try:
                conn.__exit__(None, None, None)
            except Exception:
                pass


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Sync user workflow access from AE")
    parser.add_argument("--user", help="Filter by user name (partial match)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    args = parser.parse_args()

    import json
    result = sync_all_users(dry_run=args.dry_run, user_filter=args.user or "")
    # Print summary without details for brevity
    summary = {k: v for k, v in result.items() if k != "details"}
    print(f"\n{'='*60}")
    print(f"  Sync Summary")
    print(f"{'='*60}")
    print(json.dumps(summary, indent=2, default=str))

    if result.get("details"):
        print(f"\n  Details ({len(result['details'])} users):")
        for d in result["details"]:
            status = "✓" if d.get("match_found") else "✗"
            print(
                f"    {status} {d.get('user_name', '?'):30s} → "
                f"AE:{d.get('ae_username', 'N/A'):20s} "
                f"score={d.get('match_confidence', 0):.2f} "
                f"grants={d.get('grants_upserted', 0)}"
            )
