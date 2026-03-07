> - **Performance Optimizations (2026-03-07)**: Parallel RAG fan-out, configurable embedding dimension, batched DB queries, shared MCP executor, AE path caching, coalesced state writes, capped execution polling. Details in §5.5 below.
>
> - **Documentation Update (2026-03-07)**: MCP server still exposes **106 tools** (71 P0 + 35 support-priority P1), while the main-app bridge now catalogs the full set and lazily hydrates long-tail MCP and workflow-backed tools on demand. See `SETUP_GUIDE.md` §13 and `mcp_server/README.md`.
>
> - **Multi-Agent 2.0 (Patch 2026-03-06)**:
>   - **Strict Tool Isolation**: Implemented role-based tool filtering. Diagnostic specialists are restricted to `logs`/`status` tools; Remediation specialists to `remediation`/`config`.
>   - **Verification Loop**: Added mandatory specialist handoff. Remediation actions now trigger an automatic cross-agent verification turn to confirm resolution.
>   - **Agent Memory**: Added `SharedContext` memory buckets. Specialists now maintain short-term state (e.g., specific log patterns) across multi-turn delegation chains.
>   - **Context-Aware RAG**: RAG queries now automatically ingest active issue metadata (error signatures, workflow names) to prioritize relevant SOPs and KB articles.
>   - **Rich Notifications**: Adaptive Card templates are prepared in `templates/`, but `custom/helpers/teams.py` currently uses Markdown-based fallbacks for broader client compatibility during approvals and escalations.
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
> - Validation status: `pytest -q` passed (`99 passed, 1 skipped`; `tests/test_e2e_api.py` is opt-in via `RUN_E2E=1`).
>
## AutomationEdge Agentic Support — How It Works (Step‑by‑Step)

**Purpose:** This document explains, step by step, how the Agentic Support system works end‑to‑end, with pointers to **code files** and **setup/config docs** so you can trace behaviour or extend it safely.

For installation and environment setup, see **`SETUP_GUIDE.md`**. For a high‑level architecture view, see **`TECHNICAL_BLUEPRINT.md`**.

---

## 0. Prerequisites & Configuration (Where Things Are Wired)

Before any request flows through the system, the following must be in place:

- **Environment / config:**
  - `.env` (or AI Studio Extension env) — see **Section 3 of `SETUP_GUIDE.md`**.
  - `config/settings.py` — reads env vars and exposes `CONFIG`:
    - `AE_BASE_URL`, `AE_API_KEY`
    - `POSTGRES_DSN`
    - `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`
    - `VERTEX_AI_MODEL`, `EMBEDDING_MODEL`
    - Safety knobs: `MAX_AGENT_ITERATIONS`, `MAX_RAG_TOOLS`, `MAX_RESTARTS_PER_WORKFLOW`, `MAX_BULK_OPERATIONS`, `RECURRENCE_ESCALATION_THRESHOLD`, `PROTECTED_WORKFLOWS`
  - `config/llm_client.py` — initializes Gemini (Vertex AI) using `CONFIG`.
  - `config/logging_setup.py` — configures app + audit loggers and log file locations.

- **Database & RAG:**
  - PostgreSQL (with or without `pgvector`) created as per **Section 2 of `SETUP_GUIDE.md`**.
  - Tables created by **`setup_db.py`**:
    - `rag_documents`, `conversation_state`, `issue_registry`
  - Data indexed via:
    - `python setup_db.py`
    - `python -m rag.index_all`
  - RAG engine implementation: `rag/engine.py` (pgvector + numpy fallback).

- **Tool catalog:**
  - Tool definitions and handlers in `tools/*.py`.
  - Cataloged through `tools/registry.py`.
  - Long-tail MCP and workflow-backed tools are hydrated lazily at runtime.
  - Indexed into RAG at startup from the catalog layer (see `main.py` and `SETUP_GUIDE.md` Section 5.2).

Once these are configured, every channel (Teams, AI Studio webchat, standalone webchat/CLI) uses the same **orchestration core** described below.

---

## 1. Step 1 — User Sends a Message (Per Channel)

### 1.1 MS Teams (Production path)

1. **Teams user** sends a message to the bot.
2. Message flows via **Azure Bot Service** into **AI Studio Cognibot**.
3. Cognibot invokes the Extension hook:
   - **File:** `custom/custom_hooks.py`
   - **Entry:** `CustomChatbotHooks.api_messages_hook(request, activity)`  
   - Contract details and setup are described in:
     - `AI_Studio_OnPrem_Agentic_Support_StepByStep(1).md`
     - Sections 6, 7, 12 of `SETUP_GUIDE.md`.

### 1.2 AI Studio webchat (Python project)

1. User interacts with the AI Studio web UI.
2. AI Studio calls your Python entrypoint:
   - **File:** `main.py`
   - **Entry:** `handle_chat_message(message, session_id, user_id, user_role)`
3. This immediately delegates to the **message gateway** (see Step 2).

### 1.3 Standalone webchat / CLI (local dev)

- **Standalone agent server:**
  - **File:** `agent_server.py`
  - **Endpoints:**
    - `/` — serves `webchat.html`
    - `/chat` — JSON in/out (non‑streaming)
    - `/chat/stream` — Server‑Sent Events (progress + final answer)
  - Both call into the same `MessageGateway` as AI Studio.
  - Setup and usage: **Section 8.2 of `SETUP_GUIDE.md`**.

- **CLI:**
  - Run `python main.py` and use the interactive prompt.

---

## 2. Step 2 — Channel Adapter → Message Gateway

Regardless of channel, messages are normalized and sent to the **Message Gateway**.

### 2.1 MS Teams adapter (Extension layer)

- **File:** `custom/custom_hooks.py`
- Key responsibilities:
  1. Normalize Bot Framework `activity` → dict (thread ID, message ID, text, user ID).
  2. Acquire per‑thread PostgreSQL advisory lock:
     - **File:** `custom/helpers/locks.py`
  3. Drop duplicate messages:
     - **File:** `custom/helpers/db.py`
     - **Model:** `ProcessedMessage` in `custom/models.py`
  4. Handle smalltalk fast‑path (`hi`, `hello`, etc.).
  5. Classify multi‑issue context and approvals:
     - **File:** `custom/helpers/issue_classifier.py`
  6. Route to the **support agent**:
     - **File:** `custom/functions/python/support_agent.py`
     - **Entry:** `handle_support_turn(...)`

In **agentic mode**, `support_agent.py` acts mainly as a planner/executor front for the orchestrator; the full LLM+tool loop is handled in `agents/orchestrator.py`. In deterministic mode, `support_agent.py` can execute a fixed plan using REST tools only.

### 2.2 Webchat / AI Studio adapters

- **`main.py`**:
  - Logs the message via `logging_setup`.
  - Calls:
    - `gateway.MessageGateway.process_message(conversation_id, user_message, user_id, user_role)`

- **`agent_server.py`**:
  - HTTP handlers call the same `MessageGateway.process_message` (optionally with a `ProgressCallback` from `gateway/progress.py` for streaming).

---

## 3. Step 3 — MessageGateway: Concurrency & Intent

- **File:** `gateway/message_gateway.py`
- **Class:** `MessageGateway`

Responsibilities:

1. **Session management:**
   - `get_or_create_session(conversation_id, user_id, user_role)`  
   - Creates/restores a `ConversationState` (see Step 4) and per‑session lock.

2. **Concurrent message handling:**
   - If **no agent currently working** → call:
  3. **Agent Selection & Delegation (A2A):**
   - Normalizes message and calls `AgentRouter.route()`.
   - `AgentRouter` scores the user message against all registered agents (`Supervisor`, `Diagnostic`, `Remediation`, `RCA`).
   - If `Supervisor` is selected, it can proactively hand off to specialists:
     - **File:** `agents/orchestrator_agent.py` (Supervisor)
     - Technical Investigate → `diagnostic_agent.py` (restricted to `status`, `logs`, `dependency`, `file` tools).
      - Fix/Restart → `remediation_agent.py` (restricted to `remediation`, `notification`, `config` tools).
      - RCA Generation → `rca_agent.py` (specialist for generating Root Cause Analysis reports via `generate_rca_report` tool).
    - Specialists use **Agent Memory** in `SharedContext` to persist state between turns without cluttering the global history.
    - Messages can contain a delegation chain (e.g., Supervisor → Diagnostic).

---

## 4. Step 4 — ConversationState & IssueTracker

### 4.1 Conversation state

- **File:** `state/conversation_state.py`
- Tracks per conversation:
  - `conversation_id`, `user_id`, `user_role`
  - Conversation phase (`IDLE`, `INVESTIGATING`, `AWAITING_APPROVAL`, etc.)
  - Past messages (`messages[]`)
  - Tool call log
  - Active findings and RCA data
  - Flags like `is_agent_working`, `interrupt_requested`

`ConversationState.save()` persists to the DB schema created by `setup_db.py` (see `SETUP_GUIDE.md` Section 4).

### 4.2 Multi‑issue tracking

- **File:** `state/issue_tracker.py`
- **Classes:** `IssueTracker`, `Issue`, `IssueStatus`, `MessageClassification`

Responsibilities:

1. On each message, classify it as:
   - `NEW_ISSUE`
   - `CONTINUE_EXISTING`
   - `RELATED_NEW` (cascade)
   - `RECURRENCE`
   - `FOLLOWUP`
   - `STATUS_CHECK`
2. Use three‑layer logic:
   - Fast heuristics (keywords like “different issue”, “failed again”).
   - Workflow + error signature matching.
   - LLM classification with Vertex AI when ambiguous.
3. Persist issues and state to PostgreSQL:
   - Tables: `issue_registry`, `issue_tracker_state`  
   - Created by `setup_db.py` — see `AE_Agentic_OpsSupport_Implementation_Guide_Part2.md` and `SETUP_GUIDE.md` Section 4.2 / 4.5.

The orchestrator (Step 5) always operates within the context of the **current issue** chosen or created by `IssueTracker`.

---

## 5. Step 5 — Orchestrator: LLM + Tools + RAG

- **File:** `agents/orchestrator.py`
- **Class:** `Orchestrator`

### 5.1 Entry

- Called from:
  - `MessageGateway.process_message` (webchat/AI Studio).
  - Extension planner (`support_agent.py`) when agentic mode is used.

- Main method:
  - `handle_message(user_message: str, state: ConversationState) -> str`
  - High‑level flow:
    1. Append user message to `state.messages`.
    2. Get or create `IssueTracker` for `state.conversation_id`.
    3. If awaiting approval → delegate to `_handle_approval_response`.
    4. Else, classify message via `IssueTracker`.
    5. Route to `_process_message` with an active issue.

### 5.2 Building the system prompt

- `Orchestrator._build_system_prompt(state, tracker)`:
  - Combines:
    - **Core behaviour** (verification first, auditability).
    - **Tool catalog** (from `tools/registry.py`).
    - **Persona** (business vs technical; see Section 11 of Implementation Guide).
    - **Issue context** (active issue summary, findings, recurrences).
  - Prompt templates and principles documented in:
    - `AE_Agentic_OpsSupport_Implementation_Guide_Part2.md` (Appendix C).

### 5.3 LLM tool‑calling loop

- Uses `config/llm_client.py` (`VertexAIClient.chat_with_tools`):
  1. Send messages + tool schema to Gemini.
  2. Receive tool call proposals (function‑calling style).
  3. Dispatch tool calls via `tools/registry.py`.
  4. Log results in:
     - `ConversationState.tool_call_log`
     - Audit logger from `logging_setup.py`
  5. Feed tool outputs back into the LLM until:
     - A natural‑language answer is produced, or
     - `MAX_AGENT_ITERATIONS` is hit (safety stop).

- **Tool selection & retrieval strategy** (large catalogs):
  - Use **Hybrid RAG** (`rag/engine.py`) to select tools:
    - Semantic search (pgvector) matches conceptual intent.
    - Keyword search (tsvector) matches specific workflow names or IDs.
    - RRF (Reciprocal Rank Fusion) merges results for high accuracy.
    - **RAG Context Enrichment (Feature 1.1)**:
      - Merges user input with `active_issue` metadata (Workflows + Error Signatures) before calling the search engine.
      - Pushes relevant SOPs and incidents to the top of the context block.
    - Search a unified tool catalog that includes custom tools, MCP tools, and AE workflow-backed tools.
    - Keep a small eagerly hydrated core of `always_available` tools and lazily hydrate selected long-tail tools only when they enter the turn-local set.
    - Include `discover_tools` meta‑tool to let the LLM search mid‑conversation and expand the active set on demand.
  - **Performance:** After one `embed_query()` call, the four RAG collection searches (tools, kb, sops, incidents) run in parallel via `ThreadPoolExecutor(4)`.

### 5.4 Integrating findings with IssueTracker

During `_process_message`:

- After each significant tool call, orchestrator updates the active issue via `IssueTracker`:
  - `add_workflow_to_issue(issue_id, workflow_name)`
  - `add_error_signature(issue_id, error_signature)`
  - `add_finding_to_issue(issue_id, finding_dict)`

This creates durable, per‑issue context that supports:
- Recurrence detection.
- Cascade understanding.
- RCA quality.

---

### 5.5 Performance Optimizations

Several hot‑path optimizations reduce per‑request latency:

1. **Parallel RAG fan‑out** (`agents/orchestrator.py`):
   After a single `rag.embed_query()`, the four searches (`search_tools`, `search_kb`, `search_sops`, `search_past_incidents`) run concurrently in a `ThreadPoolExecutor(4)`.

2. **Configurable embedding dimension** (`rag/engine.py`, `config/settings.py`):
   Set `EMBEDDING_DIMENSION=768` to skip the live Vertex AI probe at startup. If unset, the original dimension‑probe logic is retained as a fallback.

3. **Batched workflow catalog queries** (`tools/automationedge_client.py`):
   `resolve_cached_workflow_name` now uses a single `IN(...)` query for all name variants. `get_cached_workflow_info` returns both `workflow_id` and `parameters` in one DB call.

4. **Shared MCP executor** (`tools/mcp_tools.py`):
   A shared `ThreadPoolExecutor(4)` is used for `_run_async` instead of creating a new executor per tool call.

5. **AE REST path caching** (`mcp_server/ae_client.py`):
   `AEClient._try_paths` caches the successful `(path_index, use_rest)` per `(method, paths)` key. Cache entries are evicted on 4xx/5xx failures and the full fallback list is retried.

6. **Coalesced state writes** (`state/conversation_state.py`):
   `add_message` defers DB inserts; all pending rows are flushed in a batch `INSERT` during `save()` at turn boundaries.

7. **Capped execution polling** (`tools/automationedge_client.py`):
   Default `max_attempts` reduced to 15 (~45 s). If the execution hasn't reached a terminal state, polling returns `status="in_progress"` with an `in_progress_hint` message instead of blocking for 100+ iterations.

---

## 6. Step 6 — Tools: Typed Actions on AE & DB

- **Directory:** `tools/`
- **Key files:**
  - `base.py` — `AEApiClient`, `ToolDefinition`, base utilities.
  - `registry.py` — registers tools and exposes them to orchestrator and RAG.
  - `status_tools.py`, `log_tools.py`, `file_tools.py`, `remediation_tools.py`, `dependency_tools.py`, `notification_tools.py`, `general_tools.py`.

Tools are grouped into categories (read‑only vs write/risky) as documented in:
- `SETUP_GUIDE.md` Section 11.3
- `AE_Agentic_OpsSupport_Implementation_Guide_Part2.md` (Appendix B)

Implementation detail:

- Each tool handler:
  - Validates parameters.
  - Calls AE REST APIs (`AEApiClient`) or the ops DB using read‑only SQL.
  - Returns structured JSON back to the LLM loop.

---

## 7. Step 7 — ApprovalGate & Safe Remediation

- **File:** `agents/approval_gate.py`
- Collaborates with:
  - `config/settings.py` safety values.
  - `state/issue_tracker.py` recurrence counts.
  - Protected workflows list (`PROTECTED_WORKFLOWS`).

High‑level behaviour:

1. Classify each proposed remediation tool call:
   - `read_only`, `safe_write`, `medium_risk`, `high_risk`.
2. Check against:
   - Max restarts per workflow.
   - Max bulk operations.
   - Recurrence thresholds.
   - Protected workflows (never auto‑restart).
3. **RBAC Policy Check:**
   - Checks user role (admin, dev, support) against tool tier (high_risk, medium_risk, etc).
   - If role rank < minimum required rank for tier → block and require approval from authorized user.
4. Decide whether:
   - Auto‑run the tool.
   - Require explicit approval.
   - Block and suggest escalation.

In the **Extension layer**:

- Additional approval logic exists in:
  - `custom/functions/python/support_agent.py`
  - `custom/models.py` (`Approval` model)
  - `custom/helpers/policy.py` (for capability/risk tags)
  - `custom/helpers/roster.py` (on‑shift tech selection)

---

## 8. Step 8 — Executing Remediation & Verifying

Once remediation is allowed (auto‑run or approved):

1. **Execution:**
   - Orchestrator (or Extension executor) calls remediation tools from `tools/remediation_tools.py` or REST tools from the Extension layer.
   - All calls:
     - Use idempotency keys or locks where needed.
     - Are logged in audit logger (`logs/audit.log`).

2. **Verification:**
   - Orchestrator invokes read‑only tools again:
     - `check_workflow_status`, `get_execution_logs`, `list_recent_failures`, etc.
   - Confirms that:
     - Target workflows are now healthy.
     - Downstream cascades are cleared (if relevant).

3. **Issue lifecycle:**
   - On successful remediation:
     - `IssueTracker.resolve_issue(issue_id, resolution_summary)`
   - On recurrence:
     - `IssueTracker.reopen_issue(issue_id)` and increment recurrence count.
     - **Automatic Verification Loop**:
      - After executing a fix, `RemediationAgent` records a finding and **mandatory delegates back** to `DiagnosticAgent`.
      - `DiagnosticAgent` performs a target health check (logs/status) to confirm the fix before the session is resolved.
    - If above `RECURRENCE_ESCALATION_THRESHOLD` → escalation agent engaged.

4. **Escalation:**
   - **File:** `agents/escalation.py`
   - Uses notification tools and/or ticket creation to hand off to L2/operations.

---

## 9. Step 9 — RCA Generation & RAG Feedback Loop

- **File:** `agents/rca_agent.py`
- **Class:** `RCAAgent`

Responsibilities:

1. Generate RCA (via `generate_rca_report` tool):
   - Business RCA (plain English, no jargon) when `user_role == "business"`.
   - Technical RCA (timeline, IDs, error details) when `user_role == "technical"`.
   - Uses:
     - Current issue findings (`IssueTracker` + `ConversationState`).
     - Tool call logs.
     - Related past incidents via `rag.engine.get_rag_engine().search_past_incidents`.
     - SOP guidance via `rag.engine.get_rag_engine().search_sops`.

2. Index back into RAG:
   - `RCAAgent._index_as_past_incident` calls:
     - `rag.engine.get_rag_engine().index_past_incident(...)`
   - Stores `summary`, `root_cause`, `resolution`, `workflows_involved` in the `past_incidents` collection.

This closes the loop where **each resolved incident improves future investigations**.

---

## 10. Step 10 — Persona Filtering & Final Response

Before sending the final response to the user:

1. Orchestrator applies **persona‑based filtering**:
   - Implemented in `Orchestrator` (see Phase 8 in `AE_Agentic_OpsSupport_Implementation_Guide_Part2.md`).
   - Business persona:
     - Hide workflow IDs, execution IDs, stack traces.
     - Emphasize business impact, resolution, and prevention.
   - Technical persona:
     - Include workflow names, execution IDs, error codes, timestamps.
     - Summarize tool calls and decisions.

2. The message is sent back via:
   - Webchat / CLI → `agent_server.py` or `main.py`.
   - AI Studio Extension → `custom/custom_hooks.py` → Cognibot → Teams.

3. `ConversationState.save()` ensures:
   - All context is persisted for future turns, recurrences, and RCA.

---

## 11. End‑to‑End Example With References

**Scenario:** “Claims batch processing failed this morning. Can you fix it?”

1. **Message ingestion**
   - Teams → Azure Bot → Cognibot → `custom/custom_hooks.py` → `handle_support_turn`.
   - Or AI Studio/webchat → `main.py` → `MessageGateway.process_message`.

2. **Session & issue setup**
   - `MessageGateway.get_or_create_session` → `ConversationState`.
   - `IssueTracker.classify_message` → `NEW_ISSUE` → `IssueTracker.create_issue`.

3. **Investigation**
   - Orchestrator builds prompt with issue context.
   - LLM selects tools from:
     - `status_tools.check_workflow_status`
     - `log_tools.get_execution_logs`
     - `file_tools.check_input_file`
     - Possibly `dependency_tools.get_workflow_dependencies`
   - Tool calls executed via `tools/registry.py` and AE REST API (`tools/base.py`).
   - Findings recorded into `IssueTracker` (workflows + error signatures).

4. **Proposed remediation**
   - LLM suggests `restart_execution` for `claims_batch_processor`.
   - `ApprovalGate` checks:
     - Workflow not in `PROTECTED_WORKFLOWS`.
     - Restart count below `MAX_RESTARTS_PER_WORKFLOW`.
   - System enters `AWAITING_APPROVAL` (unless policy allows auto‑run).

5. **Approval**
   - User replies “approve”:
     - Extension: `Approval` record updated in `custom/models.py`.
     - Orchestrator: `_handle_approval_response` confirms and moves to execution.

6. **Execution & verification**
   - `remediation_tools.restart_execution` runs via AE REST API.
   - Orchestrator calls `check_workflow_status` again to confirm success.
   - Issue resolved:
     - `IssueTracker.resolve_issue` with resolution summary.

7. **RCA**
   - User asks “Give me an RCA”:
     - `RCAAgent.generate_rca` uses current findings + logs + past incidents.
     - RCA stored back into `past_incidents` via `index_past_incident`.

8. **Final response**
   - Persona‑filtered answer:
     - Business: “Claims processing was delayed because the input file was missing. After the file arrived, we restarted the process; future checks will ensure the file is present before starting.”
     - Technical: Includes workflow name, execution IDs, timestamp, and error string.

This flow touches the key code and configuration surfaces described in this document, and provides a template for reasoning about any new scenarios you introduce.


