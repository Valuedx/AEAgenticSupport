> - **V0.7 Observability & Tenant Tools (2026-03-20)**: Langfuse v4 integration (`app/observability.py`) — root trace per workflow execution, child spans per node, LLM generation recording with token usage, tool call spans. Compatible with parent project's `config/observability.py` pattern. TenantToolOverride now consumed by the tools endpoint to filter MCP tools per tenant.
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

## AE AI Hub — Agentic Orchestrator Technical Blueprint

**Version:** 0.7  
**Last updated:** 2026-03-20  
**Status:** V0.7 Langfuse observability + tenant tool overrides, V0.6 advanced agents, V0.5 hardening, V0.4 branching, V0.3 LLM, V0.2 wired, V0.1 scaffold

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
11. [Known Limitations (V0.2)](#11-known-limitations-v02)
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

| Action | Signature | Description |
|--------|-----------|-------------|
| `onNodesChange` | `OnNodesChange` | React Flow node change handler (move, resize) |
| `onEdgesChange` | `OnEdgesChange` | React Flow edge change handler |
| `onConnect` | `OnConnect` | New edge creation between handles |
| `addNode` | `(category, label, position, config?) → void` | Create node at canvas position |
| `selectNode` | `(id \| null) → void` | Set selection for property inspector |
| `updateNodeData` | `(id, partial) → void` | Merge data updates from inspector forms |
| `deleteNode` | `(id) → void` | Remove node and all connected edges |

### 3.3 Node Categories and Palette

File: `types/nodes.ts`

Four categories, each with distinct visual styling:

| Category | Color | Border | Nodes |
|----------|-------|--------|-------|
| **Trigger** | Amber | `border-amber-500/60` | Webhook Trigger, Schedule Trigger |
| **Agent** | Violet | `border-violet-500/60` | LLM Agent, ReAct Agent |
| **Action** | Sky | `border-sky-500/60` | MCP Tool, HTTP Request, Human Approval |
| **Logic** | Emerald | `border-emerald-500/60` | Condition, Merge |

The `NODE_PALETTE` array (9 items) defines every draggable item with its `nodeCategory`, `label`, `description`, `icon`, and `defaultConfig`.

### 3.4 Custom Node Component

File: `components/nodes/AgenticNode.tsx`

A single `memo`-ized component renders all node types polymorphically:

- **Visual:** shadcn `Card` with category-colored border, icon badge, label, category pill, model badge (agents), and a status dot (idle/running/completed/failed/suspended).
- **Handles:** Target (left) on all except Triggers. Source (right) on all except Merge. Condition nodes get two source handles (`true` in green, `false` in red) at 35%/65% vertical offset.
- **Icons:** Mapped via `ICON_MAP` from Lucide icon names stored in `config.icon`.

### 3.5 Property Inspector

File: `components/sidebar/PropertyInspector.tsx`

Category-specific config panels:

| Category | Fields |
|----------|--------|
| **Agent** | Provider (Google/OpenAI/Anthropic), Model (6 options), System Prompt, Temperature |
| **Trigger** | Webhook Path *or* Cron Expression (based on default config) |
| **Action** | MCP Tool Name *or* URL+Method *or* Approval Message |
| **Logic** | Condition Expression *or* Merge Strategy (waitAll/waitAny) |

All fields write back to the store via `updateNodeData`. A "Delete Node" button removes the selected node.

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

### 5.4 TenantToolOverride

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

## 7. MCP Tool Bridge

File: `app/api/tools.py`

The `/api/v1/tools` endpoint dynamically imports `mcp_server.tool_specs.TOOL_SPECS` from the parent project's MCP server directory. It caches the result after first load and returns a list of `ToolOut` objects:

```json
{
  "name": "get_request_status",
  "title": "Get Request Status",
  "description": "Retrieve the current status of an AE request",
  "category": "status",
  "safety_tier": "safe_read",
  "tags": ["request", "status"]
}
```

The frontend can use this endpoint to dynamically hydrate the Action node palette with real MCP tools, instead of relying solely on the hardcoded `NODE_PALETTE`.

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

## 12. Known Limitations (V0.7)

| Area | Limitation | Planned Resolution |
|------|------------|-------------------|
| **MCP transport** | Backend calls `POST /call-tool` (REST) | Add REST bridge to existing stdio/SSE MCP server |
| **Tenant auth** | JWT auth implemented; no external IdP integration yet | Add OIDC/SAML federation with enterprise identity providers |
| **Condition expressions** | Safe evaluator supports basic ops; no custom functions or regex | Add pluggable expression functions |
| **Frontend validation** | Config validation runs server-side on save (logs warnings); no inline form validation | Generate dynamic property forms from registry schemas with client-side validation |
| **ReAct tool discovery** | ReAct agent requires manually listing tool names in config | Auto-discover available tools from MCP registry |
| **Langfuse in threads** | Parallel node execution may not propagate OTel context to worker threads | Use explicit span passing for parallel branches |

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

**V0.7 — Observability & Tenant Tools (Implemented)**
- Langfuse v4 integration (`app/observability.py`): root traces per workflow, child spans per node, LLM generation recording with token usage, tool call spans.
- Compatible with parent project's `config/observability.py` (same env vars, same `_NoOpSpan` pattern).
- TenantToolOverride consumed by tools endpoint to filter MCP tools per tenant.

**V0.8 — Enterprise Features**
- OIDC/SAML federation with enterprise identity providers.
- Dynamic property form generation from registry config schemas.
- Workflow versioning with diff/rollback UI.
- Auto-discover available tools for ReAct agent from MCP registry.

---

This blueprint is the single technical reference for the orchestrator module. Setup instructions are in `orchestrator/SETUP_GUIDE.md`, and a step-by-step runtime walkthrough is in `orchestrator/HOW_IT_WORKS.md`. For the parent project's architecture, see `docs/TECHNICAL_BLUEPRINT.md`.
