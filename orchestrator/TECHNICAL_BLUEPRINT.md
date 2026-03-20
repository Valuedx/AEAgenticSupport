> - **V0.3 Live LLM Integration (2026-03-20)**: Agent nodes now call real LLM providers (Google Gemini via `google-genai`, OpenAI, Anthropic). Added `app/engine/llm_providers.py` multi-provider abstraction, `app/engine/prompt_template.py` Jinja2 system-prompt templating with context variable injection (dot-accessible upstream outputs), and token usage tracking in execution logs. New config keys: `ORCHESTRATOR_GOOGLE_API_KEY`, `ORCHESTRATOR_OPENAI_API_KEY`, `ORCHESTRATOR_ANTHROPIC_API_KEY`. See `SETUP_GUIDE.md` §7 for configuration.
>
> - **V0.2 UI Wiring (2026-03-20)**: Added frontend API client + workflow toolbar (save/load/execute), a saved-workflow list dialog, and an execution log panel with polling against backend instance status. See `orchestrator/HOW_IT_WORKS.md` for runtime walkthrough.
> - **Initial Scaffold (2026-03-20)**: V0.1 — React Flow visual builder (frontend), FastAPI DAG execution engine (backend), Zustand state management, shadcn/ui component library, SQLAlchemy data models with multi-tenant isolation, Celery worker stubs, and MCP tool bridge. See `SETUP_GUIDE.md` for installation and `HOW_IT_WORKS.md` for runtime walkthrough.

## AE AI Hub — Agentic Orchestrator Technical Blueprint

**Version:** 0.3  
**Last updated:** 2026-03-20  
**Status:** V0.3 live LLM integration (Google/OpenAI/Anthropic), V0.2 frontend wired to backend, V0.1 scaffold complete

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
    ├── api/
    │   ├── schemas.py              # Pydantic request/response models
    │   ├── workflows.py            # CRUD + execute + callback + status
    │   └── tools.py                # MCP tool bridge for palette
    ├── engine/
    │   ├── dag_runner.py           # Graph parser, topo sort, executor
    │   ├── node_handlers.py        # Per-type dispatch (trigger/agent/action/logic)
    │   ├── llm_providers.py        # Multi-provider LLM abstraction (Google/OpenAI/Anthropic)
    │   └── prompt_template.py      # Jinja2 system-prompt templating with context injection
    ├── models/
    │   ├── workflow.py             # WorkflowDefinition, WorkflowInstance, ExecutionLog
    │   └── tenant.py              # TenantToolOverride
    ├── workers/
    │   ├── celery_app.py           # Celery configuration
    │   └── tasks.py                # execute_workflow_task, resume_workflow_task
    └── security/
        └── tenant.py              # X-Tenant-Id header extraction
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

### 6.1 Graph Parsing

`parse_graph(graph_json)` converts React Flow JSON into three structures:

- `nodes_map`: `{node_id: node_dict}` — the full node object including `data`.
- `adj`: `{source_id: [target_id, ...]}` — forward adjacency list built from `edges`.
- `in_degree`: `{node_id: int}` — count of incoming edges per node.

### 6.2 Topological Sort

`topological_sort()` implements Kahn's algorithm:

1. Enqueue all nodes with `in_degree == 0` (typically Trigger nodes).
2. Dequeue each node, append to `order`, decrement in-degree of all neighbors.
3. If `len(order) != len(nodes_map)`, raise `ValueError` identifying cycle nodes.

### 6.3 Execution Flow

`execute_graph(db, instance_id)`:

1. Load `WorkflowInstance` and set status to `running`.
2. Parse the definition's `graph_json` and compute topological order.
3. Initialize `context` dict with `trigger_payload` under key `"trigger"`.
4. Call `_run_from()` starting at index 0.

`_run_from(db, instance, nodes_map, order, context, start_index)`:

For each node in order (from `start_index`):

1. Create an `ExecutionLog` entry with status `running`.
2. **Suspension check:** If node is an Action with `approvalMessage` config and no `"approval"` key in context → set instance to `suspended`, serialize context, return.
3. Call `dispatch_node(node_data, context, tenant_id)` to execute the node.
4. Store output in `context[node_id]` and update the log entry.
5. On exception: mark log as `failed`, mark instance as `failed`, return.
6. After all nodes: mark instance as `completed`.

### 6.4 Resume Flow

`resume_graph(db, instance_id, approval_payload)`:

1. Load suspended instance, inject `approval_payload` into context under key `"approval"`.
2. Recompute topological order from graph JSON.
3. Find the index of `current_node_id` + 1 and resume `_run_from()`.

### 6.5 Node Handlers

File: `app/engine/node_handlers.py`

`dispatch_node()` routes by `nodeCategory`:

| Category | Handler | Behavior |
|----------|---------|----------|
| `trigger` | `_handle_trigger` | Pass through `context["trigger"]` |
| `agent` | `_handle_agent` | **Stub** — logs provider/model, returns placeholder. Production will call LLM API |
| `action` | `_handle_action` | Routes to MCP tool call, HTTP request, or no-op based on config keys |
| `logic` | `_handle_logic` | Evaluates condition expressions or merges upstream outputs |

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

### 8.1 Tenant Isolation

File: `app/security/tenant.py`

Every API endpoint depends on `get_tenant_id()`, which extracts the `X-Tenant-Id` header. All database queries filter by `tenant_id`, ensuring strict row-level data isolation.

### 8.2 Database Design

All four tables include a `tenant_id` column with indexes. PostgreSQL Row-Level Security (RLS) policies can be layered on top of the application-level filtering for defense-in-depth.

### 8.3 Credential Vaulting (Planned)

In production, LLM API keys and SaaS credentials will be encrypted at rest and bound to specific `tenant_id`s, injected into node handlers only at execution time.

---

## 9. Integration with AI Studio (Sidecar Pattern)

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

## 10. Shared Schemas

File: `orchestrator/shared/node_registry.json`

A version-controlled JSON file defining all node types with their `config_schema`. This serves as the canonical schema that both frontend and backend can reference:

- 4 categories: `trigger`, `agent`, `action`, `logic`.
- 9 node types with typed `config_schema` objects (type, default, enum, min/max).
- Used for future dynamic form generation and server-side config validation.

---

## 11. Known Limitations (V0.3)

| Area | Limitation | Planned Resolution |
|------|------------|-------------------|
| **LLM calls** | Live multi-provider LLM calls implemented (Google/OpenAI/Anthropic); ReAct tool-calling loop not yet implemented | Add iterative tool-calling ReAct loop for agent nodes |
| **Condition branching** | DAG runner executes all nodes linearly | Implement edge-aware branch selection using `sourceHandle` |
| **MCP transport** | Backend calls `POST /call-tool` (REST) | Add REST bridge to existing stdio/SSE MCP server |
| **Frontend persistence** | Save/Load/Execute UI is wired, but still lacks tenant/session switching, schema validation, and graph-level validation/highlighting | Add tenant-aware session config, validate `graph_json` against node registry, and improve UX with WebSocket/SSE updates |
| **Tenant auth** | Header-based `X-Tenant-Id` only | JWT validation with tenant claim |
| **Schedule triggers** | No cron scheduler backend | Add APScheduler or Celery Beat integration |
| **ReAct loop** | Palette item exists, no iterative execution | Implement tool-calling loop in agent handler |
| **TenantToolOverride** | Model exists, not consumed | Filter tools endpoint by tenant overrides |
| **Condition safety** | Uses `eval()` with restricted builtins | Replace with safe expression parser |
| **node_registry.json** | Not consumed by frontend/backend | Hydrate palette and validate config from registry |

---

## 12. Roadmap

**V0.2 — Wire Frontend to Backend (Implemented)**
- Frontend API client for save/load/execute workflows.
- Workflow list dialog and execution status/log viewer with polling.
- Next: real-time updates via WebSocket or SSE.

**V0.3 — Live LLM Integration (Implemented)**
- Multi-provider LLM abstraction (`app/engine/llm_providers.py`): Google Gemini, OpenAI, Anthropic.
- Jinja2 system prompt templating with dot-accessible context variables (`app/engine/prompt_template.py`).
- Token usage tracking (input/output tokens) returned in execution logs.

**V0.4 — Branching and Parallel Execution**
- Condition-aware edge traversal (follow `true`/`false` handles).
- Parallel node execution for independent branches.
- Merge node waits for all/any upstream branches.

**V0.5 — Production Hardening**
- JWT-based authentication with tenant claims.
- Encrypted credential vault per tenant.
- PostgreSQL RLS policies.
- Safe expression evaluator replacing `eval()`.
- Rate limiting and execution quotas per tenant.

---

This blueprint is the single technical reference for the orchestrator module. Setup instructions are in `orchestrator/SETUP_GUIDE.md`, and a step-by-step runtime walkthrough is in `orchestrator/HOW_IT_WORKS.md`. For the parent project's architecture, see `docs/TECHNICAL_BLUEPRINT.md`.
