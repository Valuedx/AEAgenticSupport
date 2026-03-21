# AE AI Hub — Agentic Orchestrator Developer Guide

**Version:** 0.9
**Last updated:** 2026-03-21

Welcome to the Developer Guide for the AE AI Hub Agentic Orchestrator! This document provides detailed examples and instructions for extending the orchestrator, writing custom expressions, and using advanced Agentic orchestration features.

---

## Table of Contents

1. [Adding a Custom Node Type](#1-adding-a-custom-node-type)
2. [Expression Language (`safe_eval`) Guide](#2-expression-language-safe_eval-guide)
3. [Using ReAct Agents & MCP Tools](#3-using-react-agents--mcp-tools)
4. [Advanced Execution: Loops & Retries](#4-advanced-execution-loops--retries)
5. [Environment Variables & The Vault](#5-environment-variables--the-vault)
6. [Human-in-the-Loop (Suspension)](#6-human-in-the-loop-suspension)

---

## 1. Adding a Custom Node Type

The orchestrator uses a **data-driven UI** approach. Adding a new node type requires **zero frontend code changes**. You only need to define the schema in the shared registry and write a backend handler.

### Step 1. Define the Schema (`node_registry.json`)

Open `shared/node_registry.json` and add your node definition. 

**Example: Adding a "Slack Notification" Action Node**

```json
{
  "type": "slack_notification",
  "category": "action",
  "label": "Slack Notification",
  "description": "Send a message to a Slack channel",
  "icon": "MessageSquare",
  "color": "bg-blue-100 border-blue-300",
  "config_schema": {
    "channel": {
      "type": "string",
      "description": "The Slack channel name or ID (e.g., #alerts)"
    },
    "messageTemplate": {
      "type": "string",
      "description": "The message body (supports Jinja2 {{ context.variable }})"
    },
    "urgent": {
      "type": "boolean",
      "description": "Send with high priority",
      "default": false
    }
  }
}
```

*Note: The frontend will automatically generate a form with a text input for `channel`, a textarea for `messageTemplate`, and a checkbox for `urgent`.*

### Step 2. Implement the Backend Handler

Open `backend/app/engine/node_handlers.py`. Add a handler function and register it in `dispatch_node`.

```python
from app.engine.prompt_template import render_template

async def _handle_slack_notification(node_data: dict, context: dict, tenant_id: str) -> dict:
    config = node_data.get("config", {})
    channel = config.get("channel", "#general")
    template = config.get("messageTemplate", "")
    urgent = config.get("urgent", False)
    
    # 1. Render the message template using upstream context
    message = render_template(template, context)
    
    # 2. Add 'URGENT' prefix if configured
    if urgent:
        message = f"🚨 *URGENT* 🚨\n{message}"
        
    # 3. Call your internal API or external service
    # e.g., await some_slack_client.post_message(channel, message)
    print(f"Sending to {channel}: {message}")
    
    # 4. Return the output to be added to the execution context
    return {
        "status": "sent",
        "channel": channel,
        "delivered_message": message
    }
```

Finally, wire it up in `dispatch_node(...)`:

```python
# Inside dispatch_node:
if node_category == "action":
    if label == "Slack Notification":
        return await _handle_slack_notification(node_data, context, tenant_id)
    # ... other action nodes
```

---

## 2. Expression Language (`safe_eval`) Guide

The Orchestrator uses a restricted AST-based expression evaluator (`safe_eval`) for `Condition` and `ForEach` nodes. `eval()` and `exec()` are strictly prohibited for security.

### Context Variables

You can access any upstream node's output using dot notation. For example, if a previous node with ID `node_2` returned `{"status": 200, "data": {"user": "Alice"}}`, you can reference it as:
`node_2.data.user`

### Supported Operations

**1. Comparisons & Logic**
```python
node_1.status == 200 and node_1.confidence > 0.8
not trigger.is_test or trigger.override == true
"error" in node_3.logs
```

**2. Ternary Expressions**
```python
"High" if trigger.priority == 1 else "Normal"
```

**3. Whitelisted Functions (V0.9+)**
You can use these safe built-in functions:
*   `len(x)`: Length of a string or array.
*   `str(x)`, `int(x)`, `float(x)`, `bool(x)`: Type conversion.
*   `min(x, y)`, `max(x, y)`, `abs(x)`: Math operations.
*   `lower(s)`, `upper(s)`, `strip(s)`: String manipulation.
*   `startswith(s, prefix)`, `endswith(s, suffix)`: String matching.
*   `contains(collection, item)`: Same as the `in` operator.
*   `matches(string, regex)`: Safe regex matching (e.g., `matches(node_1.email, r".*@company\.com")`).

**4. Whitelisted Methods (V0.9+)**
You can call safe methods directly on objects:
*   **Strings:** `.lower()`, `.upper()`, `.strip()`, `.split(sep)`, `.startswith(prefix)`, `.endswith(suffix)`, `.isdigit()`, `.replace(old, new)`.
*   **Dictionaries/Objects:** `.get(key, default)`, `.keys()`, `.values()`.

**Example: Complex Condition Logic**
```python
# Check if the AI's response indicates an issue AND the username ends with @admin.com
lower(node_2.sentiment) == "negative" and trigger.user.endswith("@admin.com")

# Ensure an array has items and grab a safe dict value
len(node_3.results) > 0 and node_3.metadata.get("urgent", false) == true
```

---

## 3. Using ReAct Agents & MCP Tools

The **ReAct Agent** node gives an LLM the ability to autonomously loop, reason, and call tools. 

### Tool Binding

In the flow builder, the ReAct Agent has a `tools` configuration property (a multi-select dropdown hooked up to the MCP server).
*   **Explicit List:** If you select specific tools (e.g., `["ae.request.get_status", "ae.request.restart"]`), the agent is sandboxed and can *only* use those tools.
*   **Auto-Discovery:** If you leave the `tools` list **empty**, the engine will automatically discover and pass **all available tools** (106+) from the MCP server to the agent at runtime.

### The Run Loop
The `react_loop.py` handles the execution. It will:
1.  Provide the LLM with your `systemPrompt` and the execution context.
2.  If the LLM decides to call a tool, the engine pauses the LLM, connects to the MCP server (`call_tool`), gets the result, appends it to the conversation history, and calls the LLM again.
3.  This loops until the LLM returns a final text answer (or hits the hard cap of 25 iterations).

**Example System Prompt for an IT Agent:**
```text
You are an IT Diagnostic Agent.
The user reported an issue: {{ trigger.issue_description }}
Request ID: {{ trigger.request_id }}

1. Call the 'ae.request.get_logs' tool.
2. Analyze the output.
3. If the error mentions 'timeout', call 'ae.service.restart'.
4. Provide a final summary of your actions.
```

---

## 4. Advanced Execution: Loops & Retries

### The ForEach Loop
The **ForEach** node (introduced in V0.9) lets you run a subgraph multiple times.

*   **`arrayExpression`**: A safe_eval string pointing to a list. Example: `node_1.extracted_emails`
*   **`itemVariable`**: The name you want to assign to the current item. Example: `email_address`

**How it works:**
If `node_1` returns `["alice@test.com", "bob@test.com"]`, the `ForEach` node will trigger all of its immediately downstream nodes 2 times.
In the downstream nodes (like an LLM Agent), you can reference the current item in templates:
```text
Write a personalized greeting for {{ email_address }}.
(This is iteration {{ _loop_index }})
```

The output of the ForEach operation is collected into an array named `forEach_results` in the context.

### Retry from Failed Node
If a workflow fails halfway through (e.g., an external API returns a 500 error), you don't have to restart from the beginning and waste LLM tokens.

**API Endpoint:** `POST /api/v1/workflows/{workflow_id}/instances/{instance_id}/retry`
The engine will:
1. Reload the context up to the point of failure.
2. Re-parse the DAG and skip all nodes that have already successfully executed.
3. Execute the failed node and continue downstream.

You can trigger this programmatically or by clicking the **Retry** button in the frontend execution panel limit.

---

## 5. Environment Variables & The Vault

Never hardcode secrets (API keys, passwords, bearer tokens) in the visual builder's Web UI.

1.  **Store the Secret:** Use the backend Vault API to store encrypted secrets per-tenant.
2.  **Access the Secret:** In any text field in the Visual Builder (like a node's URL path, headers, or prompts), use the `{{ env.SECRET_NAME }}` syntax.

**Example: HTTP Action Node Configuration**
```json
{
  "url": "https://api.mycrm.com/v1/users",
  "headers": {
    "Authorization": "Bearer {{ env.CRM_PROD_API_KEY }}"
  }
}
```
Before the node executes, `node_handlers.py` calls `resolve_config_env_vars()`. It looks up `CRM_PROD_API_KEY` in the Fernet-encrypted database vault for the current tenant and seamlessly injects it into the configuration dict.

---

## 6. Human-in-the-Loop (Suspension)

Sometimes a workflow must pause until a human approves an action (e.g., deleting a database or restarting a production process).

### Design Time
Add a generic Action node, and configure the `approvalMessage` property.
```text
"Please approve the restart of server {{ trigger.server_name }}."
```

### Run Time
When the engine reaches this node, it detects the `approvalMessage`.
1. It records the current context in the database.
2. Changes the instance status to `suspended`.
3. The worker thread finishes and dies (freeing up resources).

### Resumption
An external application (like an MS Teams bot or email webhook) displays the approval message to the user. When approved, that system makes an API call back to the orchestrator:

```http
POST /api/v1/workflows/callback
{
  "instance_id": "123e4567-e89b-12d3...",
  "payload": {
    "approved": true,
    "user": "admin@company.com"
  }
}
```

The Celery worker resumes the graph instantly from where it left off, and downstream nodes can reference the user's decision via `node_id.approved`.
