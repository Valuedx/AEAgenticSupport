# AEAgenticSupport — Complete Knowledge Transfer Guide

> **Purpose**: This document is a comprehensive KT (Knowledge Transfer) guide for developers joining the project. It explains the entire repository structure, what every file does, how data flows through the system, and how all the pieces connect.

---

## Table of Contents

1. [What Is This Project?](#1-what-is-this-project)
2. [Architecture Overview](#2-architecture-overview)
3. [How A Message Flows (End-to-End)](#3-how-a-message-flows-end-to-end)
4. [Directory & File Map](#4-directory--file-map)
   - [Root Files](#41-root-files)
   - [config/](#42-config-directory)
   - [gateway/](#43-gateway-directory)
   - [agents/](#44-agents-directory)
   - [tools/](#45-tools-directory)
   - [mcp_server/](#46-mcp_server-directory)
   - [rag/](#47-rag-directory)
   - [state/](#48-state-directory)
   - [security/](#49-security-directory)
   - [frontend/](#410-frontend-directory)
   - [templates/](#411-templates-directory)
   - [scripts/](#412-scripts-directory)
   - [docs/](#413-docs-directory)
5. [Key Concepts You Must Know](#5-key-concepts-you-must-know)
6. [How to Run the Application](#6-how-to-run-the-application)
7. [Common Debugging Tips](#7-common-debugging-tips)

---

## 1. What Is This Project?

**AEAgenticSupport** is an AI-powered IT Operations Assistant built for the **AutomationEdge** platform. It uses Google's Gemini LLM to:

- Diagnose workflow failures (e.g., "Why did the payroll bot fail?")
- Check the status of running processes
- Restart or resubmit failed executions (with human approval)
- Provide root cause analysis (RCA) reports
- Execute AutomationEdge workflows on behalf of the user

Think of it as a **smart chatbot for IT ops teams** that can actually *do things* — check logs, restart bots, and investigate issues — not just answer questions.

---

## 2. Architecture Overview

The system is built in layers. Each layer has a specific job:

```
┌─────────────────────────────────────────────────────────────┐
│                      FRONTEND LAYER                         │
│   webchat.html │ admin_console.html │ aistudio_webchat.html │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP (REST + SSE)
┌────────────────────────▼────────────────────────────────────┐
│                    SERVER LAYER                              │
│              agent_server.py (Flask)                         │
│         /chat  /chat/stream  /api/*  /admin                 │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                   ENTRY POINT                                │
│                     main.py                                  │
│         handle_chat_message() → MessageGateway               │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                   GATEWAY LAYER                              │
│              gateway/message_gateway.py                      │
│    Classifies intent → Routes to Agent Router / Orchestrator │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    AGENT LAYER                               │
│  orchestrator.py │ agent_router.py │ diagnostic_agent.py     │
│  rca_agent.py │ remediation_agent.py │ approval_gate.py      │
└────────────────────────┬────────────────────────────────────┘
                         │ calls tools via LLM function-calling
┌────────────────────────▼────────────────────────────────────┐
│                    TOOL LAYER                                │
│  registry.py │ bootstrap.py │ catalog.py │ hydrator.py       │
│  status_tools.py │ remediation_tools.py │ log_tools.py       │
│  automationedge_client.py │ mcp_tools.py                     │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│               KNOWLEDGE & STATE LAYER                        │
│  rag/engine.py │ state/conversation_state.py                 │
│  state/app_config.py │ state/issue_tracker.py                │
│  PostgreSQL + pgvector │ JSON stores                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. How A Message Flows (End-to-End)

Here is exactly what happens when a user sends **"Why did the payroll bot fail?"**:

### Step 1 — User sends a message
The **frontend** (`webchat.html`) sends a POST request to `/api/webchat/chat/stream`.

### Step 2 — Flask receives it
`agent_server.py` validates the session, extracts the message, and calls `handle_chat_message()` from `main.py`.

### Step 3 — main.py delegates to the Gateway
`main.py` calls `gateway.process_message()`. The `MessageGateway` class:
- Loads or creates a `ConversationState` for this session.
- Checks if the agent is already working (handles concurrent messages).
- Classifies intent (is this a new request? a follow-up? an approval?).
- Routes to either the **Agent Router** (multi-agent) or the **Orchestrator** (fallback).

### Step 4 — Orchestrator investigates
The `Orchestrator` (in `agents/orchestrator.py`) is the brain:
1. **Classifies the issue** using `IssueTracker`.
2. **Discovers relevant tools** via RAG search (e.g., `check_workflow_status`, `list_recent_failures`).
3. **Builds a system prompt** with context, SOP knowledge, and tool guidance.
4. **Calls the Gemini LLM** with function declarations for the selected tools.
5. **Executes tool calls** that the LLM requests (e.g., fetching logs from AutomationEdge).
6. **Loops** — the LLM sees the tool results and either calls more tools or writes a final response.
7. **Approval Gate** — if the LLM wants to do a risky action (e.g., restart), the system pauses and asks the user for permission.

### Step 5 — Response sent back
The response string travels back up: `Orchestrator` → `Gateway` → `main.py` → `agent_server.py` → frontend.

---

## 4. Directory & File Map

### 4.1 Root Files

| File | What It Does |
|:-----|:-------------|
| `main.py` | **Entry point for AI Studio**. Initializes logging, the message gateway, and tool bootstrapping. Exposes `handle_chat_message()` which AI Studio calls for every chat message. Also has a CLI mode for testing. |
| `agent_server.py` | **Standalone Flask HTTP server** (1800+ lines). Hosts all web endpoints: `/chat`, `/chat/stream`, `/health`, `/admin`, `/webchat`, `/docs`, and all admin/API routes. This is what you run when deploying independently. |
| `setup_db.py` | **Database initializer**. Creates PostgreSQL tables and pgvector extensions needed by the RAG engine and conversation state. Run this once during setup. |
| `schema.sql` | **Raw SQL schema** for the PostgreSQL database. Contains table definitions for conversations, tool interactions, embeddings, etc. |
| `run_rag_index.py` | **Standalone RAG indexer**. Reads SOP documents from `documents/` and indexes them into the vector database. Run this when you add new knowledge documents. |
| `requirements.txt` | **Python dependencies**. Lists all pip packages needed (`flask`, `google-genai`, `psycopg`, `python-dotenv`, etc.). |
| `pytest.ini` | **Test configuration**. Points pytest to the `tests/` directory. |
| `start_servers.bat` | **Windows startup script**. Launches both the agent server and the MCP server in parallel. |
| `.env` | **Environment variables**. Contains all secrets and configuration (API keys, database URLs, model names). **Never commit this to git.** |
| `.gitignore` | Specifies files/folders that Git should ignore. |
| `README.md` | Project overview and setup instructions. |
| `RUN.md` | Step-by-step guide for running the application locally. |

---

### 4.2 `config/` Directory

This directory holds all **configuration, database connections, and LLM client setup**.

| File | What It Does |
|:-----|:-------------|
| `settings.py` | **Central configuration hub**. Reads ALL environment variables from `.env` and exposes them as a single `CONFIG` dictionary. Every other module imports from here. Contains settings for: AE API connection, Vertex AI model, PostgreSQL, RBAC, scheduling, logging, etc. |
| `db.py` | **Database connection manager**. Creates and manages the PostgreSQL connection pool using `psycopg`. Provides `get_db_connection()` used by RAG, conversation state, and tool interaction logging. |
| `llm_client.py` | **LLM client wrapper**. Wraps Google's Vertex AI / Gemini API. Provides `llm_client.chat()` for simple text generation and `llm_client.chat_with_tools()` for function-calling. Handles retries, token counting, and model configuration. |
| `logging_setup.py` | **Logging configuration**. Sets up two loggers: `app_logger` (general) and `audit_logger` (security events). Supports JSON-format logs for production. Log files go to `logs/`. |
| `metrics.py` | **Metrics collector**. Tracks tool execution counts, latencies, success rates, and error frequencies. Used by the admin dashboard for operational visibility. |
| `classification_signals.py` | **Issue classification keywords**. Lists of phrases that help the system classify user messages (e.g., "failed", "stuck", "not working" → failure issue). |
| `client_policy.py` | **Client-specific policy engine**. Loads JSON policy files from `client_policies/` to customize agent behavior per tenant/org. Generates prompt addendums for specific clients. |
| `client_policies/` | **Directory of JSON policy files**. Each file defines custom rules, wording, and restrictions for a specific client organization. |
| `related_applications.py` | **Related application registry**. Maps workflows to their dependent external systems (e.g., "payroll bot" depends on "SAP" and "HRMS"). Used during RCA to check upstream/downstream health. |
| `related_applications.json` | **Static config** listing known related applications and their health-check endpoints. |

---

### 4.3 `gateway/` Directory

The gateway is the **front door** — it receives raw user messages and decides what to do with them.

| File | What It Does |
|:-----|:-------------|
| `message_gateway.py` | **Message routing and concurrency manager**. The `MessageGateway` class: (1) Creates/loads conversation sessions. (2) If agents are NOT busy → dispatches immediately. (3) If agents ARE busy → classifies the new message as ADDITIVE, INTERRUPT, CANCEL, APPROVAL, or NEW_REQUEST. (4) Manages a per-conversation thread lock to prevent race conditions. (5) Lazily initializes the multi-agent router and specialist agents (Diagnostic, Remediation, RCA). |
| `progress.py` | **Progress callback handler**. The `ProgressCallback` class sends real-time status updates to the frontend during long operations (e.g., "Checking workflow logs...", "Analyzing 5 recent failures..."). Filters messages based on user role (business vs. technical). |

---

### 4.4 `agents/` Directory

This is the **brain of the system** — where intelligent decision-making happens.

| File | What It Does |
|:-----|:-------------|
| `orchestrator.py` | **THE MAIN FILE** (~5300 lines, the largest in the repo). The `Orchestrator` class manages the full investigation/remediation cycle. Key responsibilities: (1) Builds the system prompt with context, SOPs, and tool guidance. (2) Runs the LLM ↔ tool-call loop (up to `MAX_AGENT_ITERATIONS`). (3) Handles approval workflows (pause → ask user → resume). (4) Manages issue lifecycle (new → investigating → resolved/escalated). (5) Filters responses based on user persona (business vs. technical). (6) Handles date parsing, parameter collection, and follow-up actions. |
| `agent_router.py` | **Multi-agent routing**. Routes incoming messages to the most appropriate specialist agent based on the type of request. Uses LLM-based classification to decide: "Is this a diagnostic question? → Send to DiagnosticAgent". Falls back to the Orchestrator if no specialist matches. |
| `agent_registry.py` | **Agent catalog**. Registers and manages all available specialist agents. Provides `get()`, `register()`, `list_agents()`, and `list_agent_info()`. |
| `base_agent.py` | **Abstract base class** for all agents. Defines the interface every agent must implement: `handle()`, `can_handle()`, `agent_id`, `capabilities`, etc. |
| `orchestrator_agent.py` | **Orchestrator wrapped as an agent**. Adapts the legacy `Orchestrator` class to the `BaseAgent` interface so it can participate in the multi-agent routing system. |
| `diagnostic_agent.py` | **Specialist: Diagnostics**. Handles specific diagnostic queries (e.g., "Show me the last 10 failures of XYZ bot"). Uses a focused subset of tools. |
| `remediation_agent.py` | **Specialist: Remediation**. Handles action-oriented requests (e.g., "Restart the payroll bot"). Focuses on execution and recovery tools. |
| `rca_agent.py` | **Specialist: Root Cause Analysis**. Performs deep investigation into *why* something failed. Correlates logs, timelines, and dependency data to produce RCA reports. |
| `approval_gate.py` | **Approval workflow engine**. When a tool is classified as risky (`medium_risk` or `high_risk`), this module: (1) Creates an approval request with a unique ID. (2) Formats a human-readable approval prompt. (3) Validates user responses ("yes"/"no"/"approve"/"reject"). (4) Logs all approval decisions for audit. (5) Enforces RBAC — only authorized users can approve certain actions. |
| `escalation.py` | **Escalation logic**. When the agent cannot resolve an issue (e.g., recurrence threshold exceeded, manual intervention needed), this module: (1) Generates an escalation summary. (2) Formats it for human operators. (3) Marks the issue as escalated in the tracker. |
| `scheduler.py` | **Task scheduler**. Runs periodic background tasks: (1) Health checks on monitored workflows. (2) Daily summary reports. (3) Stale issue cleanup. (4) Workflow access sync. Uses an internal scheduling engine with configurable intervals. |

---

### 4.5 `tools/` Directory

Tools are the **hands of the agent** — they let the AI *do things* in the real world.

| File | What It Does |
|:-----|:-------------|
| `base.py` | **Core data structures**. Defines `ToolDefinition` (name, description, parameters, tier, use_when, avoid_when, input_examples) and `ToolResult` (success, data, error). Every tool in the system uses these classes. Also provides `get_ae_client()` helper. |
| `registry.py` | **Central tool registry** (~800 lines). The `ToolRegistry` class: (1) Stores all registered tools (definition + handler function). (2) Provides `execute(tool_name, **args)` to run a tool safely. (3) Provides `discover_tools(query)` for RAG-based tool search. (4) Manages dynamic workflow-backed tools (loaded from AutomationEdge). (5) Generates Vertex AI function declarations for the LLM. (6) Handles tool interaction logging and feedback tracking. |
| `bootstrap.py` | **Startup loader**. Called once during application initialization. Imports all static tool modules listed in `_STATIC_TOOL_MODULES`, reloads dynamic AE workflow tools, syncs agent-tool links, and indexes all tool metadata into RAG for discovery. |
| `catalog.py` | **Tool catalog model**. Defines `ToolCatalogEntry` — a unified metadata record for tools from all sources (custom, MCP, AutomationEdge workflows). Feeds into RAG indexing and `discover_tools`. |
| `hydrator.py` | **Turn-local tool hydration**. Converts selected catalog entries into live callable tool definitions for a single conversation turn. Only hydrates the top-ranked tools to keep the LLM prompt small. |
| `ranker.py` | **Tool ranking engine**. Scores candidate tools using multiple signals: RAG similarity score, source preference, risk tier, latency class, mutation penalty, and historical success/failure rates. |
| `executor.py` | **Tool execution wrapper**. Executes hydrated tool handlers safely. Handles timeouts, error capture, and result normalization. Preserves audit logging and RBAC checks. |
| `general_tools.py` | **General-purpose tools**. Includes: `discover_tools` (search the tool catalog), `check_workflow_status` (get workflow execution status), `list_user_workflows` (show workflows assigned to a user), and more. |
| `status_tools.py` | **Status and monitoring tools** (~1200 lines). Heavy-duty tools for checking execution status, listing failures, getting detailed execution timelines, and generating status reports. |
| `remediation_tools.py` | **Action tools** (~1400 lines). Tools that *change things*: `restart_execution`, `resubmit_execution`, `trigger_workflow`, `run_related_health_check`. These are gated behind the approval system. |
| `log_tools.py` | **Log retrieval tools**. Fetches execution logs, task-level logs, and error details from AutomationEdge. Used during investigation to find root causes. |
| `rca_tools.py` | **Root cause analysis tools**. Specialized tools for correlating failures, checking dependency health, and generating structured RCA reports. |
| `notification_tools.py` | **Notification tools**. Sends alerts, summaries, or reports via configured channels (email, Teams, etc.). |
| `dependency_tools.py` | **Dependency checking tools**. Checks the health of upstream/downstream systems that a workflow depends on. |
| `file_tools.py` | **File handling tools**. Reads files attached to workflow executions or user messages. |
| `ticket_db.py` | **Ticket/case database**. Manages internal tracking of issues and their lifecycle (open → investigating → resolved). |
| `agent_debug_tools.py` | **Debug and diagnostic tools for the agent system itself**. Lets admins inspect agent state, registered tools, RAG index contents, and configuration. |
| `automationedge_client.py` | **AutomationEdge REST API client** (~2200 lines, the second-largest file). The `AutomationEdgeClient` class handles ALL communication with the AE platform: authentication (token management), executing workflows, fetching execution status/logs/details, listing workflows, and caching. |
| `mcp_tools.py` | **MCP bridge**. Bridges MCP tool specifications into the main tool registry. In co-located mode, binds directly to local handlers. In remote mode, sends requests to an external MCP server via HTTP. |
| `ae_dynamic_tools.py` | **Dynamic tool mapper**. Converts AutomationEdge workflow metadata (from the AE API) into `ToolDefinition` objects. Reads the `AgenticToolConfiguration` block from workflow metadata to auto-generate tool name, description, parameters, safety tier, and usage guidance. |

---

### 4.6 `mcp_server/` Directory

The **Model Context Protocol (MCP) server** — an independent server that exposes tools via the MCP standard.

| File | What It Does |
|:-----|:-------------|
| `server.py` | **FastMCP server entry point**. Creates and configures the MCP server instance. Registers all tool specifications from `tool_specs.py`. |
| `__main__.py` | **CLI runner**. Allows starting the MCP server directly with `python -m mcp_server`. |
| `config.py` | **MCP-specific configuration**. Reads MCP-related environment variables (port, transport mode, authentication). |
| `ae_client.py` | **AutomationEdge client for MCP**. A version of the AE client used by MCP tool handlers. Shares credentials and token management with the main client. |
| `tool_specs.py` | **Tool specification registry** (~1000 lines). The single source of truth for ALL MCP tools. Contains: (1) `get_mcp_tool_specs()` — returns all tool specifications. (2) `_CURATED_TOOL_OVERRIDES` — human-crafted metadata (titles, descriptions, use_when, tags) for each tool. (3) `_spec()` helper to build `MCPToolSpec` objects. |
| `requirements.txt` | **MCP-specific Python dependencies**. |
| `README.md` | **MCP server documentation**. Setup instructions and usage guide. |

#### `mcp_server/tools/` — MCP Tool Implementations

| File | What It Does |
|:-----|:-------------|
| `agent_tools.py` | **Agent management tools** (~900 lines). CRUD operations for agents: list, create, update, delete, link/unlink tools, view interaction history. |
| `request_read.py` | **Request reading tools**. Read-only tools: get execution details, list executions by status, search by date range. |
| `request_diag.py` | **Request diagnostic tools**. Deep-dive diagnostics: task-level logs, error analysis, timeline reconstruction. |
| `request_mutate.py` | **Request mutation tools**. Write operations: restart, resubmit, cancel, reassign executions. |
| `workflow_tools.py` | **Workflow management tools**. List workflows, get workflow config, check workflow health. |
| `support_composite.py` | **Composite support tools** (~400 lines). High-value compound tools that combine multiple operations (e.g., "investigate and summarize" does status + logs + RCA in one call). |
| `schedule_tools.py` | **Scheduler tools**. Manage scheduled tasks: create, update, delete, list, trigger manually. |
| `task_tools.py` | **Task-level tools**. Operate on individual tasks within a workflow execution. |
| `ticket_tools.py` | **Ticket management tools**. Create and manage support tickets/cases. |
| `credential_tools.py` | **Credential tools**. Manage AutomationEdge credentials (list, validate, rotate). |
| `dependency_tools.py` | **Dependency tools**. Check external system dependencies. |
| `misc_tools.py` | **Miscellaneous tools**. Utility tools: server health check, version info, configuration inspection. |
| `agent_guard.py` | **Agent safety guard**. Validates agent operations to prevent unsafe modifications. |

---

### 4.7 `rag/` Directory

**RAG = Retrieval-Augmented Generation.** This is how the AI "knows" things beyond its training data.

| File | What It Does |
|:-----|:-------------|
| `engine.py` | **RAG engine** (~350 lines). The `RAGEngine` class manages vector embeddings and similarity search using PostgreSQL + pgvector. Key operations: (1) `index_document(collection, doc_id, text, metadata)` — stores a document's embedding. (2) `search(collection, query, top_k)` — finds the most relevant documents for a query. (3) Manages collections: `tools`, `sops`, `incidents`. |
| `document_processor.py` | **Document parser**. Reads markdown/text files from `documents/` and splits them into chunks for indexing. Handles headers, sections, and metadata extraction. |
| `index_all.py` | **Batch indexer**. Indexes all SOP documents and tool metadata into the RAG engine. Called during bootstrap and also available as a standalone script. |
| `data/` | **Directory for RAG data files**. May contain cached embeddings or pre-computed indexes. |

---

### 4.8 `state/` Directory

This directory manages all **persistent state** — what the system remembers across requests and restarts.

| File | What It Does |
|:-----|:-------------|
| `conversation_state.py` | **Conversation memory** (~600 lines). The `ConversationState` class tracks everything about an ongoing conversation: message history, current phase (IDLE → INVESTIGATING → AWAITING_APPROVAL → RESOLVED), affected workflows, tool call log, pending actions, user identity, queued messages, and parameter collection state. Persists to PostgreSQL. |
| `issue_tracker.py` | **Issue lifecycle manager** (~400 lines). The `IssueTracker` class: (1) Classifies incoming messages (new issue, follow-up, recurrence). (2) Tracks issue status (open → investigating → resolved). (3) Detects recurring issues and triggers escalation. (4) Maintains a timeline of actions taken. |
| `session_manager.py` | **Session cleanup**. Manages session TTL (time-to-live) and cleanup of stale conversations. Registers as a scheduled task. |
| `app_config.py` | **Application configuration store** (~500 lines). Persists business-level settings from the Admin Control Center. Sections: User Experience, Operations Rules, Approvals, Monitoring, Integrations. Provides `get_runtime_value()` which checks the persisted config first, then falls back to environment variables. |
| `tool_overrides.py` | **Tool override store**. Persists admin-applied overrides to tool metadata (title, description, tier, active flag, tags). Applied on top of code-defined tool definitions. |
| `agent_catalog.py` | **Agent catalog store**. Persists agent metadata, capabilities, domains, priority, and tool links. Used by the admin console. |
| `scheduler_store.py` | **Scheduler task store**. Persists scheduled task definitions (name, interval, enabled flag, last run). |
| `docs_catalog.py` | **Documentation catalog store**. Manages the list of documents shown on the `/docs` page. |
| `app_control_center.json` | **Persisted admin config data** (JSON file). |
| `tool_overrides.json` | **Persisted tool override data** (JSON file). |
| `agent_catalog.json` | **Persisted agent catalog data** (JSON file). |
| `scheduler_catalog.json` | **Persisted scheduler tasks** (JSON file). |
| `docs_catalog.json` | **Persisted docs catalog data** (JSON file). |

---

### 4.9 `security/` Directory

| File | What It Does |
|:-----|:-------------|
| `workflow_access.py` | **Workflow-level access control** (~300 lines). Enforces which users can view or execute specific workflows. Provides `can_execute_workflow(user_id, workflow_name)` and `is_execute_enforced()`. Syncs user-workflow assignments from the AutomationEdge platform. |

---

### 4.10 `frontend/` Directory

All **user-facing HTML/CSS/JS** files. These are single-file HTML applications (no build step needed).

| File | What It Does |
|:-----|:-------------|
| `webchat.html` | **Main chat interface** (~600 lines). A complete web chat UI with: login screen, message bubbles, markdown rendering, SSE streaming for real-time progress updates, session management, and responsive design. This is what end users interact with. |
| `admin_console.html` | **Operations Control Center**. A React-based admin workspace for managing settings, tools, agents, knowledge, activity, and scheduler. Loaded at `/admin`. |
| `agent_admin.html` | **Legacy admin UI**. The older tool management interface (still accessible at `/tools/legacy`). |
| `index.html` | **Documentation library page**. A public-facing page at `/docs` that displays SOP guides, reference documents, and knowledge articles. Reads content from the docs catalog. |
| `aistudio_webchat_aaaaaaa.html` | **AI Studio integration webchat**. A variant of the webchat designed to work within the AutomationEdge AI Studio embedded environment. |
| `AE_logo.png` | **AutomationEdge logo** used in the UI. |

---

### 4.11 `templates/` Directory

**Response templates** used by agents to format their output.

| File | What It Does |
|:-----|:-------------|
| `adaptive_cards.py` | **Adaptive Card templates**. Generates structured card-format responses for Teams/chat interfaces (used for approval prompts, status summaries, etc.). |
| `rca_templates.py` | **RCA report templates**. Predefined formats for Root Cause Analysis reports: timeline, impact assessment, contributing factors, and recommendations. |

---

### 4.12 `scripts/` Directory

**Utility and debugging scripts**. These are NOT part of the running application — they're developer tools.

| File | What It Does |
|:-----|:-------------|
| `smoke_test.py` | Sends a test message to the agent and verifies the response. |
| `smoke_test_final.py` | Extended smoke test with multiple scenario checks. |
| `test_llm.py` | Tests the LLM client connection (verifies Gemini API works). |
| `test_discovery.py` | Tests the `discover_tools` function. |
| `test_e2e_request.py` | End-to-end test: sends a real user question and validates the full pipeline. |
| `simulate_discovery.py` | Simulates tool discovery for a given query. |
| `dump_prompt.py` | Dumps the full system prompt that gets sent to the LLM (useful for debugging). |
| `dump_target_tool.py` | Shows the definition and metadata for a specific tool. |
| `dump_employee_context.py` | Shows what user/employee context the system has. |
| `debug_param_flow.py` | Traces how parameters flow through tool calls. |
| `debug_preflight.py` | Checks all system prerequisites before startup. |
| `debug_sop_hints.py` | Shows SOP hints that would be injected for a given query. |
| `search_all_rag.py` | Searches across all RAG collections. |
| `search_db_global.py` | Searches the PostgreSQL database globally. |
| `find_workflow_params.py` | Finds parameters for a specific AE workflow. |
| `find_user_workflow_assignments.py` | Shows workflow access assignments for a user. |
| `sync_user_workflow_access.py` | Manually triggers workflow access sync from AE. |
| `inspect_rag_wf.py` | Inspects RAG entries for a specific workflow. |
| `global_search.py` | Full-text search across the database. |

---

### 4.13 `docs/` Directory

**Project documentation**.

| File | What It Does |
|:-----|:-------------|
| `SETUP_GUIDE.md` | Comprehensive installation and configuration guide. |
| `HOW_IT_WORKS.md` | Explains the system architecture at a high level. |
| `TECHNICAL_BLUEPRINT.md` | Detailed technical design document. |
| `TOOL_ARCHITECTURE_TARGET.md` | Target architecture for the tool catalog/hydration system. |
| `CONTROL_CENTER_GUIDE.md` | Guide for using the Operations Control Center. |
| `ADD_NEW_TOOL_GUIDE.md` | Developer guide for adding new tools (code-level). |
| `NON_DEVELOPER_TOOL_GUIDE.md` | Non-developer guide for managing tools via UI. |
| `mcp_testing_guide.md` | Guide for testing MCP server tools. |
| `AE_Agentic_OpsSupport_Implementation_Guide_Part2.md` | Detailed implementation walkthrough. |
| `AI_Studio_OnPrem_Agentic_Support_StepByStep(1).md` | Step-by-step deployment guide for on-premises AI Studio. |

---

## 5. Key Concepts You Must Know

### 5.1 Tool Tiers & Approval Gates
Every tool has a **safety tier**:
- `read_only` → Runs automatically, no approval needed
- `low_risk` → Runs automatically for technical users
- `medium_risk` → **Requires user approval** before execution
- `high_risk` → Requires approval from an authorized admin

### 5.2 RAG (Retrieval-Augmented Generation)
The AI does NOT have all knowledge built-in. When a user asks a question:
1. The system searches the **vector database** for relevant SOP documents and tool descriptions.
2. The matching content is injected into the LLM's context.
3. This lets the AI give accurate, org-specific answers.

### 5.3 Dynamic Tools
Not all tools are hard-coded. The system can **auto-generate tools from AutomationEdge workflows**:
1. A workflow in AE has an `AgenticToolConfiguration` metadata block.
2. On startup, the system reads this and creates a tool definition automatically.
3. The LLM can then discover and call this tool like any other.

### 5.4 Conversation Phases
Each conversation goes through phases:
- `IDLE` → No active investigation
- `INVESTIGATING` → Agent is actively working
- `AWAITING_APPROVAL` → Waiting for human approval
- `RESOLVED` → Issue has been resolved
- `ESCALATED` → Issue was escalated to humans

### 5.5 Multi-Agent Routing
The system has specialized agents for different tasks:
- **Orchestrator** → General-purpose (handles everything)
- **DiagnosticAgent** → Focused on diagnostic queries
- **RemediationAgent** → Focused on fix/restart actions
- **RCAAgent** → Focused on root cause analysis

---

## 6. How to Run the Application

### Prerequisites
1. Python 3.11+
2. PostgreSQL 15+ with pgvector extension
3. Google Cloud project with Vertex AI enabled
4. AutomationEdge platform access

### Quick Start
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and configure environment
cp .env.example .env
# Edit .env with your credentials

# 3. Initialize the database
python setup_db.py

# 4. Index SOP documents
python run_rag_index.py

# 5. Start the server
python agent_server.py
# → Server runs at http://localhost:5000

# 6. (Optional) Start MCP server
python -m mcp_server
```

### Access Points
- **Chat UI**: `http://localhost:5000/webchat`
- **Admin Console**: `http://localhost:5000/admin`
- **Docs Library**: `http://localhost:5000/docs`
- **Health Check**: `http://localhost:5000/health`

---

## 7. Common Debugging Tips

| Problem | What to Check |
|:--------|:--------------|
| "Agent gives empty responses" | Check `logs/` for errors. Verify `GOOGLE_CLOUD_PROJECT` and `VERTEX_AI_MODEL` in `.env`. Run `scripts/test_llm.py`. |
| "Tool not found" | Verify the tool module is in `_STATIC_TOOL_MODULES` in `tools/bootstrap.py`. Restart the server. |
| "Workflow not visible" | Check `security/workflow_access.py` — the user may not have access. Run `scripts/find_user_workflow_assignments.py`. |
| "RAG returns no results" | Re-run `python run_rag_index.py`. Check `scripts/search_all_rag.py` with your query. |
| "Approval keeps failing" | Check `RBAC_ENABLED` in settings. Verify user role in `ROLE_RANK` and tool tier in `TIER_RANK`. |
| "AE API timeout" | Increase `AE_TIMEOUT_SECONDS` in `.env`. Check AE platform availability. |
| "Database connection error" | Verify `POSTGRES_DSN` in `.env`. Check if PostgreSQL is running. Run `python setup_db.py` if tables are missing. |

---

> **Last Updated**: April 2026
