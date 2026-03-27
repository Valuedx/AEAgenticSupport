# AE Agentic Support — Intelligent IT Operations Agent

An AI-powered IT operations support platform built on **Google Gemini / Vertex AI**, integrating with **AutomationEdge (T4)** workflows, **MCP (Model Context Protocol)** tools, and **RAG-based knowledge retrieval** to autonomously diagnose, remediate, and escalate infrastructure issues.

---

## ✨ Key Features

- **Multi-Agent Architecture** — Orchestrator, Diagnostic, RCA (Root Cause Analysis), Remediation, and Escalation agents collaborate to resolve incidents end-to-end.
- **RAG-Powered Tool Discovery** — Automatically discovers and ranks the best tools/SOPs/knowledge articles for each issue using pgvector embeddings.
- **AutomationEdge (T4) Integration** — Executes real workflows on the T4 platform (restart services, fetch logs, create tickets, etc.).
- **MCP Server** — Exposes automation tools over the Model Context Protocol via HTTP for external consumption.
- **Human-in-the-Loop Approvals** — Configurable approval gates for destructive or high-risk operations.
- **Ticket Generation** — Auto-creates support tickets via the HDFC Life Ticket API with full audit trail.
- **Conversation State Persistence** — PostgreSQL-backed session state, issue tracking, and chat history.
- **LangFuse Observability** — Optional tracing for LLM calls, tool executions, and RAG searches.
- **Scheduled Operations** — Built-in scheduler for recurring health checks and maintenance tasks.

---

## 📁 Project Structure

```
AEAgenticSupport/
│
├── agent_server.py          # Main Flask server — exposes /chat, /health, /approve endpoints
├── main.py                  # CLI entry point for local testing
├── setup_db.py              # Database schema creation & migration
├── run_rag_index.py         # RAG document indexing script
├── db_health_check.py       # Database health diagnostics
├── start_servers.bat        # One-click launcher for all components
├── requirements.txt         # Python dependencies
├── schema.sql               # Reference SQL schema
├── .env                     # Environment configuration (not committed)
│
├── agents/                  # Multi-agent system
│   ├── orchestrator.py      # Central orchestrator — routes queries, manages phases
│   ├── agent_router.py      # Intent classification & agent selection
│   ├── rca_agent.py         # Root Cause Analysis agent
│   ├── diagnostic_agent.py  # Diagnostic data collection agent
│   ├── remediation_agent.py # Remediation execution agent
│   ├── escalation.py        # Escalation logic & thresholds
│   ├── approval_gate.py     # Human-in-the-loop approval workflow
│   ├── scheduler.py         # Scheduled task execution engine
│   ├── base_agent.py        # Abstract base class for all agents
│   └── agent_registry.py    # Dynamic agent registration
│
├── tools/                   # Tool ecosystem
│   ├── registry.py          # Master tool registry (40+ tools)
│   ├── automationedge_client.py  # T4 REST API client
│   ├── remediation_tools.py # Service restart, config push, cleanup tools
│   ├── rca_tools.py         # Log analysis, pattern matching tools
│   ├── status_tools.py      # System status & monitoring tools
│   ├── log_tools.py         # Log retrieval & parsing
│   ├── ticket_db.py         # Ticket CRUD operations
│   ├── mcp_tools.py         # MCP tool bridge
│   ├── notification_tools.py# Alert & notification dispatch
│   └── ...                  # Additional tool modules
│
├── config/                  # Configuration & clients
│   ├── settings.py          # Central settings loader (from .env)
│   ├── db.py                # PostgreSQL connection pool & helpers
│   ├── llm_client.py        # Gemini / Vertex AI LLM client
│   ├── logging_setup.py     # Structured logging configuration
│   └── metrics.py           # Performance metrics collection
│
├── rag/                     # Retrieval-Augmented Generation
│   ├── engine.py            # Vector search engine (pgvector)
│   ├── document_processor.py# Document chunking & embedding
│   ├── index_all.py         # Batch indexing orchestrator
│   └── data/                # SOPs, KB articles, incident history
│
├── state/                   # Persistent state management
│   ├── conversation_state.py# Session state & phase tracking
│   ├── issue_tracker.py     # Issue lifecycle management
│   ├── session_manager.py   # Session creation & cleanup
│   ├── agent_catalog.py     # Dynamic agent capability catalog
│   └── app_config.py        # Runtime application configuration
│
├── mcp_server/              # MCP Server (standalone)
│   ├── server.py            # Streamable-HTTP MCP server
│   ├── ae_client.py         # AutomationEdge API adapter
│   ├── tool_specs.py        # Tool schema definitions
│   └── tools/               # MCP tool implementations
│
├── gateway/                 # API gateway layer
├── connector/               # External system connectors
├── frontend/                # Web UI assets
├── templates/               # Jinja2 HTML templates
├── static/                  # Static files (CSS, JS, images)
├── docs/                    # Documentation
├── tests/                   # Test suite
└── logs/                    # Runtime log output
```

---

## 🔧 Prerequisites

| Requirement | Version |
|---|---|
| **Python** | 3.10+ |
| **PostgreSQL** | 14+ (with `pgvector` extension recommended) |
| **Google Cloud** | Vertex AI API enabled, or a Gemini API key |
| **AutomationEdge T4** | Account with API credentials |

---

## 🚀 Quick Start

### 1. Clone & Install Dependencies

```bash
cd D:\AG_V2\AEAgenticSupport
pip install -r requirements.txt
```

### 2. Configure Environment

Copy and edit the `.env` file with your credentials:

```bash
cp .env.example .env
# Edit .env with your values
```

Key variables to set:

| Variable | Description |
|---|---|
| `POSTGRES_DSN` | PostgreSQL connection string |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID |
| `VERTEX_AI_MODEL` | Gemini model name (e.g., `gemini-2.5-flash`) |
| `VERTEX_AI_MAX_TOKENS` | Max response tokens (default: `32000`) |
| `T4_BASE_URL` | AutomationEdge T4 API base URL |
| `T4_USERNAME` / `T4_PASSWORD` | T4 credentials |
| `T4_ORG_CODE` | T4 organization code |
| `AE_MCP_SERVER_URL` | MCP server endpoint |
| `TICKET_API_URL` | Ticket generation API endpoint |

### 3. Setup Database

```bash
python setup_db.py
```

This creates all required PostgreSQL tables with `IF NOT EXISTS` — safe to re-run.

> **Existing deployments**: Run `python setup_db.py --migrate` to migrate from older schemas.

### 4. Index RAG Documents

```bash
python run_rag_index.py
```

Embeds all tools, SOPs, KB articles, and workflows into the vector database.

### 5. Start All Services

```bash
.\start_servers.bat
```

Or start individually:

| Component | Command | Port |
|---|---|---|
| **Agent Server** | `python agent_server.py` | `8001` |
| **MCP Server** | `python -m mcp_server --transport streamable-http --host 127.0.0.1 --port 3000` | `3000` |
| **AI Studio Engine** | `manage.pyc runserver localhost:8000` | `8000` |
| **Cognibot** | `manage.pyc runserver localhost:3978` | `3978` |

---

## 🗄️ Database Tables

| Table | Purpose |
|---|---|
| `rag_documents` | RAG vector store (pgvector or JSONB fallback) |
| `issue_registry` | Issue tracking per conversation |
| `conversation_state` | Session persistence, phase, active issue pointer |
| `chat_messages` | Cross-session message history |
| `user_feedback` | User rating & comment tracking |
| `approval_audit_log` | Human-in-the-loop approval audit trail |
| `tool_execution_log` | Full tool call audit log (params + result) |
| `workflow_catalog` | T4 workflow cache (avoids repeated API calls) |

---

## 📡 API Endpoints

### Agent Server (`localhost:8001`)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/chat` | Send a message to the AI agent |
| `GET` | `/health` | Health check |
| `POST` | `/approve` | Approve/reject a pending action |

### MCP Server (`localhost:3000`)

Exposes AutomationEdge tools via the **Model Context Protocol** over streamable HTTP. See [`mcp_server/README.md`](mcp_server/README.md) for full tool reference.

---

## 🔍 RAG Indexing Options

```bash
# Index everything (recommended on first run)
python run_rag_index.py

# Selective indexing
python run_rag_index.py --only tools     # Static/registered tools
python run_rag_index.py --only mcp       # Live MCP server tools
python run_rag_index.py --only t4        # Live T4 workflows
python run_rag_index.py --only sops      # SOPs only
python run_rag_index.py --only kb        # Knowledge Base articles
python run_rag_index.py --only incidents # Past incidents

# Skip specific sources
python run_rag_index.py --skip t4        # Everything except T4 (fast, offline)
```

---

## 📊 Observability

Enable **LangFuse** tracing by setting in `.env`:

```env
LANGFUSE_ENABLED=true
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

This traces all LLM calls, tool executions, RAG searches, and approval flows.

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Database health check
python db_health_check.py
```

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     Cognibot UI                         │
│                  (localhost:3978)                        │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                  Agent Server                           │
│               (localhost:8001)                           │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │ Router   │→ │Orchestr. │→ │ Specialist Agents     │  │
│  │          │  │          │  │ • Diagnostic           │  │
│  │          │  │          │  │ • RCA                  │  │
│  │          │  │          │  │ • Remediation          │  │
│  │          │  │          │  │ • Escalation           │  │
│  └──────────┘  └────┬─────┘  └───────────────────────┘  │
│                     │                                    │
│  ┌──────────────────▼──────────────────────────────┐    │
│  │              Tool Layer                          │    │
│  │  Registry │ AE Client │ MCP │ Tickets │ Logs    │    │
│  └──────────────────┬──────────────────────────────┘    │
└─────────────────────┼───────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌──────────────┐ ┌─────────┐ ┌──────────┐
│ PostgreSQL   │ │ T4 AE   │ │ MCP      │
│ + pgvector   │ │ Platform│ │ Server   │
│ (RAG + State)│ │         │ │ (:3000)  │
└──────────────┘ └─────────┘ └──────────┘
```

---

## 📄 License

Proprietary — AutomationEdge / ValueDx. All rights reserved.
