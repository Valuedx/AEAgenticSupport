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
  - Registered in `tools/registry.py`.
  - Indexed into RAG at startup (see `main.py` and `SETUP_GUIDE.md` Section 5.2).

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
     - `self.orchestrator.handle_message(user_message, state)`
   - If **agent already working**:
     - Classify new message intent via `_classify_message_intent`:
       - `ADDITIVE` (extra details for current issue)
       - `INTERRUPT` (urgent, switch topics)
       - `CANCEL` (stop current work)
       - `APPROVAL` (approve/reject)
       - `NEW_REQUEST` (queue for later)
     - Uses:
       - Simple keyword rules (`stop`, `urgent`, etc.).
       - LLM classification via `config/llm_client.py` for ambiguous cases.

3. **Queueing and flags:**
   - Queues follow‑up user messages in `ConversationState`.
   - Sets `interrupt_requested` when user demands interruption or cancel.

At the end of this step, a message plus its session context are passed to the **Orchestrator**.

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

- **Tool selection strategy** (large catalogs):
  - Always include **always‑available** tools:
    - Core status/logs + general tools (`call_ae_api`, `query_database`, `search_knowledge_base`).
  - Use RAG (`rag/engine.py`) to select up to `MAX_RAG_TOOLS` highest‑relevance typed tools.
  - Include `discover_tools` meta‑tool to let the LLM search mid‑conversation.

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
3. Decide whether:
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
     - If above `RECURRENCE_ESCALATION_THRESHOLD` → escalation agent engaged.

4. **Escalation:**
   - **File:** `agents/escalation.py`
   - Uses notification tools and/or ticket creation to hand off to L2/operations.

---

## 9. Step 9 — RCA Generation & RAG Feedback Loop

- **File:** `agents/rca_agent.py`
- **Class:** `RCAAgent`

Responsibilities:

1. Generate RCA:
   - Business RCA (plain English, no jargon) when `user_role == "business"`.
   - Technical RCA (timeline, IDs, error details) when `user_role == "technical"`.
   - Uses:
     - Current issue findings (`IssueTracker` + `ConversationState`).
     - Tool call logs.
     - Related past incidents via `rag.engine.get_rag_engine().search_past_incidents`.

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

