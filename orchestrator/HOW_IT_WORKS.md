> - **V0.6 Advanced Agent Capabilities (2026-03-20)**: ReAct iterative tool-calling loop for agent nodes (Google/OpenAI/Anthropic tool-calling APIs). SSE real-time execution updates replacing frontend polling. Celery Beat cron scheduler for schedule triggers. Frontend palette hydrated from `node_registry.json`; backend validates configs on save.
>
> - **V0.5 Production Hardening (2026-03-20)**: JWT-based auth with tenant claims (dev-mode header fallback). Fernet-encrypted credential vault per tenant. PostgreSQL RLS policies for DB-level tenant isolation. AST-based safe expression evaluator replaces `eval()`. Per-tenant rate limiting (slowapi) and hourly execution quotas.
>
> - **V0.4 Branching & Parallel Execution (2026-03-20)**: DAG engine rewritten with ready-queue model. Condition nodes now prune non-matching branches; independent nodes execute in parallel. Merge nodes wait for all upstream branches. Condition edges show colored "Yes"/"No" labels on the canvas.
>
> - **V0.3 Live LLM Integration (2026-03-20)**: Agent nodes now call real LLM providers (Google Gemini, OpenAI, Anthropic). System prompts support Jinja2 templating with upstream context injection (`{{ trigger.user_query }}`, `{{ node_1.response }}`). Token usage tracked in execution logs.
>
> - **V0.2 UI Wiring (2026-03-20)**: Frontend now saves/loads/executes workflows via the FastAPI backend and shows execution logs using a polling execution panel. See `orchestrator/TECHNICAL_BLUEPRINT.md` for architecture and `orchestrator/SETUP_GUIDE.md` for setup.
> - **Initial Walkthrough (2026-03-20)**: V0.1 — covers visual builder interaction, drag-and-drop, graph persistence, DAG execution, human-in-the-loop suspension, and MCP tool integration. See `TECHNICAL_BLUEPRINT.md` for architecture and `SETUP_GUIDE.md` for installation.

## AE AI Hub — How It Works (Step-by-Step)

**Purpose:** This document explains how the orchestrator works end-to-end, from building a visual workflow to executing it asynchronously. Each step includes pointers to the relevant **code files** so you can trace behavior or extend it.

**Version:** 0.6  
**Last updated:** 2026-03-20

---

### Table of Contents

1. [Overview](#1-overview)
2. [Step 1 — User Builds a Workflow on the Canvas](#2-step-1--user-builds-a-workflow-on-the-canvas)
3. [Step 2 — Drag-and-Drop: Palette to Canvas](#3-step-2--drag-and-drop-palette-to-canvas)
4. [Step 3 — Connecting Nodes with Edges](#4-step-3--connecting-nodes-with-edges)
5. [Step 4 — Configuring Node Properties](#5-step-4--configuring-node-properties)
6. [Step 5 — Saving the Workflow](#6-step-5--saving-the-workflow)
7. [Step 6 — Executing the Workflow](#7-step-6--executing-the-workflow)
8. [Step 7 — DAG Parsing and Topological Sort](#8-step-7--dag-parsing-and-topological-sort)
9. [Step 8 — Node-by-Node Execution](#9-step-8--node-by-node-execution)
10. [Step 9 — Human-in-the-Loop Suspension](#10-step-9--human-in-the-loop-suspension)
11. [Step 10 — Completion and Callback](#11-step-10--completion-and-callback)
12. [Step 11 — MCP Tool Bridge](#12-step-11--mcp-tool-bridge)
13. [End-to-End Example](#13-end-to-end-example)

---

## 1. Overview

The orchestrator has two independent layers:

```
 ┌────────────────────────────────────────┐
 │           DESIGN TIME                  │
 │  (Browser — React Flow visual canvas)  │
 │                                        │
 │  User drags nodes, connects edges,     │
 │  configures LLM prompts and tools.     │
 │  State lives in Zustand store.         │
 └───────────────┬────────────────────────┘
                 │  POST /api/v1/workflows
                 │  (graph_json: {nodes, edges})
                 ▼
 ┌────────────────────────────────────────┐
 │           RUN TIME                     │
 │  (Server — FastAPI + Celery worker)    │
 │                                        │
 │  Graph JSON is parsed into a DAG,      │
 │  topologically sorted, and executed    │
 │  node-by-node. Each node's output      │
 │  feeds into the next node's input.     │
 └────────────────────────────────────────┘
```

---

## 2. Step 1 — User Builds a Workflow on the Canvas

**Code:** `frontend/src/App.tsx`

When the user opens the orchestrator at `http://localhost:8080`, they see a three-panel layout:

| Panel | Component | File | Purpose |
|-------|-----------|------|---------|
| Left | `NodePalette` | `components/sidebar/NodePalette.tsx` | Draggable node list, grouped by category |
| Center | `FlowCanvas` | `components/canvas/FlowCanvas.tsx` | React Flow canvas with background grid, minimap, controls |
| Right | `PropertyInspector` | `components/sidebar/PropertyInspector.tsx` | Config form for the selected node |

The entire app is wrapped in `ReactFlowProvider` (required by React Flow for coordinate transforms) and `TooltipProvider` (required by shadcn tooltips).

---

## 3. Step 2 — Drag-and-Drop: Palette to Canvas

**Code:** `components/sidebar/NodePalette.tsx` → `components/canvas/FlowCanvas.tsx` → `store/flowStore.ts`

The data flow for a drag-and-drop operation:

```
NodePalette                    FlowCanvas                    flowStore
──────────                    ──────────                    ─────────
onDragStart                        │                             │
  │ setData("application/          │                             │
  │   reactflow", JSON)            │                             │
  └──────────────────────▶    onDragOver                         │
                              │ preventDefault()                 │
                              │                                  │
                              onDrop                             │
                              │ getData(...)                     │
                              │ screenToFlowPosition(x,y)       │
                              │                                  │
                              └──────────────────────────▶  addNode(category,
                                                              label, position,
                                                              defaultConfig)
                                                            │
                                                            ▼
                                                         nodes: [..., newNode]
                                                         selectedNodeId: newNode.id
```

The JSON payload in `dataTransfer` carries:
- `nodeCategory`: `"trigger"`, `"agent"`, `"action"`, or `"logic"`
- `label`: Display name (e.g. "LLM Agent")
- `defaultConfig`: Category-specific defaults including the `icon` key

The Zustand store generates a sequential ID (`node_1`, `node_2`, ...) and creates a React Flow node with type `"agenticNode"`.

---

## 4. Step 3 — Connecting Nodes with Edges

**Code:** `store/flowStore.ts` → `onConnect`

When the user drags from one node's output handle to another node's input handle, React Flow fires the `onConnect` callback. The store calls `addEdge(connection, edges)` to create a new edge.

Each edge records:
- `source`: The upstream node ID
- `target`: The downstream node ID
- `sourceHandle`: Handle ID (relevant for Condition nodes: `"true"` or `"false"`)

---

## 5. Step 4 — Configuring Node Properties

**Code:** `components/sidebar/PropertyInspector.tsx`

When the user clicks a node on the canvas:

1. `FlowCanvas.onNodeClick` calls `flowStore.selectNode(node.id)`.
2. `PropertyInspector` reads `selectedNodeId` from the store and finds the matching node.
3. Based on `data.nodeCategory`, it renders the appropriate config panel:

| Category | Panel | Key Fields |
|----------|-------|------------|
| Agent | `AgentConfigPanel` | Provider dropdown (Google/OpenAI/Anthropic), Model dropdown (6 models), System Prompt textarea, Temperature slider |
| Trigger | `TriggerConfigPanel` | Webhook Path input *or* Cron Expression input |
| Action | `ActionConfigPanel` | MCP Tool Name *or* URL + HTTP Method *or* Approval Message |
| Logic | `LogicConfigPanel` | Condition Expression *or* Merge Strategy (waitAll/waitAny) |

Every field change calls `flowStore.updateNodeData(id, { config: { ...updated } })`, which merges the update into the node's data immutably.

---

## 6. Step 5 — Saving the Workflow

**Code:** `backend/app/api/workflows.py` → `POST /api/v1/workflows`

*In V0.2, the frontend Toolbar persists workflows directly via the API client in `frontend/src/lib/api.ts`, using `frontend/src/store/workflowStore.ts` for save/load state.*

To persist a workflow, the frontend will serialize the Zustand store's `nodes` and `edges` into a JSON object and POST it:

```http
POST /api/v1/workflows
X-Tenant-Id: acme-corp
Content-Type: application/json

{
  "name": "IT RCA Pipeline",
  "description": "Root cause analysis for failed AE requests",
  "graph_json": {
    "nodes": [ ... React Flow node objects ... ],
    "edges": [ ... React Flow edge objects ... ]
  }
}
```

The backend creates a `WorkflowDefinition` row with `version: 1`. Subsequent saves via `PATCH` increment the version.

---

## 7. Step 6 — Executing the Workflow

**Code:** `backend/app/api/workflows.py` → `POST /api/v1/workflows/{id}/execute`

In V0.2, the frontend `Run` button calls this endpoint and the `ExecutionPanel` polls `GET /api/v1/workflows/{workflowId}/instances/{instanceId}` until the instance reaches `completed`, `failed`, or `suspended`.

```
Client                          API Gateway                     Celery Worker
──────                          ───────────                     ─────────────
POST /execute                        │                               │
  {trigger_payload: {...}}           │                               │
                                     │                               │
                              Create WorkflowInstance                │
                              status = "queued"                      │
                              ─────────────────────▶           │
                              202 Accepted                     execute_workflow_task
                              {id: <instance_id>}              │
                                                               │ execute_graph(db, instance_id)
                                                               ▼
                                                         (DAG execution begins)
```

The API immediately returns `202 Accepted` with the new instance ID. The actual execution happens asynchronously in the Celery worker, freeing the API to handle other requests.

---

## 8. Step 7 — DAG Parsing and Topological Sort

**Code:** `backend/app/engine/dag_runner.py` → `parse_graph()`, `topological_sort()`

The worker loads the `WorkflowDefinition.graph_json` and processes it:

**Step 7a — Parse (Handle-Aware):** Build data structures from the React Flow JSON, preserving `sourceHandle` info for condition branches:

```python
nodes_map = {"node_1": {...}, "node_2": {...}, "node_3": {...}, "node_4": {...}}

edges = [
    Edge(source="node_1", target="node_2", source_handle=None),      # Trigger → Condition
    Edge(source="node_2", target="node_3", source_handle="true"),    # Condition → Agent (Yes)
    Edge(source="node_2", target="node_4", source_handle="false"),   # Condition → Action (No)
]
```

**Step 7b — Cycle Detection:**

Kahn's algorithm validates the graph is a valid DAG before execution begins.

**Step 7c — Ready-Queue Initialization:**

Nodes with `in_degree == 0` (no incoming edges, typically Trigger nodes) form the initial ready set.

---

## 9. Step 8 — Ready-Queue Execution

**Code:** `backend/app/engine/dag_runner.py` → `_execute_ready_queue()`, `backend/app/engine/node_handlers.py` → `dispatch_node()`

The engine uses a ready-queue model instead of a linear topological order:

```
┌──────────────────────────────────────────────────────────────┐
│  While ready_nodes is non-empty:                             │
│                                                              │
│  1. If 1 ready node → execute sequentially                   │
│     If N ready nodes → execute in parallel (ThreadPool)      │
│                                                              │
│  2. For each executed node:                                  │
│     a. Create ExecutionLog (status: "running")               │
│     b. dispatch_node(node_data, context, tenant_id)          │
│        ├── trigger  → pass through trigger_payload           │
│        ├── agent    → render prompt + call LLM provider      │
│        ├── action   → call MCP tool / HTTP request           │
│        └── logic    → evaluate condition / merge branches    │
│     c. Store output in context[node_id]                      │
│                                                              │
│  3. Propagate edges:                                         │
│     - CONDITION node → only matching branch edges satisfied  │
│       Non-matching subtree is PRUNED (never executed)        │
│     - Other nodes → all outgoing edges satisfied             │
│                                                              │
│  4. Recompute ready set (all incoming edges satisfied,       │
│     not pruned, not yet executed)                            │
│                                                              │
│  On error: mark log and instance as "failed", stop.          │
└──────────────────────────────────────────────────────────────┘
```

The **context** dictionary accumulates outputs:

```python
context = {
    "trigger": {"user_query": "Why did request 12345 fail?"},
    "node_1": {"output": {"user_query": "Why did request 12345 fail?"}},
    "node_2": {"provider": "google", "model": "gemini-2.5-flash", "response": "..."},
    "node_3": {"status_code": 200, "body": "..."},
}
```

Each node receives its config and all upstream outputs, so it can reference previous results.

### Agent Node Deep-Dive (V0.3)

When `dispatch_node()` routes to an **agent** node, the following happens:

```
Agent Node Execution
────────────────────────────────────────────────────────
1. Read config: provider, model, systemPrompt, temperature, maxTokens
2. Render system prompt through Jinja2 engine:
   "Analyze {{ trigger.user_query }}"  →  "Analyze Why did request 12345 fail?"
3. Build user message from all upstream node outputs (JSON-formatted)
4. Route to provider SDK:
   ├── google  →  google-genai client  →  Gemini API
   ├── openai  →  openai client        →  OpenAI / compatible API
   └── anthropic → anthropic client    →  Claude API
5. Return standardized response:
   { "response": "...", "usage": { "input_tokens": 342, "output_tokens": 128 },
     "model": "gemini-2.5-flash", "provider": "google" }
6. Token counts are persisted in ExecutionLog.output_json
```

System prompts support full Jinja2 syntax with dot-access to all context keys.
Missing variables resolve to empty strings, so prompts are reusable across
different workflow topologies.

---

## 10. Step 9 — Human-in-the-Loop Suspension

**Code:** `backend/app/engine/dag_runner.py` (suspension), `backend/app/api/workflows.py` → `POST /callback` (resume)

When the DAG runner encounters an Action node with `approvalMessage` in its config:

```
DAG Runner                              Database                     External System
──────────                              ────────                     ───────────────
Reaches "Human Approval" node               │                             │
                                            │                             │
Check: is "approval" key in context?        │                             │
  NO → Suspend                              │                             │
    │                                       │                             │
    ├─ instance.status = "suspended"        │                             │
    ├─ instance.context_json = context  ───▶│ (serialized)                │
    ├─ instance.current_node_id = node_id   │                             │
    └─ log.status = "suspended"             │                             │
                                            │                             │
    (Worker thread released)                │                             │
                                            │                             │
             ... time passes ...            │                             │
                                            │                             │
POST /callback                              │                       Human approves
  {approval_payload: {"approved": true}} ──▶│                             │
                                            │                             │
resume_workflow_task.delay(instance_id)     │                             │
  │                                         │                             │
  ├─ Load context from DB                   │                             │
  ├─ Inject approval_payload                │                             │
  ├─ Find current_node_id index + 1         │                             │
  └─ Continue _run_from(start_index)        │                             │
```

This pattern allows the workflow to sleep indefinitely without holding a worker thread. The approval can come from any channel — WhatsApp, Teams, a web UI, or a direct API call.

---

## 11. Step 10 — Completion and Callback

**Code:** `backend/app/engine/dag_runner.py` → end of `_run_from()`

After the last node completes:

1. `instance.status` is set to `"completed"`.
2. `instance.context_json` contains the full execution context (all node outputs).
3. `instance.completed_at` is set.
4. The `ExecutionLog` for each node records its individual input, output, timing, and status.

To retrieve results:

```http
GET /api/v1/workflows/{workflow_id}/instances/{instance_id}
X-Tenant-Id: acme-corp
```

This returns the full instance with all execution logs, ordered by start time.

In the AI Studio sidecar pattern, the final Action node in the graph would be an HTTP Request node that POSTs the result back to AI Studio's delivery endpoint, which then formats it for WhatsApp/Teams/Webchat.

---

## 12. Step 11 — MCP Tool Bridge

**Code:** `backend/app/api/tools.py` (palette hydration), `backend/app/engine/node_handlers.py` → `_call_mcp_tool()` (runtime execution)

The orchestrator bridges to the parent project's 106 MCP tools in two ways:

### Design Time — Palette Hydration

```
GET /api/v1/tools                    tools.py
X-Tenant-Id: acme-corp               │
                                      ├─ Import mcp_server.tool_specs
                                      ├─ Read TOOL_SPECS dict
                                      ├─ Map to ToolOut schema
                                      └─ Return JSON list
```

This allows the frontend to show real MCP tools (like `get_request_status`, `restart_request`, `get_execution_logs`) in the Action node palette alongside the built-in node types.

### Run Time — Tool Execution

```
DAG Runner                            MCP Server (port 3000)
──────────                            ──────────────────────
dispatch_node("action", ...)
  │
  ├─ config.toolName = "get_request_status"
  │
  └─ _call_mcp_tool()
       │
       POST http://localhost:3000/call-tool
       {
         "tool_name": "get_request_status",
         "arguments": {"request_id": "REQ-12345"}
       }
       X-Tenant-Id: acme-corp
       │
       └─ Returns tool result JSON
```

---

## 13. End-to-End Example

**Scenario:** An IT support agent that diagnoses a failed AE request.

### Graph Design

```
[Webhook Trigger] ──▶ [LLM Agent] ──▶ [MCP: get_request_status] ──▶ [MCP: get_execution_logs] ──▶ [LLM Agent: Diagnosis] ──▶ [Human Approval] ──▶ [MCP: restart_request]
```

### Execution Trace

| Step | Node | Type | Input | Output |
|------|------|------|-------|--------|
| 1 | Webhook Trigger | trigger | `{request_id: "REQ-12345"}` | `{output: {request_id: "REQ-12345"}}` |
| 2 | LLM Agent | agent | System prompt + trigger payload | `{response: "I'll investigate REQ-12345..."}` |
| 3 | get_request_status | action | `{request_id: "REQ-12345"}` | `{status: "Failed", error: "Timeout"}` |
| 4 | get_execution_logs | action | `{request_id: "REQ-12345"}` | `{logs: [...]}` |
| 5 | Diagnosis Agent | agent | All upstream outputs + "Analyze the failure" | `{response: "Root cause: network timeout at step 3..."}` |
| 6 | Human Approval | action | `{approvalMessage: "Restart REQ-12345?"}` | **SUSPENDED** — waiting for approval |
| — | *(Human approves via Teams)* | — | `POST /callback {approved: true}` | — |
| 7 | restart_request | action | `{request_id: "REQ-12345"}` | `{status: "Restarted"}` |

### Timing

- Steps 1-5: ~10 seconds (async worker).
- Step 6: Suspended for 2 hours (human reviewing).
- Step 7: ~3 seconds (after resume).
- Total wall clock: ~2 hours. Worker thread held: ~13 seconds.

---

For architecture details, see `orchestrator/TECHNICAL_BLUEPRINT.md`.  
For installation and setup, see `orchestrator/SETUP_GUIDE.md`.  
For the parent project's architecture, see `docs/TECHNICAL_BLUEPRINT.md` and `docs/HOW_IT_WORKS.md`.
