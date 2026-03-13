# AI Studio Run Guide

This document contains instructions for starting the various components of the AI Studio system.

## Components

### 1. Agent Server
Responsible for agentic logic and communication.
- **Directory**: `D:\AEAgenticSupport`
- **Command**: `python agent_server.py`

### 2. MCP Server
Provides Model Context Protocol (MCP) tools over HTTP.
- **Directory**: `D:\AEAgenticSupport`
- **Command**: `python -m mcp_server --transport streamable-http --host 127.0.0.1 --port 3000`

### 3. AI Studio Engine
The main backend engine for AI Studio.
- **Directory**: `D:\AEAgenticSupport\AI_Studio_Local\AIStudio\engine`
- **Python**: `D:\AEAgenticSupport\AI_Studio_Local\AIStudio\python\python.exe`
- **Command**: `..\python\python.exe manage.pyc runserver localhost:8000`

### 4. Chatbot Webservice (Cognibot)
The chatbot interface service.
- **Directory**: `D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot`
- **Python**: `D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\python\python.exe`
- **Command**: `..\python\python.exe manage.pyc runserver localhost:3978`

---

## Database Setup / Migration

Run **once** on first deployment to create all required PostgreSQL tables and extensions.

- **Directory**: `D:\AEAgenticSupport`
- **Script**: `setup_db.py`
- **Requires**: `POSTGRES_DSN` set in `.env` and PostgreSQL running

### Create all tables (first-time setup)
```bash
cd D:\AEAgenticSupport
python setup_db.py
```

This will:
- Auto-detect if `pgvector` extension is available (uses native vector columns if yes, JSONB fallback if no)
- Auto-detect the embedding vector dimension from your configured `EMBEDDING_MODEL`
- Create all tables with `IF NOT EXISTS` — safe to re-run

**Tables created:**

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

### One-time migration (existing deployments only)
If upgrading from an older version that had the `issue_tracker_state` table:
```bash
python setup_db.py --migrate
```
This moves `active_issue_id` data into `conversation_state` and drops the old table. Safe to run multiple times.

---

## RAG Indexing / Tool Embedding


Run this **before starting the agent for the first time**, or whenever tools, SOPs, KB articles, or workflows are updated. It embeds all documents into the RAG vector database so the agent can discover and route to the correct tools.

- **Directory**: `D:\AEAgenticSupport`
- **Script**: `run_rag_index.py`

### Index everything (recommended on first run)
```bash
cd D:\AEAgenticSupport
python run_rag_index.py
```

### Selective indexing (only re-index what changed)
```bash
# Static MCP / registered tools only
python run_rag_index.py --only tools

# Live MCP server tools only (requires AE_MCP_SERVER_URL in .env)
python run_rag_index.py --only mcp

# Live T4 AutomationEdge workflows only (requires AE_USERNAME / AE_API_KEY in .env)
python run_rag_index.py --only t4

# SOPs only
python run_rag_index.py --only sops

# Knowledge Base articles only
python run_rag_index.py --only kb

# Past incidents only
python run_rag_index.py --only incidents

# Everything except T4 live fetch (fast, offline)
python run_rag_index.py --skip t4
```

> **Note**: T4 workflow indexing requires `AE_USERNAME` (or `AE_API_KEY`) to be set in `.env`.
> MCP server tool indexing requires `AE_MCP_SERVER_URL`. If missing, those steps are skipped automatically with a warning.

---

## Quick Start

You can use the provided `start_servers.bat` script to launch all components in separate terminal windows.

```bash
cd D:\AEAgenticSupport
.\start_servers.bat
```
