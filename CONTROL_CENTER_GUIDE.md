# Operations Control Center Guide

This guide explains the React-based admin workspace and the public documentation library added to the application.

## What it is

The control center is the main admin and business-operations workspace exposed at:

- `/admin`
- `/tools` (same React UI)

It replaces most of the older hardcoded HTML/JavaScript admin behavior with persisted settings and task catalogs that can be changed from the browser.

## Main areas

### Overview

Use this page to understand what is live right now:

- platform health
- approval queue status
- scheduler status
- recent tool activity
- metrics snapshot

### Application Settings

This area is designed for business-friendly configuration language instead of raw environment variables.

Current sections:

- `User Experience`
  - assistant name
  - welcome messages
  - quick actions
  - documentation page title and subtitle
- `Operations Rules`
  - issue-classification phrases
  - protected workflows
  - investigation limits
- `Approvals And Access`
  - role and tier matrices
  - approval-required tiers
- `Monitoring And Automation`
  - scheduler defaults
  - daily summary hour
  - monitored workflows
- `Integrations`
  - non-secret AE, tool gateway, and AI service connection settings

### Agents

Use the Agents tab to manage catalog agents and routing metadata such as:

- capabilities
- domains
- priority
- version
- linked tools

### Tools

Use the Tools tab to apply business-owned overrides to the runtime tool catalog without editing Python source.

Supported override areas include:

- display title
- description
- category
- tier
- safety guidance
- use/avoid guidance
- tags
- active flag
- always-available flag
- allowed agents

These overrides are persisted and applied when tools are cataloged and hydrated.

### Knowledge

The Knowledge tab now manages two content surfaces:

1. SOP content used by the assistant during investigations.
2. Public reference documents shown in the documentation library at `/docs`.

Reference documents can be managed with:

- title
- badge
- audience
- summary
- file path
- display order
- visibility

### Activity

The Activity tab now covers:

- recent tool interactions
- pending approvals
- scheduler tasks
- scheduler execution log
- conversation history search
- case summary refresh
- human handoff marking
- export preview in markdown or JSON

This makes the admin workspace usable as an operational review console, not just a configuration page.

## Public documentation library

The documentation library is available at:

- `/docs`

It now reads:

- page title and subtitle from the workspace configuration
- document catalog entries from the docs catalog store
- document content through server routes instead of hardcoded page constants

This means support leads can add or hide documents from the admin UI instead of editing `index.html`.

## Persisted stores

The control center uses JSON-backed stores in `state/`:

- `state/app_config.py`
- `state/tool_overrides.py`
- `state/scheduler_store.py`
- `state/docs_catalog.py`
- `state/agent_catalog.py`

Default file paths come from `config/settings.py`:

- `APP_CONTROL_CENTER_PATH`
- `TOOL_OVERRIDE_PATH`
- `SCHEDULER_CATALOG_PATH`
- `DOCS_CATALOG_PATH`
- `AGENT_CATALOG_PATH`

## API surfaces

Important admin APIs exposed by `agent_server.py`:

- `/api/admin/bootstrap`
- `/api/admin/config/<section>`
- `/api/tools/<tool_name>/config`
- `/api/scheduler/tasks`
- `/api/docs/catalog`
- `/api/history/conversations`
- `/api/history/export/<conversation_id>`
- `/api/history/summary/<conversation_id>`
- `/api/history/handoff/<conversation_id>`

## Security boundary

The UI is intended to manage configuration, wording, and operational metadata. It is not intended to expose raw infrastructure secrets in the browser.

Secrets should remain in environment variables or secret storage, including:

- `COGNIBOT_DIRECTLINE_SECRET`
- database credentials
- cloud credentials
- AE API keys and passwords

The UI should manage non-secret connection settings, masked references, and validation flows, while the server continues to own secret usage.

## Recommended operator workflow

1. Use `Application Settings` for wording, thresholds, and guardrails.
2. Use `Tools` to tune catalog visibility and agent ownership.
3. Use `Knowledge` to keep SOPs and public guides current.
4. Use `Activity` to monitor live operations, approvals, and conversation history.
5. Use `/docs` as the support-facing or business-facing knowledge library once the catalog is populated.
