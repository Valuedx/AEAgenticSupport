> - **Performance Optimizations (2026-03-07)**: Parallel RAG fan-out (4 concurrent searches), configurable embedding dimension, batched workflow catalog queries, shared MCP executor, coalesced state writes, AE path caching, capped execution polling. See §4.2 and `SETUP_GUIDE.md` §10.
>
> - **Documentation Update (2026-03-07)**: MCP server still exposes **106 tools** (P0 + P1 support), while the main-app bridge now catalogs the full set and eagerly hydrates only a curated subset; the rest are exposed via RAG and `discover_tools` with lazy runtime hydration. See `SETUP_GUIDE.md` §13 and `mcp_server/README.md`.
>
> - **Tool Ranking Update (2026-03-07)**: `discover_tools` and turn-local tool hydration now share a catalog-aware ranking step that blends retrieval score with source, risk, latency, mutation, direct-callability, and observed execution-history signals. Recent outcomes are weighted more heavily, and agent-scoped feedback is used when available.
>
> - **Tool Architecture Target (2026-03-07)**: Proposed scale-out refactor for unified catalog, turn-local tool hydration, ranking, and source-specific execution handling is documented in `TOOL_ARCHITECTURE_TARGET.md`.
>
> - **Multi-Agent 2.0 (Patch 2026-03-06)**:
>   - **Strict Tool Isolation**: Implemented role-based tool filtering. Diagnostic specialists are restricted to `logs`/`status` tools; Remediation specialists to `remediation`/`config`.
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

**Version:** 1.1  
**Last updated:** 2026-03-07

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
- **Chat interfaces**:
  - AI Studio web chat / Extension.
  - MS Teams via Azure Bot / Cognibot.
  - Standalone webchat via `agent_server.py`.

Request path examples:
- **AI Studio webchat → `main.py` → `MessageGateway` → `AgentRouter` → `Supervisor` → (Delegation) → `Specialist` → tools + Hybrid RAG → response**
- **MS Teams → Azure Bot → Cognibot hooks → `support_agent` → tools + RAG → response**
- **Browser webchat → `agent_server.py` → `MessageGateway` → `Orchestrator` → tools + RAG → response**

---

## 2. Runtime Components

- **`config/`**
  - `settings.py`: Central configuration and env loading (AE URLs, GCP, DB, safety limits).
  - `llm_client.py`: Vertex AI Gemini client (chat + tools).
  - `logging_setup.py`: Application + audit loggers.
  - `classification_signals.py`: Heuristic patterns for classifiers.
- **`agents/`**
  - `agent_router.py`: Central dispatcher for scoring and routing messages to agents.
  - `orchestrator_agent.py`: The **Supervisor** Agent. Coordinates high-level planning and chooses specialists.
  - `diagnostic_agent.py`: **Techncial Specialist**. Investigates logs, status, and infrastructure.
  - `remediation_agent.py`: **Resolution Specialist**. Restarts workflows and executes fixes.
  - `approval_gate.py`: RBAC-aware risk tiering and approval workflow.
  - `escalation.py`: Escalation logic and notifications.
  - `rca_agent.py`: Business and technical RCA generation + indexing into RAG.
  - `scheduler.py`: Background tasks for proactive health checks and webhook handling.
- **`tools/`**
  - `base.py`: AE API client and `ToolDefinition`.
  - `registry.py`: Tool catalog, categories, and registration.
  - `*_tools.py`: Typed tools grouped by concern (status, logs, files, remediation, etc.).
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
- **`templates/`**
  - `rca_templates.py`: Prompt building helpers and RCA structures.
  - `adaptive_cards.py`: **[NEW]** JSON schema generators for MS Teams rich notifications.
- **`custom/` (AI Studio Extension layer)**
  - `custom_hooks.py`: Async Cognibot hooks (`api_messages_hook`) with locks, dedupe, and routing.
  - `models.py` + `migrations/`: Django models for cases, approvals, processed messages, links.
  - `helpers/`: Locks, DB helpers, RAG stubs, REST tool client, roster, Teams helpers, issue classifier.
  - `functions/python/support_agent.py`: Planner + executor for Extension, using REST tools.
- **`custom_cognibot/`**
  - Thin-proxy hooks used for local Cognibot → standalone agent server integration.
- **`agent_server.py`**
  - Standalone Flask/SSE server that exposes the agent as HTTP (`/chat`, `/chat/stream`, webchat UI).
- **`main.py`**
  - AI Studio project entrypoint (`handle_chat_message`), used for webchat / Extension deployments.

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

- `custom/custom_hooks.py`:
  - Async `api_messages_hook(request, activity)`:
    - Converts Bot Framework activity → dict, extracts `thread_id`, message ID, text, user ID.
    - Acquires per-thread PostgreSQL advisory lock.
    - Drops duplicate messages via `ProcessedMessage`.
    - Handles smalltalk fast-path.
    - Integrates issue classification and approval flows (see Implementation Guide).
    - Delegates to `handle_support_turn` in `support_agent.py`.
- `custom/functions/python/support_agent.py`:
  - Planner:
    - Uses RAG (via REST or direct pgvector) over SOPs and tool docs.
    - Builds a strict JSON plan with steps and risk tags.
  - Executor:
    - Auto-runs safe steps using REST tools.
    - Creates `Approval` rows for risky steps (with roster targeting).
    - Updates/creates tickets and escalations through typed tools.

### 5.3 Standalone Agent Server + Webchat

- `agent_server.py`:
  - `/chat`: JSON request → synchronous response.
  - `/chat/stream`: SSE events for progress + final answer.
  - `/`: Serves `webchat.html`.
- Thin-proxy Cognibot mode:
  - `custom_cognibot/` hooks forward Cognibot traffic to `/chat`.
  - Used for local testing of full Cognibot → agent pipeline.

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

## 6. Safety, Governance, and Observability

- **Safety controls:**
  - Risk tiers per tool (`read_only`, `safe_write`, `high_risk`).
  - Max iterations, max restarts, max bulk operations enforced via config.
  - Protected workflow list (never auto-restarted; must escalate).
  - Issue recurrence thresholds trigger auto-escalation.
- **Approvals:**
  - On-shift roster in `custom/helpers/roster.py`.
  - `Approval` table persists pending decisions.
  - Typed approvals in Teams (text today; upgrade path to Adaptive Cards).
- **Observability:**
  - Structured app + audit logs via `logging_setup.py`.
  - Tool calls, errors, and RCA indexing all logged.
  - Health endpoints:
    - `agent_server.py` → `/health`.
    - Underlying AE tools expose additional telemetry via their own APIs.

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

This blueprint is intended as the single technical reference for architects and senior engineers; implementation details and step-by-step instructions remain in `SETUP_GUIDE.md` and the implementation guides.


