> **Documentation Update (2026-03-04)**  
> Patch release notes included in this version:
> - Added AutomationEdge session-token REST client support and configurable workflow discovery methods.
> - Added dynamic AE tool sync and mapping into the tool registry for agentic usecases.
> - Added tools/agents management UI and APIs for viewing/maintaining agent definitions, tool links, and interactions.
> - Added T4 tenant validation notes: token field `sessionToken`, workflow list via `POST /workflows`, details via `GET /workflows/{id}`.
> - Validation status: targeted test suite passed (`33 passed`).
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
# AI Studio (On‑Prem) Agentic Support Assistant — Step‑by‑Step Configuration + Code

> **Updated 2026-02-28** — Embeddings migrated from sentence-transformers to Google Vertex AI `text-embedding-004`. Extension file structure corrected (`migrations/` directory, `.txt` extension). Hook contract updated to async class-based pattern.

This document describes how to build an agentic support assistant using **AutomationEdge AI Studio (on‑prem)** with:
- **MS Teams** channel
- **Python Extension** as the orchestrator (no mega orchestration workflow)
- **RAG KB** for SOP grounding + tool selection
- **AE workflows exposed as REST tools**
- **Safe auto‑run**; otherwise request approval from **on‑shift tech users** (roster via Teams IDs)
- **Hard concurrency + idempotency** to avoid “stale conversation / duplicate trigger” glitches

> Note: the SOP/tool‑library files you uploaded earlier expired on my side. If you want this doc to embed your exact SOP steps and exact tool catalog entries, please re‑upload those files.

---

## 0) Prerequisites

### 0.1 Channel integration for MS Teams
AI Studio expects the Azure Bot messaging endpoint to send messages to:

- `https://<public-endpoint>/api/messages` (ingress)
- `https://<public-endpoint>/api/reply` (egress)

Ensure your on‑prem deployment is reachable via HTTPS (reverse proxy or public endpoint).

### 0.2 What we will build
We will implement:

1. **Deterministic Router (hook)** — runs for every Teams message:
   - per‑thread lock
   - idempotency
   - small‑talk gate
   - case routing (multiple cases per thread)
   - calls Planner/Executor

2. **Planner (RAG + tool selection)** — multi‑turn:
   - queries SOP KB
   - queries Tool Catalog KB
   - builds a strict Plan JSON

3. **Executor (deterministic)**:
   - enforces “hands‑off” once assigned
   - auto‑runs safe steps
   - requests approval for risky steps from on‑shift tech users
   - calls tools via REST

---

## 1) Download Extension zip baseline

In AI Studio UI:
- Cognibot → **Extension** tab → **Download** the current Extension zip.

Keep the structure; we will add modules.

---

## 2) Extension folder structure

Create/modify the Extension zip to include:

```
custom/
  apps.py
  settings.py
  models.py
  migrations/
    __init__.py
    0001_initial.py
  custom_hooks.py
  extra_requirements.txt

  helpers/
    db.py
    locks.py
    policy.py
    rag.py
    tools_rest.py
    roster.py
    teams.py

  functions/
    python/
      support_agent.py
```

---

## 3) Optional dependencies

If `requests` isn’t present in your runtime, add:

**custom/extra_requirements.txt**
```text
google-cloud-aiplatform>=1.60.0
pgvector>=0.3.0
numpy>=1.26.0
tenacity>=8.2.3
psycopg2-binary>=2.9.9
```

---

## 4) Database tables (on‑prem)

These are minimal tables required for correctness.

### 4.1 models.py

**custom/models.py**
```python
from django.db import models
from django.utils import timezone

class ProcessedMessage(models.Model):
    thread_id = models.CharField(max_length=256)
    teams_message_id = models.CharField(max_length=256)
    processed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("thread_id", "teams_message_id")
        indexes = [models.Index(fields=["thread_id", "processed_at"])]

class ConversationState(models.Model):
    thread_id = models.CharField(max_length=256, unique=True)
    active_case_id = models.CharField(max_length=64, null=True, blank=True)  # UUID string
    last_user_message_id = models.CharField(max_length=256, null=True, blank=True)
    last_bot_message_id = models.CharField(max_length=256, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

class Case(models.Model):
    case_id = models.CharField(max_length=64, unique=True)  # UUID string
    thread_id = models.CharField(max_length=256)

    state = models.CharField(max_length=64)  # PLANNING/NEED_INFO/...
    owner_type = models.CharField(max_length=32, default="BOT_L1")  # BOT_L1/HUMAN_TEAM
    owner_team = models.CharField(max_length=64, null=True, blank=True)

    user_type = models.CharField(max_length=32, null=True, blank=True)  # BUSINESS/TECH_SUPPORT
    ticket_id = models.CharField(max_length=128, null=True, blank=True)

    planner_state_json = models.JSONField(default=dict, blank=True)
    latest_plan_json = models.JSONField(default=dict, blank=True)
    plan_version = models.IntegerField(default=0)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["thread_id", "state"]),
            models.Index(fields=["ticket_id"]),
        ]

class Approval(models.Model):
    case_id = models.CharField(max_length=64)
    plan_version = models.IntegerField()
    status = models.CharField(max_length=32, default="PENDING")  # PENDING/APPROVED/REJECTED
    requested_to = models.JSONField(default=list, blank=True)  # list of Teams IDs
    decided_by = models.CharField(max_length=256, null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["case_id", "status"])]
```

### 4.2 Migrations directory

The project now uses a proper Django `custom/migrations/` directory instead of a single `migrations.py` stub. This directory contains:

- `__init__.py` — marks the directory as a Python package (required by Django)
- `0001_initial.py` — auto-generated migration that creates the `ProcessedMessage`, `ConversationState`, `Case`, `Approval`, and `IssueLink` tables defined in `models.py`

To generate or update migrations after changing models:

```bash
python manage.py makemigrations custom
python manage.py migrate
```

---

## 5) Per‑thread lock + idempotency (fixes “stale conversation”)

### 5.1 Advisory lock

**custom/helpers/locks.py**
```python
import hashlib
from contextlib import contextmanager
from django.db import connection

def _lock_key(thread_id: str) -> int:
    h = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return int(h[:16], 16)  # 64-bit

@contextmanager
def pg_advisory_lock(thread_id: str):
    key = _lock_key(thread_id)
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s);", [key])
    try:
        yield
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s);", [key])
```

### 5.2 Dedupe utilities

**custom/helpers/db.py**
```python
from custom.models import ProcessedMessage

def is_duplicate_message(thread_id: str, teams_message_id: str) -> bool:
    return ProcessedMessage.objects.filter(thread_id=thread_id, teams_message_id=teams_message_id).exists()

def mark_message_processed(thread_id: str, teams_message_id: str):
    ProcessedMessage.objects.create(thread_id=thread_id, teams_message_id=teams_message_id)
```

---

## 6) REST tool calling (AE workflows as tools)

**custom/helpers/tools_rest.py**
```python
import requests, json
from typing import Dict, Any, Optional

class ToolError(Exception):
    pass

class RestToolClient:
    def __init__(self, base_url: str, auth_token: str, timeout_s: int = 30):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout_s = timeout_s

    def call(self, tool_ref: str, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{tool_ref.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=self.timeout_s)
        if resp.status_code >= 300:
            raise ToolError(f"{tool_ref} failed: {resp.status_code} {resp.text[:500]}")
        return resp.json()
```

> You will replace the `tool_ref` paths with your actual internal REST endpoints for ticketing + AE diagnostics/remediation.

---

## 7) RAG retrieval (PostgreSQL + pgvector)

The RAG layer uses **PostgreSQL + pgvector** for vector similarity search and **Vertex AI** (Gemini) for embeddings and generation. If your deployment uses AI Studio's built-in KM, you can keep the REST stubs; otherwise, implement direct pgvector access.

**Option A: REST stubs (if using AI Studio KM or a separate RAG service)**

**custom/helpers/rag.py**
```python
from typing import List, Dict
from custom.helpers.tools_rest import RestToolClient

def rag_search_sop(client: RestToolClient, query: str, top_k: int = 6) -> List[Dict]:
    return client.call("/rag/sop/search", {"query": query, "top_k": top_k}).get("results", [])

def rag_search_tools(client: RestToolClient, query: str, top_k: int = 8) -> List[Dict]:
    return client.call("/rag/tools/search", {"query": query, "top_k": top_k}).get("results", [])
```

**Option B: Direct pgvector access (recommended for full control)**

```python
# custom/helpers/rag.py
import os
import psycopg2
import vertexai
from vertexai.language_models import TextEmbeddingModel

vertexai.init(project=os.environ.get("GOOGLE_CLOUD_PROJECT"), location="us-central1")
_embed_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
_dsn = os.environ.get("POSTGRES_DSN", "postgresql://localhost/ops_agent")

def _embed(text: str) -> list:
    result = _embed_model.get_embeddings([text])
    return result[0].values

def rag_search(query: str, collection: str, top_k: int = 5) -> list:
    emb = _embed(query)
    with psycopg2.connect(_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, content, metadata,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM rag_documents
                WHERE collection = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (emb, collection, emb, top_k))
            return [
                {"id": r[0], "content": r[1], "metadata": r[2], "similarity": r[3]}
                for r in cur.fetchall()
            ]

def rag_search_sop(client, query: str, top_k: int = 6) -> list:
    return rag_search(query, "sop", top_k)

def rag_search_tools(client, query: str, top_k: int = 8) -> list:
    return rag_search(query, "tools", top_k)
```

The `/rag/sop/search` and `/rag/tools/search` endpoints (if using Option A) should be backed by pgvector or AI Studio KM search.

---

## 8) Safe auto‑run vs approval policy

**custom/helpers/policy.py**
```python
from typing import Dict, Any, Tuple

SAFE_AUTORUN_CAPABILITIES = {
    "CAP_TICKET_UPDATE",
    "CAP_GET_REQUEST_STATUS",
    "CAP_GET_REQUEST_DETAILS",
    "CAP_GET_EXECUTION_LOGS",
    "CAP_DOWNLOAD_EXECUTION_ARTIFACTS",
    # add more safe steps here...
}

def classify_step(step: Dict[str, Any]) -> Tuple[str, bool]:
    risk = step.get("policy_tags", {}).get("risk", "READ_ONLY")
    cap = step.get("capability_id")

    if risk == "DESTRUCTIVE":
        return (risk, True)

    if risk == "SAFE_WRITE":
        # auto-run only if capability is allowlisted
        return (risk, cap not in SAFE_AUTORUN_CAPABILITIES)

    return (risk, False)  # READ_ONLY
```

---

## 9) Roster (on‑shift tech users by Teams IDs)

**custom/helpers/roster.py**
```python
from datetime import datetime, time
from typing import List, Dict

def is_on_shift(now_local: datetime, shift: Dict) -> bool:
    s_h, s_m = map(int, shift["start"].split(":"))
    e_h, e_m = map(int, shift["end"].split(":"))
    start = time(s_h, s_m)
    end = time(e_h, e_m)
    t = now_local.time()
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end  # overnight

def pick_onshift_techs(roster: List[Dict], now_local: datetime) -> List[str]:
    onshift = []
    for r in roster:
        if is_on_shift(now_local, r["shift"]):
            onshift.append(r["teams_user_id"])
    return onshift[:5]
```

---

## 10) Teams reply helper

**custom/helpers/teams.py**
```python
def make_text_reply(text: str) -> dict:
    return {"type": "message", "text": text}
```

(If you want adaptive cards for approval, replace with a card payload.)

---

## 11) Router hook — api_messages_hook

This is the “brainstem”: lock + dedupe + smalltalk gate + support handling.

> **Contract note:** The AI Studio Cognibot hook must be an **async method** inside a class that extends `ChatbotHooks`. The method signature is `async def api_messages_hook(request, activity)`. Use `from __future__ import annotations` and `asgiref.sync.sync_to_async` to bridge Django ORM calls.

**custom/custom_hooks.py**
```python
from __future__ import annotations

import uuid
from asgiref.sync import sync_to_async
from django.utils import timezone

try:
    from aistudiobot.hooks import ChatbotHooks
except ImportError:
    class ChatbotHooks:
        pass

from custom.helpers.locks import pg_advisory_lock
from custom.helpers.db import is_duplicate_message, mark_message_processed
from custom.helpers.teams import make_text_reply
from custom.models import ConversationState
from custom.functions.python.support_agent import handle_support_turn


def _extract_thread_id(activity: dict) -> str:
    convo = activity.get("conversation", {}) or {}
    return convo.get("id") or "unknown-thread"

def _extract_message_id(activity: dict) -> str:
    return activity.get("id") or str(uuid.uuid4())

def _extract_text(activity: dict) -> str:
    return (activity.get("text") or "").strip()

def _is_smalltalk(text: str) -> bool:
    t = text.lower().strip()
    return t in {"hi","hello","hey","thanks","thank you","ok"} or t.startswith(("hi ","hello ","hey "))


def _process_message_sync(activity_dict: dict):
    """Synchronous core — all Django ORM calls happen here."""
    thread_id = _extract_thread_id(activity_dict)
    msg_id = _extract_message_id(activity_dict)
    text = _extract_text(activity_dict)

    with pg_advisory_lock(thread_id):
        if is_duplicate_message(thread_id, msg_id):
            return None
        mark_message_processed(thread_id, msg_id)

        if _is_smalltalk(text):
            cs, _ = ConversationState.objects.get_or_create(thread_id=thread_id)
            cs.last_user_message_id = msg_id
            cs.updated_at = timezone.now()
            cs.save()
            return make_text_reply("Hello! How can I help you with support today?")

        return handle_support_turn(
            thread_id=thread_id, teams_message_id=msg_id,
            user_text=text, raw_activity=activity_dict,
        )


class CustomChatbotHooks(ChatbotHooks):
    async def api_messages_hook(request, activity):
        activity_dict = activity if isinstance(activity, dict) else {"text": getattr(activity, "text", "")}
        return await sync_to_async(_process_message_sync, thread_sensitive=False)(activity_dict)
```

---

## 12) Planner + Executor in one function (with approval gate)

**custom/functions/python/support_agent.py**
```python
import uuid
from datetime import datetime
from django.utils import timezone

from custom.models import ConversationState, Case, Approval
from custom.helpers.teams import make_text_reply
from custom.helpers.tools_rest import RestToolClient, ToolError
from custom.helpers.rag import rag_search_sop, rag_search_tools
from custom.helpers.policy import classify_step
from custom.helpers.roster import pick_onshift_techs

TOOL_BASE_URL = "http://your-internal-tools-gateway"   # put in env/settings
TOOL_AUTH_TOKEN = "REPLACE_ME"                         # put in env/settings

TECH_ROSTER = [
    {"teams_user_id": "TECH1_TEAMS_ID", "shift": {"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"}, "skills": ["AE_PLATFORM"]},
]

def _get_or_create_case(thread_id: str) -> Case:
    cs, _ = ConversationState.objects.get_or_create(thread_id=thread_id)
    if cs.active_case_id:
        c = Case.objects.filter(case_id=cs.active_case_id).first()
        if c and c.state not in {"CLOSED","CANCELLED"}:
            return c

    case_id = str(uuid.uuid4())
    c = Case.objects.create(
        case_id=case_id,
        thread_id=thread_id,
        state="PLANNING",
        owner_type="BOT_L1",
        planner_state_json={},
        latest_plan_json={},
        plan_version=0,
        error_signatures=[],
        workflows_involved=[],
        recurrence_count=0,
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    cs.active_case_id = case_id
    cs.updated_at = timezone.now()
    cs.save()
    return c

def _ensure_ticket(client: RestToolClient, case: Case) -> None:
    if case.ticket_id:
        return
    resp = client.call("/tools/ticket/create", {"case_id": case.case_id, "thread_id": case.thread_id},
                       idempotency_key=f"ticket-create:{case.case_id}")
    case.ticket_id = resp.get("ticket_id")
    case.updated_at = timezone.now()
    case.save()

def _build_plan_with_rag(client: RestToolClient, case: Case, user_text: str) -> dict:
    sop_hits = rag_search_sop(client, query=user_text, top_k=6)
    tool_hits = rag_search_tools(client, query=user_text, top_k=8)

    issue_bucket = "OUTPUT_NOT_RECEIVED" if ("output" in user_text.lower() and "not" in user_text.lower()) else "GENERIC"

    steps = [
        {
            "index": 1,
            "type": "TICKET_UPDATE",
            "capability_id": "CAP_TICKET_UPDATE",
            "tool_ref": "/tools/ticket/update",
            "inputs": {"ticket_id": case.ticket_id, "note": f"User reported: {user_text}"},
            "policy_tags": {"risk": "SAFE_WRITE"}
        },
        {
            "index": 2,
            "type": "TOOL_CALL",
            "capability_id": "CAP_GET_REQUEST_STATUS",
            "tool_ref": "/tools/ae/request/status",
            "inputs": {"ticket_id": case.ticket_id},
            "policy_tags": {"risk": "READ_ONLY"}
        },
    ]

    if issue_bucket == "OUTPUT_NOT_RECEIVED":
        steps.append({
            "index": 3,
            "type": "TOOL_CALL",
            "capability_id": "CAP_PUBLISH_OUTPUT_TO_SHARED_PATH",
            "tool_ref": "/tools/ae/output/publish",
            "inputs": {"ticket_id": case.ticket_id},
            "policy_tags": {"risk": "SAFE_WRITE"}
        })

    return {
        "case_id": case.case_id,
        "ticket_id": case.ticket_id,
        "issue_bucket": issue_bucket,
        "sop_refs": [h.get("id") or h.get("title") for h in sop_hits],
        "tool_refs": [h.get("tool_ref") for h in tool_hits if h.get("tool_ref")],
        "steps": steps,
        "close_criteria": {"requires_user_confirmation": True, "confirmation_prompt": "Please confirm the output is received."}
    }

def _execute_plan(client: RestToolClient, case: Case, plan: dict) -> str:
    if case.owner_type == "HUMAN_TEAM" or case.state == "WAITING_ON_TEAM":
        client.call("/tools/ticket/update", {"ticket_id": case.ticket_id, "note": "User added info (hands-off)."})
        return "This ticket is assigned to the support team. I’ve added your update to the ticket."

    needs_approval = []
    for step in plan["steps"]:
        risk, ask = classify_step(step)
        if ask:
            needs_approval.append(step)

    if needs_approval:
        now_local = datetime.now()  # adjust if server TZ differs
        onshift = pick_onshift_techs(TECH_ROSTER, now_local)

        Approval.objects.create(
            case_id=case.case_id,
            plan_version=case.plan_version + 1,
            status="PENDING",
            requested_to=onshift,
            created_at=timezone.now(),
        )

        case.state = "WAITING_APPROVAL"
        case.latest_plan_json = plan
        case.plan_version += 1
        case.updated_at = timezone.now()
        case.save()

        return (
            "Approval required for one or more actions.\n"
            f"On-shift tech reviewers: {', '.join(onshift) if onshift else '(none found)'}\n"
            "Tech user can reply: `APPROVE` or `REJECT` in this thread."
        )

    for step in plan["steps"]:
        try:
            client.call(step["tool_ref"], step.get("inputs", {}),
                        idempotency_key=f"{case.case_id}:{case.plan_version}:{step['index']}")
        except ToolError as e:
            client.call("/tools/ticket/assign", {"ticket_id": case.ticket_id, "team": "L2_SUPPORT", "reason": str(e)})
            case.owner_type = "HUMAN_TEAM"
            case.owner_team = "L2_SUPPORT"
            case.state = "WAITING_ON_TEAM"
            case.updated_at = timezone.now()
            case.save()
            return "Couldn’t auto-resolve. Assigned to L2 Support; I’ll stay hands-off and add any new info to the ticket."

    case.state = "RESOLVED_PENDING_CONFIRMATION"
    case.latest_plan_json = plan
    case.plan_version += 1
    case.resolved_at = timezone.now()
    case.resolution_summary = f"Auto-resolved: executed {len(plan['steps'])} steps for {plan.get('issue_bucket', 'GENERIC')}"
    case.updated_at = timezone.now()
    case.save()
    return "Done. Please confirm if you received the output file."

def handle_support_turn(thread_id: str, teams_message_id: str, user_text: str, raw_activity: dict) -> dict:
    client = RestToolClient(base_url=TOOL_BASE_URL, auth_token=TOOL_AUTH_TOKEN)

    case = _get_or_create_case(thread_id)
    _ensure_ticket(client, case)

    # Simple approval handler (tech user replies APPROVE/REJECT)
    if user_text.strip().upper() in {"APPROVE","REJECT"} and case.state == "WAITING_APPROVAL":
        decision = user_text.strip().upper()
        appr = Approval.objects.filter(case_id=case.case_id, status="PENDING").order_by("-created_at").first()
        if appr:
            appr.status = "APPROVED" if decision == "APPROVE" else "REJECTED"
            appr.decided_by = raw_activity.get("from", {}).get("id")
            appr.decided_at = timezone.now()
            appr.save()

            if decision == "REJECT":
                case.state = "PLANNING"
                case.updated_at = timezone.now()
                case.save()
                return make_text_reply("Approval rejected. Tell me what to do next, or I can assign it to the team.")

            # Approved: execute stored plan
            case.state = "EXECUTING"
            case.updated_at = timezone.now()
            case.save()
            msg = _execute_plan(client, case, case.latest_plan_json)
            return make_text_reply(msg)

    # Normal turn: plan then execute (auto-run safe)
    case.state = "PLANNING"
    case.updated_at = timezone.now()
    case.save()

    plan = _build_plan_with_rag(client, case, user_text)

    case.state = "EXECUTING"
    case.updated_at = timezone.now()
    case.save()

    msg = _execute_plan(client, case, plan)
    return make_text_reply(msg)
```

---

## 13) Deploy the Extension zip

- Cognibot → Extension → Upload zip
- Restart/redeploy the Cognibot service (per your on‑prem procedure)
- Validate `/api/messages` receives Teams payloads

---

## 14) What you must customize

1) **Thread ID extraction** in `custom_hooks.py` based on your Teams payload.
2) Replace REST endpoints:
   - `/tools/ticket/*`
   - `/tools/ae/*`
   - `/rag/*` (or use direct pgvector access — see section 7 Option B)
   - `/llm/classify` (for issue classifier LLM fallback)
3) Replace `TECH_ROSTER` with your real roster (DB table or config).
4) Tune `SAFE_AUTORUN_CAPABILITIES` allowlist.
5) Set environment variables:
   - `POSTGRES_DSN` — PostgreSQL connection string (must have pgvector extension)
   - `GOOGLE_CLOUD_PROJECT` — GCP project for Vertex AI
   - `GOOGLE_APPLICATION_CREDENTIALS` — path to service account JSON
   - `TOOL_BASE_URL` and `TOOL_AUTH_TOKEN` — for REST tool gateway

---

## 15) Recommended next enhancement
- Use Adaptive Cards for approvals (buttons) instead of typed `APPROVE/REJECT`
- Add tool-catalog schema enforcement (validate inputs before calling tools)
- Add “tool-gap” step type if planner needs a missing capability
- **Issue Context Tracking** — multi-issue classification per thread (see section 16)
- **pgvector RAG** — direct PostgreSQL vector search instead of REST stubs (see section 7 Option B)
- **Vertex AI** — use Gemini models for classification and generation (see section 16 LLM fallback)
- ~~**RAG-filtered tool selection**~~ — **implemented**: when catalog >30 tools, RAG filters which tools are sent to the LLM; `discover_tools` meta-tool lets the LLM search for more mid-conversation
- **General escape-hatch tools** — **implemented**: `call_ae_api`, `query_database`, `search_knowledge_base` in `tools/general_tools.py` are always available for fallback when no typed tool fits; LLM prefers typed tools for validation/audit, falls back to general when needed (see SETUP_GUIDE section 11.3)
- **Progress streaming** — **implemented**: When `AGENT_PROGRESS_ENABLED=true`, the agent sends real-time status messages (e.g., "Checking workflow status...") during long investigations. In Teams, these appear as proactive messages via Bot Framework; in webchat, they display as in-place updating italic text. See SETUP_GUIDE section 11.4.

---

## 16) Issue Context Tracking — Multi-Issue Per Thread

### The Problem

The current `_get_or_create_case` function (section 12) treats each thread as having a single active case. In practice, a user may raise multiple distinct issues within the same thread, or a previously resolved issue may recur:

```
Thread:
  User: "Claims batch failed"                  ← Case/Issue A
  Bot:  [investigates, resolves]
  User: "Reconciliation also failed"           ← Is this Case A? Or new Case B?
  User: "Claims batch failed again"            ← Recurrence of A? Or new Case C?
```

Without classification, the bot either:
- Conflates different issues into one case (losing context)
- Always creates new cases (losing resolution history and recurrence patterns)

### Solution: Issue Classifier in the Router Hook

Add a classification step in `custom_hooks.py` **before** calling `handle_support_turn`. The classifier uses three layers (fastest first):

1. **Keyword heuristics (instant)** — "approve", "reject" → continue current case; "different issue", "by the way" → new case. Note: ambiguous words like "also", "additionally" intentionally skip heuristics and fall through to the LLM
2. **Workflow + error signature matching (instant)** — if the user mentions a workflow from a resolved case with a failure keyword, it's a recurrence; if they mention a different workflow than the active case, it's a cascade
3. **LLM classification via Vertex AI (fallback)** — for ambiguous messages, asks Vertex AI with active/resolved case context

### Database Addition

Add to `custom/models.py`:

```python
class IssueLink(models.Model):
    """Links related cases (e.g., cascade failures). Bidirectional."""
    case_id_1 = models.CharField(max_length=64)
    case_id_2 = models.CharField(max_length=64)
    link_type = models.CharField(max_length=32)  # CASCADE / RELATED / RECURRENCE
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("case_id_1", "case_id_2")
        indexes = [
            models.Index(fields=["case_id_1"]),
            models.Index(fields=["case_id_2"]),
        ]

    @classmethod
    def get_linked_cases(cls, case_id: str):
        """Query both directions for all links involving this case."""
        from django.db.models import Q
        return cls.objects.filter(Q(case_id_1=case_id) | Q(case_id_2=case_id))
```

Also add these fields to the existing `Case` model:

```python
# Add to Case model
error_signatures = models.JSONField(default=list, blank=True)
workflows_involved = models.JSONField(default=list, blank=True)
recurrence_count = models.IntegerField(default=0)
resolved_at = models.DateTimeField(null=True, blank=True)
resolution_summary = models.TextField(null=True, blank=True)
```

Add a periodic cleanup task for the `ProcessedMessage` table:

```python
# Add to custom/helpers/db.py
from django.utils import timezone as tz
from datetime import timedelta

def cleanup_old_messages(days: int = 30):
    """Prune dedup records older than N days to prevent table bloat."""
    cutoff = tz.now() - timedelta(days=days)
    deleted, _ = ProcessedMessage.objects.filter(processed_at__lt=cutoff).delete()
    return deleted
```

### Issue Classifier Helper

Create `custom/helpers/issue_classifier.py`:

```python
import logging
from typing import Optional, Tuple
from custom.models import Case, IssueLink
from custom.helpers.tools_rest import RestToolClient

logger = logging.getLogger("support_agent.issue_classifier")

RECURRENCE_ESCALATION_THRESHOLD = 3


class IssueClassification:
    CONTINUE_EXISTING = "continue_existing"
    NEW_ISSUE = "new_issue"
    RELATED_NEW = "related_new"
    RECURRENCE = "recurrence"
    FOLLOWUP = "followup"
    STATUS_CHECK = "status_check"


# NOTE: "also", "additionally" are intentionally omitted — they are
# ambiguous (continuation vs related-new) and must go to LLM fallback.
APPROVAL_SIGNALS = ["approve", "reject", "yes", "no", "go ahead", "proceed"]

CONTINUE_SIGNALS = [
    "same workflow", "same one", "related to that",
    "on the same topic", "regarding that", "about that",
    "for that same", "going back to",
]

NEW_ISSUE_SIGNALS = [
    "different issue", "new problem", "something else",
    "unrelated", "separate issue", "by the way",
    "changing topic", "another workflow", "on a different note",
]

RECURRENCE_SIGNALS = [
    "happened again", "same error again", "still failing",
    "back again", "recurring", "keeps failing", "not fixed",
    "failed again", "same issue", "it's back",
]

FOLLOWUP_SIGNALS = [
    "did it work", "is it fixed", "did the restart work",
    "how did it go", "any update", "what happened after",
    "is it running now", "did it complete",
]


def classify_message(thread_id: str, user_text: str,
                     active_case: Optional[Case]) -> Tuple[str, Optional[str]]:
    """
    Three-layer classification: heuristics → workflow matching → LLM fallback.
    Returns (classification, case_id_or_none).
    """
    msg_lower = user_text.strip().lower()

    # No active case → always new
    if not active_case:
        return IssueClassification.NEW_ISSUE, None

    # Approval/rejection → always continue
    if msg_lower in APPROVAL_SIGNALS:
        return IssueClassification.CONTINUE_EXISTING, active_case.case_id

    # Explicit new-issue signals
    for signal in NEW_ISSUE_SIGNALS:
        if signal in msg_lower:
            return IssueClassification.NEW_ISSUE, None

    # Recurrence signals → validate with workflow matching
    for signal in RECURRENCE_SIGNALS:
        if signal in msg_lower:
            match = _find_recurrence_match(thread_id, msg_lower)
            if match:
                return IssueClassification.RECURRENCE, match
            # "again" without workflow match → ambiguous, fall through to LLM
            break

    # Follow-up signals → find the best matching issue
    for signal in FOLLOWUP_SIGNALS:
        if signal in msg_lower:
            target = _find_followup_target(thread_id, msg_lower, active_case)
            return IssueClassification.FOLLOWUP, target

    # Narrow continuation signals
    for signal in CONTINUE_SIGNALS:
        if msg_lower.startswith(signal) or f" {signal} " in f" {msg_lower} ":
            return IssueClassification.CONTINUE_EXISTING, active_case.case_id

    # Workflow matching: cascade detection
    cascade_match = _check_cascade(thread_id, msg_lower, active_case)
    if cascade_match:
        return IssueClassification.RELATED_NEW, cascade_match

    # Recurrence detection via workflow + failure words
    recurrence_match = _check_resolved_workflow_match(thread_id, msg_lower)
    if recurrence_match:
        return IssueClassification.RECURRENCE, recurrence_match

    # ── LLM fallback for ambiguous messages ──
    llm_result = _llm_classify(thread_id, msg_lower, active_case)
    if llm_result:
        return llm_result

    # Default: continue existing if case is active, else new
    if active_case.state not in ("CLOSED", "CANCELLED", "RESOLVED_PENDING_CONFIRMATION"):
        return IssueClassification.CONTINUE_EXISTING, active_case.case_id

    return IssueClassification.NEW_ISSUE, None


def _find_recurrence_match(thread_id: str, msg_lower: str) -> Optional[str]:
    """Find a resolved case in this thread whose workflow matches the message."""
    resolved = Case.objects.filter(
        thread_id=thread_id,
        state__in=["CLOSED", "RESOLVED_PENDING_CONFIRMATION"]
    ).order_by("-updated_at")[:5]

    for case in resolved:
        for wf in (case.workflows_involved or []):
            wf_parts = wf.lower().replace("_", " ").split()
            if any(part in msg_lower for part in wf_parts if len(part) > 3):
                return case.case_id
    return None


def _find_followup_target(thread_id: str, msg_lower: str,
                          active_case: Case) -> str:
    """Find the resolved/active case the user is asking about."""
    resolved = Case.objects.filter(
        thread_id=thread_id,
        state__in=["CLOSED", "RESOLVED_PENDING_CONFIRMATION"]
    ).order_by("-updated_at")[:5]

    for case in resolved:
        for wf in (case.workflows_involved or []):
            wf_parts = wf.lower().replace("_", " ").split()
            if any(part in msg_lower for part in wf_parts if len(part) > 3):
                return case.case_id
    return active_case.case_id


def _check_cascade(thread_id: str, msg_lower: str,
                   active_case: Case) -> Optional[str]:
    """
    Detect if the user mentions a DIFFERENT workflow than the active case,
    combined with a failure word — suggests cascade / related-new.
    """
    failure_words = ["fail", "error", "broken", "down", "stuck", "issue"]
    if not any(fw in msg_lower for fw in failure_words):
        return None

    active_wfs = {wf.lower() for wf in (active_case.workflows_involved or [])}
    mentions_active = any(
        any(part in msg_lower for part in wf.replace("_", " ").split() if len(part) > 3)
        for wf in active_wfs
    )
    if not mentions_active and active_wfs:
        return active_case.case_id
    return None


def _check_resolved_workflow_match(thread_id: str, msg_lower: str) -> Optional[str]:
    """Check if user mentions a workflow from a resolved case + failure word."""
    failure_words = ["fail", "error", "broken", "down", "stuck", "issue", "problem"]
    if not any(fw in msg_lower for fw in failure_words):
        return None

    resolved = Case.objects.filter(
        thread_id=thread_id,
        state__in=["CLOSED", "RESOLVED_PENDING_CONFIRMATION"]
    ).order_by("-updated_at")[:5]

    for case in resolved:
        for wf in (case.workflows_involved or []):
            wf_parts = wf.lower().replace("_", " ").split()
            if any(part in msg_lower for part in wf_parts if len(part) > 3):
                return case.case_id
    return None


def _llm_classify(thread_id: str, msg_lower: str,
                  active_case: Case) -> Optional[Tuple[str, Optional[str]]]:
    """
    LLM fallback for ambiguous messages (e.g., "also", "additionally",
    messages that don't match any heuristic signal).
    Uses Vertex AI via the RAG/tools REST client.
    """
    try:
        from custom.helpers.tools_rest import RestToolClient
        import os

        client = RestToolClient(
            base_url=os.environ.get("TOOL_BASE_URL", ""),
            auth_token=os.environ.get("TOOL_AUTH_TOKEN", ""),
        )

        active_desc = (
            f"Active case: {active_case.case_id}, state={active_case.state}, "
            f"workflows={active_case.workflows_involved}"
        )

        resolved = Case.objects.filter(
            thread_id=thread_id,
            state__in=["CLOSED", "RESOLVED_PENDING_CONFIRMATION"]
        ).order_by("-updated_at")[:3]
        resolved_desc = "; ".join(
            f"{c.case_id}: workflows={c.workflows_involved}" for c in resolved
        ) or "(none)"

        prompt = (
            f"Classify this support message.\n"
            f"Active: {active_desc}\n"
            f"Resolved: {resolved_desc}\n"
            f"Message: \"{msg_lower}\"\n\n"
            f"Reply with ONE word: CONTINUE_EXISTING, NEW_ISSUE, RELATED_NEW, "
            f"RECURRENCE, FOLLOWUP, or STATUS_CHECK"
        )

        resp = client.call("/llm/classify", {"prompt": prompt})
        classification_str = resp.get("result", "").strip().upper()

        classification_map = {
            "CONTINUE_EXISTING": IssueClassification.CONTINUE_EXISTING,
            "NEW_ISSUE": IssueClassification.NEW_ISSUE,
            "RELATED_NEW": IssueClassification.RELATED_NEW,
            "RECURRENCE": IssueClassification.RECURRENCE,
            "FOLLOWUP": IssueClassification.FOLLOWUP,
            "STATUS_CHECK": IssueClassification.STATUS_CHECK,
        }

        cls = classification_map.get(classification_str)
        if cls:
            issue_id = active_case.case_id if cls != IssueClassification.NEW_ISSUE else None
            return cls, issue_id
    except Exception as e:
        logger.warning(f"LLM classification fallback failed: {e}")

    return None


def link_cases(case_id_1: str, case_id_2: str, link_type: str = "RELATED"):
    """Create a link between two cases (query both directions via IssueLink.get_linked_cases)."""
    IssueLink.objects.get_or_create(
        case_id_1=case_id_1,
        case_id_2=case_id_2,
        defaults={"link_type": link_type},
    )


def should_escalate_recurrence(case: Case) -> bool:
    """Returns True if the case has recurred too many times."""
    return case.recurrence_count >= RECURRENCE_ESCALATION_THRESHOLD
```

### Updated Router Hook

**This replaces section 11's `api_messages_hook` entirely.** The new version adds issue classification, empty message guard, approval authorization, resolution lifecycle, recurrence escalation, and uses the **async class-based hook pattern** required by AI Studio.

```python
# custom/custom_hooks.py — FULL REPLACEMENT of section 11
from __future__ import annotations

import logging
import uuid

from asgiref.sync import sync_to_async
from django.utils import timezone

try:
    from aistudiobot.hooks import ChatbotHooks
except ImportError:
    class ChatbotHooks:
        pass

logger = logging.getLogger("support_agent.hooks")

from custom.helpers.locks import pg_advisory_lock
from custom.helpers.db import is_duplicate_message, mark_message_processed
from custom.helpers.teams import make_text_reply
from custom.models import ConversationState, Case, Approval
from custom.functions.python.support_agent import handle_support_turn
from custom.helpers.issue_classifier import (
    classify_message, IssueClassification, link_cases, should_escalate_recurrence,
)


def _activity_to_dict(activity) -> dict:
    if isinstance(activity, dict):
        return activity
    result = {}
    for attr in ("text", "id"):
        result[attr] = getattr(activity, attr, None) or ""
    conv = getattr(activity, "conversation", None)
    result["conversation"] = {"id": getattr(conv, "id", "") or ""} if conv else {}
    frm = getattr(activity, "from_property", None) or getattr(activity, "from", None)
    result["from"] = {"id": getattr(frm, "id", "") or ""} if frm else {}
    return result


def _extract_thread_id(activity: dict) -> str:
    convo = activity.get("conversation", {}) or {}
    return convo.get("id") or "unknown-thread"

def _extract_message_id(activity: dict) -> str:
    return activity.get("id") or str(uuid.uuid4())

def _extract_text(activity: dict) -> str:
    return (activity.get("text") or "").strip()

def _extract_user_id(activity: dict) -> str:
    return (activity.get("from", {}) or {}).get("id", "")

def _is_smalltalk(text: str) -> bool:
    t = text.lower().strip()
    return t in {"hi","hello","hey","thanks","thank you","ok"} or t.startswith(("hi ","hello ","hey "))


def _process_message_sync(activity_dict: dict):
    """Synchronous core — all Django ORM calls happen here."""
    thread_id = _extract_thread_id(activity_dict)
    msg_id = _extract_message_id(activity_dict)
    text = _extract_text(activity_dict)
    user_id = _extract_user_id(activity_dict)

    if not text:
        return make_text_reply("It looks like your message was empty. How can I help?")

    with pg_advisory_lock(thread_id):
        if is_duplicate_message(thread_id, msg_id):
            return None
        mark_message_processed(thread_id, msg_id)

        if _is_smalltalk(text):
            cs, _ = ConversationState.objects.get_or_create(thread_id=thread_id)
            cs.last_user_message_id = msg_id
            cs.updated_at = timezone.now()
            cs.save()
            return make_text_reply("Hello! How can I help you with support today?")

        cs, _ = ConversationState.objects.get_or_create(thread_id=thread_id)
        active_case = None
        if cs.active_case_id:
            active_case = Case.objects.filter(case_id=cs.active_case_id).first()

        if (text.strip().upper() in {"APPROVE", "REJECT"}
                and active_case
                and active_case.state == "WAITING_APPROVAL"):
            appr = Approval.objects.filter(
                case_id=active_case.case_id, status="PENDING",
            ).order_by("-created_at").first()

            if appr:
                if (user_id and appr.requested_to
                        and user_id not in appr.requested_to):
                    return make_text_reply(
                        "You are not authorized to approve/reject "
                        "this action. Authorized reviewers: "
                        f"{', '.join(appr.requested_to)}"
                    )
                is_approve = text.strip().upper() == "APPROVE"
                appr.status = "APPROVED" if is_approve else "REJECTED"
                appr.decided_by = user_id
                appr.decided_at = timezone.now()
                appr.save()
                if not is_approve:
                    active_case.state = "PLANNING"
                    active_case.updated_at = timezone.now()
                    active_case.save()
                    return make_text_reply(
                        "Action rejected. How would you like to proceed?"
                    )
                return handle_support_turn(
                    thread_id=thread_id, teams_message_id=msg_id,
                    user_text=text, raw_activity=activity_dict,
                )

        classification, ref_case_id = classify_message(thread_id, text, active_case)

        if classification == IssueClassification.RECURRENCE:
            old_case = (
                Case.objects.filter(case_id=ref_case_id).first()
                if ref_case_id else None
            )
            if not old_case:
                logger.warning(
                    "RECURRENCE ref_case %s not found — treating as new issue",
                    ref_case_id,
                )
                cs.active_case_id = None
                cs.updated_at = timezone.now()
                cs.save()
                return handle_support_turn(
                    thread_id=thread_id, teams_message_id=msg_id,
                    user_text=text, raw_activity=activity_dict,
                )

            old_case.recurrence_count += 1
            old_case.updated_at = timezone.now()
            old_case.save()

            if should_escalate_recurrence(old_case):
                old_case.state = "WAITING_ON_TEAM"
                old_case.owner_type = "HUMAN_TEAM"
                old_case.owner_team = "L2_SUPPORT"
                old_case.save()
                return make_text_reply(
                    f"This issue has now recurred "
                    f"{old_case.recurrence_count} times. "
                    f"The previous fix is not holding. "
                    f"Escalating to L2 support for a permanent resolution."
                )

            old_case.state = "PLANNING"
            old_case.resolved_at = None
            old_case.save()
            cs.active_case_id = old_case.case_id
            cs.updated_at = timezone.now()
            cs.save()
            prefix = (
                f"This looks like a recurrence "
                f"(#{old_case.recurrence_count}) of a previous issue. "
            )
            if old_case.resolution_summary:
                prefix += f"Last resolution: {old_case.resolution_summary[:200]}. "
            prefix += "Let me check if the same cause applies.\n\n"
            result = handle_support_turn(
                thread_id=thread_id, teams_message_id=msg_id,
                user_text=text, raw_activity=activity_dict,
            )
            result["text"] = prefix + result.get("text", "")
            return result

        elif classification == IssueClassification.NEW_ISSUE:
            cs.active_case_id = None
            cs.updated_at = timezone.now()
            cs.save()

        elif classification == IssueClassification.RELATED_NEW:
            parent_id = ref_case_id or (
                active_case.case_id if active_case else None
            )
            cs.active_case_id = None
            cs.updated_at = timezone.now()
            cs.save()
            result = handle_support_turn(
                thread_id=thread_id, teams_message_id=msg_id,
                user_text=text, raw_activity=activity_dict,
            )
            new_cs = ConversationState.objects.get(thread_id=thread_id)
            if parent_id and new_cs.active_case_id:
                link_cases(parent_id, new_cs.active_case_id, "CASCADE")
            prefix = (
                "This looks related to a previous issue but "
                "appears to be a separate problem. "
                "Tracking as a linked case.\n\n"
            )
            result["text"] = prefix + result.get("text", "")
            return result

        elif classification == IssueClassification.FOLLOWUP:
            target_case = (
                Case.objects.filter(case_id=ref_case_id).first()
                if ref_case_id else active_case
            )
            if target_case and target_case.resolution_summary:
                return make_text_reply(
                    f"Regarding [{target_case.case_id}]: "
                    f"{target_case.resolution_summary}\n\n"
                    f"Would you like me to verify the current status?"
                )

        elif classification == IssueClassification.STATUS_CHECK:
            cases = Case.objects.filter(
                thread_id=thread_id,
            ).exclude(
                state__in=["CLOSED", "CANCELLED"],
            ).order_by("-updated_at")[:10]
            summary = "\n".join(
                f"- [{c.case_id}] {c.state} | "
                f"Workflows: {c.workflows_involved}"
                for c in cases
            ) or "No active cases."
            return make_text_reply(f"Current session status:\n{summary}")

        return handle_support_turn(
            thread_id=thread_id, teams_message_id=msg_id,
            user_text=text, raw_activity=activity_dict,
        )


class CustomChatbotHooks(ChatbotHooks):
    export_dialogs = []

    async def api_messages_hook(request, activity):
        activity_dict = _activity_to_dict(activity)
        return await sync_to_async(
            _process_message_sync, thread_sensitive=False
        )(activity_dict)
```

### Updated Folder Structure

```
custom/
  ...
  helpers/
    db.py
    locks.py
    policy.py
    rag.py
    tools_rest.py
    roster.py
    teams.py
    issue_classifier.py          ← NEW
  ...
```

### How It Works — Flow Summary

| User Message | Classification | Detection Layer | Action |
|---|---|---|---|
| "Claims batch failed" | `NEW_ISSUE` | Heuristic (no active case) | Create Case A, investigate |
| "approve" | `CONTINUE_EXISTING` | Heuristic (approval signal) | Continue Case A, execute |
| "Reconciliation also failed" | `RELATED_NEW` | LLM fallback ("also" is ambiguous) | Create Case B, link to A |
| "Is claims fixed?" | `FOLLOWUP` | Heuristic + workflow match | Return Case A resolution summary |
| "Claims failed again" | `RECURRENCE` | Heuristic + workflow match | Reopen Case A, recurrence #1 |
| "Claims failed again" (4th time) | `RECURRENCE` | Escalation threshold | Auto-escalate to L2 support |
| "Different issue — agent offline" | `NEW_ISSUE` | Heuristic (explicit signal) | Create Case C, fresh |
| "How's everything going?" | `STATUS_CHECK` | LLM fallback | Return session summary |


---

---

## 17) AI Studio Webchat — Local Development (Thin Proxy Pattern)

For local development and testing with the full AI Studio Cognibot dialog engine (not just the standalone agent server), we use a **thin proxy pattern**: Cognibot's custom dialog forwards all messages to the standalone agent server via HTTP.

### Architecture

```
Browser Webchat ──WebSocket──► Cognibot (Daphne :3978)
                                  │
                                  ▼
                             RootDialog
                                  │
                                  ▼ root_dialog_hook()
                             AgentProxyDialog
                                  │
                                  ▼ HTTP POST /chat
                          Agent Server (:5050)
                                  │
                                  ▼
                            Orchestrator
                          (RAG, LLM, Tools)
```

### Required Changes to Cognibot

Three files must be modified/created in the local Cognibot installation:

**1. Default Config** (`aistudiobot/resources/cognibot_config_default.json`)

Must include a default skill so `root_dialog_hook` is invoked. See `SETUP_GUIDE.md` Section 12.10 for the exact JSON.

**2. ASGI Routing** (`common/asgi.py`)

Create this file to override the compiled `.pyc` and add WebSocket support:

```python
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "common.settings")
import django
django.setup()
from webchat_channel.routing import application
```

**3. Custom Hooks** (`custom/custom_hooks.py`)

```python
import asyncio, logging, os, requests
from concurrent.futures import ThreadPoolExecutor
from aistudiobot.hooks import ChatbotHooks
from botbuilder.dialogs import ComponentDialog, WaterfallDialog, WaterfallStepContext

logger = logging.getLogger(__name__)
AGENT_SERVER_URL = os.environ.get("AGENT_SERVER_URL", "http://localhost:5050")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "120"))
_executor = ThreadPoolExecutor(max_workers=4)

def _call_agent_simple(text, conv_id, user_id):
    try:
        resp = requests.post(
            f"{AGENT_SERVER_URL}/chat",
            json={"message": text, "session_id": conv_id,
                  "user_id": user_id, "user_role": "technical"},
            timeout=AGENT_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("response", "No response from agent.")
    except requests.RequestException as e:
        logger.error("Agent server call failed: %s", e)
        return "Sorry, the agent is temporarily unavailable."


class AgentProxyDialog(ComponentDialog):
    def __init__(self, *args, **kwargs):
        super().__init__("AgentProxyDialog")
        self.add_dialog(WaterfallDialog("AgentProxyWaterfall", [self._call_agent_step]))
        self.initial_dialog_id = "AgentProxyWaterfall"

    @staticmethod
    async def _call_agent_step(step_context: WaterfallStepContext):
        turn_context = step_context.context
        text = (getattr(turn_context.activity, "text", "") or "").strip()
        if not text:
            await turn_context.send_activity("I didn't catch that.")
            return await step_context.cancel_all_dialogs()

        conv_id = getattr(turn_context.activity.conversation, "id", "webchat-default")
        frm = getattr(turn_context.activity, "from_property", None)
        user_id = frm.id if frm else "webchat_user"

        loop = asyncio.get_event_loop()
        reply_text = await loop.run_in_executor(
            _executor, _call_agent_simple, text, conv_id, user_id
        )
        await turn_context.send_activity(reply_text)
        return await step_context.cancel_all_dialogs()


class CustomChatbotHooks(ChatbotHooks):
    export_dialogs = [AgentProxyDialog]

    async def root_dialog_hook(conv_state, user_state, turn_context):
        text = (getattr(turn_context.activity, "text", "") or "").strip()
        if not text:
            return None
        return AgentProxyDialog
```

### Understanding the Internal Flow

For a detailed explanation of how Cognibot's `RootDialog` processes messages, how the config loading works (signed envelope vs. default fallback), how the DirectLine/WebSocket protocol delivers responses, and common failure modes, see **SETUP_GUIDE.md Section 12 — Cognibot Integration Architecture (Deep Dive)**.

Key things to remember:
- `root_dialog_hook` is only reached if a skill (with `is_default: true`) exists in the config
- The hook must return a `Dialog` class (not an instance) that's also in `export_dialogs`
- `cancel_all_dialogs()` in the waterfall step prevents Cognibot from showing fallback messages
- Bot responses flow through `send_activity()` → adapter → DirectLine reply endpoint → WebSocket `group_send` → browser
- The HTTP POST that sends a user message returns immediately (no response body); the actual bot reply arrives asynchronously via WebSocket

---

**Generated:** 2026-02-20 06:19:06 | **Last updated:** 2026-03-04

