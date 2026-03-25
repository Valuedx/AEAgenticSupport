> - **Orchestrator operator controls (2026-03-22)**: The AE AI Hub orchestrator supports cooperative **pause**, **cancel**, and **resume** between nodes; see `orchestrator/TECHNICAL_BLUEPRINT.md` §6.11.
>
> - **Studio ↔ Orchestrator Proxy Bridge (2026-03-22)**: AI Studio acts as a dumb proxy to the visual orchestrator. Callers pass `orchestrator_workflow_id` (and optionally `orchestrator_payload`, `orchestrator_timeout`, `orchestrator_wait_for_result`, `orchestrator_chat_reply_mode`, `orchestrator_include_context_json`) in `user_metadata` to `handle_chat_message()` to bypass LLM routing. **Default is async:** `POST /execute` only, returning instance id and polling URLs unless `orchestrator_wait_for_result` or `ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT=true` enables blocking `run_and_wait`. **Sync** completion text prefers hub context `orchestrator_user_reply` (**Bridge User Reply** DAG node), then heuristic LLM response extraction, then JSON (`ORCHESTRATOR_BRIDGE_CHAT_REPLY_MODE` / metadata). The bridge merges chat `message` / `session_id` / user fields into the trigger payload when missing, supports optional `ORCHESTRATOR_API_TOKEN` (Bearer) for JWT orchestrator mode, returns structured suspended/HITL messages in sync mode, and is covered by `tests/test_orchestrator_bridge.py`. Config adds `ORCHESTRATOR_BRIDGE_CHAT_REPLY_MODE`. See `orchestrator/TECHNICAL_BLUEPRINT.md` §10 and `orchestrator/HOW_IT_WORKS.md` Step 17.
>
> - **AE AI Hub — Agentic Orchestrator (2026-03-20)**: New add-on module in `orchestrator/`. Visual no-code DAG builder (React Flow + Zustand + shadcn/ui) with FastAPI execution engine, Celery workers, SQLAlchemy data models (`WorkflowDefinition`, `WorkflowInstance`, `ExecutionLog`), and MCP tool bridge. Runs as a sidecar — does not modify existing codebase. See `orchestrator/TECHNICAL_BLUEPRINT.md`, `orchestrator/SETUP_GUIDE.md`, and `orchestrator/HOW_IT_WORKS.md`.
>
> - **LangFuse Observability — Full Coverage (2026-03-20)**: Optional LLM observability via LangFuse. `config/observability.py` provides lazy-initialized client, `trace_context`/`span_context` context managers, and no-op stubs for zero-overhead when disabled. **Core path**: orchestrator turns (root traces), LLM generations (`chat`, `chat_with_tools` with token usage), tool executions (per-tool spans with latency), RAG searches (per-collection spans), embeddings, and approval classification. **Extended coverage**: RCA agent (handle + generate + background indexing), message gateway intent classification, scheduler handlers (health check, daily summary), custom Cognibot issue classifier, MCP agent log analysis, conversation summary generation, and admin tool test endpoint. Every LLM call and tool execution in the codebase is now traced. Thread-local trace propagation avoids signature changes. Fixed `MetricsCollector.record_turn_error` missing method bug. See §6 and `SETUP_GUIDE.md` §15.
>
> - **Evidence-Pack Diagnostic Pipeline (2026-03-20)**: Added metadata-first diagnosis flow with structured evidence packs. New `tools/ae_diagnostic_tools.py` provides log time-window extraction, request-ID/step-name filtering, Java exception chain parsing, repeated-line collapse, multi-stream chronological merge, and a compact evidence-pack builder. New `diagnose_from_evidence_pack` tool runs LLM diagnosis with confidence scoring, alternative hypotheses, and remediation suggestions. `AutomationEdgeClient` extended with `get_normalized_instance_metadata()` and `get_workflow_step_timeline()`. DiagnosticAgent now includes the `diagnostics` tool category. **Timeout & graceful degradation (2026-03-20)**: `build_evidence_pack` now enforces a configurable `max_wait_seconds` wall-clock budget (default 45 s). Log retrieval runs in a bounded thread; when the budget expires or the T4 debug-log request is still pending, the pipeline returns a metadata-only evidence pack with a `log_retrieval` hint (including `debug_log_request_id`) so the agent can retry. `diagnose_from_evidence_pack` handles this degraded state by running metadata-only LLM diagnosis with confidence capped at 0.5. Progress callbacks stream phase-level status updates to the user during long operations. See §5.4.
>
> - **Performance Optimizations (2026-03-07)**: Parallel RAG fan-out (4 concurrent searches), configurable embedding dimension, batched workflow catalog queries, shared MCP executor, coalesced state writes, AE path caching, capped execution polling. See §4.2 and `SETUP_GUIDE.md` §10.
>
> - **Documentation Update (2026-03-09)**: MCP server still exposes **106 tools** (P0 + P1 support). The main-app bridge now supports both co-located shared-spec mode and remote MCP mode via `AE_MCP_SERVER_URL`, while still eagerly hydrating only a curated subset and exposing the rest through RAG and `discover_tools` with lazy runtime hydration. See `SETUP_GUIDE.md` §13 and `mcp_server/README.md`.
>
> - **Tool Ranking Update (2026-03-07)**: `discover_tools` and turn-local tool hydration now share a catalog-aware ranking step that blends retrieval score with source, risk, latency, mutation, direct-callability, and observed execution-history signals. Recent outcomes are weighted more heavily, and agent-scoped feedback is used when available.
>
> - **Hydrator/Executor Split (2026-03-08)**: Turn-local hydration and normalized runtime execution are now implemented in `tools/hydrator.py` and `tools/executor.py`, while `tools/registry.py` remains as a compatibility facade.
>
> - **Tool Bootstrap Update (2026-03-08)**: Startup-time tool imports, dynamic workflow reload, agent-link sync, and tool-doc RAG indexing now run through `tools/bootstrap.py`, keeping `main.py` as a thin entry adapter.
>
> - **Control Center Update (2026-03-08)**: Added a React-based admin workspace plus persisted stores for application settings, tool overrides, scheduler tasks, documentation catalog entries, and conversation-history review. The standalone server now exposes `/admin`, `/tools`, `/docs`, `/api/ui-config/docs`, `/api/docs/catalog`, and admin-friendly history APIs.
>
> - **Tool Architecture Target (2026-03-07)**: Proposed scale-out refactor for unified catalog, turn-local tool hydration, ranking, and source-specific execution handling is documented in `TOOL_ARCHITECTURE_TARGET.md`.
>
> - **Multi-Agent 2.0 (Patch 2026-03-06)**:
>   - **Strict Tool Isolation**: Implemented role-based tool filtering. Diagnostic specialists are restricted to `logs`/`status`/`diagnostics` tools; Remediation specialists to `remediation`/`config`.
>   - **Verification Loop**: Added mandatory specialist handoff. Remediation actions now trigger an automatic cross-agent verification turn to confirm resolution.
>   - **Agent Memory**: Added `SharedContext` memory buckets. Specialists now maintain short-term state (e.g., specific log patterns) across multi-turn delegation chains.
>   - **Context-Aware RAG**: RAG queries now automatically ingest active issue metadata (error signatures, workflow names) to prioritize relevant SOPs and KB articles.
>   - **Rich Notifications**: Added `Adaptive Cards` support for MS Teams, enabling interactive high-fidelity approval and escalation alerts.
> - Validation status: `test_enhancements.py` and `test_multi_agent.py` passed.
>
> **Documentation Update (2026-03-04)**  
>
> **Documentation Update (2026-03-02)**  
> Patch release notes included in this version:
> - Fixed circular import initialization in `agents` and `gateway` packages.
> - Fixed approval and protected-workflow enforcement logic.
> - Fixed tool result success/error propagation across execution paths.
> - Improved busy-turn intent routing and queued message handling.
> - Added cross-channel persona propagation (`business` and `technical`) and semantic approval handling.
> - Validation status: `pytest -q tests` passed (`31 passed`).
>
## AutomationEdge Agentic Support — Technical Blueprint

> **Documentation Update (2026-03-25)**: The main AI Studio Extension path is now a **thin async adapter**. `custom/custom_hooks.py::api_messages_hook` does cheap normalization, dedupe, minimal state persistence, and queues work to `agent_server.py /chat/async`; completion comes back through AI Studio `POST /api/reply` and `custom/custom_hooks.py::api_reply_hook`. The older inline/support-agent-in-hook and local `custom_cognibot/` dialog proxy paths are no longer the recommended production model. Optional AI Studio Dialog Designer wrappers now live in `custom/functions/python/actions.py`.

**Version:** 1.5  
**Last updated:** 2026-03-24

---

## 1. High-Level Architecture

**Goal:** Provide an agentic support assistant for AutomationEdge that:
- Investigates workflow issues using tools and RAG.
- Explains findings in business or technical language.
- Safely performs remediation with approvals and full audit trail.

**Core pieces:**
- **LLM + RAG layer**: Gemini (Vertex AI) plus PostgreSQL/pgvector.
- **Agent orchestration**: Supervisor (Orchestrator) + Specialists (Diagnostic, Remediation) + AgentRouter + Gateway + State.
- **Tool layer**: Typed tools over AE REST APIs and DB.
- **Control plane**: React control center plus JSON-backed configuration stores for UI-managed runtime metadata.
- **Chat interfaces**:
  - AI Studio web chat / Extension.
  - MS Teams via Azure Bot / Cognibot.
  - Standalone webchat via `agent_server.py`.
  - Public documentation library via `/docs`.

**Current production note:** the default AI Studio Extension path is now `custom/custom_hooks.py::api_messages_hook` -> `agent_server.py /chat/async` -> external agentic execution -> AI Studio `POST /api/reply` -> `custom/custom_hooks.py::api_reply_hook` -> channel delivery.

Request path examples:
- **AI Studio webchat → `main.py` → `MessageGateway` → `AgentRouter` → `Supervisor` → (Delegation) → `Specialist` → tools + Hybrid RAG → response**
- **MS Teams → Azure Bot → Cognibot hooks → `support_agent` → tools + RAG → response**
- **Browser webchat → `agent_server.py` → `MessageGateway` → `Orchestrator` → tools + RAG → response**

---

## 2. Runtime Components

- **`config/`**
  - `settings.py`: Central configuration and env loading (AE URLs, GCP, DB, safety limits).
  - `llm_client.py`: Vertex AI Gemini client (chat + tools). Records LLM generations to LangFuse. Provides thread-local trace propagation (`set_current_trace`/`get_current_trace`).
  - `logging_setup.py`: Application + audit loggers.
  - `metrics.py`: In-memory metrics collector for turn latencies, token usage, and tool success rates.
  - `observability.py`: Optional LangFuse integration — lazy client, `trace_context`/`span_context` context managers, `create_generation` helper, no-op stubs for zero overhead when disabled.
  - `classification_signals.py`: Heuristic patterns for classifiers.
- **`agents/`**
  - `agent_router.py`: Central dispatcher for scoring and routing messages to agents.
  - `orchestrator_agent.py`: The **Supervisor** Agent. Coordinates high-level planning and chooses specialists.
  - `diagnostic_agent.py`: **Technical Specialist**. Investigates logs, status, infrastructure, and builds structured evidence packs for LLM diagnosis (categories: status, logs, dependency, file, diagnostics).
  - `remediation_agent.py`: **Resolution Specialist**. Restarts workflows and executes fixes.
  - `approval_gate.py`: RBAC-aware risk tiering and approval workflow.
  - `escalation.py`: Escalation logic and notifications.
  - `rca_agent.py`: Business and technical RCA generation + indexing into RAG.
  - `scheduler.py`: Background tasks for proactive health checks and webhook handling.
- **`tools/`**
  - `base.py`: AE API client and `ToolDefinition`.
  - `registry.py`: Tool catalog, categories, and registration.
  - `*_tools.py`: Typed tools grouped by concern (status, logs, files, remediation, etc.).
  - `ae_diagnostic_tools.py`: Evidence-pack diagnostic pipeline — log filtering (time window, request ID, step name), exception chain extraction, noise collapse, timestamp normalization, multi-stream merge, structured evidence-pack builder, and LLM diagnosis with confidence scoring.
  - `automationedge_client.py`: AE REST client with normalized metadata (`get_normalized_instance_metadata`) and step-level timeline (`get_workflow_step_timeline`).
- **`rag/`**
  - `engine.py`: Hybrid RAG engine (Vector + Keyword + RRF) using `pgvector` and `tsvector`.
  - `processor.py`: Advanced document processing for PDF (tables), MD, and JSON.
  - `index_all.py`: Index builder for KB, SOPs, tool docs, past incidents.
  - `data/`: Content collections (`kb_articles/`, `sops/`, `tool_docs/`, `past_incidents/`).
- **`gateway/`**
  - `message_gateway.py`: Session management, concurrency and intent classification (additive/interrupt/cancel/approval/new).
  - `progress.py`: `ProgressCallback` for streaming user-friendly status updates.
- **`state/`**
  - `conversation_state.py`: Per-session state (messages, findings, tool logs, phase, persona).
  - `issue_tracker.py`: Multi-issue registry with recurrence and cascade detection (PostgreSQL-backed).
  - `app_config.py`: Persisted control-center sections for user experience, rules, approvals, monitoring, and integrations.
  - `tool_overrides.py`: Persisted admin overrides for tool metadata and visibility.
  - `scheduler_store.py`: Persisted custom scheduler task catalog.
  - `docs_catalog.py`: Persisted public documentation library manifest.
- **`templates/`**
  - `rca_templates.py`: Prompt building helpers and RCA structures.
  - `adaptive_cards.py`: **[NEW]** JSON schema generators for MS Teams rich notifications.
- **`static/`**
  - `admin_app.js` + `admin_app.css`: React control-center application.
- **`custom/` (AI Studio Extension layer)**
  - `custom_hooks.py`: Async Cognibot hooks (`api_messages_hook`) with locks, dedupe, routing, proactive Teams conversation-ref capture, and approval/card handling.
  - `models.py` + `migrations/`: Django models for cases, approvals, processed messages, links.
  - `helpers/`: Locks, DB helpers, RAG stubs, REST tool client, roster, Teams helpers, Teams proactive sender, shared activity helpers, issue classifier.
  - `functions/python/support_agent.py`: Planner + executor for the inline Extension path, using REST tools and syncing approval state between Django and the gateway.
  - `functions/python/actions.py`: Optional AI Studio Dialog Designer action wrappers that call the same external async proxy path or small local helpers.
  - In the current production path, `custom/custom_hooks.py` is thinner than that older summary: `api_messages_hook` now does cheap dedupe/minimal-state work and queues to `agent_server.py /chat/async`; `api_reply_hook` handles async completion delivery.
- **`custom_cognibot/`**
  - Thin-proxy hooks used for local Cognibot → standalone agent server integration, including `/chat/stream` SSE forwarding for Teams progress updates.
- **`agent_server.py`**
  - Standalone Flask/SSE server that exposes the agent as HTTP (`/chat`, `/chat/stream`, webchat UI, admin UI, docs UI).
  - Hosts admin configuration, tool override, scheduler, document catalog, and conversation-history APIs.
  - Proxies AI Studio Direct Line requests server-side so browser clients do not need the raw secret.
  - Also handles async AI Studio Extension turns through `POST /chat/async` and sends completion back through AI Studio `POST /api/reply`.
- **`main.py`**
  - AI Studio project entrypoint (`handle_chat_message`), used for webchat / Extension deployments.
  - Delegates tool startup initialization to `tools/bootstrap.py`.

---

## 3. Data & Persistence Design

**PostgreSQL (ops_agent DB):**
- `rag_documents`  
  - Purpose: Vector store for KB, SOPs, tool docs, past incidents.  
  - Key fields: `id`, `content`, `metadata`, `collection`, `embedding::vector`.
- `issue_registry`  
  - Purpose: Serialized `Issue` objects per conversation (multi-issue tracking).  
  - Key fields: `conversation_id`, `issue_id`, `issue_data JSONB`, `updated_at`.
- `issue_tracker_state`  
  - Purpose: Which issue is currently active per conversation.  
  - Key fields: `conversation_id`, `active_issue_id`, `updated_at`.
- Conversation state tables (managed by code in `state/`) are embedded into the above, so that conversation/issue context survives process restarts and deployments.

**JSON-backed control-plane stores:**
- `app_control_center.json`: High-level runtime settings that used to be hardcoded in UI or helper modules.
- `tool_overrides.json`: Business-owned tool labels, visibility, and routing overrides.
- `scheduler_catalog.json`: Custom monitoring and automation tasks.
- `docs_catalog.json`: Public documentation library entries.

**Django (AI Studio Extension DB):**
- `ProcessedMessage`: Idempotency log keyed by `(thread_id, teams_message_id)`.
- `ConversationState` (Extension): Thread-level pointer to active case and last message IDs.
- `Case`: Logical issue/case, state machine fields, planner/executor state, recurrence counters.
- `Approval`: Pending/approved/rejected approval requests with recipients and audit info.
- `IssueLink`: Relations between cases (cascade/related/recurrence clustering).

**RAG collections in `rag/data/`:**
- `kb_articles/`: Troubleshooting docs and workflow details.
- `sops/`: SOPs for specific failures.
- `tool_docs/`: Enriched tool metadata for RAG-filtered tool selection.
- `past_incidents/`: RCA snippets and resolutions produced by `rca_agent`.

---

## 4. LLM, RAG, and Tool-Calling Flow

### 4.1 Message Gateway + Issue Tracker

1. `MessageGateway.process_message()`:
   - Creates or restores a `ConversationState` per `conversation_id`.
   - If no work in progress → routes directly to `Orchestrator.handle_message`.
   - If work in progress → classifies intent into ADDITIVE / INTERRUPT / CANCEL / APPROVAL / NEW_REQUEST.
2. `IssueTracker` (inside `Orchestrator`):
   - Classifies each user message into:
     - `NEW_ISSUE`, `CONTINUE_EXISTING`, `RELATED_NEW`, `RECURRENCE`, `FOLLOWUP`, `STATUS_CHECK`.
   - Uses three layers: heuristics → workflow/error signature matching → Vertex AI classification.
   - Maintains per-issue findings, workflows, error signatures, recurrence counts.

### 4.2 Orchestrator Loop

For each routed message:
1. Build dynamic system prompt:
   - Role + safety rules.
   - Tool catalog summary (with risk tiers and always-available tools).
   - Persona context (business vs technical).
   - Issue context from `IssueTracker` (active issue, findings, recurrence history).
2. Call `llm_client.chat_with_tools` with:
   - Messages so far (user + agent).
   - Tools schema from `tools/registry.py` (function calling).
3. Tool-selection strategy:
   - If tool catalog is small: all cataloged tools available.
   - If catalog is large:
     - Always include a small eagerly hydrated `always_available` core (status, core logs, general tools, curated MCP support tools).
      - Use catalog-backed RAG filtering via `rag.engine.PgVectorRAGEngine.search_tools`.
      - Lazily hydrate selected tools only when they enter the turn-local set.
      - Include `discover_tools` meta-tool for on-demand search and mid-turn expansion.
   - **Performance:** After a single `embed_query()`, the four RAG collection searches (tools, kb, sops, past_incidents) run in **parallel** via `ThreadPoolExecutor(4)` to minimize retrieval latency.
4. Execute tool calls:
   - Dispatch through a turn-local hydrated tool set built from `tools/registry`.
   - Log every call in audit logger and in `ConversationState.tool_call_log`.
   - Feed results back into LLM loop.
5. Issue enrichment:
   - For each meaningful result:
     - `add_workflow_to_issue`, `add_error_signature`, `add_finding_to_issue`.
6. Approval handling:
   - If a remediation tool is proposed:
     - Route through `ApprovalGate` to decide whether to:
       - Auto-run (safe tier within limits) or
       - Enter `ConversationPhase.AWAITING_APPROVAL`.
7. Persona filtering:
   - Final natural-language response is post-processed:
     - Business persona: hide IDs/logs, emphasize impact and next steps.
     - Technical persona: include workflow names, execution IDs, error details.

---

## 5. Chat Interfaces & Integration Paths

### 5.1 AI Studio Webchat / Python Project

- Entry: `main.py` → `handle_chat_message(message, session_id, user_id, user_role)`.
- Routing:
  - Uses `MessageGateway` for concurrency and intent classification.
  - Uses same `Orchestrator`, tools, and RAG engine as other channels.
- Deployment model:
  - Python project in AI Studio with `requirements.txt`.
  - Environment variables configured via AI Studio UI.

### 5.2 AI Studio Extension + MS Teams (Cognibot)

**Current production note:** by default, the top-level Extension no longer executes the full planner/executor path inside Cognibot. It now stops after cheap validation/dedupe/minimal-state work, queues to `agent_server.py /chat/async`, returns an acknowledgement, and later delivers the final activity from `api_reply_hook` after AI Studio invokes `POST /api/reply`.

- `custom/custom_hooks.py`:
  - Async `api_messages_hook(request, activity)`:
    - Converts Bot Framework activity → dict, extracts `thread_id`, message ID, text, user ID, and the Bot Framework fields required for proactive sends.
    - Acquires per-thread PostgreSQL advisory lock.
    - Drops duplicate messages via `ProcessedMessage`.
    - Handles smalltalk fast-path.
    - Persists Teams conversation references so progress updates can be sent proactively later in the turn.
    - Integrates issue classification and approval flows, including adaptive-card button handling and card-body prefix insertion for recurrence/related-case context.
    - In the default production path, stops after dedupe/minimal-state work and queues to `agent_server.py /chat/async`.
- `custom/functions/python/support_agent.py`:
  - Planner:
    - Uses RAG (via REST or direct pgvector) over SOPs and tool docs.
    - Builds a strict JSON plan with steps and risk tags.
  - Executor:
    - Auto-runs safe steps using REST tools.
    - Creates `Approval` rows for risky steps only when an on-shift reviewer roster exists.
    - Executes an already approved risky plan without re-opening a second approval gate.
    - Syncs agentic gateway approval decisions back into the Django `Approval` row for Extension-side authorization consistency.
    - Updates/creates tickets and escalations through typed tools.
  - This file is no longer the default Teams production entrypoint; it remains the inline path and a shared library for compatibility flows.
- `custom/functions/python/actions.py`:
  - Provides optional AI Studio Dialog Designer action entrypoints under `custom/functions/python`.
  - Keeps Designer compatibility without moving the main Teams production flow away from `api_messages_hook` and `POST /chat/async`.

### 5.3 Standalone Agent Server + Webchat

`agent_server.py` now also exposes `POST /chat/async` for the main AI Studio Extension path. That path queues work and later sends completion back through AI Studio `POST /api/reply`; the `custom_cognibot/` flow remains local-only.

- `agent_server.py`:
  - `/chat`: JSON request → synchronous response.
  - `/chat/stream`: SSE events for progress + final answer.
  - `/`: Serves `webchat.html`.
  - `/admin` and `/tools`: Serve the React control center.
  - `/docs`: Serves the public documentation library.
- Thin-proxy Cognibot mode:
  - `custom_cognibot/` hooks forward Cognibot traffic to `/chat/stream` for Teams progress updates and use proactive sends for intermediate status.
  - Used for local testing of full Cognibot → agent pipeline.

### 5.4 Operations Control Center

The control center is the application's admin and business-operations workspace.

Primary responsibilities:

- manage public chat wording and quick actions
- manage monitoring and approval defaults
- manage agent metadata and tool overrides
- manage custom scheduler tasks
- manage SOPs and public documentation entries
- review conversation history, summaries, exports, and human handoff flags

Key APIs:

- `/api/admin/bootstrap`
- `/api/admin/config/<section>`
- `/api/tools/<tool_name>/config`
- `/api/scheduler/tasks`
- `/api/docs/catalog`
- `/api/history/conversations`

---

## 5.3 Performance Optimizations

The following optimizations reduce request latency and DB/API round-trips:

| Area | Optimization | Effect |
|------|-------------|--------|
| **RAG retrieval** | Four collection searches (tools, kb, sops, incidents) run in parallel after a single `embed_query()` | Retrieval latency = max(4 searches) instead of sum |
| **Embedding cold-start** | `EMBEDDING_DIMENSION` config key avoids a live embedding call at startup to discover vector size | Faster cold start; probe still used when config is unset |
| **Workflow catalog** | `resolve_cached_workflow_name` uses a single `IN(...)` query; `get_cached_workflow_info` returns id + params in one query | 1 DB round-trip instead of N per name resolution |
| **MCP tool calls** | Shared `ThreadPoolExecutor(4)` for `_run_async` instead of creating a new executor per call | Eliminates per-call executor overhead |
| **AE client fallback** | `_try_paths` caches the winning `(path_index, use_rest)` per `(method, paths)` key; evicts on failure | First-call unchanged; subsequent calls skip failed paths |
| **State persistence** | `add_message` defers DB inserts; flushed in batch during `save()` at turn boundaries | Fewer Postgres round-trips per request |
| **Execution polling** | Default `max_attempts` capped at 15 (~45 s); returns `in_progress` with hint instead of blocking 100+ iterations | Prevents worker threads from being held by long-running executions |

---

## 5.4 Evidence-Pack Diagnostic Pipeline

The diagnostic pipeline implements a **metadata-first** approach: structured AE run metadata is used to narrow the diagnostic scope before any log processing or LLM reasoning begins.

### Architecture

```
AE Instance ID
    │
    │  ┌── Wall-clock budget: max_wait_seconds (default 45 s) ──────────┐
    │  │                                                                 │
    ├──▶ get_normalized_instance_metadata()   ─┐                         │
    ├──▶ get_workflow_step_timeline()          ─┤  ── Metadata layer     │
    │                                           │     (AutomationEdgeClient)
    │                                           ▼                        │
    ├──▶ get_execution_logs()                 ─── Log retrieval          │
    │    ╎   (runs in ThreadPoolExecutor(1)      (log_tools + AE client) │
    │    ╎    with remaining-budget timeout)                              │
    │    ╎                                                               │
    │    ├─ Logs ready?  ──YES──▶  Log Processing Pipeline:             │
    │    │                         ├─ ae_extract_log_time_window()       │
    │    │                         ├─ ae_extract_log_by_request_id()     │
    │    │                         ├─ ae_extract_log_by_step_name()      │
    │    │                         ├─ ae_extract_error_blocks()          │
    │    │                         ├─ ae_extract_exception_chain()       │
    │    │                         ├─ ae_collapse_repeated_log_lines()   │
    │    │                         ├─ ae_normalize_log_timestamps()      │
    │    │                         └─ ae_merge_log_streams_chronologically()
    │    │                                │                              │
    │    │                                ▼                              │
    │    │   ae_build_log_evidence_pack() ── Full evidence pack         │
    │    │                                                               │
    │    └─ Budget expired / pending?  ──▶  Graceful degradation:       │
    │         return metadata-only pack      { evidence_pack: None,      │
    │         + log_retrieval hint             log_retrieval: {           │
    │                                           status, request_id } }   │
    │  └─────────────────────────────────────────────────────────────────┘
    │         │
    │         ▼
    └──▶ diagnose_from_evidence_pack()         ── LLM diagnosis with
              │                                    confidence scoring
              │  (metadata-only? → confidence
              │   capped at 0.5, retry hint)
              ▼
         Structured JSON:
         { primary_diagnosis, confidence, alternatives,
           reasoning, next_fetches, safe_remediation_candidates }
```

### Key modules

| Module | Responsibility |
|--------|---------------|
| `tools/ae_diagnostic_tools.py` | Log pipeline, evidence-pack builder, LLM diagnosis, timeout orchestration |
| `tools/automationedge_client.py` | `get_normalized_instance_metadata()`, `get_workflow_step_timeline()`, `get_execution_logs(timeout_seconds=)` |
| `tools/log_tools.py` | Raw log retrieval via `get_execution_logs(timeout_seconds=)` with pending-status passthrough |
| `gateway/progress.py` | Progress messages for `build_evidence_pack`, `diagnose_from_evidence_pack`, `extract_exception_chain` |

### Registered tools (category: `diagnostics`)

| Tool | Purpose |
|------|---------|
| `build_evidence_pack` | Given an execution ID (+ optional `max_wait_seconds`), fetches metadata + timeline + logs within a wall-clock budget, runs the processing pipeline, and returns a compact evidence pack. Degrades gracefully to metadata-only when logs are unavailable in time. |
| `diagnose_from_evidence_pack` | Sends the evidence pack to the LLM with a structured diagnosis prompt; returns root cause, confidence, alternatives, and remediation candidates. Handles metadata-only packs with capped confidence and retry hints. |
| `extract_exception_chain` | Parses Java exception chains from raw log text, following nested Caused-by references |

### Timeout and graceful degradation

The evidence-pack pipeline enforces a configurable wall-clock budget to prevent synchronous bottlenecks:

| Parameter | Where | Default | Effect |
|-----------|-------|---------|--------|
| `max_wait_seconds` | `build_evidence_pack` kwarg | 45 | Total budget for metadata + timeline + log retrieval + evidence reduction |
| `timeout_seconds` | `AutomationEdgeClient.get_execution_logs` | `None` (legacy ~90 s) | Caps the T4 debug-log polling loop; returns `log_retrieval_status: "pending"` on expiry |

**Degradation path:**

1. Metadata + step timeline are fetched first (fast, single HTTP calls each).
2. Log retrieval runs in a `ThreadPoolExecutor(1)`. The `timeout_seconds` budget propagates from `build_evidence_pack` → `log_tools.get_execution_logs` → `AutomationEdgeClient.get_execution_logs`, capping both the thread future and the T4 polling loop.
3. If the budget expires or T4 returns a pending status, `build_evidence_pack` returns `success: True` with `evidence_pack: None` and a `log_retrieval` dict containing `log_retrieval_status` (`"pending"` or `"timed_out"`) and `debug_log_request_id`.
4. `diagnose_from_evidence_pack` detects the missing evidence pack, synthesizes a metadata-only pack, instructs the LLM that confidence must not exceed 0.5, and programmatically caps the returned confidence. The `log_retrieval` hint is surfaced to the agent for follow-up.
5. Progress callbacks (`on_progress`) emit phase-level status updates ("Fetching execution metadata...", "Retrieving execution logs...", "Processing and filtering log evidence...", "Evidence pack ready.") so users see activity during long operations.

### Design principles

1. **Deterministic filtering first** — time-window, request-ID, and step-name filters are applied before any LLM call, keeping context tokens low.
2. **Structured output** — the LLM is instructed to return strict JSON, enabling downstream agents to act on diagnosis results programmatically.
3. **Bounded execution** — all I/O is subject to a wall-clock budget; the pipeline always returns within `max_wait_seconds`, degrading gracefully rather than blocking.
4. **Additive integration** — all new tools are registered via the existing `ToolRegistry` and are available to the `DiagnosticAgent` through the `diagnostics` category.

---

## 6. Safety, Governance, and Observability

- **Safety controls:**
  - Risk tiers per tool (`read_only`, `safe_write`, `high_risk`).
  - Max iterations, max restarts, max bulk operations enforced via config.
  - Protected workflow list (never auto-restarted; must escalate).
  - Issue recurrence thresholds trigger auto-escalation.
- **Approvals:**
  - On-shift roster in `custom/helpers/roster.py`.
  - `Approval` table persists pending decisions.
  - Teams approvals use Adaptive Cards with button payloads mapped back to semantic approve/reject intents.
  - Empty reviewer lists fail closed instead of allowing any user to approve.
- **Observability (built-in):**
  - Structured app + audit logs via `logging_setup.py`.
  - In-memory metrics collector (`config/metrics.py`) tracks turn latencies, token usage, and tool success rates. Exposed via `GET /api/metrics`.
  - Tool calls, errors, and RCA indexing all logged.
  - Health endpoints:
    - `agent_server.py` → `/health`.
    - Underlying AE tools expose additional telemetry via their own APIs.
- **Observability (LangFuse — optional):**
  - Enabled via `LANGFUSE_ENABLED=true` with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST`.
  - Self-hosted (Docker Compose) or LangFuse Cloud.
  - **Core path (inside orchestrator trace)**:
    - **Root trace**: `Orchestrator.handle_message()` — session ID, user ID, input, phase, final response.
    - **Generations**: Every `VertexAIClient.chat()` and `chat_with_tools()` call — model name, truncated input/output, token usage.
    - **Tool spans**: `ToolExecutor.execute()` — tool name, sanitized parameters, success/error, latency.
    - **RAG spans**: `PgVectorRAGEngine.search()` — collection, query, top_k, result count.
    - **Embedding spans**: `VertexEmbedder.embed()` — model, truncated text, vector dimension.
    - **Approval spans**: `ApprovalGate.classify_approval_turn()` — intent, confidence, method.
  - **Extended coverage (independent root traces for code paths outside the orchestrator)**:
    - `RCAAgent.handle()` — root trace for RCA generation with child spans for business/technical report LLM calls.
    - `RCAAgent._index_as_past_incident()` — separate root trace for background-thread RAG indexing with LLM root-cause extraction.
    - `MessageGateway._classify_message_intent()` — root trace for LLM intent classification when agents are busy.
    - `scheduler.health_check_handler()` — root trace for background health-check tool calls.
    - `scheduler.daily_summary_handler()` — root trace for background daily summary LLM generation.
    - `issue_classifier._llm_classify()` — root trace for custom Cognibot LLM-based issue classification.
    - `agent_analyze_logs()` (MCP) — root trace for MCP server AI diagnostic LLM call.
    - `ConversationState.generate_summary()` — root trace for admin-triggered conversation summary.
    - `api_tools_test` (admin endpoint) — root trace for admin tool testing.
  - Implementation details:
    - `config/observability.py` — lazy-initialized `Langfuse` singleton, `trace_context`/`span_context` context managers, `create_generation` helper.
    - Thread-local propagation via `set_current_trace()`/`get_current_trace()` in `config/llm_client.py` — avoids changing any existing function signatures.
    - Independent root traces for code running outside the orchestrator (RCA agent, scheduler, gateway, MCP, admin) — each calls `set_current_trace()` so child LLM generations and tool spans auto-nest.
    - Background threads (RCA indexing) create separate root traces since OTel context doesn't propagate across thread boundaries.
    - No-op stubs (`_NoOpSpan`) returned when disabled — zero runtime overhead, no conditional checks needed.
    - All LangFuse calls wrapped in try/except — tracing failures never break agent pipeline.
  - See `SETUP_GUIDE.md` §15 for full setup, architecture diagram, and troubleshooting.

---

## 7. Extensibility Patterns

- **Adding new tools:**
  - Implement handler in appropriate `tools/*.py`.
  - Define `ToolDefinition` (name, description, params, tier, category, always-available flag).
  - Register in `tools/registry.py`.
  - Re-index tools into RAG via `python -m rag.index_all`.
- **Adding new RAG collections:**
  - Define new collection name in `rag/engine.py`.
  - Store JSON/MD docs under `rag/data/<collection_name>/`.
  - Extend `index_all.py` and any helper search methods.
- **Customizing classification:**
  - Update `classification_signals.py` and `issue_tracker.py` for additional signals.
  - Tune LLM prompts in classifier sections for domain-specific language.
- **Multi-channel behaviour:**
  - All channels share the same orchestration core; per-channel differences live only in:
    - Entry adapters (`main.py`, `agent_server.py`, `custom/custom_hooks.py`, `custom_cognibot/custom_hooks.py`).
    - Presentation layer (e.g., Teams cards vs webchat text).

---

## 8. Deployment Views

### 8.1 Minimal Local Dev Stack

- Components:
  - PostgreSQL (with or without pgvector).
  - Agent server (`agent_server.py`).
  - Mock AE API (`tests/mock_ae_api.py`).
  - Local webchat (browser).
- Use cases:
  - Fast iteration on tools and orchestrator.
  - Unit test scenarios via `tests/test_scenarios.py`.

### 8.2 On-Prem AI Studio + Teams

- Components:
  - AI Studio Engine, Cognibot, KM, and Chatbot-Webservice.
  - AI Studio Extension zip containing `custom/`, `config/`, `agents/`, `tools/`, `rag/`, `gateway/`, `state/`, `templates/`, `documents/`.
  - PostgreSQL with pgvector as shared DB.
  - Azure Bot + MS Teams channel.
- Data flow:
  - Teams → Azure Bot → Cognibot (Extension hooks) → Agent logic → AE APIs/DB/RAG → Cognibot → Teams.

---

## 9. AE AI Hub — Agentic Orchestrator (Add-on Module)

A visual no-code workflow builder that runs as a **sidecar** to the existing agent system. Users drag-and-drop LLM agents, MCP tools, and logic nodes onto a React Flow canvas to build executable DAGs.

- **Frontend:** React 19, `@xyflow/react`, Zustand, Tailwind CSS, shadcn/ui — port 8080.
- **Backend:** FastAPI, SQLAlchemy, Celery, PostgreSQL — port 8001.
- **Integration:** Consumes the existing 106 MCP tools via a bridge endpoint. Does not modify any parent codebase files.

Full documentation in the `orchestrator/` directory:

- Architecture: `orchestrator/TECHNICAL_BLUEPRINT.md`
- Setup: `orchestrator/SETUP_GUIDE.md`
- Runtime walkthrough: `orchestrator/HOW_IT_WORKS.md`

---

This blueprint is intended as the single technical reference for architects and senior engineers; implementation details and step-by-step instructions remain in `SETUP_GUIDE.md` and the implementation guides.


