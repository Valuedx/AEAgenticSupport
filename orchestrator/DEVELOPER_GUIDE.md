# AE AI Hub — Agentic Orchestrator Developer Guide

**Version:** 0.9.8
**Last updated:** 2026-03-22

Welcome to the Developer Guide! 🚀 

If you are a fresher or new to this codebase, you are in the right place. This guide is written specifically to help you understand how the **Agentic Orchestrator** works under the hood, step-by-step, with plain English explanations and heavily commented code examples.

---

## 📚 Core Concepts (The Basics)

Before we write code, let's understand the vocabulary:

*   **Orchestrator:** A system that manages a sequence of tasks. Think of it like a factory manager ensuring every machine does its job in the right order.
*   **Node:** A single "box" or "step" on the visual canvas. A node might send an email, ask an AI a question, or check if a condition is true.
*   **DAG (Directed Acyclic Graph):** A fancy computer science term for a flowchart. "Directed" means the arrows have a direction. "Acyclic" means it doesn't loop infinitely back on itself. It always moves forward through the workflow.
*   **Context:** The highly secured "memory" of the workflow. Every time a node finishes running, it drops its results into the Context. The nodes downstream can then read those results.
*   **MCP (Model Context Protocol):** A standard way for our AI agents to securely connect to external tools (like a tool to check server status or query a database).
*   **Jinja2:** A "fill-in-the-blanks" text system. If you write `"Hello {{ user_name }}"`, Jinja2 will look inside the Context for `user_name` and replace it, resulting in `"Hello Alice"`.

---

## 🛠️ 1. Let's Build Your First Custom Node

The most common task you will do as a developer is adding a new type of Node. 
The magical part? **You don't need to write any React/Frontend code.** The UI builds itself based on a JSON file!

Let's pretend we want to build a **Slack Notification Node** that sends a message to a team channel.

### Step 1: Tell the UI about your Node (`node_registry.json`)

Open the `shared/node_registry.json` file. This is the source of truth for all nodes. We will add our new node here.

```json
{
  "type": "slack_notification",
  "category": "action",
  "label": "Slack Notification",
  "description": "Sends a message to a Slack channel",
  "icon": "MessageSquare",
  "color": "bg-blue-100 border-blue-300",
  
  // This is where the magic happens! The UI reads this config_schema
  // and automatically generates the textboxes and checkboxes for the user.
  "config_schema": {
    "channel": {
      "type": "string",
      "description": "Enter the Slack channel name (e.g., #alerts)"
    },
    "messageTemplate": {
      "type": "string",
      "description": "What to say! You can use variables like {{ context.user }}"
    },
    "urgent": {
      "type": "boolean",
      "description": "Check this box to flag it as high priority",
      "default": false
    }
  }
}
```

### Step 2: Write the Python Logic (`node_handlers.py`)

Now that the UI can place the node, we need to tell the backend what to do when the workflow actually runs.
Open `backend/app/engine/node_handlers.py`.

```python
from app.engine.prompt_template import render_template

# This function receives the user's config, the current memory (context), 
# and the tenant_id (who is running this workflow)
async def _handle_slack_notification(node_data: dict, context: dict, tenant_id: str) -> dict:
    
    # 1. Safely grab the settings the user typed into the UI
    config = node_data.get("config", {})
    channel = config.get("channel", "#general")
    template = config.get("messageTemplate", "No message provided.")
    urgent = config.get("urgent", False)
    
    # 2. Fill in the blanks! Let's render the Jinja2 template.
    # If template is "Server {{ trigger.server }} failed"
    # and context has a trigger.server value of "Web-01", 
    # message becomes "Server Web-01 failed".
    message = render_template(template, context)
    
    # 3. Apply basic business logic
    if urgent:
        message = f"🚨 *URGENT* 🚨\n{message}"
        
    # 4. Do the actual work! (e.g., call a Slack API hook)
    print(f"I am sending this to {channel}: {message}")
    
    # 5. Return a dictionary. Whatever you return here is permanently 
    # saved into the workflow's Context memory for the next nodes to use.
    return {
        "status": "success",
        "delivered_to": channel,
        "final_text": message
    }
```

Finally, at the bottom of `node_handlers.py`, just route the traffic to your new function:

```python
# Inside the dispatch_node function:
if node_category == "action":
    if label == "Slack Notification":  # MUST match the label in the JSON!
        return await _handle_slack_notification(node_data, context, tenant_id)
```
Congratulations! You just built a fully functional distributed workflow node! 🎉

---

## 🧠 2. Writing Logic Rules (`safe_eval`)

Workflows often need to make decisions like, *"If the AI found a virus, go left. If the file is safe, go right."* We do this using **Condition Nodes**.

Because letting users run random Python code is a huge security risk, we built a very strict expression evaluator called `safe_eval`. It acts like a mini-language.

### Reading from Memory (The Context)
If a previous node with the ID `node_2` returned `{"user": {"age": 25, "name": "Bob"}}`, you can check his age like this:
```python
node_2.user.age >= 18
```

### Safe Functions You Can Use
Instead of standard Python, you can only use these safe functions (added in V0.9):
*   **Math:** `len()`, `min()`, `max()`, `abs()`
*   **Types:** `str()`, `int()`, `float()`, `bool()`
*   **Text Checkers:** `startswith()`, `endswith()`, `contains()` (checks if an item is in a list)
*   **Text Changers:** `lower()`, `upper()`, `strip()`

### Examples for Freshers
Here is how you would type these inside a Condition Node on the visual canvas:

**Example A: Simple text check**
Wait, did the AI respond with an error? Let's check:
```python
lower(node_1.status) == "error"
```

**Example B: Making sure an array isn't empty**
Did the database give us any results?
```python
len(node_3.database_rows) > 0
```

**Example C: Complex security condition**
Is the user part of the `@admin.com` domain AND is this an urgent request?
```python
trigger.email.endswith("@admin.com") and trigger.priority == "High"
```

---

## 🤖 3. The ReAct Agent (AI that uses Tools)

Normally, if you ask ChatGPT a question, it just replies with text. 
But a **ReAct Agent** (Reasoning + Acting) is special. You give it a goal, and you hand it a backpack full of tools (like a tool to restart a server, or a tool to read logs).

### How to configure it:
1.  Drag a **ReAct Agent** onto the canvas.
2.  In the `tools` dropdown, you can select specific tools you want to allow it to use.
3.  **Pro Tip:** If you leave the tools dropdown completely empty, the backend will auto-discover **every single tool** available on the MCP server and hand them all to the AI.

### How it thinks:
The backend code (`react_loop.py`) runs a loop that goes like this:
1. **AI:** "I need to check the server status. I will use the `get_status` tool."
2. **Backend:** *Pauses the AI, runs the `get_status` tool, gets the result, hands the result back to the AI.*
3. **AI:** "Okay, the server is down. I will now use the `restart_server` tool."
4. **Backend:** *Runs the tool, returns the result.*
5. **AI:** "The server is back up! Here is my final summary for the user."

---

## 🔄 4. Advanced Tricks: Loops, Retries, and Suspensions

### The "ForEach" Loop (Doing things repeatedly)
Introduced in V0.9, the ForEach node takes a list, and runs every node attached to it *once per item* in the list.

If your list is `["Alice", "Bob"]`:
*   `_loop_item` will be "Alice" for the first run.
*   `_loop_item` will be "Bob" for the second run.

### The Retry Button (Oops, API failed!)
If a workflow runs 10 steps successfully, but fails on step 11 because the internet blinked, you don't want to start over from step 1!
The backend now tracks `current_node_id`. If it fails, a user can hit **Retry** in the UI. The backend deletes the error log, loads the memory right before step 11, and simply presses 'play' again.

### Human-in-the-Loop (The Pause Button)
Sometimes it is too dangerous to let an AI delete a database automatically. It needs human approval.
If a Node's config contains an `approvalMessage` (e.g., `"Approve deletion?"`), the python code (`dag_runner.py`) will literally put itself to sleep, mark its status as `suspended`, and free up its memory.
When a human clicks "Approve" via a webhook/Slack API, the backend wakes back up, loads its context, and continues the workflow exactly where it left off.

#### HITL Review UI (V0.9.4)

The orchestrator now includes a built-in review UI so operators don't need external webhooks for simple approvals.

**How it works for the operator:**

1. When a workflow suspends, the **Execution Panel** shows a yellow **Review & Resume** button.
2. Clicking it fetches the current context from `GET /instances/{id}/context` (internal keys like `_trace` are stripped before display).
3. The `HITLResumeDialog` opens showing:
   - The node's configured **approval message** (e.g., *"About to delete 500 production records — confirm?"*).
   - A read-only **JSON viewer** of every node's output up to the suspension point.
   - An editable **Context Patch** textarea (JSON object) where the operator can inject corrected values — for example, overriding a specific node's output before the workflow continues.
4. **Approve & Resume** merges the patch and calls `POST /callback` — the workflow continues.
5. **Reject** sends `{rejected: true}` in the approval payload — downstream Condition nodes can branch on `approval.rejected`.

**How to make a node require approval:**

In `shared/node_registry.json`, add `approvalMessage` to the node's `config_schema`:

```json
"config_schema": {
  "approvalMessage": {
    "type": "string",
    "default": "",
    "description": "If non-empty, execution pauses here for human approval before continuing."
  }
}
```

Set it on any action node in the Properties panel. Leave it empty to skip the approval gate.

**Context patch use cases:**

| Scenario | Patch |
|----------|-------|
| Override a condition result | `{"node_5": {"branch": "true"}}` |
| Inject corrected data | `{"node_3": {"score": 0.95, "label": "approved"}}` |
| Add a manual flag | `{"manual_override": true}` |

---

## 🔐 5. Security: The Vault

**Golden Rule:** NEVER hardcode passwords or API keys in the visual builder text boxes.

Instead, an admin saves an API key in the Database Vault (encrypted) under a name like `AWS_PROD_KEY`.
When a developer configures a node (like an HTTP request), they just type:
`{{ env.AWS_PROD_KEY }}`

When the workflow runs, exactly 1 millisecond before the node executes, `resolve_config_env_vars()` (in `prompt_template.py`) intercepts that string, safely fetches the encrypted key from the database, decrypts it in RAM, and hands it to the node. Safe and sound!

---

## 💬 6. Stateful Conversational Memory

By default, an Orchestrator DAG is acyclic and stateless. But what if you want to build a chatbot that remembers context over 10 messages? You use the **Stateful Re-Trigger Pattern** (introduced in V0.9.1).

Instead of making the DAG loop infinitely, we let each user message trigger a **fresh DAG instance**. We use two "bookend" nodes to fetch and save memory to a PostgreSQL database (`conversation_sessions`).

### How to build a conversational DAG

The canonical graph for any chat-enabled workflow is:

```text
[Webhook Trigger]
       ↓
[Load Conversation State]   ← config: sessionIdExpression = "trigger.session_id"
       ↓
[LLM Router]                ← config: intents = ["diagnose_server", "casual_chat", "escalate"]
                                       historyNodeId = "node_2"
       ↓
[Condition]                 ← condition: node_3.intent == "diagnose_server"
    ↙         ↘
[Branch A]  [Branch B]  ...  (any action/agent nodes)
    ↘         ↙
[Save Conversation State]   ← config: responseNodeId = "node_X"
                                       userMessageExpression = "trigger.message"
```

### Key design points:
1. **The DAG stays acyclic** — each user message simply fires a fresh execution instance.
2. **Load at the start / Save at the end** bookend every instance with memory fetch/store.
3. **LLM Router** reads the full history passed from the Load State node, handling pivots and follow-ups contextually.
4. **The intent value** flows dynamically into standard Condition nodes — keeping routine routing out of arbitrary Python code.

---

## 📋 7. Execution Log — Copy & Expand

After running a workflow, every node's **Input** and **Output** JSON blocks in the execution panel now have two action buttons in the top-right corner:

| Button | What it does |
|--------|-------------|
| **Copy** (clipboard icon) | Copies the full JSON string to the clipboard. The icon turns green with a ✓ for 2 seconds to confirm. |
| **Expand** (maximize icon) | Opens a full-size dialog showing the complete JSON (no height cap) with its own Copy button. |

This is especially useful for large LLM responses or deeply nested tool outputs that are truncated in the 128px preview area.

---

## 🔍 8. Palette Search

The Node Palette has a **search box** at the top. Type any part of a node's label or description (e.g., "http", "loop", "approval") to instantly filter the list.

- Categories with zero matches are hidden
- Matching categories auto-expand
- Category headers show `matched/total` while searching
- ✕ button clears the filter

No code changes needed when you add a new node to `node_registry.json` — the search automatically covers its `label` and `description` fields.

---

## 🔴 9. Validation Highlighting on Node Cards

Node cards show red or yellow visual indicators **in real time** as you edit the canvas — no need to click Run to discover problems.

### How it works

**Files:** `frontend/src/lib/useNodeValidation.ts`, `frontend/src/components/nodes/AgenticNode.tsx`

The `useNodeValidation()` hook subscribes to `nodes` and `edges` from the Zustand store and runs `validateWorkflow()` inside `useMemo`. It returns two sets:

```ts
const { errorIds, warningIds } = useNodeValidation();
// errorIds  → Set of node IDs with hard errors (broken config)
// warningIds → Set of node IDs with warnings (e.g. disconnected)
```

`AgenticNode` checks `errorIds.has(id)` and `warningIds.has(id)` to decide which ring to show.

### Visual priority (highest → lowest)

1. **Blue ring** — node is selected (always wins)
2. **Red ring + `AlertCircle`** — configuration error
3. **Yellow ring + `AlertTriangle`** — disconnected from trigger
4. **Coloured status dot** — runtime execution status (default)

### Adding validation rules automatically updates the highlighting

Because `useNodeValidation` calls the same `validateWorkflow()` function used by the Run button, any rule you add to `REQUIRED_FIELDS` in `validateWorkflow.ts` will **automatically light up** the corresponding node card in red — no extra code needed.

---

## 🔧 10. MCP Tool Node — Visual Tool Picker

When you drop an **MCP Tool** node onto the canvas and click it, the `toolName` field is rendered as a searchable visual picker instead of a plain text input.

### What you see
- A search box to filter by tool name, title, or description
- Tools grouped by category, each card showing: **title**, **safety tier badge**, description snippet, and the exact `tool.name` in monospace
- Clicking a card selects it and shows it in a highlighted "selected" bar with a ✕ clear button
- The selected tool's exact API name is stored in `config.toolName` — no typos possible

### Why it matters
Previously you had to know the exact internal tool name (e.g., `get_server_status`) and type it correctly. Now you browse the live MCP tool registry the same way you pick tools for a ReAct Agent.

### Offline fallback
If the MCP server is unreachable, the component shows a message and you can fall back to typing the tool name manually.

---

## ⚡ 11. Expression Variable Picker — Autocomplete in Config Fields

Whenever you click a Condition node, a ForEach, a Save Conversation State, or any node with a **systemPrompt**, the property panel automatically shows an autocomplete dropdown as you type in expression fields.

### How to use it

- **Condition → `condition` field**: Type `node` and a dropdown appears showing all upstream node outputs (e.g., `node_3.intent`, `node_2.response`). Arrow keys to navigate, Enter/Tab to insert.
- **systemPrompt fields**: Type `{{` and you'll get Jinja2 suggestions like `{{ trigger.message }}` or `{{ node_2.response }}`.
- **responseNodeId / historyNodeId**: Typing shows only node IDs (`node_1`, `node_2`) — no path, just the ID.

The picker is **cursor-aware**: if your expression already has `node_2.intent == "` and you position the cursor back on `node_2`, the picker will replace only that token, not the whole line.

### How to add output fields for your new node

Open `frontend/src/lib/expressionVariables.ts` and find `NODE_OUTPUT_FIELDS`:

```ts
const NODE_OUTPUT_FIELDS: Record<string, string[]> = {
  "LLM Agent":   ["response", "input_tokens", "output_tokens"],
  "LLM Router":  ["intent"],
  // 👉 Add your node label and what fields it outputs at runtime:
  "Slack Notification": ["delivered_to", "final_text", "status"],
};
```

That's it — the autocomplete will immediately suggest `node_X.delivered_to`, `node_X.final_text`, etc. for any Slack Notification node on the canvas.

### How to add a new expression field

If your new node type has a field that should get autocomplete (e.g., a `filterExpression`), open `DynamicConfigForm.tsx` and add the key to the appropriate set:

```ts
const EXPRESSION_KEYS = new Set([
  "condition", "arrayExpression", "sessionIdExpression", "userMessageExpression",
  "filterExpression",  // 👈 add here for dot-path expressions
]);
```

---

## ↩️ 12. Undo / Redo — Canvas History

The workflow canvas supports full undo/redo with **Ctrl+Z** (undo) and **Ctrl+Y** or **Ctrl+Shift+Z** (redo). Toolbar buttons show the same actions with disabled state when history is empty.

### How it works

**File:** `frontend/src/store/flowStore.ts`

The store maintains two history stacks: `past[]` and `future[]`, each capped at 50 snapshots. A snapshot is `{ nodes: Node[], edges: Edge[] }`.

`_pushHistory()` is called automatically **before** every destructive action:

| Action | When snapshot is taken |
|--------|----------------------|
| `addNode()` | Before the node is added |
| `deleteNode()` | Before the node and its edges are removed |
| `onConnect()` | Before the new edge is created |
| `onNodesChange()` with drag | On first `dragging: true` event per drag (once per gesture) |
| `onNodesChange()` with remove | Before a node is removed via Delete key |
| `onEdgesChange()` with remove | Before an edge is removed via Delete key |

> `updateNodeData()` (property panel edits) is **not** snapshotted because it fires on every keystroke. Config changes can be reverted by simply editing the field back.

### Loading a workflow resets history

Calling `replaceGraph()` (used by load, new workflow, and example loaders) always resets both `past` and `future` to empty arrays — this prevents confusing undo across different workflows.

---

## 🛡️ 13. Pre-Run Validation — Catching Mistakes Before They Run

The orchestrator validates your workflow **in the browser** the moment you hit **Run**. This prevents common mistakes without wasting an API call.

### What gets checked?

**File:** `frontend/src/lib/validateWorkflow.ts`

| Check | What it catches | Severity |
|-------|----------------|----------|
| No trigger | Canvas has no Webhook or Schedule Trigger | Error |
| Disconnected node | A node exists on canvas but nothing connects it to a trigger | Warning |
| Empty required field | e.g., Condition has no expression, HTTP Request has no URL | Error |
| LLM Router: no intents | The `intents` array is empty | Error |
| Broken node reference | `responseNodeId` or `historyNodeId` points to a non-existent node | Error |

**Errors** block execution entirely. **Warnings** allow you to click **"Run Anyway"** (useful when you intentionally have a disconnected utility branch you're testing).

### How to add a validation rule for your new node

Open `frontend/src/lib/validateWorkflow.ts` and find `REQUIRED_FIELDS`:

```ts
const REQUIRED_FIELDS: Record<string, string[]> = {
  "Condition":               ["condition"],
  "HTTP Request":            ["url"],
  "MCP Tool":                ["toolName"],
  "ForEach":                 ["arrayExpression"],
  "Save Conversation State": ["responseNodeId"],
  // 👉 Add your new node label and required field names here:
  "Slack Notification":      ["channel", "messageTemplate"],
};
```

That's it! The validator will automatically show an error if those fields are empty when a user tries to run a workflow containing your node.

If your node has a **node-ID reference field** (a field where the user types another node's ID like `node_4`), also add it to `NODE_ID_REF_FIELDS`:

```ts
const NODE_ID_REF_FIELDS: Record<string, string[]> = {
  "Save Conversation State": ["responseNodeId"],
  "LLM Router":              ["historyNodeId"],
  // 👉 Add reference fields for your node:
  "Data Aggregator":         ["sourceNodeId"],
};
```

The validator will cross-check that the referenced node ID actually exists on the canvas.

## 🪪 14. Node ID Visibility — Copy a Node's ID from the Properties Panel

Every node on the canvas has a **machine ID** (`node_1`, `node_2`, …) that is separate from its human-readable label. When you write expressions like `node_3.intent` or set `responseNodeId` to `node_5`, you need this ID — but it was previously invisible unless you opened DevTools.

**File:** `frontend/src/components/sidebar/PropertyInspector.tsx`

### What was added

A grey `bg-muted` chip is now rendered at the very top of the Properties panel (above the Label field). It shows:

```
ID  node_3  [copy icon]
```

Clicking the copy icon writes the ID to the clipboard. The icon swaps to a green checkmark for 2 seconds as confirmation, then reverts.

### How it works

```tsx
const [idCopied, setIdCopied] = useState(false);
const handleCopyId = useCallback(() => {
  navigator.clipboard.writeText(selectedNode.id).then(() => {
    setIdCopied(true);
    setTimeout(() => setIdCopied(false), 2000);
  });
}, [selectedNode.id]);
```

The chip renders `selectedNode.id` (e.g., `node_3`) in a `font-mono` `<code>` span. The `Copy` / `Check` icons from `lucide-react` toggle based on `idCopied`.

### Typical workflow

1. Drop a **Save Conversation State** node onto the canvas.
2. Drop a **Webhook Trigger** node and connect it.
3. Click the **Webhook Trigger** node → Properties panel opens → ID chip shows `node_1`.
4. Click the copy icon next to `node_1`.
5. Click the **Save Conversation State** node → find the `responseNodeId` field → paste `node_1`.

The expression picker autocomplete on nodeId fields also surfaces this, but the chip is faster when you already know the node you want.

## 📝 15. Inline Field Help Text — Schema Descriptions in Config Forms

Every field in the Properties panel can now show a small grey hint line below the input. The hint comes directly from the `description` property in `shared/node_registry.json`.

**Files involved:**
- `shared/node_registry.json` — source of truth for all `description` strings
- `frontend/src/components/sidebar/DynamicConfigForm.tsx` — renders `<FieldHint>`

### Adding a description to an existing field

Open `shared/node_registry.json` and find the field you want to document:

```json
"config_schema": {
  "myField": {
    "type": "string",
    "default": "",
    "description": "Explain what this field does and give a concrete example"
  }
}
```

That's it — the frontend reads `description` from the schema and renders it automatically. No TypeScript changes needed.

### Adding a description to a new node's fields

When you add a new node type (see §1), add a `description` to every `config_schema` entry from the start. Good descriptions:
- Explain *what* the field controls
- Include a concrete example value (e.g., `e.g. trigger.session_id`)
- Mention units for numeric fields (e.g., `seconds`, `0–2 range`)
- Explain the difference between enum options when it isn't obvious

### How `DynamicConfigForm` renders hints

The `FieldHint` component is a single line:

```tsx
function FieldHint({ text }: { text: string }) {
  return (
    <p className="text-[10px] text-muted-foreground leading-snug">{text}</p>
  );
}
```

Every renderer branch in `DynamicConfigForm` ends with:

```tsx
{field.description && <FieldHint text={field.description} />}
```

This covers all nine field types: enum/Select, array/ToolMultiSelect, array/JSON textarea, object/JSON textarea, boolean/checkbox, number/Input, ToolSingleSelect, ExpressionInput, and plain string/Input.

## 🔁 16. ForEach & Merge — Canvas-Level UX Clarity

These two logic nodes now surface key config on the canvas card so users don't have to open the Properties panel to understand what they do.

**File:** `frontend/src/components/nodes/AgenticNode.tsx`

### Merge — strategy badge

The `waitAll`/`waitAny` strategy is shown as a secondary badge next to the category pill:

```tsx
{label === "Merge" && config?.strategy != null && (
  <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
    {String(config.strategy)}
  </Badge>
)}
```

This mirrors the pattern already used for agent model badges. If you add a new strategy option to `node_registry.json`, it appears automatically.

### ForEach — array expression hint

When `arrayExpression` is set, a small `↻ expr` line appears below the badge row:

```tsx
{label === "ForEach" && config?.arrayExpression && (
  <p className="text-[10px] font-mono text-muted-foreground truncate mt-1 leading-tight"
     title={String(config.arrayExpression)}>
    ↻ {String(config.arrayExpression)}
  </p>
)}
```

The line is `truncate` (with full text in `title` for hover) so it doesn't blow out the card width. When the field is empty (node just dropped, not configured) the line is hidden entirely.

### Adding similar hints for your own node

Follow the same pattern — guard with `label === "YourNodeLabel" && config?.yourField` and render a `<p>` or `<Badge>` inside `CardHeader` after the badge `<div>`. Keep the text short and `truncate` anything that could be long.

---

## ⚙️ 17. Deterministic Batch Execution — Reproducible Log Ordering

**Introduced in V0.9.3**

By default, when multiple nodes in a workflow are ready at the same time (e.g., two parallel branches after a fan-out), they are submitted to a `ThreadPoolExecutor` and their results are processed as each thread completes (`as_completed`). This maximises throughput but means execution logs may appear in a different order on each run.

For debugging, testing, or replay scenarios where you need the **same log sequence every time**, you can enable **deterministic mode**.

### How to enable it

Pass `"deterministic_mode": true` in the execute request body:

```json
POST /api/v1/workflows/{workflow_id}/execute
{
  "trigger_payload": { "input": "test value" },
  "deterministic_mode": true
}
```

Or from the frontend API client:

```ts
await api.executeWorkflow(workflowId, triggerPayload, /* deterministicMode */ true);
```

### What changes when it's on

| Aspect | Default (`false`) | Deterministic (`true`) |
|--------|-------------------|------------------------|
| Submission order | Arbitrary | Sorted by node ID |
| Result processing | `as_completed` (fastest thread first) | `.result()` in sorted order |
| Log write order | Non-deterministic | Stable across every run |
| Langfuse tag | — | `"deterministic"` tag added |
| Throughput | Maximum | Slightly lower for large parallel batches |

### When to use it

- **Integration tests** — assert exact log sequences without flaky ordering.
- **Replay / debugging** — compare two runs of the same workflow and diff their logs.
- **On-call investigations** — reproduce the exact execution sequence that caused a failure.
- Leave **off** (`false`) for all production hot-paths.

### Code path (for contributors)

**`backend/app/api/schemas.py`** — `ExecuteRequest.deterministic_mode: bool`

**`backend/app/api/workflows.py`** — passes the flag to `execute_workflow_task.delay()`

**`backend/app/workers/tasks.py`** — `execute_workflow_task(instance_id, deterministic_mode)` forwards it to `execute_graph`

**`backend/app/engine/dag_runner.py`** — `_execute_parallel` reads `deterministic_mode`:
- `True`: sorts `ready_nodes` → creates log entries in sorted order → submits in sorted order → calls `future.result()` in sorted order
- `False` (default): original `as_completed` path, unchanged

---

## 🧠 18. Reflection Node — Workflow Self-Assessment

**Introduced in V0.9.5**

The Reflection node lets a workflow "look back" at everything that has happened so far and ask an LLM to produce a structured JSON decision. A downstream Condition node then routes based on that decision.

### When to use it

- **Quality gate**: after several agent nodes, ask "is the output good enough, or should we escalate?"
- **Loop controller**: after a ForEach, ask "did enough items succeed, or do we retry?"
- **Routing decision**: given the full execution history, pick the next department/queue/action.

### How it works

**File:** `backend/app/engine/reflection_handler.py`

```
Reflection node executes
        │
        ▼
_build_execution_summary(context, max_history_nodes)
  ├── Collects last N node_* keys from context (insertion order = execution order)
  ├── Hard cap at 25 nodes regardless of config
  ├── Truncates each to 800 chars (prevents token explosion)
  └── Prepends trigger payload if present
        │
        ▼
render_prompt(reflectionPrompt, {**context, "execution_summary": summary})
  └── Jinja2 template — {{ execution_summary }} injects the history block
        │
        ▼
call_llm(provider, model, system_prompt, user_message, temperature=0.3)
  └── user_message always ends with "respond ONLY with a valid JSON object"
        │
        ▼
_parse_json_response(raw)
  ├── Strip ```json ... ``` fences
  ├── json.loads() → if dict, return; if primitive, wrap {"reflection": value}
  ├── Regex {…} extraction fallback
  └── Last resort: {"reflection": raw, "parse_error": True}
        │
        ▼
Returns {**parsed, "_usage": usage, "_raw_response": raw_response}
  └── dag_runner stores this under context["node_X"]
```

### Configuring a Reflection node

| Field | Default | What it does |
|-------|---------|-------------|
| `provider` | `google` | LLM provider |
| `model` | `gemini-2.5-flash` | Model variant |
| `reflectionPrompt` | *(required)* | Jinja2 system prompt; use `{{ execution_summary }}` |
| `outputKeys` | `[]` | Expected top-level keys in the JSON response — warns if absent |
| `maxHistoryNodes` | `10` | How many recent node outputs to include in the summary |
| `temperature` | `0.3` | Lower = more deterministic JSON output |
| `maxTokens` | `1024` | Enough for structured JSON; increase for verbose responses |

### Example prompt template

```jinja2
You are a quality-control engine for an IT support workflow.
Review the execution history and decide whether the issue has been resolved.

{{ execution_summary }}

Respond with a JSON object with exactly these keys:
- "resolved": true or false
- "confidence": 0.0–1.0
- "next_action": one of "close_ticket", "escalate", "retry_diagnosis"
- "reason": one-sentence explanation
```

### Example downstream condition

```
node_5.resolved == True          → close ticket branch
node_5.next_action == "escalate" → escalate branch
```

### Key design constraint: read-only

The Reflection node **never mutates `context`**. It only returns a value. The dag_runner stores that value under the node's own key. This means:

- Earlier node outputs are never overwritten
- There is no dynamic graph mutation (the DAG is Kahn-sorted upfront)
- The pattern is fully composable with ForEach, HITL, and Condition nodes

### Code path (for contributors)

1. `node_handlers.dispatch_node()` matches `label == "Reflection"` and imports `_handle_reflection` from `reflection_handler.py`
2. `_handle_reflection()` reads config, builds summary, renders prompt, calls LLM
3. `_parse_json_response()` normalises the raw text to a dict
4. `record_generation()` logs the call to Langfuse under `reflection:{provider}/{model}`
5. dag_runner receives `{**parsed, "_usage": ..., "_raw_response": ...}` and stores it in context

### Frontend integration

- `shared/node_registry.json` — `reflection` type under `agent` category with full `config_schema`
- `validateWorkflow.ts` — `"Reflection": ["reflectionPrompt"]` in `REQUIRED_FIELDS` blocks execution if prompt is empty
- `expressionVariables.ts` — `"Reflection": ["_raw_response"]` in `NODE_OUTPUT_FIELDS`; user-defined `outputKeys` fields (e.g., `node_X.next_action`) are also accessible at runtime but can't be statically enumerated

---

## 💾 19. Checkpointing — Per-Node Context Snapshots

**Introduced in V0.9.6**

Every time a node completes successfully, the engine automatically saves a **checkpoint** — a full snapshot of the execution context at that exact moment. This lets you inspect what the workflow "knew" after each step, without having to run it again.

### What a checkpoint contains

A checkpoint stores the `context_json` minus all internal runtime keys (anything starting with `_` — like `_trace`, `_loop_item`, `_loop_index`). What remains is:
- `trigger` — the original webhook/schedule payload
- `node_1`, `node_2`, … — outputs from every node that has completed up to that point

### Where checkpoints are written

**File:** `backend/app/engine/dag_runner.py` → `_save_checkpoint(db, instance_id, node_id, context)`

```python
# After a single node completes (execute_single_node):
log_entry.completed_at = _utcnow()
db.commit()
_save_checkpoint(db, instance.id, node_id, context)   # ← here

# After a parallel batch node completes (_apply_result):
context[node_id] = output
log_entry.status = "completed"
log_entry.completed_at = _utcnow()
_save_checkpoint(db, instance.id, node_id, context)   # ← here
```

**ForEach iterations** are covered automatically because they call `_execute_single_node` for each iteration — one checkpoint per iteration per downstream node.

### Non-fatal design

```python
def _save_checkpoint(db, instance_id, node_id, context):
    try:
        clean_context = {k: v for k, v in context.items() if not k.startswith("_")}
        db.add(InstanceCheckpoint(instance_id=instance_id, node_id=node_id,
                                  context_json=clean_context, saved_at=_utcnow()))
        db.commit()
    except Exception as exc:
        logger.warning("Failed to save checkpoint: %s", exc)
        db.rollback()   # ← never propagated upward
```

If the checkpoint write fails (e.g., transient DB error), execution continues uninterrupted. Only a warning appears in the logs.

### Reading checkpoints via the API

```
GET /api/v1/workflows/{workflow_id}/instances/{instance_id}/checkpoints
```
Returns a list ordered by `saved_at` — each entry has `id`, `instance_id`, `node_id`, `saved_at`. No context payload.

```
GET /api/v1/workflows/{workflow_id}/instances/{instance_id}/checkpoints/{checkpoint_id}
```
Returns the full checkpoint including `context_json`.

### Database

**Table:** `instance_checkpoints`
**Migration:** `alembic/versions/0004_instance_checkpoints.py`
**Model:** `app/models/workflow.py` → `InstanceCheckpoint`

Rows are cascade-deleted when the parent `WorkflowInstance` is deleted.

---

## 🔬 20. Checkpoint-aware Langfuse — Linking Traces to DB Snapshots

**Introduced in V0.9.7**

After Item 4 introduced DB checkpoints, Item 5 connects them to Langfuse so that every node span in the Langfuse UI carries a direct reference to its DB context snapshot.

### How it works

**`_save_checkpoint` now returns the checkpoint UUID:**

```python
# Before (returned None):
_save_checkpoint(db, instance.id, node_id, context)

# After (returns str UUID or None):
checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
```

**For sequential nodes** (`_execute_single_node`), the span is still open when the checkpoint is saved. The checkpoint_id is passed directly to `span.update()`:

```python
checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
span_meta = {"status": "completed", "has_output": output is not None}
if checkpoint_id:
    span_meta["checkpoint_id"] = checkpoint_id
span.update(output=span_meta)
```

In Langfuse, the node's span now shows `checkpoint_id: "abc123-..."` in its output metadata. You can copy this UUID and look up the exact context snapshot via:
```
GET /api/v1/workflows/{wf_id}/instances/{inst_id}/checkpoints/{checkpoint_id}
```

**For parallel nodes** (`_apply_result`), the Langfuse span has already exited by the time `_apply_result` runs. Instead, the checkpoint_id is embedded in the execution log entry's `output_json`:

```python
checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
log_entry.output_json = (
    {**(output or {}), "_checkpoint_id": checkpoint_id}
    if checkpoint_id else output
)
```

This means the checkpoint_id is accessible via `GET /instances/{id}` → `logs[i].output_json._checkpoint_id`.

### `span_node` signature update

`observability.py` → `span_node()` now accepts an optional `checkpoint_id` kwarg:

```python
@contextmanager
def span_node(
    parent,
    *,
    node_id: str,
    node_type: str,
    node_label: str = "",
    input_data: Any = None,
    checkpoint_id: str | None = None,   # ← new
) -> Generator:
```

When `checkpoint_id` is provided at span creation time, it is written into the Langfuse span's metadata immediately. This kwarg is available for any future caller that has the checkpoint_id before the span opens (e.g., resume-from-checkpoint scenarios in Item 7).

### Debugging workflow: sequential node

1. Open Langfuse → find the workflow trace
2. Click a node span
3. In **Output metadata**, find `checkpoint_id`
4. Call `GET .../checkpoints/{checkpoint_id}` → get exact context snapshot at that point
5. Compare with the next checkpoint to see exactly what the node added

### Debugging workflow: parallel node

1. Call `GET .../instances/{id}` → find the node's log entry
2. Read `output_json._checkpoint_id`
3. Call `GET .../checkpoints/{checkpoint_id}` → full snapshot

### Why different for sequential vs parallel?

In `_execute_single_node`, the node runs inside a `with span_node(...) as span:` block. The checkpoint is saved AFTER `dispatch_node` returns but BEFORE the `with` block exits — so the span is still live.

In `_execute_parallel`, each node runs in a `ThreadPoolExecutor` thread. The thread's `_run_node` function creates its own `with span_node(...)` block, which exits when the thread returns. The main thread then collects the future result in `_apply_result` — by then the span is already committed to Langfuse. We embed the checkpoint_id in the execution log as a fallback linkage mechanism.

---

## 🌊 21. Rich Token Streaming — Live LLM Output in the Browser

**Introduced in V0.9.8**

LLM Agent nodes stream tokens to the browser in real time as the model generates them — no waiting for the full response.

### Architecture

```
Celery worker (LLM call)               Redis                FastAPI SSE
────────────────────────               ─────                ──────────
  stream_google / stream_openai
  / stream_anthropic
        │
        │ each token arrives
        ▼
  publish_token(instance_id, node_id, token)
        │                              │
        └──────────▶ PUBLISH ─────────▶ orch:stream:{instance_id}
                                       │
                                       │ SUBSCRIBE
                                       ◀─────────── _subscribe_tokens task
                                                         │
                                                   asyncio.Queue
                                                         │
                                                   event_generator loop
                                                         │
                                              event: token
                                              data: {"node_id": "node_2",
                                                     "token": "The ",
                                                     "done": false}
                                                         │
                                                    Browser SSE
```

### File: `backend/app/engine/streaming_llm.py`

Three streaming functions — `stream_google`, `stream_openai`, `stream_anthropic` — each:
1. Call the provider's streaming API
2. Accumulate the full text
3. Call `publish_token(instance_id, node_id, token)` for each chunk
4. Call `publish_stream_end(instance_id, node_id)` after the last chunk
5. Return the same `{response, usage, model, provider}` dict as the non-streaming path

Redis publish failures are caught and logged as warnings — execution is never blocked.

### File: `backend/app/engine/llm_providers.py`

`call_llm_streaming(...)` routes to the streaming variants when `instance_id` and `node_id` are non-empty. Falls back to `call_llm` silently if either is empty (e.g., Reflection node calls, ReAct loop).

### How node_id gets into the handler

```python
# execute_graph — once per execution
context["_instance_id"] = str(instance.id)

# _execute_single_node — before each sequential node
context["_current_node_id"] = node_id

# _handle_agent reads:
instance_id = context.get("_instance_id", "")
node_id = context.get("_current_node_id", "")
result = call_llm_streaming(..., instance_id=instance_id, node_id=node_id)
```

### File: `backend/app/api/sse.py`

```python
token_queue: asyncio.Queue = asyncio.Queue()
redis_task = asyncio.create_task(_subscribe_tokens(instance_id, token_queue))

while True:
    # Drain token queue (non-blocking, no sleep needed)
    while not token_queue.empty():
        token_msg = token_queue.get_nowait()
        yield f"event: token\ndata: {json.dumps(token_msg)}\n\n"

    # DB poll every 1s for log/status/done events
    ...
    await asyncio.sleep(1.0)
```

`_subscribe_tokens` uses `redis.asyncio` (bundled in `redis>=5.0.0` — no new dependency) and terminates cleanly when the asyncio task is cancelled.

### Frontend

| Layer | Change |
|-------|--------|
| `api.ts` | `streamInstance` gains optional `onToken` callback for `event: token` events |
| `workflowStore.ts` | `streamingTokens: Record<string, string>` state; accumulated per `node_id`; cleared on execution start and done |
| `ExecutionPanel.tsx` | `LogEntry` receives `streamingText` prop; running nodes show a pulsing blue dot + live text in expanded view |

### Adding streaming support to a new node type

1. In your handler (`node_handlers.py`), read `instance_id` and `node_id` from context
2. Call `call_llm_streaming(...)` instead of `call_llm(...)`
3. The streaming infrastructure handles Redis publish automatically

For node types that should **not** stream (e.g., LLM Router which needs a deterministic 64-token classification response), continue using `call_llm` directly — `call_llm_streaming` is not called unless `instance_id` and `node_id` are provided.
