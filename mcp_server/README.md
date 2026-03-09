# AutomationEdge MCP Server

Independent [Model Context Protocol](https://modelcontextprotocol.io/) server for AutomationEdge IT Operations support. Exposes **106 tools** (71 P0 + 35 support-priority P1) for investigating, diagnosing, and remediating automation issues via any MCP-compatible client (Cursor, Claude Desktop, etc.).

When these tools are bridged into the main app (`AE_MCP_TOOLS_ENABLED=true`), the app now catalogs the full MCP surface but eagerly hydrates only a curated support subset. In co-located mode, it uses the shared local spec registry; when `AE_MCP_SERVER_URL` is set, it discovers tools remotely with `list_tools()` and executes them with `call_tool()`. The remaining MCP tools stay searchable through RAG and `discover_tools`, then rank alongside custom tools and AE workflow-backed tools using retrieval plus observed execution-history signals. Recent outcomes are weighted more heavily, and agent-scoped feedback is preferred when it exists, before hydrating on demand for the active turn.

When the main app bridge is enabled, in-app metadata can now also be tuned through the React control center. Operations owners can override title, description, category, tier, tags, always-available behavior, active status, and allowed-agent routing for the bridged tool catalog without editing the MCP server itself.

Recent MCP SDK features are wired through the registry now:

- Every tool publishes a human-friendly `title`.
- Safety metadata is exposed through MCP `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
- Tool `meta` includes source, category, safety, tier, mutating flag, tags, and structured-output hints.
- Server registrations use `structured_output=True`, so clients receive `outputSchema` plus structured results instead of only raw JSON text.
- The standalone MCP server and co-located in-app bridge share the same tool spec registry, eliminating metadata drift. Remote bridge mode consumes the same metadata over the MCP protocol.

## Quick Start

### 1. Install dependencies

```bash
pip install -r mcp_server/requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` (or set environment variables) with your AE connection details:

```env
AE_BASE_URL=https://your-ae-server:8443
AE_USERNAME=your-username
AE_PASSWORD=your-password
AE_ORG_CODE=your-org-code
```

### 3. Run the server

**stdio transport** (for Cursor / Claude Desktop):

```bash
python -m mcp_server
```

**Streamable HTTP transport** (localhost only by default):

```bash
python -m mcp_server --transport streamable-http --host 127.0.0.1 --port 8000
```

**Streamable HTTP transport** (reachable from other machines):

```bash
python -m mcp_server --transport streamable-http --host 0.0.0.0 --port 8000
```

The MCP endpoint URL for HTTP clients is `http://<host>:8000/mcp`.

## Main App Remote Bridge

If the AI Studio/Teams app is running on a different machine than the MCP server, configure the main app like this:

```env
AE_MCP_TOOLS_ENABLED=true
AE_MCP_SERVER_URL=http://mcp-host:8000/mcp
AE_MCP_SERVER_TRANSPORT=streamable-http
AE_MCP_SERVER_HEADERS_JSON=
AE_MCP_SERVER_TIMEOUT_SECONDS=30
```

Leave `AE_MCP_SERVER_URL` blank only when the app and `mcp_server` package are co-located and you want in-process execution instead of network MCP calls.

## Cursor Integration

Add to your `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "automationedge": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/AEAgenticSupport",
      "env": {
        "AE_BASE_URL": "https://your-ae-server:8443",
        "AE_USERNAME": "your-username",
        "AE_PASSWORD": "your-password",
        "AE_ORG_CODE": "your-org-code"
      }
    }
  }
}
```

## Claude Desktop Integration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "automationedge": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/AEAgenticSupport"
    }
  }
}
```

## Tool Catalog (106 tools: P0 + support-priority P1)

### Request Read (14 P0 + 3 P1)
| Tool | Description |
|------|-------------|
| `ae.request.get_by_id` | Fetch full request record |
| `ae.request.get_status` | Current status with timestamps |
| `ae.request.get_summary` | One-shot triage summary |
| `ae.request.search` | Search by workflow, user, status, agent, time |
| `ae.request.list_for_user` | Requests submitted by a user |
| `ae.request.list_for_workflow` | Requests for a workflow |
| `ae.request.list_by_status` | Requests in a specific status |
| `ae.request.list_stuck` | Detect stuck running requests |
| `ae.request.list_failed_recently` | Recent failures |
| `ae.request.list_retrying` | Requests in Retry |
| `ae.request.list_awaiting_input` | Blocked for human input |
| `ae.request.get_input_parameters` | Runtime input params |
| `ae.request.get_failure_message` | Error message extraction |
| `ae.request.build_support_snapshot` | Structured triage payload |
| *P1:* `ae.request.list_recent` | List recent requests for support review |
| *P1:* `ae.request.get_source_context` | Trigger source: schedule, catalog, API |
| *P1:* `ae.request.get_time_details` | Timing: created, picked, completed, duration |

### Request Diagnostics (6 P0 + 4 P1)
| Tool | Description |
|------|-------------|
| `ae.request.get_execution_details` | Full execution metadata |
| `ae.request.get_audit_logs` | Audit trail |
| `ae.request.get_step_logs` | Step-level logs |
| `ae.request.get_live_progress` | Live running state |
| `ae.request.get_last_error_step` | Failing step RCA |
| `ae.request.get_manual_intervention_context` | HITL block details |
| *P1:* `ae.request.get_last_successful_step` | Last successful step for resume planning |
| *P1:* `ae.request.compare_attempts` | Compare multiple attempts for same request |
| *P1:* `ae.request.export_diagnostic_bundle` | Export case evidence for escalation |
| *P1:* `ae.request.generate_support_narrative` | Plain-language support summary for handoff |

### Request Mutations (4 P0 + 4 P1) — Guarded/Privileged
| Tool | Safety | Description |
|------|--------|-------------|
| `ae.request.restart_failed` | guarded | Restart with same params |
| `ae.request.terminate_running` | privileged | Kill active request |
| `ae.request.resubmit_from_failure_point` | guarded | Resume from failure |
| `ae.request.add_support_comment` | safe_mutation | Add case note |
| *P1:* `ae.request.cancel_new_or_retry` | guarded | Cancel request not yet started |
| *P1:* `ae.request.resubmit_from_start` | guarded | Resubmit from beginning |
| *P1:* `ae.request.tag_case_reference` | safe_mutation | Link request to support case |
| *P1:* `ae.request.raise_manual_handoff` | safe_mutation | Mark for human handling |

### Workflow Read (7 P0 + 3 P1)
| Tool | Description |
|------|-------------|
| `ae.workflow.search` | Search workflows |
| `ae.workflow.list_for_user` | User-visible workflows |
| `ae.workflow.get_details` | Full workflow config |
| `ae.workflow.get_runtime_parameters` | Input parameter schema |
| `ae.workflow.get_flags` | Monitoring/retry/logging flags |
| `ae.workflow.get_assignment_targets` | Assigned agents/controllers |
| `ae.workflow.get_permissions` | Permission configuration |
| *P1:* `ae.workflow.get_by_id` | Fetch workflow by ID for support context |
| *P1:* `ae.workflow.get_recent_failure_stats` | Recent failure counts for workflow |
| *P1:* `ae.workflow.enable` | [GUARDED] Re-enable disabled workflow |

### Workflow Mutations (4 tools)
| Tool | Safety | Description |
|------|--------|-------------|
| `ae.workflow.disable` | guarded | Disable workflow |
| `ae.workflow.assign_to_agent` | guarded | Assign to agent |
| `ae.workflow.update_permissions` | privileged | Update ACL |
| `ae.workflow.rollback_version` | privileged | Revert version |

### Agent Read (9 P0 + 4 P1)
| Tool | Description |
|------|-------------|
| `ae.agent.list_stopped` | Stopped agents |
| `ae.agent.list_unknown` | Unknown-state agents |
| `ae.agent.get_status` | Agent health |
| `ae.agent.get_details` | Full agent details |
| `ae.agent.get_current_load` | Workload summary |
| `ae.agent.get_running_requests` | Active requests on agent |
| `ae.agent.get_assigned_workflows` | Assigned workflows |
| `ae.agent.get_connectivity_state` | Controller/platform connectivity |
| `ae.agent.get_rdp_session_state` | RDP session state |
| *P1:* `ae.agent.list_running` | Agents currently running and load |
| *P1:* `ae.agent.get_recent_failures` | Recent request failures for agent |
| *P1:* `ae.agent.get_last_heartbeat` | Last heartbeat and connectivity |
| *P1:* `ae.agent.collect_diagnostics` | Collect agent diagnostics for support |

### Agent Mutations (2 tools)
| Tool | Safety | Description |
|------|--------|-------------|
| `ae.agent.restart_service` | privileged | Restart agent service |
| `ae.agent.clear_stale_rdp_session` | privileged | Clear stuck RDP |

### Schedule Read (5 P0 + 1 diagnose + 4 P1)
| Tool | Description |
|------|-------------|
| `ae.schedule.list_for_workflow` | Linked schedules |
| `ae.schedule.get_details` | Full schedule config |
| `ae.schedule.get_missed_runs` | Missed run detection |
| `ae.schedule.get_recent_schedule_generated_requests` | Schedule-to-request map |
| `ae.schedule.diagnose_not_triggered` | One-shot trigger diagnosis |
| *P1:* `ae.schedule.get_next_runs` | Upcoming scheduled run times |
| *P1:* `ae.schedule.get_last_runs` | Last run times and outcomes |
| *P1:* `ae.schedule.enable` | [GUARDED] Re-enable disabled schedule |
| *P1:* `ae.schedule.run_now` | [GUARDED] Trigger schedule run immediately |
| `ae.schedule.disable` | [GUARDED] Disable schedule |

### Task Read (2 P0 + 6 P1)
| Tool | Description |
|------|-------------|
| `ae.task.get_request_context` | Task-to-request mapping |
| `ae.task.list_blocking_requests` | Awaiting Input queue |
| *P1:* `ae.task.search_pending` | Search pending tasks by workflow/limit |
| *P1:* `ae.task.get_assignees` | Users who can be assigned to a task |
| *P1:* `ae.task.get_overdue` | List overdue tasks |
| *P1:* `ae.task.cancel_admin` | [GUARDED] Cancel a task (admin) |
| *P1:* `ae.task.reassign` | [GUARDED] Reassign task to another user |
| *P1:* `ae.task.explain_awaiting_input` | Why request is awaiting input |

### Credential Pool (3 P0 + 1 P1)
| Tool | Description |
|------|-------------|
| `ae.credential_pool.get_availability` | Available vs in-use |
| `ae.credential_pool.get_waiting_requests` | Waiting request queue |
| `ae.credential_pool.diagnose_retry_state` | Retry root cause |
| *P1:* `ae.credential_pool.validate_for_workflow` | Validate pool for workflow |

### Dependency (3 P1 tools)
| Tool | Description |
|------|-------------|
| `ae.dependency.check_input_file_exists` | Check input file/path exists for workflow run |
| `ae.dependency.check_output_folder_writable` | Check output folder writable for workflow |
| `ae.dependency.run_full_preflight_for_workflow` | Full preflight: files, creds, agent |

### User, Permission, Platform, Result (6 tools)
| Tool | Description |
|------|-------------|
| `ae.user.get_accessible_workflows` | User's workflow access |
| `ae.permission.get_workflow_permissions` | Workflow ACL |
| `ae.permission.explain_user_access_issue` | Access diagnosis |
| `ae.platform.get_license_status` | License state |
| `ae.platform.get_queue_depth` | Queue depth |
| `ae.result.get_failure_category` | Failure classification |

### Support Composite (8 P0 + 3 P1)
| Tool | Description |
|------|-------------|
| `ae.support.diagnose_failed_request` | Failed request RCA |
| `ae.support.diagnose_stuck_running_request` | Hung request diagnosis |
| `ae.support.diagnose_retry_due_to_credentials` | Credential pool diagnosis |
| `ae.support.diagnose_no_output_file` | Missing output diagnosis |
| `ae.support.diagnose_schedule_not_triggered` | Schedule trigger diagnosis |
| `ae.support.diagnose_user_cannot_find_workflow` | Access/visibility diagnosis |
| `ae.support.diagnose_awaiting_input` | Awaiting Input diagnosis |
| `ae.support.diagnose_agent_unavailable` | Agent unavailability diagnosis |
| *P1:* `ae.support.diagnose_rdp_blocked_workflow` | Workflow blocked by RDP/session on agent |
| *P1:* `ae.support.build_case_snapshot` | Full case snapshot for escalation |
| *P1:* `ae.support.prepare_human_handoff_note` | Human-readable handoff note for support |

## Safety Levels

| Level | Description |
|-------|-------------|
| `safe_read` | Read-only, no state change |
| `safe_mutation` | Low-risk change, usually reversible |
| `guarded` | State change requiring reason, supports dry_run |
| `privileged` | Potentially disruptive, requires reason + dry_run |

All mutating tools accept `reason`, `requested_by`, `case_id`, and `dry_run` parameters.

## Architecture

```
mcp_server/
├── __init__.py          # Package marker
├── __main__.py          # CLI entry point
├── server.py            # FastMCP server wiring shared, structured MCP tool specs
├── config.py            # Environment-based configuration
├── ae_client.py         # Standalone AE REST client with auth + fallback + path caching
├── tool_specs.py        # Shared MCP tool definitions, curated metadata, and schema helpers
├── requirements.txt     # Python dependencies
└── tools/
    ├── __init__.py
    ├── request_read.py      # 14 P0 + 3 P1 request read tools
    ├── request_diag.py      # 6 P0 + 4 P1 request diagnostic tools
    ├── request_mutate.py    # 4 P0 + 4 P1 request mutation tools
    ├── workflow_tools.py    # 7 read + 3 P1 + 4 mutate workflow tools
    ├── agent_tools.py       # 9 read + 4 P1 + 2 mutate agent tools
    ├── schedule_tools.py    # 5 read + 1 diagnose + 4 P1 + 1 mutate schedule tools
    ├── task_tools.py       # 2 P0 + 6 P1 task tools
    ├── credential_tools.py # 3 P0 + 1 P1 credential pool tools
    ├── dependency_tools.py  # 3 P1 dependency/preflight tools
    ├── misc_tools.py       # user, permission, platform, result tools
    └── support_composite.py # 8 P0 + 3 P1 one-shot diagnostic tools
```
