# Related Issue Health Checks

## Purpose

This change adds an extra condition on top of the existing `check_workflow_status` flow.

It does **not** replace the current behavior.
It only adds a related-system health check when:

1. the latest execution is a failure, and
2. the latest failure logs suggest either:
   - a `Life Asia` connectivity/system issue, or
   - a `TEBT` login/portal issue

If those conditions do not match, the current code behaves exactly as before.

## What Was Added

### 1. Config flags and workflow mapping

File: [config/settings.py](d:/AG_V2/AEAgenticSupport/config/settings.py)

Added new config keys:

- `ENABLE_RELATED_ISSUE_HEALTH_CHECK`
- `RELATED_ISSUE_HEALTH_CHECK_USE_ADMIN_SCOPE`
- `LIFE_ASIA_HEALTH_CHECK_WORKFLOW`
- `TEBT_HEALTH_CHECK_WORKFLOW`

These make the feature configurable and avoid burying workflow-name mapping inside the logic.

### 2. Root `.env` entries

File: [`.env`](d:/AG_V2/AEAgenticSupport/.env)

Added:

- `ENABLE_RELATED_ISSUE_HEALTH_CHECK=true`
- `RELATED_ISSUE_HEALTH_CHECK_USE_ADMIN_SCOPE=true`
- `LIFE_ASIA_HEALTH_CHECK_WORKFLOW=TEBT_Health_Check`
- `TEBT_HEALTH_CHECK_WORKFLOW=Life_Asia_Health_Check`

Important note:

- Logical `Life Asia health check` is mapped to actual AE workflow `TEBT_Health_Check`
- Logical `TEBT health check` is mapped to actual AE workflow `Life_Asia_Health_Check`

This matches the workflow-name swap you provided.

### 3. Related issue detection from failure evidence

File: [status_tools.py](d:/AG_V2/AEAgenticSupport/tools/status_tools.py)

Added helper methods:

- `_extract_related_issue_text(...)`
- `_detect_related_issue(...)`
- `_build_related_health_check_summary(...)`
- `_run_related_health_check(...)`
- `_maybe_add_related_issue_health_check(...)`

What they do:

- collect text from execution-log output
- classify whether the failure looks like `Life Asia` or `TEBT`
- trigger the mapped health-check workflow
- run it using the AE admin/service-account path
- append a business-readable summary into the status result

### 4. Additive enrichment inside existing status flow

File: [status_tools.py](d:/AG_V2/AEAgenticSupport/tools/status_tools.py)

Inside `check_workflow_status(...)`, after the existing latest-status logic is built:

- the code now calls `_maybe_add_related_issue_health_check(latest)`
- if a related issue is detected, it appends extra text to the existing message
- the result payload also includes `related_issue_check`

This means the old status response is preserved and only enriched when the new condition matches.

### 5. Prompt guidance for orchestrator

File: [orchestrator.py](d:/AG_V2/AEAgenticSupport/agents/orchestrator.py)

Added one extra rule in the system prompt:

- if the user asks for process status, failure reason, or health check, the agent should call `check_workflow_status` first
- if that tool returns a related issue check/result, the agent should surface it clearly

This improves scenario handling from the prompt side without changing the core old flow.

### 6. Tests

File: [test_related_issue_health_checks.py](d:/AG_V2/AEAgenticSupport/tests/test_related_issue_health_checks.py)

Added tests for:

- Life Asia failure detection -> mapped health check trigger
- TEBT failure detection -> mapped health check trigger

Verified with:

```bash
python -m pytest tests/test_related_issue_health_checks.py -q
```

Result:

- `2 passed`

## Trigger Logic

The extra health-check branch runs only when all of these are true:

1. `ENABLE_RELATED_ISSUE_HEALTH_CHECK=true`
2. `check_workflow_status(...)` is called for a workflow/process
3. the latest execution status is `Failure`, `Failed`, or `Error`
4. the latest execution has an execution/request ID
5. execution logs indicate one of these patterns:
   - `Life Asia` + connection/system terms
   - `TEBT` + login/portal/auth/session terms

If any of those do not match, no health check is triggered.

## Admin Scope Behavior

The health check is intentionally triggered using the AE admin/service account path, not user-wise workflow access.

Reason:

- you explicitly requested this to run via admin credentials
- these are platform/system health checks, not end-user business workflows

The execution path relies on the existing service-account mode already present in the codebase, using the AE credentials from the root [`.env`](d:/AG_V2/AEAgenticSupport/.env).

## What Was Not Changed

These parts were intentionally left unchanged:

- existing `check_workflow_status` lookup flow
- existing user-wise access logic for normal workflows
- existing remediation/retry flow
- existing log retrieval flow
- existing chatbot behavior for unrelated failures

So this is an additive feature, not a rewrite of the current status implementation.

## Example Outcomes

### Life Asia related

If the latest failure logs show a Life Asia connection issue:

- the bot still returns the normal process status
- then it appends:
  - detected `Life Asia` issue
  - triggered `Life Asia health check`
  - result such as:
    - `System is down. Please investigate system connectivity.`
    - or `System is up. Issue may be intermittent. Retry recommended.`

### TEBT related

If the latest failure logs show a TEBT login/portal issue:

- the bot still returns the normal process status
- then it appends:
  - detected `TEBT` issue
  - triggered `TEBT health check`
  - result such as:
    - `Login service down. Please check credentials/server.`
    - or `Portal accessible. Check bot credentials or session issue.`

## Files Changed

- [config/settings.py](d:/AG_V2/AEAgenticSupport/config/settings.py)
- [`.env`](d:/AG_V2/AEAgenticSupport/.env)
- [tools/status_tools.py](d:/AG_V2/AEAgenticSupport/tools/status_tools.py)
- [agents/orchestrator.py](d:/AG_V2/AEAgenticSupport/agents/orchestrator.py)
- [tests/test_related_issue_health_checks.py](d:/AG_V2/AEAgenticSupport/tests/test_related_issue_health_checks.py)
