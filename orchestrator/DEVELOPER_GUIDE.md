# AE AI Hub — Agentic Orchestrator Developer Guide

**Version:** 0.9.1
**Last updated:** 2026-03-21

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

## 🔧 7. MCP Tool Node — Visual Tool Picker

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

## ⚡ 8. Expression Variable Picker — Autocomplete in Config Fields

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

## ↩️ 9. Undo / Redo — Canvas History

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

## 🛡️ 10. Pre-Run Validation — Catching Mistakes Before They Run

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
