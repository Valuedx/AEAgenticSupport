> - **Initial Setup (2026-03-20)**: V0.1 scaffold — frontend dev server, backend API, prerequisites, and configuration. See `TECHNICAL_BLUEPRINT.md` for architecture and `HOW_IT_WORKS.md` for runtime walkthrough.

## AE AI Hub — Orchestrator Setup Guide

**Version:** 0.1  
**Last updated:** 2026-03-20

---

### Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Project Structure](#2-project-structure)
3. [Frontend Setup](#3-frontend-setup)
4. [Backend Setup](#4-backend-setup)
5. [Database Setup](#5-database-setup)
6. [Running the Services](#6-running-the-services)
7. [Environment Variables](#7-environment-variables)
8. [Verifying the Installation](#8-verifying-the-installation)
9. [Development Workflow](#9-development-workflow)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

### 1.1 Software Requirements

| Software | Version | Purpose |
|----------|---------|---------|
| **Node.js** | >= 18.x | Frontend build and dev server |
| **npm** | >= 9.x | Frontend package manager |
| **Python** | >= 3.11 | Backend API and worker |
| **PostgreSQL** | >= 15 | Workflow state and execution logs |
| **Redis** | >= 7.x | Celery message broker and result backend |

### 1.2 Parent Project

The orchestrator sits inside the `AEAgenticSupport` repository and depends on:

- **MCP Server** (optional): If running, the tool bridge endpoint (`GET /api/v1/tools`) will import tool specs from `mcp_server/tool_specs.py`. If unavailable, the endpoint returns an empty list and the frontend uses its built-in palette.

### 1.3 Network Ports

| Service | Default Port | Configurable Via |
|---------|-------------|-----------------|
| Frontend (Vite dev server) | 8080 | `orchestrator/frontend/vite.config.ts` |
| Backend (FastAPI) | 8001 | `uvicorn` CLI argument |
| PostgreSQL | 5432 | `ORCHESTRATOR_DATABASE_URL` |
| Redis | 6379 | `ORCHESTRATOR_REDIS_URL` |
| MCP Server (parent) | 3000 | `ORCHESTRATOR_MCP_SERVER_URL` |

---

## 2. Project Structure

```
orchestrator/
├── TECHNICAL_BLUEPRINT.md          # Architecture documentation
├── SETUP_GUIDE.md                  # This file
├── HOW_IT_WORKS.md                 # Runtime walkthrough
│
├── frontend/                       # React + TypeScript visual builder
│   ├── src/
│   │   ├── App.tsx                 # Three-panel layout
│   │   ├── store/flowStore.ts      # Zustand state management
│   │   ├── types/nodes.ts          # Node types and palette definition
│   │   ├── components/
│   │   │   ├── canvas/             # React Flow canvas
│   │   │   ├── nodes/              # Custom AgenticNode component
│   │   │   ├── sidebar/            # NodePalette + PropertyInspector
│   │   │   └── ui/                 # shadcn/ui components
│   │   └── lib/utils.ts            # Tailwind class merge utility
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
│
├── backend/                        # FastAPI execution engine
│   ├── main.py                     # App entry point
│   ├── requirements.txt            # Python dependencies
│   ├── alembic.ini                 # Migration config
│   ├── alembic/                    # Database migrations
│   └── app/
│       ├── config.py               # Settings from env
│       ├── database.py             # SQLAlchemy setup
│       ├── api/                    # REST endpoints
│       ├── engine/                 # DAG parser and executor
│       ├── models/                 # SQLAlchemy ORM models
│       ├── workers/                # Celery tasks
│       └── security/               # Tenant isolation
│
└── shared/
    └── node_registry.json          # Canonical node type schemas
```

---

## 3. Frontend Setup

### 3.1 Install Dependencies

```bash
cd orchestrator/frontend
npm install
```

This installs React 19, `@xyflow/react`, Zustand, Tailwind CSS v4, shadcn/ui, and Lucide icons.

### 3.2 Start the Dev Server

```bash
npm run dev
```

The Vite dev server starts on **http://localhost:8080** with hot module replacement.

### 3.3 Production Build

```bash
npm run build
```

Output goes to `orchestrator/frontend/dist/`. Serve with any static file server or configure Vite preview:

```bash
npm run preview
```

### 3.4 Type Checking

```bash
npx tsc -b --noEmit
```

---

## 4. Backend Setup

### 4.1 Create a Virtual Environment

```bash
cd orchestrator/backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 4.2 Install Dependencies

```bash
pip install -r requirements.txt
```

### 4.3 Configure Environment

Create a `.env` file in `orchestrator/backend/` or set environment variables with the `ORCHESTRATOR_` prefix:

```env
ORCHESTRATOR_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ae_orchestrator
ORCHESTRATOR_REDIS_URL=redis://localhost:6379/0
ORCHESTRATOR_MCP_SERVER_URL=http://localhost:3000
ORCHESTRATOR_SECRET_KEY=your-secret-key-here
ORCHESTRATOR_CORS_ORIGINS=["http://localhost:8080"]
```

---

## 5. Database Setup

### 5.1 Create the Database

```bash
psql -U postgres -c "CREATE DATABASE ae_orchestrator;"
```

### 5.2 Run Migrations

```bash
cd orchestrator/backend

# Generate the initial migration from models
alembic revision --autogenerate -m "initial schema"

# Apply migrations
alembic upgrade head
```

This creates four tables: `workflow_definitions`, `workflow_instances`, `execution_logs`, and `tenant_tool_overrides`.

### 5.3 Schema Overview

```
workflow_definitions     1 ──── * workflow_instances     1 ──── * execution_logs
  id (PK, UUID)                   id (PK, UUID)                   id (PK, UUID)
  tenant_id                       tenant_id                       instance_id (FK)
  name                            workflow_def_id (FK)            node_id
  graph_json (JSONB)              status                          node_type
  version                         context_json (JSONB)            status
  created_at                      current_node_id                 input_json (JSONB)
  updated_at                      started_at                      output_json (JSONB)
                                  completed_at                    error

tenant_tool_overrides
  id (PK, UUID)
  tenant_id
  tool_name
  enabled
  config_json (JSONB)
```

---

## 6. Running the Services

### 6.1 Start All Services

Open separate terminals for each service:

**Terminal 1 — Frontend:**
```bash
cd orchestrator/frontend
npm run dev
```

**Terminal 2 — Backend API:**
```bash
cd orchestrator/backend
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

**Terminal 3 — Celery Worker:**
```bash
cd orchestrator/backend
celery -A app.workers.celery_app worker --loglevel=info
```

**Terminal 4 — Celery Beat (schedule triggers):**
```bash
cd orchestrator/backend
celery -A app.workers.celery_app beat --loglevel=info
```

**Terminal 5 — Redis** (if not already running):
```bash
redis-server
```

### 6.2 Quick Start (Frontend Only)

If you just want to use the visual builder without backend execution:

```bash
cd orchestrator/frontend
npm run dev
```

Open **http://localhost:8080**. You can drag nodes, connect them, and configure properties. Workflow execution requires the backend services.

---

## 7. Environment Variables

Backend settings use the `ORCHESTRATOR_` prefix, while the frontend API client uses `VITE_` variables. Both can be set via a `.env` file or shell environment:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ORCHESTRATOR_DATABASE_URL` | Yes | `postgresql://postgres:postgres@localhost:5432/ae_orchestrator` | PostgreSQL connection string |
| `ORCHESTRATOR_REDIS_URL` | Yes | `redis://localhost:6379/0` | Redis connection for Celery |
| `ORCHESTRATOR_MCP_SERVER_URL` | No | `http://localhost:3000` | Parent MCP server URL |
| `ORCHESTRATOR_SECRET_KEY` | Yes | `change-me-in-production` | Signing key for future JWT support |
| `ORCHESTRATOR_CORS_ORIGINS` | No | `["http://localhost:8080"]` | Allowed CORS origins (JSON array) |
| `ORCHESTRATOR_GOOGLE_API_KEY` | No | `""` | Google AI API key for Gemini models |
| `ORCHESTRATOR_GOOGLE_PROJECT` | No | `""` | GCP project ID (optional for Vertex AI) |
| `ORCHESTRATOR_GOOGLE_LOCATION` | No | `us-central1` | GCP region |
| `ORCHESTRATOR_OPENAI_API_KEY` | No | `""` | OpenAI API key for GPT models |
| `ORCHESTRATOR_OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | OpenAI-compatible base URL |
| `ORCHESTRATOR_ANTHROPIC_API_KEY` | No | `""` | Anthropic API key for Claude models |
| `ORCHESTRATOR_AUTH_MODE` | No | `dev` | Auth mode: `dev` (X-Tenant-Id header) or `jwt` (Bearer token) |
| `ORCHESTRATOR_VAULT_KEY` | No | `""` | Fernet encryption key for credential vault |
| `ORCHESTRATOR_RATE_LIMIT_REQUESTS` | No | `100` | Max API requests per tenant per window |
| `ORCHESTRATOR_RATE_LIMIT_WINDOW` | No | `1 minute` | Rate limit time window |
| `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` | No | `50` | Max workflow executions per tenant per hour |
| `VITE_API_URL` | No | `http://localhost:8001` | Orchestrator backend base URL for the frontend API client |
| `VITE_TENANT_ID` | No | `default` | Tenant id injected as `X-Tenant-Id` header in frontend requests |

---

## 8. Verifying the Installation

### 8.1 Frontend

1. Open **http://localhost:8080**.
2. You should see a three-panel layout: Node Palette (left), Canvas (center), Properties (right).
3. Drag a "Webhook Trigger" from the palette onto the canvas.
4. Drag an "LLM Agent" and connect the Trigger's output handle to the Agent's input handle.
5. Click the Agent node — the Property Inspector should show provider, model, and prompt fields.

### 8.2 Backend API

```bash
# Health check
curl http://localhost:8001/health
# Expected: {"status":"ok","service":"ae-ai-hub-orchestrator"}

# OpenAPI docs
# Open http://localhost:8001/docs in a browser
```

### 8.3 API Smoke Test

```bash
# Create a workflow
curl -X POST http://localhost:8001/api/v1/workflows \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: test-tenant" \
  -d '{
    "name": "Hello World",
    "graph_json": {
      "nodes": [
        {"id": "node_1", "type": "agenticNode", "position": {"x": 0, "y": 0},
         "data": {"label": "Webhook", "nodeCategory": "trigger", "config": {"method": "POST", "path": "/webhook"}}},
        {"id": "node_2", "type": "agenticNode", "position": {"x": 300, "y": 0},
         "data": {"label": "LLM Agent", "nodeCategory": "agent", "config": {"provider": "google", "model": "gemini-2.5-flash", "systemPrompt": "You are helpful."}}}
      ],
      "edges": [
        {"id": "e1-2", "source": "node_1", "target": "node_2"}
      ]
    }
  }'

# List workflows
curl http://localhost:8001/api/v1/workflows \
  -H "X-Tenant-Id: test-tenant"
```

---

## 9. Development Workflow

### 9.1 Frontend Development

- **Hot reload:** Vite automatically reloads on file changes.
- **Adding shadcn components:** `npx shadcn@latest add <component-name>` inside `orchestrator/frontend/`.
- **Import alias:** Use `@/` to reference `src/` (e.g. `import { cn } from "@/lib/utils"`).

### 9.2 Backend Development

- **Auto-reload:** `uvicorn main:app --reload` watches for file changes.
- **Adding models:** Define in `app/models/`, import in `app/models/__init__.py`, then run `alembic revision --autogenerate -m "description"` and `alembic upgrade head`.
- **OpenAPI docs:** Available at `http://localhost:8001/docs` (Swagger) and `http://localhost:8001/redoc`.

### 9.3 Adding a New Node Type

1. **Shared schema:** Add the node type to `shared/node_registry.json`.
2. **Frontend palette:** Add a `PaletteItem` entry to `NODE_PALETTE` in `frontend/src/types/nodes.ts`.
3. **Frontend inspector:** Add config fields to the appropriate panel in `PropertyInspector.tsx`.
4. **Backend handler:** Add or extend a handler in `backend/app/engine/node_handlers.py`.

---

## 10. Troubleshooting

| Problem | Likely Cause | Solution |
|---------|-------------|----------|
| Frontend blank page | CSS not loading | Ensure `index.css` has `@import "tailwindcss"` at top |
| `Module not found: @/...` | Path alias misconfigured | Check `tsconfig.json` has `"paths": {"@/*": ["./src/*"]}` and `vite.config.ts` has `resolve.alias` |
| shadcn init fails | Missing Tailwind or alias | Run `npm install tailwindcss @tailwindcss/vite` and ensure tsconfig has path alias |
| Backend import error | Missing dependencies | Run `pip install -r requirements.txt` in the venv |
| `401 Missing X-Tenant-Id` | No tenant header in request | Set `VITE_TENANT_ID` for the frontend or add `-H "X-Tenant-Id: your-tenant"` to API calls |
| Celery tasks not executing | Redis not running | Start Redis with `redis-server` |
| Migration fails | DB doesn't exist | Create it: `psql -U postgres -c "CREATE DATABASE ae_orchestrator;"` |
| MCP tools endpoint empty | MCP server dir not found | Ensure `mcp_server/tool_specs.py` exists in parent project root |
| Canvas nodes not appearing | Drag-and-drop broken | Check browser console for JS errors; ensure `ReactFlowProvider` wraps the app |

---

**Document version:** 0.6  
**Last updated:** 2026-03-20
