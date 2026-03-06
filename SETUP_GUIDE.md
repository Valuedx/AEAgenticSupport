> **Documentation Update (2026-03-06)**  
> Patch release notes:
> - **Multi-Agent Orchestration (Feature 2.1)**: Refactored existing monolithic orchestrator into a Multi-Agent Supervisor system. Added `DiagnosticAgent` and `RemediationAgent` specialists with A2A delegation protocol.
> - **Hybrid Search (Feature 2.2)**: Upgraded RAG engine to support hybrid search using `pgvector` (semantic) + `tsvector` (keyword) + Reciprocal Rank Fusion (RRF). Added `DocumentProcessor` for PDF/Markdown/JSON with table extraction.
> - **Multi-Language Support**: Added dynamic LLM-based language detection and localized system instruction propagation.
> - **Enterprise Security (Feature 2.6)**: Implemented RBAC-based approval gates and automatic PII masking in JSON logs.
> - **Proactive Monitoring**: Added background `Scheduler` and `/api/webhooks` endpoint for event-driven autonomous response.
> - Validation status: `test_a2a_delegation.py` and `test_rag_hybrid.py` passed.
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
# AutomationEdge Agentic Support — Setup Guide

Complete step-by-step guide to deploy the Agentic Support Assistant on **AutomationEdge AI Studio (on-prem)** with **MS Teams** and **webchat** integration.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Infrastructure Setup](#2-infrastructure-setup)
3. [Environment Configuration](#3-environment-configuration)
4. [Database Setup](#4-database-setup)
5. [RAG Knowledge Base Setup](#5-rag-knowledge-base-setup)
6. [AI Studio Extension Deployment](#6-ai-studio-extension-deployment)
7. [MS Teams Integration](#7-ms-teams-integration)
8. [Verification & Testing](#8-verification--testing)
9. [Go-Live Checklist](#9-go-live-checklist)
10. [Troubleshooting](#10-troubleshooting)
11. [Architecture Reference](#11-architecture-reference)
12. [Cognibot Integration Architecture (Deep Dive)](#12-cognibot-integration-architecture-deep-dive)

---

## 1. Prerequisites

### 1.1 Software Requirements

| Component | Version | Purpose |
|---|---|---|
| AutomationEdge AI Studio | On-prem (latest) | Hosts the Extension, provides Cognibot |
| PostgreSQL | 14+ | State persistence, RAG vector store |
| pgvector extension | 0.5+ | Recommended (numpy fallback available) |
| Python | 3.9.6+ (bundled with AI Studio) | Runtime for the Extension |
| Google Cloud SDK | Latest | Vertex AI authentication (LLM + embeddings) |
| MS Teams | - | Chat channel for end users (optional — webchat also available) |
| Azure Bot Service | - | Routes Teams messages to AI Studio (only needed for Teams channel) |

### 1.2 Accounts & Credentials

You will need:

- **AE service account** with API key (for calling AE REST APIs)
- **Google Cloud service account** with `Vertex AI User` role (for Gemini LLM)
- **Azure Bot registration** (for Teams messaging endpoint)
- **PostgreSQL user** with CREATE TABLE and CREATE EXTENSION privileges

### 1.3 Network Requirements

| Source | Destination | Port | Purpose |
|---|---|---|---|
| AI Studio server | PostgreSQL | 5432 | State + RAG storage |
| AI Studio server | Vertex AI (GCP) | 443 | LLM inference |
| AI Studio server | AE REST API | 8443 | Workflow tool calls |
| Azure Bot Service | AI Studio server | 443 | Teams webhook ingress |
| AI Studio server | Azure Bot Service | 443 | Teams reply egress |

### 1.4 Project Structure

```
AEAgenticSupport/
├── main.py                              # Standalone entry point (CLI)
├── agent_server.py                      # Flask REST API server (port 5050)
├── webchat.html                         # Browser-based chat UI
├── setup_db.py                          # Database schema setup script
├── requirements.txt                     # Python dependencies
├── .env.example                         # Environment variable template
│
├── custom/                              # ← AI Studio Extension layer (Teams)
│   ├── apps.py                          #   Django AppConfig
│   ├── settings.py                      #   Extension settings
│   ├── models.py                        #   Django models (dedup, cases, approvals)
│   ├── migrations/                      #   Django migrations directory
│   │   ├── __init__.py
│   │   └── 0001_initial.py             #   Initial schema migration
│   ├── custom_hooks.py                  #   CustomChatbotHooks (Teams entry point)
│   ├── extra_requirements.txt           #   Extension dependencies
│   ├── helpers/
│   │   ├── locks.py                     #   PostgreSQL advisory locks
│   │   ├── db.py                        #   Message deduplication
│   │   ├── policy.py                    #   Safe auto-run vs approval
│   │   ├── tools_rest.py               #   REST tool client
│   │   ├── rag.py                       #   RAG bridge (direct pgvector or REST)
│   │   ├── roster.py                    #   On-shift tech user roster
│   │   ├── teams.py                     #   Teams reply helpers
│   │   └── issue_classifier.py          #   Multi-issue classification
│   └── functions/python/
│       └── support_agent.py             #   Planner/executor (bridges to orchestrator)
│
├── custom_cognibot/                     # ← Thin proxy hooks for local dev (no AI Studio)
│   ├── custom_hooks.py                  #   Forwards webchat messages to agent_server
│   └── ...
│
├── config/                              # ← Standalone modules
│   ├── settings.py                      #   Central configuration
│   ├── classification_signals.py        #   Heuristic classification patterns
│   ├── llm_client.py                    #   Vertex AI (Gemini) client
│   └── logging_setup.py                 #   App + audit loggers
├── agents/
│   ├── agent_router.py                  #   Central agent dispatcher
│   ├── orchestrator_agent.py            #   Supervisor agent (A2A gateway)
│   ├── diagnostic_agent.py              #   Technical Specialist (logs/status)
│   ├── remediation_agent.py             #   Resolution Specialist (restarts/fixes)
│   ├── approval_gate.py                 #   RBAC-aware approval logic
│   ├── escalation.py                    #   Escalation agent
│   ├── rca_agent.py                     #   RCA generation
│   └── scheduler.py                     #   Proactive background tasks
├── tools/
│   ├── base.py                          #   AE API client, ToolDefinition
│   ├── registry.py                      #   Tool registry
│   ├── general_tools.py                 #   3 general escape-hatch tools (call_ae_api, query_database, search_knowledge_base)
│   ├── status_tools.py                  #   5 status/health tools
│   ├── log_tools.py                     #   2 log/history tools
│   ├── file_tools.py                    #   2 file validation tools
│   ├── remediation_tools.py             #   5 remediation tools
│   ├── dependency_tools.py              #   4 dependency/config tools
│   └── notification_tools.py            #   2 notification tools
├── rag/
│   ├── engine.py                        #   Hybrid RAG (Vector + Keyword + RRF)
│   ├── processor.py                     #   Document processing (PDF/Tables/MD)
│   ├── index_all.py                     #   Index builder script
│   └── data/
│       ├── kb_articles/                 #   Knowledge base JSON/MD files
│       ├── sops/                        #   Standard operating procedures
│       ├── tool_docs/                   #   Extended tool documentation
│       └── past_incidents/              #   Historical incident data
├── gateway/
│   ├── message_gateway.py               #   Thread-safe message routing
│   └── progress.py                      #   ProgressCallback — real-time status messages
├── state/
│   ├── conversation_state.py            #   Session state (PostgreSQL-backed)
│   └── issue_tracker.py                 #   Multi-issue tracking
├── templates/
│   └── rca_templates.py                 #   RCA report templates
└── tests/
    ├── test_scenarios.py                #   Unit tests
    └── mock_ae_api.py                   #   Mock AE API server
```

---

## 2. Infrastructure Setup

### 2.1 PostgreSQL + pgvector

> **Note:** pgvector is **recommended for production** but **optional for local development**. When the pgvector extension is not available, the RAG engine automatically falls back to a **numpy-based cosine similarity** implementation. In fallback mode, embeddings are stored as JSONB and similarity is computed in Python. This is adequate for development and small knowledge bases but pgvector is strongly recommended for production workloads.

**Option A: Existing AI Studio PostgreSQL**

If your AI Studio on-prem already uses PostgreSQL, connect to it and install pgvector:

```bash
# Connect to the AI Studio database server
psql -h <db-host> -U <admin-user> -d postgres

# Create a dedicated database
CREATE DATABASE ops_agent;

# Connect to it and install pgvector (recommended, not required)
\c ops_agent
CREATE EXTENSION vector;
```

**Option B: Separate PostgreSQL instance**

```bash
# Install PostgreSQL 14+
sudo apt install postgresql-14

# (Recommended) Install pgvector
sudo apt install postgresql-14-pgvector

# Or build from source:
git clone https://github.com/pgvector/pgvector.git
cd pgvector
make && sudo make install

# Create database
sudo -u postgres createdb ops_agent
# Optional: install pgvector extension (the RAG engine works without it)
sudo -u postgres psql -d ops_agent -c "CREATE EXTENSION vector;"
```

**Option C: Local dev without pgvector**

If you just want to get started quickly without installing the pgvector extension:

```bash
# Create the database — no extension needed
sudo -u postgres createdb ops_agent
# The RAG engine will detect pgvector is missing and use numpy fallback
```

**Create the application user:**

```sql
CREATE USER ops_agent_user WITH PASSWORD 'your-secure-password';
GRANT ALL PRIVILEGES ON DATABASE ops_agent TO ops_agent_user;
-- After running setup_db.py, also grant table permissions:
GRANT ALL ON ALL TABLES IN SCHEMA public TO ops_agent_user;
```

### 2.2 Google Cloud / Vertex AI

```bash
# Install Google Cloud SDK (if not already present)
curl https://sdk.cloud.google.com | bash

# Authenticate with a service account
gcloud auth activate-service-account \
  --key-file=/path/to/service-account-key.json

# Verify Vertex AI access
gcloud ai models list --region=us-central1 --project=your-project-id
```

**Required GCP IAM roles for the service account:**
- `roles/aiplatform.user` (Vertex AI User)

**Enable the API:**
```bash
gcloud services enable aiplatform.googleapis.com --project=your-project-id
```

### 2.3 AE Service Account

In the AutomationEdge Admin console:

1. Go to **Administration** → **Service Accounts**
2. Create a new service account named `ops_agent_svc`
3. Grant it permissions to:
   - Read workflow status and execution logs
   - Trigger and restart executions
   - Read/write queue items
   - Read agent status and resources
   - Send notifications
4. Generate an API key and save it securely

---

## 3. Environment Configuration

### 3.1 Create the .env file

```bash
cd /path/to/AEAgenticSupport
cp .env.example .env
```

Edit `.env` with your actual values:

```bash
# AutomationEdge API
AE_BASE_URL=https://your-ae-server:8443
AE_API_KEY=your-ae-service-account-api-key
AE_TIMEOUT_SECONDS=30

# Google Cloud / Vertex AI
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/opt/automationedge/keys/sa-key.json
VERTEX_AI_MODEL=gemini-2.0-flash

# PostgreSQL
POSTGRES_DSN=postgresql://ops_agent_user:your-password@db-host:5432/ops_agent

# Embeddings (Vertex AI — uses the same GCP credentials as the LLM)
EMBEDDING_MODEL=text-embedding-004

# Tool Gateway
TOOL_BASE_URL=https://your-ae-server:8443/api/v1
TOOL_AUTH_TOKEN=your-ae-service-account-api-key

# Agent Behaviour
MAX_AGENT_ITERATIONS=15
MAX_RAG_TOOLS=12
MAX_RESTARTS_PER_WORKFLOW=3
MAX_BULK_OPERATIONS=10
STALE_ISSUE_MINUTES=30
RECURRENCE_ESCALATION_THRESHOLD=3

# Proactive Monitoring & Scheduling (Feature 2.2)
ENABLE_PROACTIVE_MONITORING=true
HEALTH_CHECK_INTERVAL_SECONDS=300
ENABLE_DAILY_SUMMARY=true
DAILY_SUMMARY_HOUR=8
MONITORED_WORKFLOWS=inventory_sync,claims_batch

# RBAC & Security (Feature 2.6)
RBAC_ENABLED=true
AGENT_ADMIN_TOKEN=your-random-secret
# (Roles and ranks are defined in config/settings.py)
```

### 3.2 Set environment variables on the server

For AI Studio Extension, set these in the Extension's environment configuration (see Section 6). For standalone testing:

```bash
# Linux
export $(cat .env | grep -v '^#' | xargs)

# PowerShell
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2])
    }
}
```

---

## 4. Database Setup

### 4.1 Install Python dependencies

```bash
pip install -r requirements.txt
```

> **Note:** Embeddings use **Google Vertex AI `text-embedding-004`** (768 dimensions) via the `VertexEmbedder` class in `rag/engine.py`. The same GCP credentials used for the LLM are used for embeddings — no separate model download is needed.
>
> If the **pgvector** extension is not installed, the RAG engine automatically falls back to numpy-based cosine similarity (embeddings stored as JSONB). This is transparent — `setup_db.py` detects the available mode and creates the appropriate schema.

### 4.2 Run the schema setup script

```bash
python setup_db.py
```

Expected output:

```
Connecting to: postgresql://ops_agent_user:***@db-host:5432/ops_agent
Database setup complete.
Tables created:
  - `rag_documents` (RAG vector store)
  - `issue_registry` (issue tracking)
  - `conversation_state` (session persistence + active issue pointer)
```

### 4.4 Migration (for existing deployments)

If you are upgrading from a version before the database optimization (which merged `issue_tracker_state` into `conversation_state`), run the migration script:

```bash
python setup_db.py --migrate
```

This will move the `active_issue_id` data and drop the redundant table.

### 4.5 Verify tables

```bash
psql -h <db-host> -U ops_agent_user -d ops_agent -c "\dt"
```

Expected:

```
              List of relations
 Schema |        Name           | Type  |     Owner
--------+-----------------------+-------+----------------
 public | conversation_state    | table | ops_agent_user
 public | issue_registry        | table | ops_agent_user
 public | rag_documents         | table | ops_agent_user
```

### 4.6 Django migrations (for the Extension layer)

The `custom/models.py` Django models (ProcessedMessage, Case, Approval, etc.) are migrated by AI Studio's built-in migration system when you upload the Extension zip. No manual migration is needed for those tables.

---

## 5. RAG Knowledge Base Setup

### 5.1 Add your knowledge base documents

Place your documents in the appropriate directories:

```
rag/data/
├── kb_articles/          ← Troubleshooting guides, workflow documentation
│   ├── claims_processing.json
│   ├── workflow_dependencies.json
│   └── your_custom_article.json
├── sops/                 ← Standard operating procedures
│   ├── failed_workflow_sop.json
│   ├── escalation_sop.json
│   └── your_custom_sop.json
├── tool_docs/            ← Extended tool documentation
│   └── remediation_tools_extended.json
└── past_incidents/       ← Historical incident data for pattern matching
    └── sample_incident.json
```

**JSON document format:**

```json
{
  "id": "unique-document-id",
  "title": "Human-readable title",
  "content": "The full text content that will be embedded and searched. Include all relevant details, workflow names, error messages, resolution steps.",
  "metadata": {
    "category": "troubleshooting",
    "workflows": ["Claims_Processing_Daily"],
    "tags": ["claims", "batch", "file"]
  }
}
```

You can also use Markdown (`.md`) files — they will be indexed with the filename as the ID.

### 5.2 Index the knowledge base

```bash
python -m rag.index_all
```

Expected output:

```
INFO - Starting full RAG index build...
INFO - Indexed 2 KB articles
INFO - Indexed 2 SOPs
INFO - Indexed 21 tool documents (20 from registry, 1 from files)
INFO - Indexed 1 past incidents
INFO - RAG index build complete.
```

### 5.3 Verify RAG is working

```bash
python -c "
from rag.engine import get_rag_engine
rag = get_rag_engine()
results = rag.search_kb('claims processing failed', top_k=3)
for r in results:
    print(f\"  [{r['similarity']:.3f}] {r['id']}: {r['content'][:80]}...\")
"
```

You should see results with similarity scores > 0.3 for relevant documents.

---

## 6. AI Studio Extension Deployment

### 6.1 Download the baseline Extension zip

1. Open **AI Studio** → navigate to `https://<ae-server>:<port>/aistudio`
2. Go to **Cognibot** → **Extension** tab
3. Click **Download** to get the current Extension zip
4. Unzip it to a working directory

### 6.2 Add project files to the Extension

Copy the following into the Extension directory:

```bash
# Copy the custom/ folder (AI Studio integration layer)
cp -r custom/ <extension-dir>/custom/

# Copy standalone modules (used by the orchestrator)
cp -r config/ <extension-dir>/config/
cp -r agents/ <extension-dir>/agents/
cp -r tools/ <extension-dir>/tools/
cp -r rag/ <extension-dir>/rag/
cp -r gateway/ <extension-dir>/gateway/
cp -r state/ <extension-dir>/state/
cp -r templates/ <extension-dir>/templates/
cp -r documents/ <extension-dir>/documents/

# Copy requirements
cp requirements.txt <extension-dir>/
```

### 6.3 Verify Extension structure

Your Extension directory should now look like:

```
<extension-dir>/
├── custom/
│   ├── apps.py
│   ├── settings.py
│   ├── models.py
│   ├── migrations/
│   │   ├── __init__.py
│   │   └── 0001_initial.py
│   ├── custom_hooks.py          ← CustomChatbotHooks class (async)
│   ├── extra_requirements.txt
│   ├── helpers/
│   │   ├── locks.py
│   │   ├── db.py
│   │   ├── policy.py
│   │   ├── rag.py
│   │   ├── tools_rest.py
│   │   ├── roster.py
│   │   ├── teams.py
│   │   └── issue_classifier.py
│   └── functions/python/
│       └── support_agent.py
├── config/
├── agents/
├── tools/
├── rag/
│   ├── engine.py
│   ├── index_all.py
│   └── data/                    ← Make sure this has your KB data
├── gateway/
├── state/
├── templates/
├── documents/
└── (... existing Extension files ...)
```

### 6.4 Configure the tech roster

Edit `custom/helpers/roster.py` and replace the placeholder with your actual tech team:

```python
TECH_ROSTER = [
    {
        "teams_user_id": "john.doe@company.com",
        "shift": {"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"},
        "skills": ["AE_PLATFORM", "CLAIMS"],
    },
    {
        "teams_user_id": "jane.smith@company.com",
        "shift": {"start": "14:00", "end": "23:00", "timezone": "Asia/Kolkata"},
        "skills": ["AE_PLATFORM", "INFRASTRUCTURE"],
    },
]
```

### 6.5 Configure protected workflows

Edit `config/settings.py` or set the `PROTECTED_WORKFLOWS` environment variable:

```
PROTECTED_WORKFLOWS=regulatory_report_irdai,financial_close_batch
```

### 6.6 AI Studio Integration Contract

The Extension integrates with AI Studio's Cognibot via the hook system. Key requirements:

| Item | Requirement |
|---|---|
| Hook class | `CustomChatbotHooks` extends `ChatbotHooks` in `custom/custom_hooks.py` |
| App config | `CustomAppConfig` in `custom/apps.py` with `name = Constants.CUSTOM` |
| Hook methods | All hooks are **async** (`async def`) — sync logic wrapped via `sync_to_async` |
| `api_messages_hook` | Signature: `async def api_messages_hook(request, activity)` |
| Migrations | Proper Django migrations directory at `custom/migrations/` |
| Dependencies | Listed in `custom/extra_requirements.txt` (`.txt` extension, not `.text`) |
| Python compat | AI Studio bundles Python 3.9.6 — all files use `from __future__ import annotations` |

### 6.7 Create the Extension zip and upload

```bash
cd <extension-dir>
zip -r ae_agentic_support_extension.zip .
```

1. Go to **AI Studio** → **Cognibot** → **Extension** tab
2. Click **Upload** and select `ae_agentic_support_extension.zip`
3. Set environment variables in the Extension configuration:
   - All variables from your `.env` file
4. Click **Deploy** / **Restart Cognibot**

### 6.8 Verify Extension loaded

Check the AI Studio logs:

```bash
tail -f /opt/automationedge/aistudio/logs/cognibot.log
```

Look for:

```
INFO - Custom extension loaded: AE Agentic Support Extension
INFO - Registered hook class: CustomChatbotHooks
INFO - Registered hook: api_messages_hook (async)
```

---

## 7. MS Teams Integration

### 7.1 Azure Bot Service registration

1. Go to [Azure Portal](https://portal.azure.com) → **Create a resource** → **Azure Bot**
2. Configure:
   - **Bot handle**: `ae-ops-support-bot`
   - **Messaging endpoint**: `https://<your-public-endpoint>/api/messages`
   - **Microsoft App ID**: Create new (auto-generated)
3. Note the **App ID** and **App Password**

### 7.2 Configure the messaging endpoint

Ensure your AI Studio on-prem deployment is reachable via HTTPS:

```
Inbound:  https://<public-endpoint>/api/messages   → AI Studio Cognibot
Outbound: https://<public-endpoint>/api/reply       → Azure Bot Service
```

If behind a reverse proxy (nginx):

```nginx
location /api/messages {
    proxy_pass http://localhost:<cognibot-port>/api/messages;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

### 7.3 Enable Teams channel

1. In Azure Portal → your Bot → **Channels**
2. Click **Microsoft Teams** → **Save**
3. This creates the Teams app manifest

### 7.4 Install the bot in Teams

1. In Azure Portal → your Bot → **Channels** → **Microsoft Teams** → **Open in Teams**
2. Or create a Teams App Package:
   - Download the manifest from Azure Bot
   - Upload to **Teams Admin Center** → **Manage Apps** → **Upload**
3. Users can now find the bot by searching in Teams

### 7.5 Test the Teams integration

1. Open a chat with the bot in Teams
2. Send: `hi`
3. Expected response: `Hello! How can I help you with support today?`
4. Send: `What workflows failed today?`
5. Expected: The bot investigates using `list_recent_failures` and responds

---

## 8. Verification & Testing

### 8.1 Run unit tests

```bash
python -m pytest tests/test_scenarios.py -v
```

Expected: All tests pass (conversation state, approval gate, tool registry, templates).

### 8.2 Standalone Agent Server

The project includes a standalone Flask-based agent server (`agent_server.py`) that exposes the agent as a REST API on port **5050**. This is useful for local testing and for the Cognibot thin proxy architecture (see Section 8.6).

**Start the server:**

```bash
python agent_server.py
# Output: * Running on http://0.0.0.0:5050
```

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves `webchat.html` — a browser-based chat UI |
| `GET` | `/health` | Health check (returns `{"status": "ok"}`) |
| `POST` | `/chat` | Send a message. Body: `{"message": "...", "thread_id": "..."}` (non-streaming) |
| `POST` | `/chat/stream` | SSE streaming: sends `event: progress` (status text) during investigation, then `event: done` (final response) |

**Webchat:** Open `http://localhost:5050/` in your browser for an interactive chat interface. This is the fastest way to test the agent locally without Teams or AI Studio. When using the streaming endpoint, you will see real-time progress messages (e.g., "Looking into this...", "Checking workflow status...") as italic status text that updates in-place during long investigations.

### 8.3 Run the mock AE API (for local testing)

In one terminal:

```bash
python tests/mock_ae_api.py
# Output: Starting Mock AE API on http://localhost:5051
```

In another terminal, set `AE_BASE_URL=http://localhost:5051` and:

```bash
python main.py
```

### 8.4 Test scenarios via CLI

```
[technical] You: What workflows failed today?
  Agent: (investigates using list_recent_failures, returns results)

[technical] You: Can you restart Claims_Processing_Daily?
  Agent: (checks status, asks for approval since it's a remediation action)

[technical] You: approve
  Agent: (executes restart, confirms success)

[technical] You: role:business
  Switched to role: business

[business] You: What happened with the claims today?
  Agent: (responds in plain English, no technical jargon)
```

### 8.5 Test Teams-specific scenarios

| Test | Message | Expected Result |
|---|---|---|
| Smalltalk | `hello` | Friendly greeting, no case created |
| Empty message | (empty) | "Message was empty" prompt |
| New issue | `Claims batch failed` | Creates case, investigates |
| Approval | `approve` | Executes pending action |
| Rejection | `reject` | Cancels action, asks for alternatives |
| Duplicate message | (send same message twice) | Second message ignored (dedup) |
| Concurrent messages | Send while bot is working | Classified as additive/interrupt/cancel |
| Recurrence | `Claims failed again` (after resolution) | Reopens previous case |
| Escalation | 3+ recurrences | Auto-escalates to L2 |
| Protected workflow | `Restart regulatory_report_irdai` | Refuses, suggests escalation |

### 8.6 Cognibot Thin Proxy (local dev without AI Studio)

For local development without an AI Studio license, the `custom_cognibot/` directory contains **thin proxy hooks** that forward webchat messages to the agent server (`agent_server.py`) via HTTP. This avoids the need to install heavy AI Studio dependencies in Python 3.9 and lets you develop and test the full agent pipeline using only the standalone server.

To use this mode:

1. Start the agent server: `python agent_server.py`
2. Open the webchat at `http://localhost:5050/`
3. The thin proxy hooks in `custom_cognibot/` are only needed if you want to simulate the AI Studio Cognibot request flow locally.

### 8.7 AI Studio Local .env Files

When running AI Studio locally (e.g., for integration testing with the full Cognibot stack), three `.env` files need updating:

| .env File | Key Changes |
|---|---|
| AIStudio Engine `.env` | Set `DB_PASS` to your local PostgreSQL password |
| Cognibot `.env` | Set `DB_PASS` to your local PostgreSQL password; set `CHATBOT_WEB_SERVICE_HOME` to the Cognibot service directory |
| KMEngine `.env` | Set `DB_PASS` to your local PostgreSQL password |

> **Windows note:** In all three `.env` files, uncomment `SCHEDULER_LOCK_TYPE=Database` — the default file-based locking does not work reliably on Windows.

### 8.9 Starting the AI Studio Local Stack (Webchat via Cognibot)

To test the full AI Studio Cognibot → Agent pipeline locally (not just the standalone agent server), follow these steps. This runs the real Cognibot dialog engine with DirectLine/WebSocket webchat.

**Prerequisites:**
- AI Studio packages extracted at `AI_Studio_Local/aistudio-packages/Chatbot-Webservice/`
- PostgreSQL running locally (Cognibot uses its own `cognibot_db`)
- The three AI Studio `.env` files configured (see Section 8.7)
- AI Studio license file placed in `AI_Studio_Local/aistudio-packages/Chatbot-Webservice/CBWS_HOME/license/`

**Step 1: Start the Agent Server**

```bash
python agent_server.py
# Output: Agent server starting on http://localhost:5050
```

**Step 2: Start Cognibot (Daphne ASGI server)**

```bash
cd AI_Studio_Local/aistudio-packages/Chatbot-Webservice/cognibot
<python-path>/python.exe -m daphne -b 0.0.0.0 -p 3978 common.asgi:application
# Output: Listening on TCP address 0.0.0.0:3978
```

> **Note:** The `python` used here is the one bundled with AI Studio (Python 3.9), not your system Python 3.12.

**Step 3: Open the Webchat**

Navigate to `http://localhost:5050/aistudio-webchat` in your browser. The webchat will:
1. Create a DirectLine conversation via `POST /v3/directline/conversations`
2. Open a WebSocket to `ws://localhost:3978/v3/directline/<conversation_id>`
3. Send messages via `POST /v3/directline/conversations/<id>/activities`
4. Receive bot responses via the WebSocket

**Troubleshooting the local stack:**

| Symptom | Cause | Fix |
|---|---|---|
| "No skill available" message | Default config has no skills; `root_dialog_hook` is never reached | See Section 12.10 — the default config must include an `ops_agent` skill |
| WebSocket errors: "Django can only handle ASGI/HTTP" | The ASGI app doesn't route WebSocket connections | See Section 12.7 — `common/asgi.py` must import from `webchat_channel.routing` |
| Config loads as default despite edits to `cognibot_config.json` | The main config expects a signed envelope (`cognibot_json` + `signature` + `keygen` + `key`); unsigned configs always fail | Edit `cognibot_config_default.json` instead (see Section 12.1) |
| `AgentProxyDialog` not executing | The dialog class is not registered in `export_dialogs`, or the class name doesn't match | Ensure `export_dialogs = [AgentProxyDialog]` and the dialog's init passes `"AgentProxyDialog"` to `super().__init__()` |
| Agent called but response not in webchat | WebSocket not connected, or `send_activity` not routing through adapter | Check browser console for WebSocket messages; the response flows through Django Channels `group_send` |
| Stale Python code after edits | `.pyc` cache not cleared | Delete `custom/__pycache__/` and restart Cognibot |

### 8.8 Verify audit logging

```bash
cat logs/audit.log | tail -20
```

Every tool call should be logged:

```
2026-02-27 10:15:23 | ops_agent.audit | INFO | TOOL_CALL tool=check_workflow_status params={'workflow_name': 'Claims_Processing_Daily'}
2026-02-27 10:15:24 | ops_agent.audit | INFO | TOOL_OK tool=check_workflow_status
```

---

## 9. Go-Live Checklist

| # | Step | Owner | Status |
|---|---|---|---|
| 1 | PostgreSQL + pgvector deployed and accessible | DevOps | ☐ |
| 2 | `python setup_db.py` completed successfully | DevOps | ☐ |
| 3 | AE service account created with minimal permissions | AE Admin | ☐ |
| 4 | Vertex AI API enabled, service account has `Vertex AI User` role | DevOps | ☐ |
| 5 | All environment variables configured | DevOps | ☐ |
| 6 | KB articles and SOPs indexed via `python -m rag.index_all` | Ops Team | ☐ |
| 7 | RAG search verified with test queries | Dev Team | ☐ |
| 8 | Protected workflow list reviewed and approved | Business | ☐ |
| 9 | Tech roster populated with real Teams IDs | Ops Lead | ☐ |
| 10 | Extension zip uploaded and Cognibot restarted | AE Admin | ☐ |
| 11 | Azure Bot registered, Teams channel enabled | DevOps | ☐ |
| 12 | Teams bot reachable and responding to `hello` | QA | ☐ |
| 13 | Unit tests passing (`python -m pytest tests/`) | QA | ☐ |
| 14 | All 10 test scenarios verified in Teams | QA | ☐ |
| 15 | Audit logging verified — all tool calls recorded | Compliance | ☐ |
| 16 | Escalation notifications reaching Teams/email | DevOps | ☐ |
| 17 | UAT with 3 business users and 3 technical users | UAT Lead | ☐ |
| 18 | Rollback plan documented and tested | Ops Lead | ☐ |

---

## 10. Troubleshooting

| Problem | Likely Cause | Solution |
|---|---|---|
| `setup_db.py` fails with "connection refused" | PostgreSQL not running or DSN wrong | Verify `POSTGRES_DSN`, check `pg_hba.conf` allows connections |
| `CREATE EXTENSION vector` fails | pgvector not installed | Install pgvector: `sudo apt install postgresql-14-pgvector` |
| Agent gives generic/empty answers | RAG not indexed | Run `python -m rag.index_all`, verify with search query |
| Vertex AI 403 / permission denied | Service account lacks permissions | Grant `Vertex AI User` role in GCP IAM |
| Vertex AI quota exceeded | Too many concurrent requests | Request quota increase in GCP console |
| Tool calls fail with 401 | AE API key invalid or expired | Regenerate API key in AE Admin |
| Tool calls fail with timeout | AE server overloaded | Increase `AE_TIMEOUT_SECONDS`, check AE health |
| Teams messages not arriving | Webhook endpoint unreachable | Verify HTTPS endpoint, check reverse proxy config |
| Duplicate bot responses | Missing advisory lock or dedup | Check PostgreSQL advisory lock is working |
| Approval not working | Phase state not transitioning | Check `ConversationPhase` in orchestrator |
| Business user sees technical details | `user_role` not set correctly | Verify persona detection in session creation |
| Issue tracker state lost on restart | PostgreSQL persistence failed | Check `issue_registry` table exists, check logs |
| Vertex AI embeddings slow on first call | Connection initialization overhead | First call initializes the Vertex AI connection; subsequent calls are faster |
| pgvector not available | Extension not installed in PostgreSQL | The RAG engine automatically falls back to numpy-based cosine similarity. Performance is adequate for local dev but pgvector is recommended for production |
| Recurrence not detected | `workflows_involved` not populated | Ensure orchestrator calls `tracker.add_workflow_to_issue()` |
| LLM classification slow | Model too large or network latency | Switch to `gemini-2.0-flash`, use closer GCP region |

---

## 11. Architecture Reference

### 11.1 Request Flow (Teams → Response)

```
MS Teams User
     │
     ▼
Azure Bot Service
     │
     ▼ HTTPS POST /api/messages
AI Studio Cognibot
     │
     ▼ (request, activity)
custom/custom_hooks.py :: CustomChatbotHooks.api_messages_hook()
     │
     ├─ pg_advisory_lock(thread_id)         ← Prevents race conditions
     ├─ is_duplicate_message(msg_id)        ← Dedup check
     ├─ _is_smalltalk("hello")              ← Fast-path greeting
     │
     ├─ classify_message()                  ← 3-layer issue classifier
     │   ├─ Heuristic signals (instant)
     │   ├─ Workflow + error matching (instant)
     │   └─ LLM fallback (Vertex AI)
     │
     ▼
custom/functions/python/support_agent.py :: handle_support_turn()
     │
     ├─ USE_AGENTIC_MODE=true?
     │   └─ YES → gateway/message_gateway.py :: process_message()
     │              │
     │              ▼
     │         agents/orchestrator.py :: handle_message()
     │              │
     │              ├─ RAG search (KB + SOPs + Tools)
     │              ├─ Three-layer tool hierarchy (hybrid architecture):
     │              │   ├─ General (3): call_ae_api, query_database, search_knowledge_base — always available, escape hatches
     │              │   ├─ Typed (20+): RAG-filtered structured tools with validation and audit trails
     │              │   └─ Meta (1): discover_tools for mid-conversation catalog search
     │              ├─ RAG-filtered tool selection (if catalog >30 tools)
     │              │   ├─ always_available tools (general + core status/logs) always sent
     │              │   ├─ RAG-matched typed tools (up to MAX_RAG_TOOLS=12) sent
     │              │   └─ discover_tools meta-tool for mid-conversation search
     │              ├─ LLM reasoning loop (Vertex AI Gemini)
     │              │   ├─ Tool selection via function calling
     │              │   ├─ Tool execution via tools/registry.py
     │              │   ├─ Approval gate check
     │              │   └─ Persona-based response filtering
     │              │
     │              ├─ Issue tracking (state/issue_tracker.py)
     │              └─ State persistence (PostgreSQL)
     │
     └─ USE_AGENTIC_MODE=false?
         └─ Deterministic plan-execute via RAG
              ├─ _build_plan_with_rag()
              └─ _execute_plan()
```

### 11.2 Data Flow

```
PostgreSQL (ops_agent database)
├── rag_documents          ← RAG vector store (KB, SOPs, tools, incidents)
├── issue_registry         ← Per-issue state (survives restarts)
├── conversation_state     ← Session state (messages, findings, tool logs, active issue)
│
├── (Django-managed, created by AI Studio):
├── custom_processedmessage  ← Message deduplication
├── custom_case              ← Case lifecycle (planning → executing → resolved)
├── custom_approval          ← Approval requests and decisions
├── custom_conversationstate ← Per-thread state for Extension hook
└── custom_issuelink         ← Links between related cases
```

### 11.3 Tool Catalog (23+ tools)

| Category | Tools | Risk Tier | Notes |
|---|---|---|---|
| **General** (3) | call_ae_api, query_database, search_knowledge_base | read_only / medium_risk | Always available. Escape hatches: call_ae_api (direct AE REST; GET bypasses approval, write methods require approval), query_database (read-only SQL, 50-row cap), search_knowledge_base (semantic RAG search) |
| **Status** (5) | check_workflow_status, list_recent_failures, get_system_health, get_queue_status, get_agent_status | read_only | check_workflow_status, list_recent_failures, get_system_health are `always_available` |
| **Logs** (2) | get_execution_logs, get_execution_history | read_only | get_execution_logs is `always_available` |
| **File** (2) | check_input_file, check_output_file | read_only | |
| **Config** (2) | get_workflow_config, get_schedule_info | read_only | |
| **Dependency** (2) | get_workflow_dependencies, check_agent_resources | read_only | |
| **Remediation** (5) | restart_execution, trigger_workflow, requeue_item, bulk_retry_failures, disable_workflow | low_risk → high_risk | |
| **Notification** (2) | send_notification, create_incident_ticket | medium_risk | |
| **Meta** (1) | discover_tools | read_only | Search the tool catalog for tools matching a query or category; enables mid-conversation tool discovery when RAG filtering is active |

### 11.4 Progress Streaming

The agent sends real-time progress messages to users during long investigations so they know the agent is working. Implemented via:

- **gateway/progress.py** — `ProgressCallback` class maps tool names to user-friendly status messages (different text for business vs technical users), throttles messages (min 3 seconds apart), and fires at key milestones: investigation start, each tool call, errors found, almost done, and heartbeat every 4 iterations for long investigations.

- **Orchestrator** — `handle_message()` and `_process_message()` accept an `on_progress` callback. Progress fires at: investigation start ("Looking into this..."); before each tool call (e.g., "Checking workflow status..."); after tool errors ("Found an issue — analyzing the cause..."); every 4th iteration ("Still investigating..."); and before final response ("Almost done...").

- **Agent Server** — `POST /chat/stream` sends SSE `event: progress` with status text during investigation, then `event: done` with the final response. The original `POST /chat` remains for backwards compatibility.

- **Cognibot proxy** — When `AGENT_PROGRESS_ENABLED=true` (default), uses the SSE endpoint and sends proactive messages via Bot Framework's `turn_context.send_activity()`. Falls back to `/chat` if proactive messaging isn't available.

### 11.5 Adding New Tools

1. Create the handler function in the appropriate `tools/*.py` file
2. Create a `ToolDefinition` with name, description, category, tier, and parameters
3. Set `always_available=True` for tools that should always be in the LLM context (e.g., core investigation tools like check_workflow_status, list_recent_failures, get_system_health, get_execution_logs)
4. Register it: `tool_registry.register(definition, handler)`
5. Re-index RAG: `python -m rag.index_all`
6. The orchestrator will automatically discover and use the new tool

---

## 12. Cognibot Integration Architecture (Deep Dive)

This section documents the internal workings of AI Studio's Cognibot discovered through bytecode analysis of the compiled (`.pyc`) modules. This knowledge is critical for debugging integration issues.

### 12.1 Configuration Loading

Cognibot loads config in two stages:

1. **Primary config** (`cognibot-home/config/cognibot_config.json`): Expected to be a **signed envelope** with this structure:
   ```json
   {
     "cognibot_json": "<JSON string of BotConfig>",
     "signature": "<digital signature>",
     "keygen": "<key generation data>",
     "key": "<encryption key>"
   }
   ```
   All four fields are required (`CognibotJsonEnvelope` dataclass). This is what AI Studio pushes when you deploy a chatbot config from the UI. **You cannot create this locally** without the signing mechanism.

2. **Fallback default config** (`aistudiobot/resources/cognibot_config_default.json`): When the primary config fails to load (always happens locally since we can't sign it), Cognibot falls back to this plain JSON config. **This is the file you must edit for local development.**

**Key model: `BotConfig`**
```
BotConfig:
  version: str
  cognibot_settings: CognibotSettings
  skills: list[Skill]         ← CRITICAL: must have at least 1 skill
  connections: list[Connection]
  credentials: list[Credential]
  nlu_settings: list[AIStudioNLU]
  km_settings: list[AIStudioKM]
  schedules: list[Schedule]
```

**Key model: `Skill`**
```
Skill:
  name: str                    ← Used as skill identifier
  is_default: bool = False     ← Set to true for the default skill
  nlu_enabled: bool = False
  km_enabled: bool = False
  welcome_message: WelcomeMessage  ← Set enabled=false to skip welcome
  dialog_designer: DialogDesigner  ← Can be empty {dialogs:[], triggers:[]}
  custom_trigger: CustomTrigger
  handoff: Handoff
  feedback: Feedback
  nlu: list
  km: list
```

The `default_skill` property on the cognibot object is computed from the skills list — it's the `name` of the first skill with `is_default=True`.

### 12.2 RootDialog — The Critical Code Path

The `RootDialog` is a `WaterfallDialog` with two steps: `auth_step` → `root_step`.

**`auth_step`:** Checks if OAuth is configured (`settings.MS_OAUTH_CONNECTION_NAME`). For local dev (no OAuth), it calls `step_context.next()` to skip directly to `root_step`.

**`root_step` — simplified pseudocode** (from bytecode at offsets 0-2898):

```python
async def root_step(self, step_context):
    # 1. Get conversation/user state
    aistudio_conv_state = await AIStudioConvState.get(self.conv_state, step_context.context)
    aistudio_user_state = await AIStudioUserState.get(self.user_state, step_context.context)

    # 2. Check for pre-set AIStudio dialog (from button clicks, etc.)
    if self.aistudio_dialog_name or conv_params.get(ROOT_AISTUDIO_DIALOG):
        return await step_context.begin_dialog(AIStudioDialog.__name__, ...)

    # 3. Check OAuth (skipped locally)
    if settings.MS_OAUTH_CONNECTION_NAME and adapter.settings.app_id:
        # ... OAuth + handoff logic ...
        return  # <-- returns before reaching hook

    # 4. Get user input
    user_input = step_context.context.activity.text or ""

    # 5. i18n language change trigger
    if i18n.enabled and user_input.lower() in i18n.triggers:
        return await step_context.begin_dialog(ChangeLanguageDialog.__name__, ...)

    # 6. Skill selection trigger (multi-skill)
    if user_input.lower() in skill_settings.triggers and len(skills) > 1:
        return await step_context.begin_dialog(SkillDialog.__name__, ...)

    # 7. ★ SKILL RESOLUTION ★ — This is where things go wrong without config
    self.skill = conv_params.get(Constants.SKILL)

    if not self.skill:
        default_skill = self.cognibot.default_skill

        if default_skill is not None:
            # Set the default skill
            self.skill = default_skill
            aistudio_conv_state.add_param(SKILL, default_skill)
            DBHelper.update_conversation_skill(...)

            # Show welcome message (if configured)
            welcome_result = BotHelper.show_skill_welcome_message(...)
            if welcome_result and welcome_result in skill_dialog_map:
                return await step_context.begin_dialog(AIStudioDialog.__name__, ...)
            # If no welcome → falls through to hook ✓

        elif len(skills) > 1:
            return await step_context.begin_dialog(SkillDialog.__name__, ...)

        else:
            # ★ THE PROBLEM: No skills, no default → sends "No skill available"
            message = i18n_manager.get_str(language, 'no_skills_message')
            await log_and_send_activity(step_context.context, message)
            return await step_context.end_dialog()  # ← RETURNS HERE, hook never reached!

    # 8. ★ HOOK INVOCATION ★ (offset 1448-1524)
    from custom.custom_hooks import CustomChatbotHooks
    custom_dialog = await CustomChatbotHooks.root_dialog_hook(
        self.conv_state, self.user_state, step_context.context
    )

    if custom_dialog and issubclass(custom_dialog, Dialog):
        return await step_context.begin_dialog(custom_dialog.__name__, step_context.options)
    elif custom_dialog:
        logger.exception(f"Failed to execute custom dialog {custom_dialog} - {type(custom_dialog)}")

    # 9. Trigger matching, NLU, KM search...
    # 10. "trigger_match_fail_message" (catch-all)
```

**Key takeaway:** `root_dialog_hook` at offset 1448 is only reached if `self.skill` is set. Without a configured skill, the code returns at step 7 with "No skill available".

### 12.3 How `root_dialog_hook` Works

**Signature:** `async def root_dialog_hook(conv_state, user_state, turn_context)`

Called as: `CustomChatbotHooks.root_dialog_hook(self.conv_state, self.user_state, step_context.context)` (3 arguments; no `self` since it's on the class, not an instance).

**Return value handling:**
- If hook returns a **truthy value** that passes `issubclass(result, Dialog)`:
  - Calls `step_context.begin_dialog(result.__name__, step_context.options)`
  - The dialog must be registered via `export_dialogs` (see Section 12.4)
- If hook returns a truthy value that is NOT a Dialog subclass:
  - Logs error and falls through to trigger matching
- If hook returns `None` / falsy:
  - Falls through to trigger matching, NLU, KM search

### 12.4 Custom Dialog Registration (`export_dialogs`)

In `RootDialog.__init__`, custom dialogs are registered:

```python
from custom.custom_hooks import CustomChatbotHooks
for dialog_cls in CustomChatbotHooks.export_dialogs:
    if issubclass(dialog_cls, Dialog):
        instance = dialog_cls(user_state, conv_state, cognibot)  # ← 3 args!
        self.add_dialog(instance)
```

**Important:**
- `export_dialogs` must contain **classes**, not instances
- The class `__init__` must accept `*args, **kwargs` (RootDialog passes 3 positional args)
- The dialog's `super().__init__("DialogName")` ID must match what `begin_dialog` looks up by `custom_dialog.__name__`
- Example: `AgentProxyDialog.__name__` = `"AgentProxyDialog"`, and `super().__init__("AgentProxyDialog")`

### 12.5 The `AgentProxyDialog` Pattern

Our custom dialog (`AgentProxyDialog`) bridges Cognibot to the standalone agent server:

```python
class AgentProxyDialog(ComponentDialog):
    def __init__(self, *args, **kwargs):      # Accept RootDialog's 3 args
        super().__init__("AgentProxyDialog")   # Dialog ID = class name
        self.add_dialog(WaterfallDialog("AgentProxyWaterfall", [self._call_agent_step]))
        self.initial_dialog_id = "AgentProxyWaterfall"

    @staticmethod
    async def _call_agent_step(step_context):
        # 1. Extract text, conversation_id, user_id from turn_context
        # 2. HTTP POST to agent_server:5050/chat
        # 3. send_activity(reply_text)
        # 4. cancel_all_dialogs() — prevents RootDialog from continuing
```

**Why `cancel_all_dialogs()`?** After the agent sends its response, we must prevent the RootDialog waterfall from continuing (which would trigger "No skill available" or other fallback messages). `cancel_all_dialogs()` halts the entire dialog stack.

### 12.6 DirectLine / WebSocket Message Flow

```
Browser (webchat)                    Cognibot (port 3978)              Agent Server (5050)
      │                                      │                                │
      │  POST /v3/directline/conversations   │                                │
      │────────────────────────────────────►  │                                │
      │  ◄── {conversationId}                │                                │
      │                                      │                                │
      │  WS /v3/directline/<conv_id>         │                                │
      │════════════════════════════════════►  │ (WebChatConsumer joins group)  │
      │                                      │                                │
      │  POST .../activities {message}       │                                │
      │────────────────────────────────────►  │                                │
      │                                      │                                │
      │     ┌─ activities view ──────────────┤                                │
      │     │  1. send_activity_over_ws(     │                                │
      │     │       conv_id, user_msg_echo)  │                                │
      │  ◄══│══ WS: user message echo        │                                │
      │     │                                │                                │
      │     │  2. send_message_to_webservice │                                │
      │     │     → messages(payload)        │                                │
      │     │       → adapter.process_activity│                               │
      │     │         → bot.on_turn           │                                │
      │     │           → RootDialog          │                                │
      │     │             → AgentProxyDialog   │                               │
      │     │               → HTTP POST /chat ─┼──────────────────────────────►│
      │     │               ◄── agent response ┼◄─────────────────────────────│
      │     │               → send_activity    │                               │
      │     │                 → adapter callback│                              │
      │     │                   → POST /v3/.../activities (reply)              │
      │     │                     → send_activity_over_ws(conv_id, reply)      │
      │  ◄══│══ WS: bot reply                 │                               │
      │     └─────────────────────────────────┤                                │
```

**Key insight:** The bot's response goes through the adapter's `send_activity` callback, which POSTs back to the DirectLine `/activities` reply endpoint. That endpoint calls `send_activity_over_websocket()` which uses Django Channels' `group_send` to push the activity to the WebSocket consumer for that conversation.

### 12.7 ASGI / WebSocket Routing

The compiled `common/asgi.pyc` uses plain `get_asgi_application()` (HTTP only). WebSocket support requires Django Channels' `ProtocolTypeRouter`.

**Fix:** Create `common/asgi.py` (source file overrides `.pyc`):

```python
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "common.settings")
from django.core.asgi import get_asgi_application
django_asgi_app = get_asgi_application()    # Must come first (breaks circular import otherwise)
from webchat_channel.routing import application  # ProtocolTypeRouter with WS routes
```

The `webchat_channel.routing.application` provides:
- **HTTP** → Django ASGI handler (all REST endpoints)
- **WebSocket** → `WebChatConsumer` at URL pattern `v3/directline/(?P<conversation_id>[\w-]+)`

Settings involved:
```python
ASGI_APPLICATION = "webchat_channel.routing.application"
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
INSTALLED_APPS = [..., "channels", "channels_redis", "webchat_channel"]
```

### 12.8 Authentication (Local Dev)

Cognibot's DirectLine uses `CLIENT_SECRET` (set in the Cognibot `.env`). For local dev, set it to `secret`:

```env
CLIENT_SECRET=secret
```

The webchat sends `Authorization: Bearer secret` with all requests. Cognibot's token validation tries to decode it as a JWT — this fails (it's not a JWT), but the exception handler allows it through for the `emulator`/local channel. This is expected and produces a non-blocking `DecodeError: Not enough segments` warning in logs.

### 12.9 Summary of Required Files for Local Webchat

| File | What It Does |
|---|---|
| `cognibot_config_default.json` | Must contain a skill with `is_default: true` so `root_dialog_hook` is reached |
| `common/asgi.py` | Overrides compiled `.pyc` to add WebSocket routing via `ProtocolTypeRouter` |
| `custom/custom_hooks.py` | Defines `AgentProxyDialog` and `CustomChatbotHooks` with `root_dialog_hook` and `export_dialogs` |
| `aistudio_webchat.html` | Browser webchat client (served by agent server at `/aistudio-webchat`) |

### 12.10 Minimal Skill Configuration

Add this to `cognibot_config_default.json` to make `root_dialog_hook` reachable:

```json
{
  "skills": [
    {
      "name": "ops_agent",
      "is_default": true,
      "nlu": [],
      "km": [],
      "dialog_designer": { "dialogs": [], "triggers": [] },
      "nlu_enabled": false,
      "km_enabled": false,
      "welcome_message": { "type": "", "message": "", "enabled": false },
      "custom_trigger": { "enabled": false, "function": "", "order": null },
      "handoff": { "agents": [], "enabled": false, "triggers": [] },
      "feedback": { "enabled": false, "dialog": "", "rating": "", "feedback": "" }
    }
  ]
}
```

This ensures:
1. `cognibot.default_skill = "ops_agent"` (not None)
2. `self.skill` gets set on first message
3. `welcome_message.enabled = false` → no welcome dialog, falls through
4. Code reaches offset 1448 → `root_dialog_hook` is called
5. Hook returns `AgentProxyDialog` → `begin_dialog("AgentProxyDialog")`

---

**Document version:** 3.0
**Last updated:** 2026-03-04

