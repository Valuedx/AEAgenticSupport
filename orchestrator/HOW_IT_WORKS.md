> - **V0.9.2 UX Improvements (2026-03-21)**: New Step 5b (pre-run validation) — `validateWorkflow()` runs client-side before every execution. Checks: trigger presence, node reachability (BFS from triggers), required empty fields, broken node-ID cross-references. `ValidationDialog` blocks hard errors and allows "Run Anyway" for warnings only. Undo/Redo — `flowStore.past[]`/`future[]` history stacks (max 50); `_pushHistory()` called before add/delete/connect/drag-start/edge-delete; Ctrl+Z/Ctrl+Y keyboard shortcuts in `FlowCanvas.tsx`; toolbar Undo/Redo buttons with disabled state. Node ID chip — `PropertyInspector` now shows the node's machine ID (e.g., `node_3`) in a monospace chip at the top of the panel with a one-click copy button so users can easily reference it in expression fields on other nodes. Inline field help text — every field in `DynamicConfigForm` now renders a `FieldHint` (10px muted subtext) when the field's `config_schema` entry carries a `description`; all node types in `node_registry.json` have been populated with descriptions. ForEach/Merge canvas clarity — `AgenticNode` renders a `waitAll`/`waitAny` strategy badge for Merge nodes and a `↻ arrayExpression` monospace hint for ForEach nodes, making both nodes interpretable at a glance without opening the properties panel.
>
> - **V0.9 Execution Enhancements (2026-03-21)**: New Step 14 — ForEach Loop iteration with downstream node re-execution per array element. New Step 15 — Retry from Failed Node (API + engine). Step 11 MCP section updated — connection pooling with configurable pool size. Step 8 updated — enhanced safe expression evaluator supports whitelisted functions (`len`, `lower`, `matches` etc.) and method calls. New config: `ORCHESTRATOR_MAX_SNAPSHOTS` (snapshot pruning) and `ORCHESTRATOR_MCP_POOL_SIZE`. Environment variable mapping via `{{ env.SECRET_NAME }}` for node config values. Langfuse context fix for parallel execution.
> - **V0.8 Enterprise Features (2026-03-20)**: Step 4 updated — property forms now generated from registry schemas via DynamicConfigForm; no more hardcoded panels. Step 5 updated — each graph save creates a snapshot in workflow_snapshots. New Step 12 — Version History & Rollback. Step 13 MCP section updated — 5-minute TTL cache + invalidate-cache endpoint. New Step 14 — OIDC Authentication. ReAct section updated for auto-discovery.
>
> - **V0.7 Observability, MCP Streaming & Tenant Tools (2026-03-20)**: Langfuse v4 integration — root trace per workflow, child spans per node, LLM generation recording with token usage, tool call spans. MCP client rewritten to use MCP Python SDK with Streamable HTTP transport — replaces raw REST bridge with standard MCP protocol. Tool listing and ReAct tool definitions fetched live from MCP server. TenantToolOverride consumed by tools endpoint.
>
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

**Version:** 0.9
**Last updated:** 2026-03-21

---

### Table of Contents

1. [Overview](#1-overview)
2. [Step 1 — User Builds a Workflow on the Canvas](#2-step-1--user-builds-a-workflow-on-the-canvas)
3. [Step 2 — Drag-and-Drop: Palette to Canvas](#3-step-2--drag-and-drop-palette-to-canvas)
4. [Step 3 — Connecting Nodes with Edges](#4-step-3--connecting-nodes-with-edges)
5. [Step 4 — Configuring Node Properties (Dynamic Forms)](#5-step-4--configuring-node-properties-dynamic-forms)
6. [Step 5 — Saving the Workflow (with Snapshots)](#6-step-5--saving-the-workflow-with-snapshots)
7. [Step 6 — Executing the Workflow](#7-step-6--executing-the-workflow)
8. [Step 7 — DAG Parsing and Topological Sort](#8-step-7--dag-parsing-and-topological-sort)
9. [Step 8 — Node-by-Node Execution](#9-step-8--node-by-node-execution)
10. [Step 9 — Human-in-the-Loop Suspension](#10-step-9--human-in-the-loop-suspension)
11. [Step 10 — Completion and Callback](#11-step-10--completion-and-callback)
12. [Step 11 — MCP Tool Bridge (Streamable HTTP)](#12-step-11--mcp-tool-bridge-streamable-http)
13. [Step 12 — Version History and Rollback](#13-step-12--version-history-and-rollback)
14. [Step 13 — OIDC Authentication Flow](#14-step-13--oidc-authentication-flow)
15. [Step 14 — ForEach Loop Iteration](#15-step-14--foreach-loop-iteration)
16. [Step 15 — Retry from Failed Node](#16-step-15--retry-from-failed-node)
17. [End-to-End Example](#17-end-to-end-example)

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
| Right | `PropertyInspector` | `components/sidebar/PropertyInspector.tsx` | Schema-driven config form for the selected node |

The entire app is wrapped in `ReactFlowProvider` (required by React Flow for coordinate transforms) and `TooltipProvider` (required by shadcn tooltips).

If `VITE_AUTH_MODE=oidc` and no token is stored in `localStorage`, the OIDC `LoginPage` is shown instead.

### Node card visual indicators

Each node card shows one of four visual states at a glance, evaluated in priority order:

| Priority | Visual | Meaning |
|----------|--------|---------|
| 1 (highest) | Blue ring | Node is currently selected |
| 2 | Red ring + `AlertCircle` icon | Configuration error (empty required field, broken node-ID reference) |
| 3 | Yellow ring + `AlertTriangle` icon | Warning — node is not reachable from any trigger |
| 4 (default) | Coloured dot | Runtime status: grey=idle, blue=running, green=completed, red=failed, yellow=suspended |

The `useNodeValidation` hook (`src/lib/useNodeValidation.ts`) runs `validateWorkflow()` inside a `useMemo` whenever `nodes` or `edges` change, returning `errorIds` and `warningIds` sets. Each `AgenticNode` reads its own `id` from NodeProps and checks membership in these sets.

In addition to the validation ring, node cards show type-specific summary info in the badge row:

| Node type | Extra info shown on card |
|-----------|--------------------------|
| Agent / ReAct Agent / LLM Router | Model badge (e.g. `gemini-2.5-flash`) |
| Merge | Strategy badge (`waitAll` or `waitAny`) |
| ForEach | `↻ arrayExpression` monospace line (when set) |

---

## 3. Step 2 — Finding and Dragging Nodes: Palette to Canvas

**Code:** `components/sidebar/NodePalette.tsx` → `components/canvas/FlowCanvas.tsx` → `store/flowStore.ts`

The palette has a **search input** at the top. Typing filters nodes by label and description:
- Categories with zero matches are hidden entirely
- Categories with matches auto-expand (collapsing is disabled during a search)
- Category headers show `matched/total` count while a query is active
- A ✕ button clears the search

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
- `defaultConfig`: Derived from the registry schema defaults

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

## 5. Step 4 — Configuring Node Properties (Dynamic Forms + Expression Picker)

**Code:** `components/sidebar/PropertyInspector.tsx` → `components/sidebar/DynamicConfigForm.tsx` → `lib/registry.ts`

When the user clicks a node on the canvas:

1. `FlowCanvas.onNodeClick` calls `flowStore.selectNode(node.id)`.
2. `PropertyInspector` reads `selectedNodeId` from the store and finds the matching node.
3. A **Node ID chip** at the top of the panel shows the node's machine ID (e.g., `node_3`) in a monospace badge with a one-click copy button. This is the value to use in expressions on other nodes (e.g., `node_3.intent`).
4. It calls `getRegistryNodeType(data.label)` and `getConfigSchema(data.label)` from `lib/registry.ts` to load the node's schema from `shared/node_registry.json`.
5. It renders `<DynamicConfigForm>` with the schema, current config, and an `onUpdate` callback.

`DynamicConfigForm` renders one field per schema entry:

| Schema field type | Rendered as | Notes |
|-------------------|-------------|-------|
| `string` + `enum` | `<Select>` dropdown | Options from enum array |
| `string` key in `EXPRESSION_KEYS` | `<ExpressionInput>` (expression mode) | Autocomplete: `node_2.intent`, `trigger.body` |
| `string` key in `NODE_ID_KEYS` | `<ExpressionInput>` (nodeId mode) | Autocomplete: `node_3`, `node_5` |
| `systemPrompt` | `<ExpressionInput>` (jinja2 mode) | Autocomplete: `{{ node_2.response }}` |
| `string` (`approvalMessage`, `body`) | `<Textarea>` | Multi-line plain text |
| `string` (other) | `<Input type="text">` | |
| `number` / `integer` | `<Input type="number">` | `min`/`max`/`step` from schema |
| `boolean` | `<input type="checkbox">` | |
| `object` | `<Textarea>` (JSON) | Validated on blur; red border on invalid JSON |
| `string` key `toolName` on `mcp_tool` | `ToolSingleSelect` | Searchable single-select from live tool list; shows title, description, safety tier |
| `array` + key is `tools` on `react_agent` | `ToolMultiSelect` | Fetches live tool list from `/api/v1/tools` |
| `array` (other) | `<Textarea>` (JSON array) | |

Every field change calls `flowStore.updateNodeData(id, { config: { ...updated } })`, merging the update immutably.

Each field also renders a `FieldHint` — a 10px muted grey line of subtext below the input — when the `config_schema` entry for that field has a `description` property. This gives users in-context guidance without leaving the panel (e.g., *"Cron expression in UTC (e.g. '0 * * * *' = every hour)"* or *"waitAll: hold until every incoming branch completes; waitAny: continue as soon as any one branch completes"*).

### ExpressionInput autocomplete

`ExpressionInput` wraps an `<input>` or `<textarea>` and shows a **fixed-position dropdown** (rendered via `createPortal` to `document.body` — not clipped by sidebar `overflow: hidden`). On every keystroke, `getCurrentToken()` detects the word under the cursor and filters suggestions. Pressing **Enter** or **Tab** calls `insertAtCursor()` to splice the selected variable in-place, leaving the rest of the expression intact.

### ToolMultiSelect (ReAct Agent)

When configuring a **ReAct Agent** node's `tools` field, a special `ToolMultiSelect` sub-component:

1. On mount, calls `api.listTools()` → `GET /api/v1/tools`.
2. Renders tools grouped by category, each with a checkbox and safety tier badge.
3. Selected tool names are stored in `config.tools` as a string array.
4. If no tools are selected (empty array), the backend will **auto-discover** all available MCP tools at runtime.

---

## 6. Step 5 — Saving the Workflow (with Snapshots)

**Code:** `backend/app/api/workflows.py` → `PATCH /{workflow_id}`

To persist a workflow, the frontend serializes the Zustand store's `nodes` and `edges` into a JSON object:

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

The backend creates a `WorkflowDefinition` row with `version: 1`.

**On subsequent saves** (PATCH), before overwriting `graph_json`, the backend:

1. Creates a `WorkflowSnapshot` row with the **current** `graph_json` and `version`.
2. Replaces `graph_json` with the new content.
3. Increments `version`.

This gives every save an immutable point-in-time backup. The version badge in the Toolbar (`v{n}`) reflects the current version number.

---

## 7. Step 5a — Undo / Redo (Client-Side Canvas History)

**Code:** `frontend/src/store/flowStore.ts`, `frontend/src/components/canvas/FlowCanvas.tsx`

Every destructive canvas action is tracked in two in-memory stacks inside the Zustand store so users can freely experiment and reverse mistakes without saving.

```
User action (add / delete / connect / drag)
      │
      ▼
_pushHistory()
  → push {nodes, edges} snapshot to past[]
  → clear future[]
      │
      ▼
Apply the change to canvas

Ctrl+Z (undo)                      Ctrl+Y / Ctrl+Shift+Z (redo)
      │                                       │
      ▼                                       ▼
pop past[last]               pop future[0]
push current to future[]     push current to past[]
set nodes/edges = snapshot   set nodes/edges = snapshot
```

Key rules:
- **Max 50 snapshots** in each direction — older history is evicted.
- **Drag captures once per gesture** via `_draggingNodeIds`: the first `dragging: true` event pushes a snapshot; subsequent pixel-by-pixel events do not.
- **Config edits are not snapshotted** — property panel keystrokes fire too frequently; users edit the field back instead.
- **`replaceGraph()` resets both stacks** — loading a different workflow always starts with a clean history.

---

## Step 5b — Pre-Run Validation (Client-Side)

**Code:** `frontend/src/lib/validateWorkflow.ts`, `frontend/src/components/toolbar/ValidationDialog.tsx`

Before calling the execute API, the **Run** button runs `validateWorkflow(nodes, edges)` entirely in the browser. This gives instant feedback without making a network request.

```
User clicks Run
      │
      ▼
validateWorkflow(nodes, edges)
      │
      ├── Check 1: At least one Trigger node exists
      │
      ├── Check 2: BFS reachability from all triggers
      │              → orphaned nodes → WARNING
      │
      ├── Check 3: Required fields per node type
      │              condition, url, toolName,
      │              arrayExpression, responseNodeId,
      │              intents (≥1) → ERROR
      │
      └── Check 4: Node ID cross-references
                     responseNodeId, historyNodeId
                     must point to existing node IDs → ERROR

      │
      ├── errors.length === 0 → proceed to API
      │
      ├── only warnings → show ValidationDialog with "Run Anyway"
      │
      └── any hard errors → show ValidationDialog, block execution
```

`ValidationError` shape:
```ts
interface ValidationError {
  nodeId: string;       // e.g. "node_3" (empty for graph-level errors)
  nodeLabel: string;    // e.g. "Save Conversation State"
  message: string;      // human-readable description
  severity: "error" | "warning";
}
```

---

## Step 6 — Executing the Workflow

**Code:** `backend/app/api/workflows.py` → `POST /api/v1/workflows/{id}/execute`

The frontend `Run` button calls this endpoint (only after validation passes). Real-time status arrives via SSE stream (`/instances/{instanceId}/stream`).

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

The API immediately returns `202 Accepted` with the new instance ID. The actual execution happens asynchronously in the Celery worker.

---

## 8. Step 7 — DAG Parsing and Topological Sort

**Code:** `backend/app/engine/dag_runner.py` → `parse_graph()`, `_detect_cycles()`

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
│        │              (or run ReAct loop for ReAct Agent)    │
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

### LLM Agent Node

When `dispatch_node()` routes to an **LLM Agent** node:

```
1. Read config: provider, model, systemPrompt, temperature, maxTokens
2. Render system prompt via Jinja2:
   "Analyze {{ trigger.user_query }}"  →  "Analyze Why did request 12345 fail?"
3. Build user message from all upstream node outputs (JSON-formatted)
4. Route to provider SDK (Google / OpenAI / Anthropic)
5. Return: { "response": "...", "usage": {...}, "model": "...", "provider": "..." }
6. Token counts persisted in ExecutionLog.output_json
```

### ReAct Agent Node

When `dispatch_node()` routes to a **ReAct Agent** node (detected by `label == "ReAct Agent"`):

```
react_loop.py
─────────────
1. config.tools = ["ae.request.get_status"]  (explicit list)
   OR
   config.tools = []   →  auto-discover ALL tools from MCP via list_tools()

2. Load tool definitions in OpenAI function-calling format

3. Iterative loop (max maxIterations, hard cap 25):
   a. Call LLM with system prompt + conversation history + tool schemas
   b. If LLM returns tool_calls:
      - Execute each tool via call_tool()
      - Append tool results to conversation
      - Continue loop
   c. If LLM returns text (no tool calls):
      - Return final response + token usage + iteration log

4. If max iterations reached: return "Maximum iterations reached"
```

Auto-discovery means ReAct agents configured with an empty `tools` list will use every tool the MCP server exposes. Use the explicit list to restrict a ReAct agent to a safe subset.

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
  ├─ Re-parse graph, skip executed nodes    │                             │
  └─ Continue _execute_ready_queue()        │                             │
```

This pattern allows the workflow to sleep indefinitely without holding a worker thread. The approval can come from any channel — WhatsApp, Teams, a web UI, or a direct API call.

---

## 11. Step 10 — Completion and Callback

**Code:** `backend/app/engine/dag_runner.py` → end of `_execute_ready_queue()`

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

### Viewing results in the Execution Panel

The `ExecutionPanel` (`components/toolbar/ExecutionPanel.tsx`) displays each node's log as a collapsible row. Each JSON block (Input and Output) has two action buttons:

| Button | Action |
|--------|--------|
| `Copy` (clipboard icon) | Copies full JSON to clipboard; icon becomes a green ✓ for 2 seconds |
| `Expand` (maximize icon) | Opens `FullJsonDialog` — scrollable full-size view of the JSON, also with a copy button |

The panel header shows "streaming…" while the SSE connection is active (not "polling…" — the frontend uses Server-Sent Events, not HTTP polling).

---

## 12. Step 11 — MCP Tool Bridge (Streamable HTTP)

**Code:** `backend/app/engine/mcp_client.py`, `backend/app/api/tools.py`, `backend/app/engine/node_handlers.py` → `_call_mcp_tool()`

The orchestrator connects to the parent project's MCP server using the **MCP Python SDK** over **Streamable HTTP** transport.

### Design Time — Palette Hydration

```
GET /api/v1/tools                    mcp_client.py             MCP Server (:8000)
X-Tenant-Id: acme-corp               │                         │
                                      ├─ list_tools()          │
                                      │  ├─ Check TTL cache    │
                                      │  │   (5 min)           │
                                      │  ├─ If stale:          │
                                      │  │   streamablehttp    │
                                      │  │   _client(/mcp) ──▶ │ tools/list
                                      │  └─ Cache result       │
                                      ├─ Filter by tenant      │
                                      └─ Return JSON list      │
```

Tools are cached for 5 minutes. To force an immediate refresh (e.g. after deploying new MCP tools):

```bash
curl -X POST http://localhost:8001/api/v1/tools/invalidate-cache \
  -H "X-Tenant-Id: acme-corp"
```

### Run Time — Tool Execution

```
DAG Runner                            mcp_client.py          MCP Server (:8000)
──────────                            ────────────           ──────────────────
dispatch_node("action", ...)
  │
  ├─ config.toolName = "ae.request.get_status"
  │
  └─ _call_mcp_tool()
       │
       call_tool("ae.request.get_status",
                 {"request_id": "REQ-12345"})
         │
         streamablehttp_client(/mcp) ──▶ tools/call
         │                                │
         ◀── SSE response stream ────────┘
         │
         └─ Returns parsed JSON result
```

---

## 13. Step 12 — Version History and Rollback

**Code:** `backend/app/api/workflows.py` → `GET /{id}/versions`, `POST /{id}/rollback/{v}`, `frontend/src/components/toolbar/VersionHistoryDialog.tsx`

Every time a workflow's graph is saved (PATCH with `graph_json`), the backend creates an immutable snapshot **before** overwriting:

```
PATCH /api/v1/workflows/{id}
  body: { graph_json: <new graph> }

Backend:
  1. Create WorkflowSnapshot(version=wf.version, graph_json=wf.graph_json)
  2. wf.graph_json = new graph
  3. wf.version += 1
  4. Commit
```

### Listing Snapshots

```http
GET /api/v1/workflows/{id}/versions
X-Tenant-Id: acme-corp

Response: [
  {"id": "...", "workflow_def_id": "...", "version": 2, "saved_at": "2026-03-20T10:00:00Z"},
  {"id": "...", "workflow_def_id": "...", "version": 1, "saved_at": "2026-03-20T09:50:00Z"}
]
```

### Rolling Back

```
POST /api/v1/workflows/{id}/rollback/{version}

Backend:
  1. Snapshot current state (version N) → creates snapshot
  2. wf.graph_json = snapshot[version].graph_json
  3. wf.version = N + 1  (rollback is a forward operation, not a rewind)
  4. Commit + refresh
  5. Return updated WorkflowOut
```

Rollback **always increments** the version counter — version history is an append-only ledger. This prevents accidentally losing the current state when restoring an old snapshot.

### Frontend

The **History** (clock) button in the Toolbar opens `VersionHistoryDialog`. It shows:
- The current live version (highlighted)
- All snapshots with timestamps and version numbers
- A **Restore** button per snapshot that calls rollback and reloads the canvas

---

## 14. Step 13 — OIDC Authentication Flow

**Code:** `backend/app/api/auth.py`, `frontend/src/components/auth/LoginPage.tsx`, `frontend/src/lib/api.ts`

The OIDC flow is opt-in. It activates when `ORCHESTRATOR_OIDC_ENABLED=true` (backend) and `VITE_AUTH_MODE=oidc` (frontend).

```
Browser                        FastAPI (/auth/oidc)        Identity Provider
───────                        ───────────────────        ─────────────────
App.tsx: no token in                  │                          │
  localStorage → show LoginPage       │                          │
                                      │                          │
"Sign in with SSO" clicked            │                          │
  └── GET /auth/oidc/login            │                          │
                                      │                          │
                              Generate state + nonce + PKCE      │
                              Store in Redis (5-min TTL)          │
                              Redirect to authorization_endpoint  │
                              ─────────────────────────────────▶ │
                                                                  │
                                                         User authenticates
                                                         Redirect to callback
                              ◀───────────────────────────────── │
                              GET /auth/oidc/callback             │
                                ?code=...&state=...               │
                                                                  │
                              Validate state from Redis           │
                              Exchange code for tokens            │
                              ─────────────────────────────────▶ │
                              ◀─ id_token ──────────────────────  │
                                                                  │
                              Validate ID token (authlib + JWKS)  │
                              Extract tenant_id from claim         │
                              Issue internal JWT                   │
                              Return { access_token, tenant_id }  │
                                                                  │
  Frontend stores token in           │                            │
    localStorage ("ae_access_token") │                            │
  App renders normally               │                            │
```

Once stored, all API calls use `Authorization: Bearer <token>` instead of `X-Tenant-Id`. The backend validates the JWT via `app/security/jwt_auth.py`.

---

## 15. Step 14 — ForEach Loop Iteration

**Code:** `backend/app/engine/dag_runner.py` → `_run_forEach_iterations()`, `backend/app/engine/node_handlers.py` → `_handle_forEach()`

The **ForEach** node (category: `logic`) enables iterating over an array, executing all immediately-downstream nodes once per element.

### Configuration

| Field | Type | Description |
|-------|------|-------------|
| `arrayExpression` | string | Expression that resolves to an iterable from the context (e.g. `trigger.items`) |
| `itemVariable` | string | Variable name injected into context per iteration (default: `item`) |

### Execution Flow

```
_execute_ready_queue
  │
  ├─ Detects ForEach node completed
  │
  └─ _run_forEach_iterations()
       │
       for each element in items:
       │  ├─ context["_loop_item"] = element
       │  ├─ context["_loop_index"] = idx
       │  ├─ context[itemVariable] = element
       │  │
       │  └─ for each downstream node:
       │       └─ _execute_single_node(...)
       │           → output collected into iteration_results
       │
       └─ context[downstream_id] = {
            "forEach_results": [...all_outputs...],
            "iterations": N
          }
```

Downstream nodes receive `loop_item`, `loop_index`, and `loop_variable` in their input payload, allowing prompts and expressions to reference the current iteration item.

---

## 16. Step 15 — Retry from Failed Node

**Code:** `backend/app/engine/dag_runner.py` → `retry_graph()`, `backend/app/api/workflows.py` → `POST /{id}/instances/{iid}/retry`, `backend/app/workers/tasks.py` → `retry_workflow_task`

When a workflow instance fails, users can retry execution from the point of failure instead of re-running the entire workflow.

### API

```http
POST /api/v1/workflows/{workflow_id}/instances/{instance_id}/retry
X-Tenant-Id: acme-corp
Content-Type: application/json

{
  "from_node_id": "node_5"  // optional — defaults to current_node_id
}
```

### Engine Behavior

```
retry_graph(db, instance_id, from_node_id)
  │
  ├─ Validate instance.status == "failed"
  ├─ Determine retry node (from_node_id or instance.current_node_id)
  ├─ Remove failed node output from context
  ├─ Delete failed ExecutionLog entry
  ├─ Set instance.status = "running"
  │
  ├─ Re-parse graph, build forward/reverse/in_degree
  ├─ All nodes with context entries = "already_executed" (skipped)
  │
  └─ _execute_ready_queue(... skipped=already_executed)
       → Only the failed node and its downstream successors re-execute
```

### Frontend

The `workflowStore.retryInstance(workflowId, instanceId, fromNodeId?)` action calls the retry API and re-streams the instance logs via SSE.

---

## 17. End-to-End Example

**Scenario:** An IT support agent that diagnoses a failed AE request using a ReAct loop with auto-discovered tools.

### Graph Design

```
[Webhook Trigger] ──▶ [ReAct Agent] ──▶ [Human Approval] ──▶ [MCP: restart_request]
```

The ReAct Agent has `tools: []` (empty) — it will auto-discover all MCP tools at runtime.

### Execution Trace

| Step | Node | Type | Key Behavior |
|------|------|------|-------------|
| 1 | Webhook Trigger | trigger | Pass through `{request_id: "REQ-12345"}` |
| 2 | ReAct Agent | agent | Auto-discovers 106 MCP tools. Calls `get_request_status(REQ-12345)`, then `get_execution_logs(REQ-12345)`, then reasons over the results and produces a diagnosis. |
| 3 | Human Approval | action | Workflow suspended. Teams message sent with diagnosis. |
| — | *(Human approves)* | — | `POST /callback {approved: true, action: "restart"}` |
| 4 | restart_request | action | `call_tool("ae.request.restart", {request_id: "REQ-12345"})` |

### Version History

After editing this workflow (e.g. changing the ReAct system prompt), the user saves again. The previous version is automatically snapshotted. If the new prompt breaks the agent's behavior, they open the History dialog and click **Restore** on the previous version.

### Timing

- Step 1-2: ~15 seconds (ReAct loop with 2-3 tool calls).
- Step 3: Suspended for 2 hours (human reviewing).
- Step 4: ~3 seconds (after resume).
- Total worker thread held: ~18 seconds across all steps.

---

For architecture details, see `orchestrator/TECHNICAL_BLUEPRINT.md`.
For installation and setup, see `orchestrator/SETUP_GUIDE.md`.
For the parent project's architecture, see `docs/TECHNICAL_BLUEPRINT.md` and `docs/HOW_IT_WORKS.md`.
