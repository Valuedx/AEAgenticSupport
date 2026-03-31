# Implementation Plan v2: User-Scoped Workflow Access Control

This plan hardens workflow access control for AutomationEdge so each user can only discover and execute workflows they are authorized for.

## 0. Why This Revision

The repository already has key building blocks:

- `user_workflow_access` table exists in `setup_db.py` and `schema.sql`.
- `scripts/sync_user_workflow_access.py` already syncs grants.
- Multiple runtime paths still call global workflow search/resolve logic without user-aware checks.

This v2 plan focuses on security hardening, gap closure, and safe rollout.

## 1. Goals

1. Enforce deny-by-default access to workflow discovery and execution.
2. Use trusted identity data for admin privileges (not chat-provided `user_role`).
3. Prevent stale access after AE permission changes.
4. Enforce authorization consistently across all read and execute paths.
5. Keep performance impact low and measurable.

## 2. Non-Goals

1. Re-architecting RAG storage into per-user indexes.
2. Replacing existing role-based conversational persona behavior.
3. Changing AE platform-side authorization semantics.

## 3. Security Principles

1. Fail closed: if user identity or grant lookup fails, return no workflows.
2. Trust boundary: workflow authorization must not rely on user-supplied request fields.
3. Single policy engine: all code paths call one shared access-check module.
4. Authorization at execution-time: discovery filtering alone is insufficient.
5. Org-aware checks: always include `org_code` in authorization decisions.

## 4. Target Architecture

Global tables remain shared (`workflow_catalog`, `rag_documents`), but every workflow read/resolve/execute operation is wrapped by a centralized access service:

- `security/workflow_access.py` (new module)
  - `is_workflow_admin(user_id) -> bool`
  - `get_allowed_workflows(user_id, org_code) -> set[str]`
  - `can_view_workflow(user_id, workflow_id, org_code) -> bool`
  - `can_execute_workflow(user_id, workflow_id, org_code) -> bool`
  - `filter_tool_hits_for_user(user_id, org_code, hits) -> list[dict]`

## 5. Data Model and Schema Changes

### 5.1 Existing Tables Used

- `user_registry`
- `user_workflow_access`
- `workflow_catalog`
- `rag_documents` (collection `tools`)

### 5.2 Required Schema Updates

Update `setup_db.py` and `schema.sql` with:

```sql
ALTER TABLE user_registry
    ADD COLUMN IF NOT EXISTS ae_user_id INTEGER,
    ADD COLUMN IF NOT EXISTS ae_username VARCHAR(256),
    ADD COLUMN IF NOT EXISTS ae_is_admin BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE user_workflow_access
    ADD COLUMN IF NOT EXISTS match_method VARCHAR(32) DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_uwa_user_org
    ON user_workflow_access(user_id, org_code);

CREATE INDEX IF NOT EXISTS idx_uwa_user_wf_org
    ON user_workflow_access(user_id, workflow_id, org_code);
```

## 6. Identity Matching and Sync Hardening

### 6.1 Files

- `[MODIFY] scripts/sync_user_workflow_access.py`
- `[MODIFY] agents/scheduler.py`
- `[MODIFY] state/conversation_state.py`

### 6.2 Teams-to-AE Connection and Search Plan

This section is mandatory because the workflow access model depends on reliable linkage between the Teams user identity and the AE user identity.

#### Teams Identity Sources

Capture and persist the following fields from the incoming Teams/message context into `user_registry`:

| Source System | Field | Example | Purpose |
| --- | --- | --- | --- |
| Teams | `user_id` | `29:1abc...` | Stable chat identity used by the app |
| Teams | `user_name` | `Kirtibala Gujar` | Human-readable fallback match input |
| Teams | `user_email` | `kirtibala.gujar@company.com` | Primary deterministic match input |
| Teams metadata | `aadObjectId` | Azure AD GUID | Best enterprise identity key when available |
| Teams metadata | tenant/team info | tenant/team IDs | Audit and diagnostics only |

#### AE Identity Sources

Use the AE user directory and permission APIs as the source of truth:

1. Fetch AE users from `/users`.
2. Read AE user identity fields such as:
   - `id`
   - `userName`
   - `email`
   - `firstName`
   - `lastName`
   - role/admin markers
3. For the matched AE user, fetch workflow grants using:
   - `get_user_workflows(ae_user_id)`
   - fallback `/user/{ae_user_id}/all/permissions`

#### Connection Search Flow

For each Teams user in `user_registry`, run the following search sequence:

1. Extract `aadObjectId` from Teams metadata if present.
2. Search for a direct AE identity match using enterprise identifiers first.
3. If no deterministic match exists, search AE users by exact email.
4. If email is missing or unmatched, search by exact `userName`.
5. If still unresolved, search by normalized `firstName + lastName`.
6. If multiple AE candidates remain, do not auto-grant. Mark for manual review.
7. Once a single AE user is selected, fetch workflow assignments and reconcile `user_workflow_access`.

#### Match Output To Persist

Persist the resolved linkage for auditability:

- `user_registry.ae_user_id`
- `user_registry.ae_username`
- `user_registry.ae_is_admin`
- `user_workflow_access.match_confidence`
- `user_workflow_access.match_method`
- `user_workflow_access.last_seen_at`

#### Failure Handling

If Teams-to-AE linkage cannot be established safely:

1. Do not grant any workflows.
2. Keep the user in a deny-by-default state.
3. Log the reason as one of:
   - `missing_teams_identity`
   - `no_ae_match`
   - `multiple_ae_candidates`
   - `low_confidence_match`
4. Surface the user in sync diagnostics for admin review.

#### Sync Triggers

The Teams-to-AE search flow should run through three supported entry points:

1. Scheduled full sync from `agents/scheduler.py`.
2. First-message targeted sync for a newly seen user from `state/conversation_state.py`.
3. Manual CLI dry-run and single-user sync for admin troubleshooting.

### 6.3 Matching Strategy (Priority Order)

1. AAD object ID match (if available in both systems).
2. Exact email match.
3. Exact username/email alias match.
4. Normalized full-name match.
5. Fuzzy name match only for review mode, not auto-grant by default.

### 6.4 Confidence and Grant Rules

- Auto-grant only for high-confidence trusted matches.
- `MIN_MATCH_CONFIDENCE` for auto-grant should be strict (recommended `0.90`).
- Below threshold: no grants written, flagged in sync output for review.

Suggested `match_method` values:

- `aad_object_id`
- `email_exact`
- `username_exact`
- `full_name_exact`
- `name_fuzzy_review`

### 6.5 Deprovisioning (Required)

Current sync upserts grants but does not fully remove stale ones. Add per-user reconciliation:

1. Build the latest authorized workflow set from AE.
2. Upsert current grants.
3. Delete grants for `(user_id, org_code)` not present in latest set.

This prevents lingering access when permissions are revoked upstream.

### 6.6 Admin Source of Truth

- Populate `user_registry.ae_is_admin` only from AE sync data.
- Never derive workflow admin bypass from chat/session `user_role`.
- `user_role` remains persona/RBAC context for conversation behavior only.
- Treat `ae_user_id`, `ae_username`, and `ae_is_admin` as sync-owned fields, not request-owned fields.

## 7. Coverage Matrix: All Paths To Enforce

| Path | File(s) | Required Change |
| --- | --- | --- |
| RAG tool search | `rag/engine.py` | Add `search_tools_for_user(user_id, org_code, ...)` and filter by allowed `(workflow_id, org_code)` pairs |
| Orchestrator discovery prefetch | `agents/orchestrator.py` | Replace `search_tools(...)` with user-aware search |
| Meta-tool discovery | `tools/registry.py` | Pass user context into discover path and filter before ranking |
| Catalog name/id resolution | `tools/automationedge_client.py` | Add user-aware overloads for `resolve_cached_workflow_name`, `get_cached_workflow_info`, `get_cached_workflow_id` |
| RAG fallback resolution | `tools/automationedge_client.py`, `tools/status_tools.py` | Ensure semantic resolve only returns user-authorized workflows |
| Generic workflow runner | `tools/registry.py` | Require `user_id` and enforce `can_execute_workflow(...)` before `execute_workflow(...)` |
| Remediation trigger | `tools/remediation_tools.py` | Extend `trigger_workflow(...)` to accept `user_id`/`org_code` and enforce execution check |
| Direct execute-and-poll tool | `tools/status_tools.py` | Extend `t4_execute_and_poll(...)` to accept `user_id`/`org_code` and enforce execution check |
| Direct workflow list tool | `tools/status_tools.py` | Filter `ae.workflow.list` results to workflows visible to the calling user |
| External helper entrypoints | `custom/helpers/rag.py` and any `/rag/*` handlers | Ensure user-aware search contracts are used |

## 8. Execution-Time Guardrails (Critical)

Discovery filtering alone is not enough. Add execution checks in all workflow-triggering paths:

1. Resolve workflow to `(workflow_id, org_code)`.
2. Authorize with `can_execute_workflow(user_id, workflow_id, org_code)`.
3. If unauthorized, block execution with a safe error message.
4. Log denied attempts with structured audit fields.

Also ensure orchestrator carries `user_id` into tool call args when auto-executing workflow tools.

## 9. Caching and Leakage Controls

1. No shared cache entries for user-filtered workflow hits.
2. Any cache key must include `user_id` and `org_code`.
3. Avoid logging raw sensitive parameter payloads in denial or sync logs.
4. Keep match diagnostics but redact PII where possible.

## 10. Rollout Plan (Feature-Flagged)

### Phase 1: Observe (No Blocking)

- Add shared access module and instrumentation.
- Log what would be denied on read/execute paths.
- Flags:
  - `WF_ACCESS_ENFORCE_READ=false`
  - `WF_ACCESS_ENFORCE_EXECUTE=false`

### Phase 2: Enforce Read Paths

- Enable user-filtered search/resolve paths.
- Keep execution guard in monitor mode.
- Flag:
  - `WF_ACCESS_ENFORCE_READ=true`

### Phase 3: Enforce Execution Paths

- Enable hard authorization checks before all workflow execution calls.
- Flag:
  - `WF_ACCESS_ENFORCE_EXECUTE=true`

### Phase 4: Cleanup

- Remove legacy unscoped calls.
- Keep periodic compliance checks for path regressions.

## 11. Verification Plan

### 11.1 Automated Tests

Add tests for:

1. User cannot discover unauthorized workflows.
2. User cannot execute unauthorized workflows even if workflow name is guessed.
3. Admin bypass only works when `ae_is_admin=true` in DB.
4. Spoofed chat `user_role='admin'` does not bypass workflow authorization.
5. Deprovisioning removes revoked grants after sync.
6. Org collision safety: same `workflow_id` in different `org_code` does not leak.
7. Missing `user_id` fails closed.
8. `discover_tools` only returns authorized workflow-backed tools.

### 11.2 Manual Tests

1. Standard user sees only assigned workflows.
2. AE admin sees full catalog through DB-backed admin marker.
3. Unmatched user sees zero workflows.
4. Revoked user loses access after next sync run.
5. Approval/resume flow keeps user context and still enforces execution authorization.

### 11.3 Commands

```bash
python scripts/sync_user_workflow_access.py --dry-run
pytest -q tests
```

### 11.4 Performance SLOs

1. P95 additional latency from access checks: <= 50 ms on tool search.
2. No regression in median workflow trigger startup time.

## 12. Observability and Audit

Add structured logs and metrics:

- `access_check.allowed_count`
- `access_check.denied_count`
- `access_check.path` (rag_search, discover, execute)
- `sync.users_processed`
- `sync.users_skipped_low_confidence`
- `sync.grants_added`
- `sync.grants_removed`
- `sync.errors`

## 13. Definition of Done

1. All read and execute workflow paths call centralized access service.
2. Admin bypass uses AE-synced DB flag only.
3. Stale grants are removed during sync reconciliation.
4. Feature flags support staged rollout and rollback.
5. Test coverage exists for negative and bypass scenarios.
6. Runbooks updated with operational commands and troubleshooting notes.
