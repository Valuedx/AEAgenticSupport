> - **V0.9.2 UX Improvements (2026-03-21)**: Execution log UX — `JsonBlock` component adds Copy button (clipboard + 2s checkmark) and Expand button (opens `FullJsonDialog` with full scrollable JSON) to every input/output block in `ExecutionPanel`; "polling…" label corrected to "streaming…". Palette search — filter input in `NodePalette` hides non-matching categories, auto-expands matching ones, shows `n/total` count per category, clears with ✕ button. Validation highlighting on node cards — `useNodeValidation` hook runs `validateWorkflow()` reactively on every canvas change; `AgenticNode` applies red ring + `AlertCircle` icon for errors, yellow ring + `AlertTriangle` for warnings; selection ring always takes priority. MCP Tool node `toolName` field replaced with `ToolSingleSelect` — searchable list with tool title, description, safety tier badge, and clear button; live from `/api/v1/tools`. Expression variable picker (`src/lib/expressionVariables.ts`, `ExpressionInput.tsx`) — autocomplete dropdown on condition, *Expression, *NodeId, and systemPrompt fields; three modes (expression / nodeId / jinja2); cursor-aware token detection; keyboard navigation; fixed-position portal dropdown. Pre-run workflow validation (`src/lib/validateWorkflow.ts`) — checks for missing trigger, disconnected nodes, required empty fields (condition, url, toolName, arrayExpression, responseNodeId), and broken node-ID cross-references (responseNodeId, historyNodeId). `ValidationDialog` surfaces errors and warnings before execution; hard errors block run, warnings allow "Run Anyway". `Toolbar.tsx` now calls `validateWorkflow()` on every Run click. Undo/Redo — `flowStore.ts` gains `past[]`/`future[]` snapshot arrays (max 50) with `_pushHistory()` called before every destructive canvas action; `FlowCanvas.tsx` registers Ctrl+Z/Ctrl+Y/Ctrl+Shift+Z global keyboard handlers; Toolbar shows Undo/Redo buttons with disabled state when history is empty. Node ID chip — `PropertyInspector.tsx` now shows the node's machine ID (e.g., `node_3`) in a monospace chip at the top of the panel with a one-click copy button (2s checkmark confirmation), so users can easily reference nodes in expressions like `node_3.intent`. Inline field help text — every `config_schema` property in `node_registry.json` now carries a `description` string; `DynamicConfigForm.tsx` renders these as `text-[10px] text-muted-foreground` subtext below each field via a `FieldHint` helper, covering all nine renderer branches (enum, array, object, boolean, number, ToolMultiSelect, ToolSingleSelect, ExpressionInput, plain input). ForEach/Merge canvas clarity — `AgenticNode` now renders a `waitAll`/`waitAny` strategy badge for Merge nodes (same slot as the agent model badge) and a `↻ arrayExpression` monospace line below the badge row for ForEach nodes when the expression is set, so both nodes are interpretable without opening the properties panel.
>
> - **V0.9.1 Stateful DAGs (2026-03-21)**: Added robust Stateful Re-Trigger DAG Pattern. Added `ConversationSession` PostgreSQL table with Alembic migration `0003_conversation_sessions.py` + unique index `(tenant_id, session_id)`. Added REST APIs in `conversations.py` (`GET /api/v1/conversations`, `GET /{id}`, `DELETE /{id}`). Exposes 3 new conversational memory nodes in `node_registry.json`: `Load Conversation State`, `Save Conversation State`, and `LLM Router`.
> - **V0.9 Execution Enhancements (2026-03-21)**: ForEach loop node (`_handle_forEach`, `_run_forEach_iterations`) — iterates downstream subgraph per array element. Retry from failed node (`retry_graph()`, `POST /{id}/instances/{iid}/retry`). MCP connection pooling (`_MCPSessionPool`). Enhanced safe expression evaluator with whitelisted function/method calls (`len`, `lower`, `matches`, etc.). Snapshot pruning via Celery Beat (`prune_old_snapshots`, `ORCHESTRATOR_MAX_SNAPSHOTS`). Environment variable mapping (`{{ env.SECRET_NAME }}` resolved from vault). Langfuse parallel context fix — explicit trace propagation into threads. Frontend `retryInstance` action.
> - **V0.8 Enterprise Features (2026-03-20)**: Dynamic property forms generated from `shared/node_registry.json` schemas (`DynamicConfigForm.tsx`) — PropertyInspector no longer hardcoded. ReAct agent auto-discovers all MCP tools when `tools` config is empty; MCP tool cache upgraded to 5-minute TTL with `POST /api/v1/tools/invalidate-cache`. Workflow versioning: `workflow_snapshots` table + Alembic migration 0002; snapshot saved before each overwrite; `GET /{id}/versions` and `POST /{id}/rollback/{v}` endpoints; `VersionHistoryDialog` with Restore button in Toolbar. OIDC federation: Authorization Code + PKCE flow (`app/api/auth.py`), `authlib` for ID token validation, Redis PKCE state, issues internal JWT; frontend `LoginPage` + `VITE_AUTH_MODE=oidc` gate in `App.tsx`.

## AE AI Hub — Agentic Orchestrator Technical Blueprint

**Version:** 0.9.1
**Last updated:** 2026-03-21
**Status:** V0.9.1 Stateful DAGs, V0.9 execution enhancements, V0.8 enterprise features, V0.7 Langfuse + MCP streaming, V0.6 advanced agents, V0.5 hardening, V0.4 branching, V0.3 LLM, V0.2 wired, V0.1 scaffold
> - **V0.7 Observability, MCP Streaming & Tenant Tools (2026-03-20)**: Langfuse v4 integration (`app/observability.py`) — root trace per workflow execution, child spans per node, LLM generation recording with token usage, tool call spans. MCP client rewritten to use MCP Python SDK with Streamable HTTP transport (`app/engine/mcp_client.py`) — replaces raw httpx REST bridge with standard MCP protocol. Tool listing and ReAct tool definitions now fetched live from MCP server. TenantToolOverride consumed by tools endpoint to filter MCP tools per tenant.
>
> - **V0.6 Advanced Agent Capabilities (2026-03-20)**: ReAct iterative tool-calling loop (`app/engine/react_loop.py`) with multi-provider support (Google/OpenAI/Anthropic tool-calling APIs). SSE real-time execution updates (`app/api/sse.py`) replacing frontend polling. Celery Beat cron scheduler (`app/workers/scheduler.py`) for schedule triggers with croniter. Frontend palette now hydrated from `shared/node_registry.json` via `src/lib/registry.ts`. Backend config validation against registry schemas on save (`app/engine/config_validator.py`).
>
> - **V0.5 Production Hardening (2026-03-20)**: JWT-based auth with tenant claims (`app/security/jwt_auth.py`, dev-mode header fallback). Fernet-encrypted credential vault (`app/security/vault.py` + `TenantSecret` model). AST-based safe expression evaluator replaces `eval()` (`app/engine/safe_eval.py`). PostgreSQL RLS migration for tenant isolation (`alembic/versions/0001`). Per-tenant rate limiting via slowapi and execution quotas (`app/security/rate_limiter.py`). See §8 for updated security docs.
>
> - **V0.4 Branching & Parallel Execution (2026-03-20)**: Rewrote `dag_runner.py` with a ready-queue execution model. Condition nodes now prune non-matching branches (only `true` or `false` edges are followed). Independent branches execute in parallel via `ThreadPoolExecutor`. Merge nodes naturally wait for all upstream branches. Frontend edges from condition nodes show colored labels (green "Yes" / red "No") with arrow markers. See §6 for updated DAG engine docs.
>
> - **V0.3 Live LLM Integration (2026-03-20)**: Agent nodes now call real LLM providers (Google Gemini via `google-genai`, OpenAI, Anthropic). Added `app/engine/llm_providers.py` multi-provider abstraction, `app/engine/prompt_template.py` Jinja2 system-prompt templating with context variable injection (dot-accessible upstream outputs), and token usage tracking in execution logs. New config keys: `ORCHESTRATOR_GOOGLE_API_KEY`, `ORCHESTRATOR_OPENAI_API_KEY`, `ORCHESTRATOR_ANTHROPIC_API_KEY`. See `SETUP_GUIDE.md` §7 for configuration.
>
> - **V0.2 UI Wiring (2026-03-20)**: Added frontend API client + workflow toolbar (save/load/execute), a saved-workflow list dialog, and an execution log panel with polling against backend instance status. See `orchestrator/HOW_IT_WORKS.md` for runtime walkthrough.
> - **Initial Scaffold (2026-03-20)**: V0.1 — React Flow visual builder (frontend), FastAPI DAG execution engine (backend), Zustand state management, shadcn/ui component library, SQLAlchemy data models with multi-tenant isolation, Celery worker stubs, and MCP tool bridge. See `SETUP_GUIDE.md` for installation and `HOW_IT_WORKS.md` for runtime walkthrough.

---

### Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Architecture Overview](#2-architecture-overview)
3. [Frontend: Visual DAG Builder](#3-frontend-visual-dag-builder)
4. [Backend: Execution Engine](#4-backend-execution-engine)
5. [Data Models](#5-data-models)
6. [DAG Execution Engine](#6-dag-execution-engine)
7. [MCP Tool Bridge](#7-mcp-tool-bridge)
8. [Multi-Tenancy and Security](#8-multi-tenancy-and-security)
9. [Integration with AI Studio (Sidecar Pattern)](#9-integration-with-ai-studio-sidecar-pattern)
10. [Shared Schemas](#10-shared-schemas)
11. [Known Limitations (V0.8)](#12-known-limitations-v08)
12. [Roadmap](#12-roadmap)

---

## 1. Purpose and Scope

The AE AI Hub is an **add-on module** (sidecar) to AutomationEdge AI Studio. It provides a **no-code visual builder** for constructing agentic workflows as Directed Acyclic Graphs (DAGs), replacing the need for hardcoded Python pipelines.

This module does **not** modify any existing `AEAgenticSupport` code. It runs as an independent service pair (React frontend + FastAPI backend) that consumes the existing MCP server's 106 tools as a client.

**Relationship to parent project:**

| Concern | Parent (`AEAgenticSupport`) | Orchestrator (`orchestrator/`) |
|---------|---------------------------|-------------------------------|
| Use case | IT RCA via hardcoded pipeline | Generic enterprise workflows |
| Frontend | Vanilla JS admin + webchat | React Flow visual canvas |
| Backend | Flask + ThreadPoolExecutor | FastAPI + Celery |
| Tools | 106 tools via MCP server | Same tools, consumed via bridge |
| State | Django models (Case, Issue) | SQLAlchemy (WorkflowInstance, ExecutionLog) |
| LLM | Google Vertex AI (direct) | Multi-provider via Agent nodes |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Browser (port 8080)                          │
│  ┌──────────────┬───────────────────────┬───────────────────────┐   │
│  │ Node Palette │     React Flow        │  Property Inspector   │   │
│  │ (drag src)   │     Canvas            │  (config panel)       │   │
│  │              │  ┌─────┐   ┌─────┐    │                       │   │
│  │  Triggers    │  │Trig │──▶│Agent│──┐ │  LLM Provider: [v]    │   │
│  │  Agents      │  └─────┘   └─────┘  │ │  Model:        [v]    │   │
│  │  Actions     │         ┌─────┐     │ │  System Prompt: [  ]  │   │
│  │  Logic       │         │Action│◀───┘ │  Temperature:   [0.7] │   │
│  │              │         └─────┘       │                       │   │
│  └──────────────┴───────────────────────┴───────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST (JSON)
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  FastAPI Gateway (port 8001)                         │
│                                                                      │
│  POST /api/v1/workflows          — Save graph JSON                   │
│  POST /api/v1/workflows/{id}/execute  — Enqueue to Celery            │
│  POST /api/v1/workflows/{id}/callback — Resume suspended workflow    │
│  GET  /api/v1/workflows/{id}/status   — Execution logs               │
│  GET  /api/v1/tools                   — MCP palette hydration        │
└────────────────────┬─────────────────────────────────────────────────┘
                     │ Celery task
                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Celery Worker                                     │
│                                                                      │
│  1. Parse graph JSON → adjacency list                                │
│  2. Topological sort (Kahn's algorithm)                              │
│  3. Execute nodes sequentially:                                      │
│     Trigger → Agent (LLM) → Action (MCP tool) → Logic (branch)      │
│  4. Human Approval? → suspend, serialize, wait for callback          │
│  5. Store per-node ExecutionLog                                      │
└────────────────────┬─────────────────────────────────────────────────┘
                     │ httpx
                     ▼
┌─────────────────────────────────┐    ┌────────────────────────────┐
│  MCP Server (port 3000)         │    │  PostgreSQL                │
│  106 tools (existing)           │    │  workflow_definitions      │
│  /call-tool                     │    │  workflow_instances        │
└─────────────────────────────────┘    │  execution_logs            │
                                       │  tenant_tool_overrides     │
                                       └────────────────────────────┘
```

---

## 3. Frontend: Visual DAG Builder

**Stack:** React 19, TypeScript, Vite 8, `@xyflow/react` 12, Zustand 5, Tailwind CSS 4, shadcn/ui.

### 3.1 Directory Layout

```
orchestrator/frontend/src/
├── App.tsx                         # Root layout — three-panel flex
├── main.tsx                        # Entry point — StrictMode + CSS
├── index.css                       # Tailwind + shadcn theme tokens
├── store/
│   └── flowStore.ts                # Zustand: nodes, edges, selection, CRUD
├── types/
│   └── nodes.ts                    # NodeCategory, AgenticNodeData, NODE_PALETTE
├── components/
│   ├── canvas/
│   │   └── FlowCanvas.tsx          # ReactFlow wrapper + drop handler
│   ├── nodes/
│   │   └── AgenticNode.tsx         # Polymorphic custom node
│   ├── sidebar/
│   │   ├── NodePalette.tsx         # Left: draggable node categories
│   │   └── PropertyInspector.tsx   # Right: node config forms
│   └── ui/                         # shadcn components (11 total)
└── lib/
    └── utils.ts                    # cn() utility
```

### 3.2 State Management (Zustand)

File: `store/flowStore.ts`

The single Zustand store manages all canvas state:

| State | Type | Purpose |
|-------|------|---------|
| `nodes` | `Node[]` | React Flow node objects |
| `edges` | `Edge[]` | React Flow edge connections |
| `selectedNodeId` | `string \| null` | Currently selected node for inspector |
| `past` | `Snapshot[]` | Undo history stack (max 50 entries) |
| `future` | `Snapshot[]` | Redo history stack (max 50 entries) |
| `_draggingNodeIds` | `Set<string>` | Tracks in-flight drag operations to avoid duplicate snapshots |

| Action | Signature | Description |
|--------|-----------|-------------|
| `onNodesChange` | `OnNodesChange` | React Flow node change handler; snapshots before drag-start and remove |
| `onEdgesChange` | `OnEdgesChange` | React Flow edge change handler; snapshots before remove |
| `onConnect` | `OnConnect` | New edge creation; always snapshots before connecting |
| `addNode` | `(category, label, position, config?) → void` | Create node at canvas position; snapshots before creation |
| `selectNode` | `(id \| null) → void` | Set selection for property inspector |
| `updateNodeData` | `(id, partial) → void` | Merge data updates from inspector forms |
| `deleteNode` | `(id) → void` | Remove node and connected edges; snapshots before deletion |
| `undo` | `() → void` | Restore previous canvas state from `past` stack |
| `redo` | `() → void` | Replay next state from `future` stack |
| `_pushHistory` | `() → void` | Internal: push current `{nodes, edges}` snapshot to `past`, clear `future` |

### 3.3 Node Categories and Palette

File: `types/nodes.ts`

Four categories, each with distinct visual styling:

| Category | Color | Border | Nodes |
|----------|-------|--------|-------|
| **Trigger** | Amber | `border-amber-500/60` | Webhook Trigger, Schedule Trigger |
| **Agent** | Violet | `border-violet-500/60` | LLM Agent, ReAct Agent |
| **Action** | Sky | `border-sky-500/60` | MCP Tool, HTTP Request, Human Approval |
| **Logic** | Emerald | `border-emerald-500/60` | Condition, Merge |

The `NODE_PALETTE` array defines every draggable item with its `nodeCategory`, `label`, `description`, `icon`, and `defaultConfig`. A **search input** at the top of the palette filters by label and description: non-matching categories are hidden, matching categories auto-expand, and each category header shows a `matched/total` count while a query is active.

### 3.4 Custom Node Component

File: `components/nodes/AgenticNode.tsx`

A single `memo`-ized component renders all node types polymorphically:

- **Visual:** shadcn `Card` with category-colored border, icon badge, label, category pill, model badge (agents), strategy badge (Merge), `↻ arrayExpression` line (ForEach when set), and a status/validation indicator.
- **Handles:** Target (left) on all except Triggers. Source (right) on all except Merge. Condition nodes get two source handles (`true` in green, `false` in red) at 35%/65% vertical offset.
- **Icons:** Mapped via `ICON_MAP` from Lucide icon names stored in `config.icon`.
- **Validation indicators** (design-time, via `useNodeValidation` hook):
  - `red ring + AlertCircle` — node has a hard configuration error (empty required field, broken node ID ref)
  - `yellow ring + AlertTriangle` — node is disconnected from all triggers (warning)
  - `blue ring` — node is selected (always takes priority over validation rings)
  - `status dot` — runtime execution status (shown when no validation issue)

### 3.5 Pre-Run Workflow Validation

Files: `src/lib/validateWorkflow.ts`, `src/components/toolbar/ValidationDialog.tsx`

Before any execution begins, `validateWorkflow(nodes, edges)` is called by the Toolbar's Run handler. It returns an array of `ValidationError` objects, each with:

| Field | Type | Description |
|-------|------|-------------|
| `nodeId` | `string` | ID of the offending node (empty for graph-level errors) |
| `nodeLabel` | `string` | Human-readable node name |
| `message` | `string` | Description of the problem |
| `severity` | `"error" \| "warning"` | Errors block execution; warnings allow "Run Anyway" |

**Checks performed (in order):**

1. **No trigger** — workflow must have at least one Trigger category node
2. **Reachability (BFS)** — every node must be reachable from a trigger via edges; orphaned nodes produce a warning
3. **Required fields** — per node label, specific fields must be non-empty:
   - `Condition` → `condition`
   - `HTTP Request` → `url`
   - `MCP Tool` → `toolName`
   - `ForEach` → `arrayExpression`
   - `Save Conversation State` → `responseNodeId`
   - `LLM Router` → `intents` array must have ≥ 1 entry
4. **Node ID cross-references** — `responseNodeId` (Save Conversation State) and `historyNodeId` (LLM Router), when set, must match an existing node ID

`ValidationDialog` presents errors in red and warnings in yellow. If only warnings exist, a **Run Anyway** button is offered. Hard errors disable execution entirely until fixed.

### 3.6 Expression Variable Picker

Files: `src/lib/expressionVariables.ts`, `src/components/sidebar/ExpressionInput.tsx`

Fields that accept runtime expressions get an autocomplete dropdown instead of a plain text input. The dropdown is positioned with `position: fixed` (portal to `document.body`) so it is never clipped by the sidebar's `ScrollArea`.

**Three rendering modes** selected by `DynamicConfigForm` per field key:

| Mode | Format | Fields |
|------|--------|--------|
| `expression` | `node_2.intent` | `condition`, `arrayExpression`, `sessionIdExpression`, `userMessageExpression` |
| `nodeId` | `node_3` | `responseNodeId`, `historyNodeId` |
| `jinja2` | `{{ node_2.response }}` | `systemPrompt` |

**Known output fields per node type** (defined in `expressionVariables.ts`):

| Node | Suggested outputs |
|------|-------------------|
| Webhook Trigger | `trigger.body`, `trigger.message`, `trigger.session_id`, `trigger.headers`, `trigger.method`, `trigger.path` |
| Schedule Trigger | `trigger.scheduled_at`, `trigger.cron` |
| LLM Agent | `response`, `input_tokens`, `output_tokens` |
| ReAct Agent | `response`, `tool_calls`, `iterations` |
| LLM Router | `intent` |
| MCP Tool | `result` |
| HTTP Request | `status_code`, `body`, `headers` |
| Human Approval | `approved`, `approver` |
| Load Conversation State | `history`, `session_id` |

**Token detection:** `getCurrentToken()` walks backward from the cursor to the last word boundary (`space`, `(`, `=`, `!`, `<`, `>`, `,`, `"`) and uses that substring as the filter. `insertAtCursor()` replaces only the current token, preserving the rest of the expression.

**Keyboard shortcuts:** ArrowUp/Down to navigate, Enter or Tab to insert, Escape to close.

### 3.7 Property Inspector

File: `components/sidebar/PropertyInspector.tsx`

Category-specific config panels:

| Category | Fields |
|----------|--------|
| **Agent** | Provider (Google/OpenAI/Anthropic), Model (6 options), System Prompt, Temperature |
| **Trigger** | Webhook Path *or* Cron Expression (based on default config) |
| **Action** | MCP Tool Name *or* URL+Method *or* Approval Message |
| **Logic** | Condition Expression *or* Merge Strategy (waitAll/waitAny) |

All fields write back to the store via `updateNodeData`. A "Delete Node" button removes the selected node.

**Node ID chip** — at the top of the panel (above the Label field) a `bg-muted` chip displays the node's machine ID (e.g., `node_3`) in a monospace font. A copy button (`Copy` icon → 2s `Check` icon) writes the ID to the clipboard so it can be pasted into expression fields on other nodes.

**Inline field help text** — `DynamicConfigForm` reads the optional `description` field from each `config_schema` entry and renders it as `<FieldHint>` (10px muted grey text) below the input. All nine renderer branches emit a hint when a description is present. All node type schemas in `shared/node_registry.json` have been populated with descriptions.

### 3.6 Drag-and-Drop Flow

1. `NodePalette` items set `onDragStart` → `dataTransfer.setData("application/reactflow", JSON.stringify({nodeCategory, label, defaultConfig}))`.
2. `FlowCanvas` handles `onDragOver` (preventDefault) and `onDrop`.
3. On drop, the canvas reads the transfer data, converts screen coordinates via `reactFlowInstance.screenToFlowPosition()`, and calls `flowStore.addNode()`.

---

## 4. Backend: Execution Engine

**Stack:** Python, FastAPI, SQLAlchemy 2, Alembic, Celery (Redis), httpx, Pydantic v2.

### 4.1 Directory Layout

```
orchestrator/backend/
├── main.py                         # FastAPI app, CORS, routers, health
├── alembic.ini                     # Migration config
├── requirements.txt                # Python dependencies
├── alembic/
│   ├── env.py                      # Migration environment
│   ├── script.py.mako              # Migration template
│   └── versions/                   # Migration files (empty — no DB yet)
└── app/
    ├── config.py                   # Pydantic Settings (env-driven)
    ├── database.py                 # SQLAlchemy engine, session, Base
    ├── observability.py            # Langfuse v4 traces, spans, generations
    ├── api/
    │   ├── schemas.py              # Pydantic request/response models
    │   ├── workflows.py            # CRUD + execute + callback + status
    │   ├── tools.py                # MCP tool bridge for palette
    │   └── sse.py                  # Server-Sent Events for real-time execution updates
    ├── engine/
    │   ├── dag_runner.py           # Ready-queue DAG executor with branching + parallelism
    │   ├── node_handlers.py        # Per-type dispatch (trigger/agent/action/logic)
    │   ├── llm_providers.py        # Multi-provider LLM abstraction (Google/OpenAI/Anthropic)
    │   ├── react_loop.py           # ReAct iterative tool-calling loop for agent nodes
    │   ├── mcp_client.py           # MCP SDK client (Streamable HTTP transport)
    │   ├── prompt_template.py      # Jinja2 system-prompt templating with context injection
    │   ├── safe_eval.py            # AST-based safe expression evaluator for conditions
    │   └── config_validator.py     # Validates node configs against node_registry.json
    ├── models/
    │   ├── workflow.py             # WorkflowDefinition, WorkflowInstance, ExecutionLog
    │   └── tenant.py              # TenantToolOverride
    ├── workers/
    │   ├── celery_app.py           # Celery configuration
    │   ├── tasks.py                # execute_workflow_task, resume_workflow_task
    │   └── scheduler.py            # Celery Beat cron scheduler for schedule triggers
    └── security/
        ├── tenant.py              # Re-exports get_tenant_id for backward compat
        ├── jwt_auth.py            # JWT creation + validation with tenant claim
        ├── vault.py               # Fernet-encrypted credential vault + TenantSecret model
        └── rate_limiter.py        # Per-tenant rate limiting + execution quotas
```

### 4.2 Configuration

File: `app/config.py`

| Setting | Env Variable | Default |
|---------|-------------|---------|
| `database_url` | `ORCHESTRATOR_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/ae_orchestrator` |
| `redis_url` | `ORCHESTRATOR_REDIS_URL` | `redis://localhost:6379/0` |
| `mcp_server_url` | `ORCHESTRATOR_MCP_SERVER_URL` | `http://localhost:3000` |
| `secret_key` | `ORCHESTRATOR_SECRET_KEY` | `change-me-in-production` |
| `cors_origins` | `ORCHESTRATOR_CORS_ORIGINS` | `["http://localhost:8080"]` |
| `google_api_key` | `ORCHESTRATOR_GOOGLE_API_KEY` | `""` |
| `google_project` | `ORCHESTRATOR_GOOGLE_PROJECT` | `""` |
| `google_location` | `ORCHESTRATOR_GOOGLE_LOCATION` | `us-central1` |
| `openai_api_key` | `ORCHESTRATOR_OPENAI_API_KEY` | `""` |
| `openai_base_url` | `ORCHESTRATOR_OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `anthropic_api_key` | `ORCHESTRATOR_ANTHROPIC_API_KEY` | `""` |
| `auth_mode` | `ORCHESTRATOR_AUTH_MODE` | `dev` |
| `vault_key` | `ORCHESTRATOR_VAULT_KEY` | `""` |
| `rate_limit_requests` | `ORCHESTRATOR_RATE_LIMIT_REQUESTS` | `100` |
| `rate_limit_window` | `ORCHESTRATOR_RATE_LIMIT_WINDOW` | `1 minute` |
| `execution_quota_per_hour` | `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` | `50` |

### 4.3 LLM Provider Abstraction

File: `app/engine/llm_providers.py`

The `call_llm()` function routes to one of three provider backends based on the
node's `config.provider` value:

| Provider | SDK | Default Model | Config Key |
|----------|-----|---------------|------------|
| `google` | `google-genai` | `gemini-2.5-flash` | `ORCHESTRATOR_GOOGLE_API_KEY` |
| `openai` | `openai` | `gpt-4o` | `ORCHESTRATOR_OPENAI_API_KEY` |
| `anthropic` | `anthropic` | `claude-sonnet-4-20250514` | `ORCHESTRATOR_ANTHROPIC_API_KEY` |

Each provider returns a standardized response:

```python
{
    "response": str,          # LLM text output
    "usage": {
        "input_tokens": int,  # tracked per node in ExecutionLog
        "output_tokens": int,
    },
    "model": str,
    "provider": str,
}
```

### 4.4 Jinja2 Prompt Templating

File: `app/engine/prompt_template.py`

System prompts support Jinja2 template syntax with upstream context injection.
All execution context keys are available as top-level template variables with
dot-access to nested fields:

```jinja2
You are an IT support assistant for {{ trigger.customer_name }}.
The user reported: {{ trigger.user_query }}
Ticket status from ServiceNow: {{ node_1.output.status }}
Recent logs: {{ node_2.body | truncate(500) }}
```

Missing variables resolve to empty strings instead of raising errors, allowing
prompts to be reusable across different workflow topologies.

### 4.5 API Endpoints

**Workflow CRUD** (prefix: `/api/v1/workflows`)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/` | 201 | Create a workflow definition |
| `GET` | `/` | 200 | List workflows for tenant |
| `GET` | `/{workflow_id}` | 200 | Get single workflow |
| `PATCH` | `/{workflow_id}` | 200 | Update name/description/graph (bumps version) |
| `DELETE` | `/{workflow_id}` | 204 | Delete workflow and cascade instances |
| `POST` | `/{workflow_id}/execute` | 202 | Create instance, enqueue to Celery |
| `POST` | `/{workflow_id}/callback` | 200 | Resume most recent suspended instance |
| `GET` | `/{workflow_id}/status` | 200 | List execution instances (limit 50) |
| `GET` | `/{workflow_id}/instances/{instance_id}` | 200 | Instance detail with execution logs |

**Tools** (prefix: `/api/v1/tools`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all MCP tools from parent `tool_specs.py` |

**Health**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok", "service": "ae-ai-hub-orchestrator"}` |

All workflow/tool endpoints require the `X-Tenant-Id` request header for tenant isolation.

---

## 5. Data Models

File: `app/models/workflow.py`, `app/models/tenant.py`

### 5.1 WorkflowDefinition

Stores the visual graph designed in the React Flow canvas.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `tenant_id` | `VARCHAR(64)` | Indexed. RLS discriminator |
| `name` | `VARCHAR(256)` | Workflow display name |
| `description` | `TEXT` | Optional |
| `graph_json` | `JSONB` | Full React Flow export `{nodes: [], edges: []}` |
| `version` | `INTEGER` | Bumped on each graph update |
| `created_at` | `TIMESTAMPTZ` | Auto |
| `updated_at` | `TIMESTAMPTZ` | Auto on update |

Index: `(tenant_id, name)`.

### 5.2 WorkflowInstance

One row per execution run of a workflow definition.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `tenant_id` | `VARCHAR(64)` | Indexed |
| `workflow_def_id` | `UUID` (FK) | References `workflow_definitions.id` |
| `status` | `VARCHAR(32)` | `queued` → `running` → `completed` / `failed` / `suspended` |
| `trigger_payload` | `JSONB` | Input data from webhook/schedule |
| `context_json` | `JSONB` | Accumulated node outputs during execution |
| `current_node_id` | `VARCHAR(128)` | Last node executed (for resume) |
| `started_at` | `TIMESTAMPTZ` | Set when worker picks up |
| `completed_at` | `TIMESTAMPTZ` | Set on completion/failure |
| `created_at` | `TIMESTAMPTZ` | Auto |

Index: `(tenant_id, status)`.

### 5.3 ExecutionLog

Per-node execution trace within a workflow instance.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `instance_id` | `UUID` (FK) | References `workflow_instances.id` |
| `node_id` | `VARCHAR(128)` | React Flow node ID (e.g. `node_3`) |
| `node_type` | `VARCHAR(64)` | Format: `category:label` (e.g. `agent:LLM Agent`) |
| `status` | `VARCHAR(32)` | `pending` → `running` → `completed` / `failed` / `suspended` |
| `input_json` | `JSONB` | Node input (config + upstream outputs) |
| `output_json` | `JSONB` | Node return value |
| `error` | `TEXT` | Error message on failure |
| `started_at` | `TIMESTAMPTZ` | |
| `completed_at` | `TIMESTAMPTZ` | |

### 5.4 ConversationSession

Persistent multi-turn conversation history for the Stateful Re-Trigger Pattern.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `session_id` | `VARCHAR(256)` | Unique conversational thread ID |
| `tenant_id` | `VARCHAR(64)` | Indexed |
| `messages` | `JSONB` | Array of `{"role": "user"|"assistant", "content": "...", "timestamp": "..."}` |
| `created_at` | `TIMESTAMPTZ` | Auto |
| `updated_at` | `TIMESTAMPTZ` | Auto on update |

Index: `(tenant_id, session_id)` (Unique).

### 5.5 TenantToolOverride

Per-tenant MCP tool visibility and configuration overrides.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `tenant_id` | `VARCHAR(64)` | |
| `tool_name` | `VARCHAR(256)` | MCP tool name |
| `enabled` | `BOOLEAN` | Show/hide tool in palette for this tenant |
| `config_json` | `JSONB` | Tenant-specific parameter defaults |

Unique index: `(tenant_id, tool_name)`.

---

## 6. DAG Execution Engine

File: `app/engine/dag_runner.py`

### 6.1 Graph Parsing (Handle-Aware)

`parse_graph(graph_json)` converts React Flow JSON into two structures:

- `nodes_map`: `{node_id: node_dict}` — the full node object including `data`.
- `edges`: list of `_Edge(source, target, source_handle)` — preserves `sourceHandle` from React Flow edges (e.g. `"true"` / `"false"` for condition outputs).

`_build_graph_structures()` derives forward adjacency, reverse adjacency, and in-degree maps from the parsed edges.

### 6.2 Cycle Detection

`_detect_cycles()` runs Kahn's algorithm over the graph to verify it is a valid DAG before execution begins.

### 6.3 Ready-Queue Execution Model

Instead of a simple linear topological order, the engine uses a **ready-queue** model that naturally supports branching and parallelism:

```
┌───────────────────────────────────────────────────────────────┐
│  1. Find all nodes with in_degree == 0 → initial ready set   │
│  2. While ready set is non-empty:                             │
│     a. If 1 ready node  → execute sequentially                │
│     b. If N ready nodes → execute in parallel (ThreadPool)    │
│     c. After each node completes:                             │
│        - If CONDITION node → propagate only matching branch   │
│          edges; prune the non-matching subtree                │
│        - Otherwise → propagate all outgoing edges             │
│     d. Recompute ready set (nodes with all incoming edges     │
│        satisfied, not pruned, not yet executed)                │
│  3. Mark instance completed/failed                            │
└───────────────────────────────────────────────────────────────┘
```

### 6.4 Branch Pruning

When a Condition node evaluates to `{"branch": "true"}`:

1. Edges with `sourceHandle == "true"` → satisfied (downstream nodes may become ready).
2. Edges with `sourceHandle == "false"` → target and its entire subtree are **pruned** (never executed).
3. Pruned nodes are excluded from the ready-set and the completion check.

This ensures only the chosen branch executes, matching the visual flow on the canvas.

### 6.5 Parallel Execution

When multiple nodes are ready simultaneously (e.g. two branches after a fan-out):

- A `ThreadPoolExecutor` (max 8 workers) runs their handlers concurrently.
- Each node writes to a unique key in the shared context dict (`context[node_id]`), so no locking is needed.
- `ExecutionLog` entries are created before dispatch and updated after all futures complete.
- If any parallel node fails or suspends, the engine stops after the current batch.

### 6.6 Merge / Wait-All

Merge nodes have multiple incoming edges. Under the ready-queue model, a merge node only becomes ready when **all** non-pruned upstream edges are satisfied — implementing wait-all semantics naturally without special-case code.

### 6.7 Resume Flow

`resume_graph(db, instance_id, approval_payload)`:

1. Load suspended instance, inject `approval_payload` into context under key `"approval"`.
2. Re-parse the graph and mark already-executed nodes (from context keys) as skipped.
3. Re-run `_execute_ready_queue()`, which finds the next ready nodes and continues.

### 6.8 Node Handlers

File: `app/engine/node_handlers.py`

`dispatch_node()` routes by `nodeCategory`:

| Category | Handler | Behavior |
|----------|---------|----------|
| `trigger` | `_handle_trigger` | Pass through `context["trigger"]` |
| `agent` | `_handle_agent` | Render Jinja2 prompt, call LLM provider (Google/OpenAI/Anthropic), return response + token usage |
| `action` | `_handle_action` | Routes to MCP tool call, HTTP request, or no-op based on config keys |
| `logic` | `_handle_logic` | Evaluates condition expressions (returns `{branch: "true"|"false"}`) or merges upstream outputs |

**MCP tool invocation:** `_call_mcp_tool()` sends `POST {mcp_server_url}/call-tool` with `{"tool_name": ..., "arguments": ...}` and the `X-Tenant-Id` header.

**HTTP request:** `_call_http()` makes arbitrary HTTP requests via httpx with a 30s timeout.

---

## 7. MCP Tool Bridge (Streamable HTTP)

Files: `app/engine/mcp_client.py`, `app/api/tools.py`

The orchestrator connects to the parent project's MCP server using the **MCP Python SDK** over **Streamable HTTP** transport — the standard MCP protocol, not a custom REST bridge.

### 7.1 Architecture

```
Orchestrator (FastAPI / Celery)          MCP Server (FastMCP)
┌───────────────────────────┐           ┌──────────────────────┐
│ mcp_client.call_tool()    │  HTTP     │ --transport           │
│ mcp_client.list_tools()   │ ───────▶ │   streamable-http     │
│                           │  /mcp     │   --port 8000         │
│ Uses:                     │           │                       │
│  streamablehttp_client()  │ ◀─────── │ SSE response stream   │
│  ClientSession            │           │                       │
└───────────────────────────┘           └──────────────────────┘
```

### 7.2 MCP Client Module

`app/engine/mcp_client.py` provides:

| Function | Purpose |
|----------|---------|
| `call_tool(name, args)` | Invoke an MCP tool; returns parsed JSON result |
| `list_tools()` | List all available tools with schemas (cached) |
| `get_openai_style_tool_defs(names)` | Convert MCP tool schemas to OpenAI function-calling format for LLM providers |

All functions are synchronous wrappers around the async MCP SDK client, safe to call from Celery workers and FastAPI sync endpoints.

### 7.3 Tool Listing API

The `/api/v1/tools` endpoint uses `mcp_client.list_tools()` to fetch tools directly from the running MCP server:

```json
{
  "name": "ae.request.get_status",
  "title": "Get Request Status",
  "description": "Retrieve the current status of an AE request",
  "category": "status",
  "safety_tier": "safe_read",
  "tags": ["request", "status"]
}
```

Tools are filtered per-tenant using `TenantToolOverride` records (V0.7). The frontend uses this endpoint to hydrate the Action node palette with real MCP tools.

### 7.4 Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ORCHESTRATOR_MCP_SERVER_URL` | `http://localhost:8000/mcp` | MCP server Streamable HTTP endpoint |

The MCP server must be running with `--transport streamable-http`.

---

## 8. Multi-Tenancy and Security

### 8.1 Authentication

File: `app/security/jwt_auth.py`

The `get_tenant_id()` dependency supports two modes controlled by `ORCHESTRATOR_AUTH_MODE`:

| Mode | Header | Behavior |
|------|--------|----------|
| `dev` (default) | `X-Tenant-Id` | Extracts tenant from plain header — for local development only |
| `jwt` | `Authorization: Bearer <token>` | Validates HS256-signed JWT, extracts `tenant_id` claim |

A development-only `/auth/token?tenant_id=xxx` endpoint generates test JWTs.
In production, tokens are issued by the organization's identity provider.

### 8.2 Database-Level Tenant Isolation (RLS)

Migration: `alembic/versions/0001_enable_rls_policies.py`

PostgreSQL Row-Level Security policies enforce that every query only sees rows
belonging to the current tenant. The application sets `app.tenant_id` via
`SET LOCAL` at the start of each database session.

Tables with RLS: `workflow_definitions`, `workflow_instances`, `tenant_tool_overrides`, `tenant_secrets`.

This provides defense-in-depth on top of application-level `WHERE tenant_id = ...` filtering.

### 8.3 Encrypted Credential Vault

File: `app/security/vault.py`

Per-tenant secrets (LLM API keys, SaaS credentials) are stored in the
`tenant_secrets` table encrypted at rest using Fernet symmetric encryption.
The vault key is set via `ORCHESTRATOR_VAULT_KEY`.

```python
from app.security.vault import encrypt_secret, decrypt_secret

ciphertext = encrypt_secret("sk-my-openai-key")
plaintext  = decrypt_secret(ciphertext)
```

### 8.4 Safe Expression Evaluator

File: `app/engine/safe_eval.py`

Condition node expressions are evaluated by an AST-walking evaluator that
**only** allows: comparisons, boolean ops, arithmetic, variable lookups,
attribute/subscript access on dict values, and literals. Function calls,
imports, `exec`, `eval`, and all other code execution are rejected.

### 8.5 Rate Limiting and Execution Quotas

File: `app/security/rate_limiter.py`

Two levels of protection:

| Level | Mechanism | Config |
|-------|-----------|--------|
| API request rate | slowapi (backed by Redis) | `ORCHESTRATOR_RATE_LIMIT_REQUESTS` / `ORCHESTRATOR_RATE_LIMIT_WINDOW` |
| Execution quota | DB count of recent instances | `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` |

The execute endpoint checks the hourly quota before creating a new instance,
returning HTTP 429 if the tenant has exceeded their limit.

---

## 9. Observability (Langfuse)

File: `app/observability.py`

The orchestrator integrates with Langfuse v4 (OpenTelemetry-based) for full execution tracing.
It shares the same `LANGFUSE_*` environment variables as the parent project's `config/observability.py`.

### 9.1 Trace Hierarchy

```
workflow:My Workflow            ← root trace (trace_workflow)
├── node:Webhook Trigger        ← child span (span_node)
├── node:LLM Agent              ← child span
│   └── llm:google/gemini-2.5   ← generation (record_generation)
├── node:Condition              ← child span
├── node:MCP Tool               ← child span
│   └── tool:get_request_status  ← tool span (span_tool)
└── node:HTTP Request           ← child span
```

### 9.2 What Gets Recorded

| Observation | Type | Data |
|-------------|------|------|
| Workflow execution | Root trace | workflow_id, instance_id, tenant_id, trigger payload, final status |
| Node execution | Span | node_id, node_type, input config, output/error |
| LLM call | Generation | provider, model, system prompt, user message, response, token usage |
| Tool call | Tool span | tool_name, arguments, result |
| ReAct iteration | Nested generations + tool spans | Per-iteration tool calls and LLM responses |

### 9.3 Compatibility

The module follows the same patterns as the parent project:
- Lazy singleton initialization via `get_langfuse()`
- `_NoOpSpan` stub when Langfuse is disabled — callers never need null checks
- All operations wrapped in try/except — Langfuse failures never break execution
- `atexit` shutdown hook registered in `main.py`
- `flush()` called after each workflow completes

---

## 10. Integration with AI Studio (Sidecar Pattern)

The orchestrator is **not** embedded into AI Studio. It runs as an external sidecar:

```
AI Studio (existing)                    Orchestrator (new)
┌──────────────────┐                   ┌─────────────────────┐
│ Dialog Designer   │  POST /execute   │ FastAPI Gateway      │
│ detects complex   │ ───────────────▶ │ (tenant_id,          │
│ agentic query     │                  │  session_id,          │
│                   │                  │  user_query)          │
│                   │  POST /callback  │                       │
│ Delivery endpoint │ ◀─────────────── │ Final node output     │
│ (WhatsApp/Teams)  │                  │                       │
└──────────────────┘                   └─────────────────────┘
```

1. AI Studio's Dialog Designer triggers a webhook to `POST /api/v1/workflows/{id}/execute`.
2. The DAG executes asynchronously (Celery worker).
3. The final Action node in the graph makes an HTTP POST back to AI Studio's delivery endpoint.
4. AI Studio formats the response for the appropriate channel (WhatsApp, Teams, Webchat).

---

## 11. Shared Schemas

File: `orchestrator/shared/node_registry.json`

A version-controlled JSON file defining all node types with their `config_schema`. This serves as the canonical schema that both frontend and backend can reference:

- 4 categories: `trigger`, `agent`, `action`, `logic`.
- 9 node types with typed `config_schema` objects (type, default, enum, min/max).
- Used for future dynamic form generation and server-side config validation.

---

## 12. Known Limitations (V0.8)

| Area | Limitation | Planned Resolution |
|------|------------|-------------------|
| **MCP sessions** | New session per call; no connection pooling | Add session pool for high-throughput deployments |
| **SAML federation** | OIDC implemented; SAML requires XML parsing + SP metadata | Add SAML 2.0 SP via python3-saml in V0.9 |
| **Condition expressions** | Safe evaluator supports basic ops; no custom functions or regex | Add pluggable expression functions |
| **Snapshot pruning** | Snapshots accumulate indefinitely; no max-per-workflow limit | Add background Celery task to prune oldest beyond N snapshots |
| **Langfuse in threads** | Parallel node execution may not propagate OTel context to worker threads | Use explicit span passing for parallel branches |
| **OIDC frontend callback** | Token must be stored via a thin redirect page after OIDC callback | Add `/auth/oidc/callback` frontend route that stores token and redirects |

---

## 13. Roadmap

**V0.2 — Wire Frontend to Backend (Implemented)**
- Frontend API client for save/load/execute workflows.
- Workflow list dialog and execution status/log viewer with polling.
- Next: real-time updates via WebSocket or SSE.

**V0.3 — Live LLM Integration (Implemented)**
- Multi-provider LLM abstraction (`app/engine/llm_providers.py`): Google Gemini, OpenAI, Anthropic.
- Jinja2 system prompt templating with dot-accessible context variables (`app/engine/prompt_template.py`).
- Token usage tracking (input/output tokens) returned in execution logs.

**V0.4 — Branching and Parallel Execution (Implemented)**
- Ready-queue execution model replaces linear topological traversal.
- Condition nodes prune non-matching branch subtrees based on `sourceHandle`.
- Independent nodes execute in parallel via `ThreadPoolExecutor` (max 8 workers).
- Merge nodes wait for all upstream branches naturally via the ready-queue model.
- Frontend edges from condition nodes show colored "Yes"/"No" labels with arrow markers.

**V0.5 — Production Hardening (Implemented)**
- JWT-based authentication with tenant claims (`jwt_auth.py`) + dev-mode fallback.
- Fernet-encrypted credential vault per tenant (`vault.py` + `TenantSecret` model).
- PostgreSQL RLS policies via Alembic migration (`0001_enable_rls_policies.py`).
- AST-based safe expression evaluator replacing `eval()` (`safe_eval.py`).
- Per-tenant rate limiting (slowapi + Redis) and hourly execution quotas.

**V0.6 — Advanced Agent Capabilities (Implemented)**
- ReAct iterative tool-calling loop (`react_loop.py`) with Google/OpenAI/Anthropic tool-calling APIs.
- SSE real-time execution updates (`sse.py`) replacing frontend polling.
- Celery Beat cron scheduler (`scheduler.py`) with croniter for schedule triggers.
- Frontend palette hydrated from `shared/node_registry.json`; backend validates configs on save.

**V0.7 — Observability, MCP Streaming & Tenant Tools (Implemented)**
- Langfuse v4 integration (`app/observability.py`): root traces per workflow, child spans per node, LLM generation recording with token usage, tool call spans.
- MCP client rewritten to use MCP Python SDK with Streamable HTTP transport (`app/engine/mcp_client.py`).
- Tool listing, tool execution, and ReAct tool definitions all fetched live from MCP server via standard protocol.
- TenantToolOverride consumed by tools endpoint to filter MCP tools per tenant.

**V0.8 — Enterprise Features (Implemented)**
- Dynamic property forms generated from `shared/node_registry.json` schemas (`DynamicConfigForm.tsx`): Select for enum fields, Textarea for prompts, number inputs with min/max, JSON textarea for objects/arrays, ToolMultiSelect for the ReAct tools field. `PropertyInspector.tsx` refactored to delegate entirely to the dynamic form.
- ReAct auto-discovery: when `tools` config is empty, `react_loop.py` calls `list_tools()` and passes all MCP tools. Tool cache upgraded to 5-minute TTL; `POST /api/v1/tools/invalidate-cache` for manual refresh.
- Workflow version history: `workflow_snapshots` table (Alembic 0002); snapshot inserted before each graph overwrite; `GET /{id}/versions`, `POST /{id}/rollback/{v}` endpoints; `VersionHistoryDialog` in Toolbar with Restore button.
- OIDC federation: Authorization Code + PKCE flow (`app/api/auth.py`), `authlib` for ID token validation, Redis PKCE state (5-min TTL); issues internal JWT; frontend `LoginPage` + `VITE_AUTH_MODE=oidc` gate.

**V0.9 — Planned**
- SAML 2.0 federation (python3-saml).
- Snapshot pruning (max N snapshots per workflow via Celery task).
- OIDC frontend callback route that auto-stores token.
- Pluggable condition expression functions.

---

This blueprint is the single technical reference for the orchestrator module. Setup instructions are in `orchestrator/SETUP_GUIDE.md`, and a step-by-step runtime walkthrough is in `orchestrator/HOW_IT_WORKS.md`. For the parent project's architecture, see `docs/TECHNICAL_BLUEPRINT.md`.
