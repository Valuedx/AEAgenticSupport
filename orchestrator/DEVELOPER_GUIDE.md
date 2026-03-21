# AE AI Hub — Agentic Orchestrator Developer Guide

**Version:** 0.9
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
